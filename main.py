#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Music Bot - 精简版音乐下载机器人
专注于音乐下载功能，支持网易云音乐、Apple Music
"""

import os
import sys
import time
import logging
import asyncio
import threading
from pathlib import Path
from typing import Optional, Dict, Any

# 设置环境变量
os.environ['PYTHONWARNINGS'] = 'ignore:Unverified HTTPS request'

import warnings
warnings.filterwarnings('ignore')

# 版本信息
BOT_VERSION = "1.1.0"

# 设置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger('music_bot')

# 导入配置管理器
from config_manager import ConfigManager, get_config_manager

# 导入下载器
from downloaders.netease import NeteaseDownloader
from downloaders.apple_music import AppleMusicDownloader

# 导入 Web 服务
from web.app import app as web_app, init_app as init_web_app

# 导入数据库日志处理器
from web.db_logger import setup_database_logging, get_metadata_logger

# 导入 TG 通知模块
from web.tg_notifier import (
    TelegramNotifier,
    ProgressFormatter, MessageTemplates,
    DownloadType, ProgressInfo, DownloadResult
)

# 导入下载队列
from download_queue import get_download_queue, DownloadTask, TaskStatus

# Telegram 相关导入
try:
    from telegram import Update, Bot, InlineKeyboardButton, InlineKeyboardMarkup, InputFile
    from telegram.ext import (
        Application, CommandHandler, MessageHandler,
        filters, ContextTypes, CallbackQueryHandler
    )
    from telegram.constants import ParseMode
    from telegram.error import TelegramError
    TELEGRAM_AVAILABLE = True
except ImportError:
    TELEGRAM_AVAILABLE = False
    logger.warning("⚠️ python-telegram-bot 未安装")


class MusicBot:
    """音乐下载机器人"""
    
    def __init__(self, db_path: str = None):
        """初始化机器人"""
        # 初始化配置管理器
        self.config_manager = get_config_manager(db_path)
        
        # 加载配置
        self.config = self.config_manager.get_all_config()
        
        # 设置数据库日志记录
        setup_database_logging(self.config_manager)
        
        # 初始化下载器
        self.downloaders = {}
        self._init_downloaders()

        # Telegram 应用
        self.app: Optional[Application] = None

        # 下载路径
        self.download_path = self.config.get('download_path', '/downloads')

        # 下载队列（控制并发 + 重试）
        max_concurrent = self._cfg('download_max_concurrent', 3)
        self.download_queue = get_download_queue(max_concurrent=max_concurrent)
        
        # 下载重试配置
        self.download_max_retries = self._cfg('download_max_retries', 3)
        self.download_retry_delay_base = self._cfg('download_retry_delay_base', 2.0)

        logger.info(f"🎵 Music Bot v{BOT_VERSION} 初始化完成")
        self.download_path = self.config.get('download_path', '/downloads')
        
        logger.info(f"🎵 Music Bot v{BOT_VERSION} 初始化完成")

    def _cfg(self, key: str, default=None):
        """实时读取配置（热加载）。

        取代直接读 self.config.get(key)——后者是启动期快照，
        Web 界面修改后不会生效，导致“改了设置必须重启容器”。
        所有运行期需要读取的配置都应走这里。
        """
        try:
            return self.config_manager.get_config(key, default)
        except Exception:
            return self.config.get(key, default) if self.config else default

    def _init_downloaders(self):
        """初始化下载器"""
        # 网易云音乐
        if self.config.get('netease_enabled', True):
            try:
                self.downloaders['netease'] = NeteaseDownloader(self.config_manager)
                logger.info("✅ 网易云音乐下载器已启用")
            except Exception as e:
                logger.error(f"❌ 网易云音乐下载器初始化失败: {e}")
        
        # Apple Music
        if self.config.get('apple_music_enabled', True):
            try:
                self.downloaders['apple_music'] = AppleMusicDownloader(self.config_manager)
                logger.info("✅ Apple Music 下载器已启用")
            except Exception as e:
                logger.error(f"❌ Apple Music 下载器初始化失败: {e}")
    
    def get_downloader_for_url(self, url: str) -> Optional[tuple]:
        """根据 URL 获取对应的下载器"""
        for name, downloader in self.downloaders.items():
            if downloader.is_supported_url(url):
                return name, downloader
        return None
    
    async def handle_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """处理 /start 命令"""
        user = update.message.from_user
        logger.info(f"📨 收到 /start 命令: 用户={user.id}({user.username})")
        
        # 检查用户权限
        if not self.check_allowed_user(user.id):
            logger.warning(f"⚠️ 用户 {user.id} 不在允许列表中")
            await update.message.reply_text("⚠️ 您没有权限使用此 Bot")
            return
            
        welcome_msg = (
            "🎵 *Music Bot* - 音乐下载机器人\n\n"
            "发送音乐链接即可下载！\n\n"
            "*支持的平台：*\n"
            "• 🎵 网易云音乐 - 歌曲/专辑/歌单\n"
            "• 🍎 Apple Music - 歌曲/专辑\n\n"
            "*命令：*\n"
            "/start - 显示帮助\n"
            "/status - 查看状态\n"
            "/history - 查看下载历史\n"
            "/cookie - 更新网易云 cookies\n"
        )
        await update.message.reply_text(welcome_msg, parse_mode=ParseMode.MARKDOWN)
    
    async def handle_status(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """处理 /status 命令"""
        status_lines = ["📊 *Bot 状态*\n"]
        
        for name, downloader in self.downloaders.items():
            status_lines.append(f"• {name}: ✅ 已启用")
        
        if not self.downloaders:
            status_lines.append("• 暂无可用下载器")
        
        # 下载队列状态
        q = self.download_queue
        qs = q.get_status()
        status_lines.append(f"\n📥 *下载队列*")
        status_lines.append(f"• 最大并发: {qs['max_concurrent']}")
        status_lines.append(f"• 排队中: {qs['pending']}")
        status_lines.append(f"• 执行中: {qs['active']}")
        status_lines.append(f"• 已完成: {qs['completed']}")
        status_lines.append(f"• 失败: {qs['failed']}")
        
        await update.message.reply_text('\n'.join(status_lines), parse_mode=ParseMode.MARKDOWN)
    
    async def handle_queue(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """处理 /queue 命令 - 查看下载队列状态"""
        user = update.message.from_user
        if not self.check_allowed_user(user.id):
            await update.message.reply_text("⚠️ 您没有权限使用此命令")
            return
        
        qs = self.download_queue.get_status()
        
        lines = ["📥 *下载队列状态*\n"]
        lines.append(f"最大并发: `{qs['max_concurrent']}`")
        lines.append(f"排队中: `{qs['pending']}` | 执行中: `{qs['active']}`")
        lines.append(f"已完成: `{qs['completed']}` | 失败: `{qs['failed']}`")
        
        tasks = qs.get('tasks', [])
        if tasks:
            lines.append("\n*最近任务:*")
            for t in tasks[:10]:
                status_icon = {'pending':'⏳','running':'▶️','retrying':'🔄','completed':'✅','failed':'❌'}.get(t['status'],'❓')
                lines.append(f"{status_icon} `{t['platform']}/{t['content_type']}` `{t['content_id']}` — 重试:{t['retry_count']}/{t['max_retries']}")
                if t['last_error']:
                    lines.append(f"  _错误: {t['last_error'][:80]}_")
        else:
            lines.append("\n_暂无任务记录_")
        
        await update.message.reply_text('\n'.join(lines), parse_mode=ParseMode.MARKDOWN)
    
    async def handle_history(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """处理 /history 命令 - 显示下载历史"""
        history = self.config_manager.get_download_history(limit=20)
        
        if not history:
            await update.message.reply_text("📭 暂无下载历史")
            return
        
        lines = ["📜 *最近下载历史*\n"]
        
        for i, item in enumerate(history, 1):
            platform = item.get('platform', '未知')
            content_type = item.get('content_type', '')
            title = item.get('title', '未知')
            artist = item.get('artist', '')
            created_at = item.get('created_at', '')[:16]  # 只显示日期和时间
            
            # 平台图标
            platform_icon = {'netease': '🎵', 'apple_music': '🍎'}.get(platform, '📀')
            type_icon = {'song': '🎵', 'album': '💿', 'playlist': '📋'}.get(content_type, '📁')
            
            lines.append(f"{i}. {platform_icon}{type_icon} *{title}*")
            if artist:
                lines.append(f"   _{artist}_")
            lines.append(f"   🕐 {created_at}\n")
        
        await update.message.reply_text('\n'.join(lines), parse_mode=ParseMode.MARKDOWN)

    async def handle_cookie(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """处理 /cookie 命令 - 更新网易云 cookies"""
        user = update.message.from_user
        logger.info(f"📨 收到 /cookie 命令: 用户={user.id}({user.username})")
        
        # 检查用户权限
        if not self.check_allowed_user(user.id):
            await update.message.reply_text("⚠️ 您没有权限使用此命令")
            return
        
        # 获取参数
        args = context.args if context.args else []
        
        if not args:
            # 没有参数，显示使用说明和当前状态
            current_cookies = self._cfg('netease_cookies', '')
            has_cookies = bool(current_cookies)
            
            # 检查登录状态
            login_status = "未知"
            if 'netease' in self.downloaders:
                downloader = self.downloaders['netease']
                if hasattr(downloader, 'logged_in') and downloader.logged_in:
                    nickname = getattr(downloader, 'user_info', {}).get('nickname', '未知')
                    login_status = f"✅ 已登录 ({nickname})"
                elif has_cookies:
                    login_status = "⚠️ cookies 可能已失效"
                else:
                    login_status = "❌ 未配置 cookies"
            
            msg = (
                "🍪 *网易云 Cookie 管理*\n\n"
                f"*当前状态:* {login_status}\n"
                f"*Cookies:* {'已配置' if has_cookies else '未配置'}\n\n"
                "*使用方法:*\n"
                "`/cookie <cookies字符串>`\n\n"
                "*如何获取 Cookies:*\n"
                "1. 打开 music.163.com 并登录\n"
                "2. 按 F12 打开开发者工具\n"
                "3. 切换到 Application/存储 标签\n"
                "4. 找到 Cookies → music.163.com\n"
                "5. 复制 `MUSIC_U` 的值\n"
                "6. 发送: `/cookie MUSIC_U=你的值`\n\n"
                "*或者复制完整 cookies:*\n"
                "在 Console 中执行 `document.cookie` 并复制结果"
            )
            await update.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN)
            return
        
        # 有参数，更新 cookies
        new_cookies = ' '.join(args)
        
        # 验证 cookies 格式
        if '=' not in new_cookies and not new_cookies.startswith('{'):
            await update.message.reply_text(
                "❌ *Cookies 格式错误*\n\n"
                "正确格式示例:\n"
                "• `MUSIC_U=xxx; __csrf=xxx`\n"
                "• `{\"MUSIC_U\": \"xxx\"}`",
                parse_mode=ParseMode.MARKDOWN
            )
            return
        
        # 保存 cookies
        try:
            self.config_manager.set_config('netease_cookies', new_cookies)
            
            # 更新当前配置
            self.config['netease_cookies'] = new_cookies
            
            # 重新初始化网易云下载器以加载新 cookies
            if 'netease' in self.downloaders:
                try:
                    self.downloaders['netease'] = NeteaseDownloader(self.config_manager)
                    downloader = self.downloaders['netease']
                    
                    # 检查新的登录状态
                    if hasattr(downloader, 'logged_in') and downloader.logged_in:
                        nickname = getattr(downloader, 'user_info', {}).get('nickname', '未知')
                        vip_type = getattr(downloader, 'user_info', {}).get('vipType', 0)
                        vip_str = '黑胶VIP' if vip_type == 11 else ('普通VIP' if vip_type > 0 else '普通用户')
                        
                        await update.message.reply_text(
                            f"✅ *Cookies 更新成功！*\n\n"
                            f"👤 用户: {nickname}\n"
                            f"💎 会员: {vip_str}",
                            parse_mode=ParseMode.MARKDOWN
                        )
                        logger.info(f"✅ 用户 {user.id} 更新了网易云 cookies，登录用户: {nickname}")
                    else:
                        await update.message.reply_text(
                            "⚠️ *Cookies 已保存，但验证失败*\n\n"
                            "可能原因:\n"
                            "• Cookies 已过期\n"
                            "• 格式不正确\n"
                            "• 网络问题\n\n"
                            "请检查 cookies 是否正确",
                            parse_mode=ParseMode.MARKDOWN
                        )
                        logger.warning(f"⚠️ 用户 {user.id} 更新了 cookies，但验证失败")
                except Exception as e:
                    logger.error(f"❌ 重新初始化网易云下载器失败: {e}")
                    await update.message.reply_text(
                        f"⚠️ *Cookies 已保存，但下载器重新加载失败*\n\n"
                        f"错误: {str(e)[:100]}",
                        parse_mode=ParseMode.MARKDOWN
                    )
            else:
                await update.message.reply_text(
                    "✅ *Cookies 已保存*\n\n"
                    "⚠️ 网易云下载器未启用，请在设置中启用",
                    parse_mode=ParseMode.MARKDOWN
                )
                
        except Exception as e:
            logger.error(f"❌ 保存 cookies 失败: {e}")
            await update.message.reply_text(
                f"❌ *保存失败*\n\n错误: {str(e)[:100]}",
                parse_mode=ParseMode.MARKDOWN
            )

    def get_download_path_for_platform(self, platform: str) -> str:
        """获取平台专属的下载路径"""
        platform_paths = {
            'netease': self._cfg('netease_download_path', '/downloads/netease'),
            'apple_music': self._cfg('apple_music_download_path', '/downloads/apple_music'),
        }
        return platform_paths.get(platform, self.download_path)
    
    async def _safe_handle_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """带超时保护的消息处理包装器"""
        try:
            # 设置消息处理超时（30分钟，足够长的下载时间）
            await asyncio.wait_for(
                self.handle_message(update, context),
                timeout=self._cfg('download_timeout', 1800)  # 可配置，默认 30 分钟
            )
        except asyncio.TimeoutError:
            logger.error("❌ 消息处理超时 (超过30分钟)")
            if update.message:
                try:
                    await update.message.reply_text("❌ 处理超时，请重试")
                except Exception:
                    pass
        except Exception as e:
            logger.error(f"❌ 消息处理异常: {e}")
            import traceback
            logger.error(traceback.format_exc())
            if update.message:
                try:
                    await update.message.reply_text(f"❌ 处理出错: {str(e)[:100]}")
                except Exception:
                    pass

    async def handle_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """处理消息"""
        if not update.message or not update.message.text:
            return
        
        user = update.message.from_user
        text = update.message.text.strip()
        
        # 记录收到的消息
        logger.info(f"📨 收到消息: 用户={user.id}({user.username}), 内容={text[:50]}...")
        
        # 检查用户权限
        if not self.check_allowed_user(user.id):
            logger.warning(f"⚠️ 用户 {user.id} 不在允许列表中")
            return
        
        # 检查是否是链接
        if not ('http://' in text or 'https://' in text or 'music.163.com' in text):
            logger.debug(f"📝 消息不是链接，已忽略")
            return
        
        # 提取 URL
        url = self._extract_url(text)
        if not url:
            await update.message.reply_text("❌ 无法识别链接")
            return
        
        # 获取对应的下载器
        result = self.get_downloader_for_url(url)
        if not result:
            await update.message.reply_text("❌ 不支持此链接")
            return
        
        downloader_name, downloader = result
        
        # 解析 URL
        parsed = downloader.parse_url(url)
        if not parsed:
            await update.message.reply_text("❌ 无法解析链接")
            return
        
        content_type = parsed.get('type')
        content_id = parsed.get('id')
        
        # 检查是否已下载过
        existing = self.config_manager.check_download_exists(downloader_name, content_type, content_id)
        if existing:
            # 已下载过，询问是否重新下载
            download_time = existing.get('created_at', '未知时间')
            title = existing.get('title', '未知')
            
            keyboard = [
                [
                    InlineKeyboardButton("✅ 重新下载", callback_data=f"redownload:{downloader_name}:{content_type}:{content_id}"),
                    InlineKeyboardButton("❌ 取消", callback_data="cancel_download")
                ]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await update.message.reply_text(
                f"⚠️ 此内容之前已下载过\n\n"
                f"📀 {title}\n"
                f"📍 平台: {downloader_name}\n"
                f"🕐 下载时间: {download_time}\n\n"
                f"是否重新下载？",
                reply_markup=reply_markup
            )
            return
        
        # 未下载过，直接开始下载
        await self._do_download(update.message, downloader_name, downloader, content_type, content_id)
    
    async def handle_callback_query(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """处理回调查询（按钮点击）"""
        query = update.callback_query
        await query.answer()
        
        data = query.data
        
        if data == "cancel_download":
            await query.edit_message_text("❌ 已取消下载")
            return
        
        if data.startswith("redownload:"):
            # 解析重新下载的数据
            parts = data.split(":")
            if len(parts) == 4:
                _, downloader_name, content_type, content_id = parts
                
                # 获取下载器
                downloader = self.downloaders.get(downloader_name)
                if downloader:
                    await query.edit_message_text(f"🔄 开始重新下载...")
                    await self._do_download(query.message, downloader_name, downloader, content_type, content_id, is_redownload=True)
                else:
                    await query.edit_message_text("❌ 下载器不可用")
            return
    
    async def _do_download(self, message, downloader_name: str, downloader, content_type: str, content_id: str, is_redownload: bool = False):
        """执行下载任务（通过下载队列，支持并发控制和重试）"""
        import uuid
        
        # 发送处理中消息
        start_msg = MessageTemplates.download_started(downloader_name, content_type, content_id, is_redownload)
        progress_msg = await message.reply_text(start_msg)
        
        try:
            # 使用平台专属下载路径
            download_dir = self.get_download_path_for_platform(downloader_name)
            
            # 获取当前事件循环
            main_loop = asyncio.get_running_loop()
            
            # 为每个下载任务创建独立的通知器实例（避免并发任务进度串写）
            update_interval = self._cfg('telegram_progress_interval', 1.0)
            task_notifier = TelegramNotifier(update_interval)
            task_notifier.set_main_loop(main_loop)
            task_notifier.set_message(progress_msg)
            
            # 确定下载类型
            download_type = {
                'song': DownloadType.SONG,
                'album': DownloadType.ALBUM,
                'playlist': DownloadType.PLAYLIST
            }.get(content_type, DownloadType.SONG)
            
            # 创建进度回调
            progress_callback = task_notifier.create_progress_callback(download_type)
            
            # 定义异步下载函数（在线程中执行实际下载）
            async def run_download_async():
                """在线程池中执行下载（供队列调用）"""
                def run_download():
                    try:
                        if hasattr(downloader, 'reload_config'):
                            downloader.reload_config()
                        if content_type == 'song':
                            return downloader.download_song(
                                content_id, download_dir,
                                progress_callback=progress_callback
                            )
                        elif content_type == 'album':
                            return downloader.download_album(
                                content_id, download_dir,
                                progress_callback=progress_callback
                            )
                        elif content_type == 'playlist':
                            return downloader.download_playlist(
                                content_id, download_dir,
                                progress_callback=progress_callback
                            )
                        else:
                            return {'success': False, 'error': f'不支持的类型: {content_type}'}
                    except Exception as e:
                        logger.error(f"下载线程异常: {e}")
                        import traceback
                        logger.error(traceback.format_exc())
                        return {'success': False, 'error': str(e)}
                
                return await asyncio.to_thread(run_download)
            
            # 创建下载任务
            task_id = f"{downloader_name}_{content_id}_{uuid.uuid4().hex[:8]}"
            task = DownloadTask(
                task_id=task_id,
                coroutine_func=run_download_async,
                platform=downloader_name,
                content_type=content_type,
                content_id=content_id,
                max_retries=self.download_max_retries,
            )
            
            # 确保队列已启动
            if not self.download_queue._running:
                await self.download_queue.start()
            
            # 提交到队列并等待完成
            self.download_queue.enqueue(task)
            logger.info(f"🚀 任务已入队: {task_id}")
            
            try:
                # 等待任务完成（无超时限制，由下载超时配置控制）
                download_timeout = self._cfg('download_timeout', 600)
                result = await asyncio.wait_for(task._future, timeout=download_timeout)
            except asyncio.TimeoutError:
                logger.error(f"⏱️ 下载超时: {task_id}")
                result = {'success': False, 'error': f'下载超时（{download_timeout}s）'}
            except Exception as e:
                logger.error(f"❌ 下载任务异常: {e}")
                result = {'success': False, 'error': str(e)}
            
            # 等待一小段时间，确保最后的进度更新完成
            await asyncio.sleep(0.5)
            
            # 处理结果
            if result.get('success'):
                logger.info(f"📝 准备更新完成消息...")
                
                # 保存下载历史
                self._save_download_history(downloader_name, content_type, content_id, result, download_dir)
                
                # 使用通知模块格式化完成消息
                success_msg = TelegramNotifier.format_result(result, content_type, downloader_name)
                
                try:
                    await progress_msg.edit_text(success_msg)
                    logger.info(f"✅ 消息更新成功")
                except Exception as e:
                    logger.error(f"❌ 编辑消息失败: {e}")
            else:
                error_msg = MessageTemplates.download_error(result.get('error', '未知错误'))
                try:
                    await progress_msg.edit_text(error_msg)
                except Exception as e:
                    logger.error(f"❌ 编辑失败消息失败: {e}")
            
        except Exception as e:
            logger.error(f"下载错误: {e}")
            import traceback
            logger.error(traceback.format_exc())
            try:
                await progress_msg.edit_text(f"❌ 下载出错: {str(e)}")
            except Exception:
                pass
    
    def _save_download_history(self, platform: str, content_type: str, content_id: str, result: Dict[str, Any], download_dir: str):
        """保存下载历史记录"""
        try:
            if content_type == 'song':
                self.config_manager.add_download_history(
                    platform=platform,
                    content_type=content_type,
                    content_id=content_id,
                    title=result.get('song_title', '未知'),
                    artist=result.get('song_artist', '未知'),
                    file_path=result.get('filepath', ''),
                    file_size=int(result.get('size_mb', 0) * 1024 * 1024),
                    quality=result.get('quality', '')
                )
            elif content_type in ['album', 'playlist']:
                songs_list = result.get('songs', [])
                success_songs = [s for s in songs_list if s.get('success')]
                title = result.get('album_name', result.get('playlist_title', '未知'))
                artist_name = result.get('artist', '未知艺术家')
                
                self.config_manager.add_download_history(
                    platform=platform,
                    content_type=content_type,
                    content_id=content_id,
                    title=title,
                    artist=artist_name,
                    file_path=download_dir,
                    file_size=len(success_songs),
                    quality=f"{len(success_songs)}/{result.get('total_songs', 0)}"
                )
        except Exception as e:
            logger.error(f"保存下载历史失败: {e}")
    
    def _extract_url(self, text: str) -> Optional[str]:
        """从文本中提取 URL"""
        import re
        
        # 匹配 URL
        url_pattern = r'https?://[^\s<>"\']+|music\.163\.com[^\s<>"\']*'
        match = re.search(url_pattern, text)
        
        if match:
            url = match.group(0)
            if not url.startswith('http'):
                url = 'https://' + url
            return url
        
        return None
    
    def check_allowed_user(self, user_id: int) -> bool:
        """检查用户是否被允许"""
        allowed_users = self._cfg('telegram_allowed_users', '')
        
        if not allowed_users:
            return True  # 未配置则允许所有人
        
        allowed_ids = [int(uid.strip()) for uid in allowed_users.split(',') if uid.strip()]
        return user_id in allowed_ids
    
    async def _playlist_sync_loop(self):
        """歌单定时同步循环"""
        logger.info("📋 歌单定时同步服务已启动")
        
        # 首次启动等待 60 秒再开始检查
        await asyncio.sleep(60)
        
        while True:
            try:
                # 获取所有启用的订阅歌单
                playlists = self.config_manager.get_subscribed_playlists()
                
                for playlist in playlists:
                    if not playlist.get('enabled', True):
                        continue
                    
                    if not playlist.get('auto_download', False):
                        continue
                    
                    # 检查是否到了同步时间
                    last_check = playlist.get('last_check_time')
                    check_interval = playlist.get('check_interval', 3600)  # 默认1小时
                    
                    should_sync = False
                    if last_check is None:
                        should_sync = True
                    else:
                        from datetime import datetime
                        try:
                            last_check_time = datetime.fromisoformat(last_check)
                            elapsed = (datetime.now() - last_check_time).total_seconds()
                            if elapsed >= check_interval:
                                should_sync = True
                        except Exception:
                            should_sync = True
                    
                    if should_sync:
                        await self._sync_single_playlist(playlist)
                
            except Exception as e:
                logger.error(f"❌ 歌单同步循环出错: {e}")
            
            # 每分钟检查一次
            await asyncio.sleep(60)
    
    async def _sync_single_playlist(self, playlist: dict):
        """同步单个歌单"""
        platform = playlist.get('platform', 'netease')
        playlist_id = playlist.get('playlist_id')
        playlist_name = playlist.get('playlist_name', playlist_id)
        
        logger.info(f"🔄 开始同步歌单: {playlist_name} ({playlist_id})")
        
        try:
            if platform == 'netease':
                downloader = self.downloaders.get('netease')
                if not downloader:
                    logger.warning(f"⚠️ 网易云下载器未启用，跳过歌单 {playlist_name}")
                    return
                
                # 获取下载目录
                download_dir = self._cfg('netease_download_path', '/downloads/netease')

                # 重新加载配置与 cookies，使 Web 修改即时生效
                if hasattr(downloader, 'reload_config'):
                    downloader.reload_config()

                # 在线程池中执行同步（因为下载是同步操作）
                result = await asyncio.to_thread(
                    downloader.sync_playlist, playlist_id, download_dir
                )
                
                if result:
                    new_count = result.get('new_songs', 0)
                    downloaded = result.get('downloaded_songs', 0)
                    skipped = result.get('skipped_songs', 0)
                    failed = result.get('failed_songs', 0)
                    total = result.get('total_songs', 0)
                    
                    if new_count > 0:
                        logger.info(f"✅ 歌单 {playlist_name} 同步完成: 新增 {new_count} 首，成功 {downloaded}，失败 {failed}")
                        
                        # 发送 Telegram 通知（使用新模板）
                        await self._send_playlist_sync_notification(
                            result=result,
                            playlist_name=playlist_name,
                            is_auto=True
                        )
                    else:
                        logger.info(f"✅ 歌单 {playlist_name} 已是最新，无新歌曲")
                else:
                    logger.warning(f"⚠️ 歌单 {playlist_name} 同步失败")
            else:
                logger.warning(f"⚠️ 暂不支持 {platform} 平台的歌单同步")
                
        except Exception as e:
            logger.error(f"❌ 同步歌单 {playlist_name} 出错: {e}")
    
    async def _send_playlist_sync_notification(self, result: dict, playlist_name: str, is_auto: bool = False):
        """发送歌单同步通知（使用新模板）"""
        try:
            if not self.app or not self.app.bot:
                return
            
            # 检查是否启用通知
            if not self._cfg('telegram_notify_enabled', True):
                return
            if not self._cfg('telegram_notify_complete', True):
                return

            # 获取允许的用户列表
            allowed_users = self._cfg('telegram_allowed_users', '')
            if not allowed_users:
                return
            
            # 使用新的消息模板
            from web.tg_notifier import MessageTemplates
            
            total = result.get('total_songs', 0)
            new_songs = result.get('new_songs', 0)
            downloaded = result.get('downloaded_songs', 0)
            skipped = result.get('skipped_songs', 0)
            failed = result.get('failed_songs', 0)
            
            # 获取失败歌曲列表
            songs = result.get('songs', [])
            failed_songs_list = [s for s in songs if not s.get('success')]
            
            message = MessageTemplates.playlist_sync_completed(
                playlist_name=playlist_name,
                total_songs=total,
                new_songs=new_songs,
                downloaded=downloaded,
                failed=failed,
                skipped=skipped,
                failed_songs_list=failed_songs_list
            )
            
            # 如果是自动同步，添加标识
            if is_auto:
                message = "🤖 [自动同步]\n\n" + message
            
            # 向所有配置的用户发送通知
            for user_id in allowed_users.split(','):
                user_id = user_id.strip()
                if user_id:
                    try:
                        await self.app.bot.send_message(
                            chat_id=int(user_id),
                            text=message
                        )
                    except Exception as e:
                        logger.debug(f"发送通知给用户 {user_id} 失败: {e}")
                        
        except Exception as e:
            logger.debug(f"发送歌单同步通知失败: {e}")
    
    async def run_bot(self):
        """运行 Telegram Bot"""
        if not TELEGRAM_AVAILABLE:
            logger.error("❌ Telegram 模块不可用")
            return
        
        bot_token = self.config.get('telegram_bot_token')
        if not bot_token or bot_token == '******':
            logger.error("❌ 未配置 Telegram Bot Token")
            logger.info("💡 请访问 Web 配置界面 (http://localhost:5000) 配置 Bot Token")
            # 不退出，保持 Web 服务运行，定期检查配置
            while True:
                await asyncio.sleep(60)
                # 重新加载配置检查是否已配置
                self.config = self.config_manager.get_all_config()
                bot_token = self.config.get('telegram_bot_token')
                if bot_token and bot_token != '******':
                    logger.info("✅ 检测到 Bot Token 已配置，正在启动...")
                    break
        
        # 配置代理
        proxy_url = None
        if self.config.get('proxy_enabled', False):
            proxy_url = self.config.get('proxy_host', '')
            if proxy_url:
                logger.info(f"🌐 使用代理: {proxy_url}")
        
        # 创建应用
        try:
            builder = Application.builder().token(bot_token)
            if proxy_url:
                builder = builder.proxy_url(proxy_url).get_updates_proxy_url(proxy_url)
            self.app = builder.build()
        except Exception as e:
            logger.error(f"❌ 创建 Telegram 应用失败: {e}")
            return
        
        # 添加处理器
        self._register_handlers(self.app)
        
        logger.info("🤖 Telegram Bot 启动中...")
        
        # 运行，添加重试机制
        max_retries = 3
        retry_count = 0
        
        while retry_count < max_retries:
            try:
                await self.app.initialize()
                await self.app.start()
                await self.app.updater.start_polling(drop_pending_updates=True)
                logger.info("✅ Telegram Bot 已启动")
                break
            except Exception as e:
                retry_count += 1
                logger.error(f"❌ Telegram Bot 启动失败 (尝试 {retry_count}/{max_retries}): {e}")
                if retry_count < max_retries:
                    logger.info(f"⏳ 等待 30 秒后重试...")
                    await asyncio.sleep(30)
                else:
                    logger.error("❌ Telegram Bot 启动失败，请检查网络连接或代理设置")
                    logger.info("💡 如果在中国大陆，请在 Web 界面配置代理")
                    return
        
        logger.info("✅ Telegram Bot 已启动")
        
        # 启动定时同步歌单任务
        sync_task = asyncio.create_task(self._playlist_sync_loop())
        logger.info("✅ 歌单定时同步任务已启动")
        
        # 启动健康检查任务
        health_task = asyncio.create_task(self._health_check_loop())
        logger.info("✅ Bot 健康检查任务已启动")
        
        # 保持运行
        try:
            while True:
                await asyncio.sleep(1)
        except asyncio.CancelledError:
            pass
        finally:
            sync_task.cancel()
            health_task.cancel()
            try:
                await sync_task
            except asyncio.CancelledError:
                pass
            try:
                await health_task
            except asyncio.CancelledError:
                pass
            await self.app.updater.stop()
            await self.app.stop()
            await self.app.shutdown()
    
    def _register_handlers(self, app):
        """注册 Telegram 处理器（初始化与重建时复用）"""
        app.add_handler(CommandHandler('start', self.handle_start))
        app.add_handler(CommandHandler('help', self.handle_start))
        app.add_handler(CommandHandler('status', self.handle_status))
        app.add_handler(CommandHandler('history', self.handle_history))
        app.add_handler(CommandHandler('cookie', self.handle_cookie))
        app.add_handler(CommandHandler('queue', self.handle_queue))
        app.add_handler(CallbackQueryHandler(self.handle_callback_query))
        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self._safe_handle_message))

    async def _health_check_loop(self):
        """Bot 健康检查 + 看门狗

        设计要点（修复“长时间运行后崩溃、再也没反馈”）：
        - 不再调用 get_updates：它会与长轮询抢占 offset，导致丢消息 / 409 Conflict
        - 通过 _polling_task.done() 检测 polling 是否已静默死亡（核心看门狗）
        - 通过 get_me() 验证网络连通
        - 连续异常先轻量重连；仍失败则完全重建 Application
        """
        logger.info("💓 Bot 健康检查与看门狗已启动")
        check_interval = 120
        consecutive_failures = 0
        max_failures = 3

        while True:
            try:
                await asyncio.sleep(check_interval)

                need_reconnect = False
                reason = ""

                if not self.app or not self.app.bot:
                    need_reconnect, reason = True, "Application 未初始化"
                else:
                    updater = getattr(self.app, 'updater', None)
                    # 1) 看 polling task 是否还活着——这是“再也没反馈”的根因
                    polling_task = getattr(updater, '_polling_task', None) if updater else None
                    if polling_task is not None and polling_task.done():
                        exc = polling_task.exception()
                        need_reconnect = True
                        reason = f"polling task 已结束({exc})" if exc else "polling task 已结束"
                    elif updater is None or not getattr(updater, 'running', False):
                        need_reconnect, reason = True, "updater 未运行"
                    else:
                        # 2) 验证网络连通
                        try:
                            await asyncio.wait_for(self.app.bot.get_me(), timeout=15)
                            consecutive_failures = 0
                        except asyncio.TimeoutError:
                            need_reconnect, reason = True, "get_me 超时"
                        except Exception as e:
                            need_reconnect, reason = True, f"get_me 失败: {e}"

                if need_reconnect:
                    consecutive_failures += 1
                    logger.warning(f"⚠️ 健康检查异常: {reason}（连续 {consecutive_failures}/{max_failures}）")
                    if consecutive_failures >= max_failures:
                        logger.warning("⚠️ 连续失败达上限，执行完全重建")
                        await self._rebuild_application()
                        consecutive_failures = 0
                    else:
                        await self._reconnect_bot()
                else:
                    if consecutive_failures:
                        consecutive_failures = 0

            except asyncio.CancelledError:
                logger.info("💓 健康检查任务已取消")
                break
            except Exception as e:
                logger.error(f"❌ 健康检查循环异常: {e}")
                await asyncio.sleep(60)
    
    async def _reconnect_bot(self):
        """轻量重连：仅重启 polling，不动 Application。

        单次尝试，失败则升级为完全重建（_rebuild_application）。
        不在这里做长时间重试循环，避免阻塞健康检查。
        """
        if not self.app or not self.app.updater:
            await self._rebuild_application()
            return
        try:
            if getattr(self.app.updater, 'running', False):
                try:
                    await asyncio.wait_for(self.app.updater.stop(), timeout=10)
                except asyncio.TimeoutError:
                    logger.warning("⚠️ 停止 updater 超时，继续重启")
            await asyncio.sleep(2)
            # drop_pending_updates=False：重连不丢弃未处理消息，避免漏消息
            await asyncio.wait_for(
                self.app.updater.start_polling(drop_pending_updates=False),
                timeout=30
            )
            logger.info("✅ 轻量重连成功（polling 已重启）")
        except Exception as e:
            logger.error(f"❌ 轻量重连失败: {e}，升级为完全重建")
            await self._rebuild_application()

    async def _rebuild_application(self):
        """完全重建 Application：轻量重连无效时的兜底。

        长时间运行后 Application 内部状态可能损坏，
        仅重启 polling 无法恢复，需要整体销毁重建。
        """
        logger.warning("🔄 完全重建 Telegram Application...")
        old_app = self.app
        bot_token = self._cfg('telegram_bot_token')
        if not bot_token or bot_token == '******':
            logger.error("❌ 重建失败：未配置 Bot Token")
            return
        proxy_url = None
        if self._cfg('proxy_enabled', False):
            proxy_url = self._cfg('proxy_host', '')

        # 1) 关闭旧 Application（每步都加超时，防止卡死）
        if old_app:
            try:
                if old_app.updater and getattr(old_app.updater, 'running', False):
                    await asyncio.wait_for(old_app.updater.stop(), timeout=10)
            except Exception as e:
                logger.warning(f"⚠️ 停止旧 updater: {e}")
            try:
                if getattr(old_app, 'running', False):
                    await asyncio.wait_for(old_app.stop(), timeout=10)
            except Exception as e:
                logger.warning(f"⚠️ 停止旧 app: {e}")
            try:
                await asyncio.wait_for(old_app.shutdown(), timeout=10)
            except Exception as e:
                logger.warning(f"⚠️ shutdown 旧 app: {e}")

        await asyncio.sleep(2)

        # 2) 重建
        try:
            builder = Application.builder().token(bot_token)
            if proxy_url:
                builder = builder.proxy_url(proxy_url).get_updates_proxy_url(proxy_url)
            self.app = builder.build()
            self._register_handlers(self.app)
            await self.app.initialize()
            await self.app.start()
            await self.app.updater.start_polling(drop_pending_updates=False)
            logger.info("✅ Application 完全重建成功")
        except Exception as e:
            logger.error(f"❌ Application 重建失败: {e}，将在下次健康检查时重试")


def run_web_server(host: str = '0.0.0.0', port: int = 5000):
    """运行 Web 服务器"""
    init_web_app()
    web_app.run(host=host, port=port, debug=False, threaded=True)


def main():
    """主函数"""
    import argparse
    
    parser = argparse.ArgumentParser(description='Music Bot - 音乐下载机器人')
    parser.add_argument('--web-only', action='store_true', help='仅启动 Web 配置服务')
    parser.add_argument('--bot-only', action='store_true', help='仅启动 Telegram Bot')
    parser.add_argument('--web-port', type=int, default=5000, help='Web 服务端口')
    parser.add_argument('--db-path', type=str, default=None, help='数据库路径')
    
    args = parser.parse_args()
    
    logger.info(f"🎵 Music Bot v{BOT_VERSION}")
    logger.info("=" * 50)
    
    if args.web_only:
        # 仅运行 Web 服务
        logger.info(f"🌐 启动 Web 配置服务 (端口: {args.web_port})")
        run_web_server(port=args.web_port)
    elif args.bot_only:
        # 仅运行 Bot
        bot = MusicBot(args.db_path)
        asyncio.run(bot.run_bot())
    else:
        # 同时运行 Web 和 Bot
        bot = MusicBot(args.db_path)
        
        # 在后台线程运行 Web 服务
        web_thread = threading.Thread(
            target=run_web_server,
            kwargs={'port': args.web_port},
            daemon=True
        )
        web_thread.start()
        logger.info(f"🌐 Web 配置服务已启动 (端口: {args.web_port})")
        
        # 在主线程运行 Bot
        try:
            asyncio.run(bot.run_bot())
        except KeyboardInterrupt:
            logger.info("👋 程序已停止")


if __name__ == '__main__':
    main()
