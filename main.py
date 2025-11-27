#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Music Bot - 精简版音乐下载机器人
专注于音乐下载功能，支持网易云音乐、Apple Music、YouTube Music
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
BOT_VERSION = "1.0.0"

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
from downloaders.youtube_music import YouTubeMusicDownloader
from downloaders.apple_music import AppleMusicDownloader

# 导入 Web 服务
from web.app import app as web_app, init_app as init_web_app

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
        
        # 初始化下载器
        self.downloaders = {}
        self._init_downloaders()
        
        # Telegram 应用
        self.app: Optional[Application] = None
        
        # 下载路径
        self.download_path = self.config.get('download_path', '/downloads')
        
        logger.info(f"🎵 Music Bot v{BOT_VERSION} 初始化完成")
    
    def _init_downloaders(self):
        """初始化下载器"""
        # 网易云音乐
        if self.config.get('netease_enabled', True):
            try:
                self.downloaders['netease'] = NeteaseDownloader(self.config_manager)
                logger.info("✅ 网易云音乐下载器已启用")
            except Exception as e:
                logger.error(f"❌ 网易云音乐下载器初始化失败: {e}")
        
        # YouTube Music
        if self.config.get('youtube_music_enabled', True):
            try:
                self.downloaders['youtube_music'] = YouTubeMusicDownloader(self.config_manager)
                logger.info("✅ YouTube Music 下载器已启用")
            except Exception as e:
                logger.error(f"❌ YouTube Music 下载器初始化失败: {e}")
        
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
        welcome_msg = (
            "🎵 *Music Bot* - 音乐下载机器人\n\n"
            "发送音乐链接即可下载！\n\n"
            "*支持的平台：*\n"
            "• 🎵 网易云音乐 - 歌曲/专辑/歌单\n"
            "• 🍎 Apple Music - 歌曲/专辑\n"
            "• ▶️ YouTube Music - 歌曲/播放列表\n\n"
            "*命令：*\n"
            "/start - 显示帮助\n"
            "/status - 查看状态\n"
            "/history - 查看下载历史\n"
        )
        await update.message.reply_text(welcome_msg, parse_mode=ParseMode.MARKDOWN)
    
    async def handle_status(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """处理 /status 命令"""
        status_lines = ["📊 *Bot 状态*\n"]
        
        for name, downloader in self.downloaders.items():
            status_lines.append(f"• {name}: ✅ 已启用")
        
        if not self.downloaders:
            status_lines.append("• 暂无可用下载器")
        
        await update.message.reply_text('\n'.join(status_lines), parse_mode=ParseMode.MARKDOWN)
    
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
            platform_icon = {'netease': '🎵', 'apple_music': '🍎', 'youtube_music': '▶️'}.get(platform, '📀')
            type_icon = {'song': '🎵', 'album': '💿', 'playlist': '📋'}.get(content_type, '📁')
            
            lines.append(f"{i}. {platform_icon}{type_icon} *{title}*")
            if artist:
                lines.append(f"   _{artist}_")
            lines.append(f"   🕐 {created_at}\n")
        
        await update.message.reply_text('\n'.join(lines), parse_mode=ParseMode.MARKDOWN)

    def get_download_path_for_platform(self, platform: str) -> str:
        """获取平台专属的下载路径"""
        platform_paths = {
            'netease': self.config.get('netease_download_path', '/downloads/netease'),
            'apple_music': self.config.get('apple_music_download_path', '/downloads/apple_music'),
            'youtube_music': self.config.get('youtube_music_download_path', '/downloads/youtube_music'),
        }
        return platform_paths.get(platform, self.download_path)
    
    async def handle_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """处理消息"""
        if not update.message or not update.message.text:
            return
        
        text = update.message.text.strip()
        
        # 检查是否是链接
        if not ('http://' in text or 'https://' in text or 'music.163.com' in text):
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
        """执行下载任务"""
        # 发送处理中消息
        progress_msg = await message.reply_text(
            f"🎵 {'重新' if is_redownload else '正在'}下载 {content_type}...\n"
            f"📍 平台: {downloader_name}\n"
            f"🔗 ID: {content_id}"
        )
        
        try:
            # 使用平台专属下载路径
            download_dir = self.get_download_path_for_platform(downloader_name)
            
            # 创建进度回调 - 用于动态更新进度
            last_update_time = [0]  # 使用列表以便在闭包中修改
            
            async def update_progress_message(progress_text: str):
                """更新进度消息，限制更新频率"""
                current_time = time.time()
                if current_time - last_update_time[0] >= 2:  # 至少间隔2秒更新一次
                    try:
                        await progress_msg.edit_text(progress_text)
                        last_update_time[0] = current_time
                    except Exception:
                        pass  # 忽略编辑消息的错误
            
            def sync_progress_callback(progress_info: dict):
                """同步进度回调（将被转换为异步调用）"""
                status = progress_info.get('status', '')
                
                if status in ['album_progress', 'playlist_progress']:
                    current = progress_info.get('current', 0)
                    total = progress_info.get('total', 0)
                    song_name = progress_info.get('song', '')
                    
                    # 创建进度条
                    bar_length = 20
                    if total > 0:
                        filled_length = int(bar_length * current / total)
                        percentage = current / total * 100
                    else:
                        filled_length = 0
                        percentage = 0
                    progress_bar = '█' * filled_length + '░' * (bar_length - filled_length)
                    
                    # 构建进度消息 - 参考原项目格式
                    progress_text = (
                        f"📥 下载中\n\n"
                        f"📝 当前: {song_name}\n"
                        f"📊 进度: {current}/{total} 首\n\n"
                        f"{progress_bar} {percentage:.1f}%"
                    )
                    
                    # 使用 asyncio 调度更新
                    try:
                        loop = asyncio.get_event_loop()
                        if loop.is_running():
                            asyncio.create_task(update_progress_message(progress_text))
                    except Exception:
                        pass
            
            # 下载
            if content_type == 'song':
                result = downloader.download_song(content_id, download_dir, progress_callback=sync_progress_callback)
            elif content_type == 'album':
                result = downloader.download_album(content_id, download_dir, progress_callback=sync_progress_callback)
            elif content_type == 'playlist':
                result = downloader.download_playlist(content_id, download_dir, progress_callback=sync_progress_callback)
            else:
                result = {'success': False, 'error': f'不支持的类型: {content_type}'}
            
            # 处理结果
                if result.get('success'):
                # 保存下载历史
                if content_type == 'song':
                    self.config_manager.add_download_history(
                        platform=downloader_name,
                        content_type=content_type,
                        content_id=content_id,
                        title=result.get('song_title', '未知'),
                        artist=result.get('song_artist', '未知'),
                        file_path=result.get('filepath', ''),
                        file_size=int(result.get('size_mb', 0) * 1024 * 1024),
                        quality=result.get('quality', '')
                    )
                    
                    # 平台图标
                    platform_icon = {'netease': '🎵', 'apple_music': '🍎', 'youtube_music': '▶️'}.get(downloader_name, '🎵')
                    
                    filepath = result.get('filepath', '')
                    # 参考原项目格式 - 单曲下载完成
                    success_msg = (
                        f"{platform_icon} 音乐下载完成\n\n"
                        f"🎵 音乐: {result.get('song_title', '未知')} - {result.get('song_artist', '未知')}\n"
                        f"💾 大小: {result.get('size_mb', 0):.2f}MB\n"
                        f"🖼️ 码率: {result.get('bitrate', '未知')}\n"
                        f"🎚️ 音质: {result.get('quality', '未知')}\n"
                        f"⏱️ 时长: {result.get('duration', '未知')}\n"
                        f"📂 保存位置: {filepath}"
                    )
                    await progress_msg.edit_text(success_msg)                elif content_type in ['album', 'playlist']:
                    # 构建歌曲列表
                    songs_list = result.get('songs', [])
                    success_songs = [s for s in songs_list if s.get('success')]
                    failed_songs = [s for s in songs_list if not s.get('success')]
                    
                    # 保存专辑/歌单下载历史
                    title = result.get('album_name', result.get('playlist_title', '未知'))
                    artist_name = result.get('artist', '未知艺术家')
                    self.config_manager.add_download_history(
                        platform=downloader_name,
                        content_type=content_type,
                        content_id=content_id,
                        title=title,
                        artist=artist_name,
                        file_path=download_dir,
                        file_size=len(success_songs),
                        quality=f"{len(success_songs)}/{result.get('total_songs', 0)}"
                    )
                    
                    # 平台图标
                    platform_icon = {'netease': '🎵', 'apple_music': '🍎', 'youtube_music': '▶️'}.get(downloader_name, '🎵')
                    type_label = '专辑' if content_type == 'album' else '歌单'
                    
                    # 计算总大小
                    total_size_mb = sum(s.get('size_mb', 0) for s in success_songs if s.get('size_mb'))
                    
                    # 获取音质信息
                    quality_name = result.get('quality_name', result.get('quality', '未知'))
                    bitrate = result.get('bitrate', '未知')
                    file_format = result.get('file_format', 'MP3')
                    
                    # 构建成功的歌曲列表（参考原项目格式）
                    song_lines = []
                    for i, song in enumerate(success_songs[:15], 1):
                        song_title = song.get('song_title', '未知')
                        song_size = song.get('size_mb', 0)
                        song_lines.append(f"{i:02d}. {song_title} ({song_size:.1f}MB)")
                    
                    if len(success_songs) > 15:
                        song_lines.append(f"... 还有 {len(success_songs) - 15} 首歌曲")
                    
                    # 构建完整消息 - 参考原项目格式
                    summary = f"{platform_icon} {type_label}下载完成\n\n"
                    
                    if content_type == 'album':
                        summary += f"📀 专辑名称: {title}\n"
                        if artist_name != '未知艺术家':
                            summary += f"👤 艺术家: {artist_name}\n"
                    else:
                        summary += f"📋 歌单名称: {title}\n"
                    
                    summary += (
                        f"🎵 歌曲数量: {result.get('total_songs', len(success_songs))} 首\n"
                        f"✅ 成功下载: {len(success_songs)} 首\n"
                    )
                    
                    if failed_songs:
                        summary += f"❌ 失败数量: {len(failed_songs)} 首\n"
                    
                    summary += (
                        f"💾 总大小: {total_size_mb:.1f} MB\n"
                        f"🎚️ 音质: {quality_name}\n"
                        f"🎼 文件格式: {file_format}\n"
                        f"📊 码率: {bitrate}\n"
                        f"📂 保存位置: {download_dir}\n"
                    )
                    
                    # 添加歌曲列表
                    if song_lines:
                        summary += "\n🎵 歌曲列表:\n\n" + "\n".join(song_lines)
                    
                    # 如果有失败的歌曲，列出失败原因
                    if failed_songs and len(failed_songs) <= 5:
                        summary += "\n\n❌ 下载失败的歌曲:\n"
                        for song in failed_songs[:5]:
                            song_name = song.get('song_title', '未知')
                            error = song.get('error', '未知错误')
                            summary += f"  • {song_name}: {error}\n"
                    
                    await progress_msg.edit_text(summary)
                else:
                    await progress_msg.edit_text(self._format_success_message(result, content_type))
            else:
                await progress_msg.edit_text(f"❌ 下载失败\n{result.get('error', '未知错误')}")
            
        except Exception as e:
            logger.error(f"下载错误: {e}")
            await progress_msg.edit_text(f"❌ 下载出错: {str(e)}")
    
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
    
    def _format_success_message(self, result: Dict[str, Any], content_type: str, platform: str = '') -> str:
        """格式化成功消息 - 参考原项目格式"""
        platform_icon = {'netease': '🎵', 'apple_music': '🍎', 'youtube_music': '▶️'}.get(platform, '🎵')
        
        if content_type == 'song':
            return (
                f"{platform_icon} 音乐下载完成\n\n"
                f"🎵 音乐: {result.get('song_title', '未知')} - {result.get('song_artist', '未知')}\n"
                f"💾 大小: {result.get('size_mb', 0):.2f}MB\n"
                f"🖼️ 码率: {result.get('bitrate', '未知')}\n"
                f"🎚️ 音质: {result.get('quality', '未知')}\n"
                f"⏱️ 时长: {result.get('duration', '未知')}\n"
                f"📂 保存位置: {result.get('filepath', '未知')}"
            )
        elif content_type in ['album', 'playlist']:
            type_label = '专辑' if content_type == 'album' else '歌单'
            return (
                f"{platform_icon} {type_label}下载完成\n\n"
                f"📀 名称: {result.get('album_name', result.get('playlist_title', '未知'))}\n"
                f"🎵 歌曲数量: {result.get('total_songs', 0)} 首\n"
                f"✅ 成功下载: {result.get('downloaded_songs', 0)} 首\n"
                f"💾 总大小: {result.get('total_size_mb', 0):.1f} MB\n"
                f"📂 保存位置: {result.get('download_path', '未知')}"
            )
        else:
            return "✅ 下载完成！"
    
    def check_allowed_user(self, user_id: int) -> bool:
        """检查用户是否被允许"""
        allowed_users = self.config.get('telegram_allowed_users', '')
        
        if not allowed_users:
            return True  # 未配置则允许所有人
        
        allowed_ids = [int(uid.strip()) for uid in allowed_users.split(',') if uid.strip()]
        return user_id in allowed_ids
    
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
        self.app.add_handler(CommandHandler('start', self.handle_start))
        self.app.add_handler(CommandHandler('help', self.handle_start))
        self.app.add_handler(CommandHandler('status', self.handle_status))
        self.app.add_handler(CommandHandler('history', self.handle_history))
        self.app.add_handler(CallbackQueryHandler(self.handle_callback_query))
        self.app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_message))
        
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
        
        # 保持运行
        try:
            while True:
                await asyncio.sleep(1)
        except asyncio.CancelledError:
            pass
        finally:
            await self.app.updater.stop()
            await self.app.stop()
            await self.app.shutdown()


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
