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
            
            # 获取当前事件循环，用于从子线程调度异步任务
            main_loop = asyncio.get_running_loop()
            
            # 创建进度回调 - 用于动态更新进度
            last_update_time = [0]  # 使用列表以便在闭包中修改
            
            async def update_progress_message(progress_text: str):
                """更新进度消息，限制更新频率"""
                current_time = time.time()
                if current_time - last_update_time[0] >= 1:  # 至少间隔1秒更新一次
                    try:
                        await progress_msg.edit_text(progress_text)
                        last_update_time[0] = current_time
                    except Exception:
                        pass  # 忽略编辑消息的错误
            
            def sync_progress_callback(progress_info: dict):
                """同步进度回调（将被转换为异步调用）- 复刻原项目格式"""
                status = progress_info.get('status', '')
                
                # 处理单文件下载进度（实时更新）
                if status == 'file_progress':
                    percent = progress_info.get('percent', 0)
                    downloaded = progress_info.get('downloaded', 0)
                    total = progress_info.get('total', 0)
                    speed = progress_info.get('speed', 0)
                    eta = progress_info.get('eta', 0)
                    filename = progress_info.get('filename', '未知文件')
                    
                    # 检查是否有专辑上下文
                    album_context = progress_info.get('album_context')
                    
                    # 智能截断过长文件名（参考原项目）
                    if len(filename) > 35:
                        import os
                        name, ext = os.path.splitext(filename)
                        filename = name[:30] + "..." + ext
                    
                    # 创建进度条（参考原项目：20字符长度）
                    bar_length = 20
                    filled_length = int(bar_length * percent / 100)
                    progress_bar = '█' * filled_length + '░' * (bar_length - filled_length)
                    
                    # 格式化大小和速度
                    downloaded_mb = downloaded / (1024 * 1024)
                    total_mb = total / (1024 * 1024)
                    speed_mb = speed / (1024 * 1024) if speed > 0 else 0
                    
                    # 格式化预计剩余时间（参考原项目格式）
                    if eta > 0:
                        if eta >= 60:
                            mins, secs = divmod(int(eta), 60)
                            eta_str = f"{mins}分{secs}秒"
                        else:
                            eta_str = f"{int(eta)}秒"
                    else:
                        eta_str = "计算中..."
                    
                    # 构建进度消息
                    if album_context:
                        # 专辑下载模式 - 显示专辑进度 + 当前歌曲进度
                        album_name = album_context.get('album', '')
                        current_song = album_context.get('current', 0)
                        total_songs = album_context.get('total', 0)
                        song_name = album_context.get('song', filename)
                        
                        # 智能截断
                        if len(album_name) > 25:
                            album_name = album_name[:22] + "..."
                        if len(song_name) > 30:
                            song_name = song_name[:27] + "..."
                        
                        progress_text = (
                            f"📀 专辑：{album_name}\n"
                            f"📝 进度：{current_song}/{total_songs} 首\n\n"
                            f"🎵 音乐：{song_name}\n"
                            f"💾 大小：{downloaded_mb:.2f}MB / {total_mb:.2f}MB\n"
                            f"⚡ 速度：{speed_mb:.2f}MB/s\n"
                            f"⏳ 预计剩余：{eta_str}\n"
                            f"📊 进度：{progress_bar} ({percent:.1f}%)"
                        )
                    elif playlist_context := progress_info.get('playlist_context'):
                        # 歌单下载模式 - 显示歌单进度 + 当前歌曲进度
                        playlist_name = playlist_context.get('playlist', '歌单')
                        current_song = playlist_context.get('current', 0)
                        total_songs = playlist_context.get('total', 0)
                        song_name = playlist_context.get('song', filename)
                        
                        # 智能截断
                        if len(playlist_name) > 25:
                            playlist_name = playlist_name[:22] + "..."
                        if len(song_name) > 30:
                            song_name = song_name[:27] + "..."
                        
                        progress_text = (
                            f"📋 歌单：{playlist_name}\n"
                            f"📝 进度：{current_song}/{total_songs} 首\n\n"
                            f"🎵 音乐：{song_name}\n"
                            f"💾 大小：{downloaded_mb:.2f}MB / {total_mb:.2f}MB\n"
                            f"⚡ 速度：{speed_mb:.2f}MB/s\n"
                            f"⏳ 预计剩余：{eta_str}\n"
                            f"📊 进度：{progress_bar} ({percent:.1f}%)"
                        )
                    else:
                        # 单曲下载模式
                        progress_text = (
                            f"🎵 音乐：{filename}\n"
                            f"💾 大小：{downloaded_mb:.2f}MB / {total_mb:.2f}MB\n"
                            f"⚡ 速度：{speed_mb:.2f}MB/s\n"
                            f"⏳ 预计剩余：{eta_str}\n"
                            f"📊 进度：{progress_bar} ({percent:.1f}%)"
                        )
                    
                    # 使用 asyncio 调度更新 - 从子线程安全调用
                    try:
                        asyncio.run_coroutine_threadsafe(update_progress_message(progress_text), main_loop)
                    except Exception:
                        pass
                
                # 处理下载中状态（yt-dlp格式，参考原项目）
                elif status == 'downloading':
                    downloaded_bytes = progress_info.get('downloaded_bytes', 0)
                    total_bytes = progress_info.get('total_bytes', 0) or progress_info.get('total_bytes_estimate', 0)
                    speed_bytes = progress_info.get('speed', 0) or 0
                    eta_seconds = progress_info.get('eta', 0) or 0
                    filename = progress_info.get('filename', '未知文件')
                    
                    # 智能截断过长文件名
                    if len(filename) > 35:
                        import os
                        name, ext = os.path.splitext(os.path.basename(filename))
                        filename = name[:30] + "..." + ext
                    else:
                        import os
                        filename = os.path.basename(filename)
                    
                    if total_bytes > 0:
                        percent = (downloaded_bytes / total_bytes) * 100
                        bar_length = 20
                        filled_length = int(bar_length * percent / 100)
                        progress_bar = '█' * filled_length + '░' * (bar_length - filled_length)
                        
                        downloaded_mb = downloaded_bytes / (1024 * 1024)
                        total_mb = total_bytes / (1024 * 1024)
                        speed_mb = speed_bytes / (1024 * 1024) if speed_bytes > 0 else 0
                        
                        # 格式化预计剩余时间
                        if eta_seconds > 0:
                            mins, secs = divmod(int(eta_seconds), 60)
                            if mins > 0:
                                eta_str = f"{mins}分{secs}秒"
                            else:
                                eta_str = f"{secs}秒"
                        elif speed_bytes > 0 and total_bytes > downloaded_bytes:
                            remaining = total_bytes - downloaded_bytes
                            eta_calc = int(remaining / speed_bytes)
                            mins, secs = divmod(eta_calc, 60)
                            if mins > 0:
                                eta_str = f"{mins}分{secs}秒"
                            else:
                                eta_str = f"{secs}秒"
                        else:
                            eta_str = "计算中..."
                        
                        # 原项目格式
                        progress_text = (
                            f"🎵 音乐：{filename}\n"
                            f"💾 大小：{downloaded_mb:.2f}MB / {total_mb:.2f}MB\n"
                            f"⚡ 速度：{speed_mb:.2f}MB/s\n"
                            f"⏳ 预计剩余：{eta_str}\n"
                            f"📊 进度：{progress_bar} ({percent:.1f}%)"
                        )
                    else:
                        downloaded_mb = downloaded_bytes / (1024 * 1024)
                        speed_mb = speed_bytes / (1024 * 1024) if speed_bytes > 0 else 0
                        
                        progress_text = (
                            f"🎵 音乐：{filename}\n"
                            f"💾 大小：{downloaded_mb:.2f}MB / 计算中...\n"
                            f"⚡ 速度：{speed_mb:.2f}MB/s\n"
                            f"⏳ 预计剩余：计算中...\n"
                            f"📊 进度：下载中..."
                        )
                    
                    # 使用 asyncio 调度更新 - 从子线程安全调用
                    try:
                        asyncio.run_coroutine_threadsafe(update_progress_message(progress_text), main_loop)
                    except Exception:
                        pass
                
                # 处理下载完成状态
                elif status == 'finished':
                    filename = progress_info.get('filename', '未知文件')
                    total_bytes = progress_info.get('total_bytes', 0)
                    
                    import os
                    filename = os.path.basename(filename) if filename else '未知文件'
                    if len(filename) > 35:
                        name, ext = os.path.splitext(filename)
                        filename = name[:30] + "..." + ext
                    
                    total_mb = total_bytes / (1024 * 1024) if total_bytes > 0 else 0
                    progress_bar = '█' * 20
                    
                    # 原项目完成格式
                    progress_text = (
                        f"🎵 音乐：{filename}\n"
                        f"💾 大小：{total_mb:.2f}MB\n"
                        f"⚡ 速度：完成\n"
                        f"⏳ 预计剩余：0秒\n"
                        f"📊 进度：{progress_bar} (100.0%)"
                    )
                    
                    # 使用 asyncio 调度更新 - 从子线程安全调用
                    try:
                        asyncio.run_coroutine_threadsafe(update_progress_message(progress_text), main_loop)
                    except Exception:
                        pass
                
                elif status in ['album_progress', 'playlist_progress']:
                    # 开始下载新歌曲时的简单提示（实际进度会由 file_progress 更新）
                    current = progress_info.get('current', 0)
                    total = progress_info.get('total', 0)
                    song_name = progress_info.get('song', '')
                    album_name = progress_info.get('album', progress_info.get('playlist', ''))
                    
                    # 智能截断
                    if len(song_name) > 30:
                        song_name = song_name[:27] + "..."
                    if len(album_name) > 25:
                        album_name = album_name[:22] + "..."
                    
                    type_label = '专辑' if status == 'album_progress' else '歌单'
                    type_icon = '📀' if status == 'album_progress' else '📋'
                    
                    progress_text = (
                        f"{type_icon} {type_label}：{album_name}\n"
                        f"📝 进度：{current}/{total} 首\n\n"
                        f"🎵 准备下载：{song_name}\n"
                        f"💾 大小：获取中...\n"
                        f"⚡ 速度：--\n"
                        f"⏳ 预计剩余：计算中...\n"
                        f"📊 进度：░░░░░░░░░░░░░░░░░░░░ (0.0%)"
                    )
                    
                    # 使用 asyncio 调度更新 - 从子线程安全调用
                    try:
                        asyncio.run_coroutine_threadsafe(update_progress_message(progress_text), main_loop)
                    except Exception:
                        pass
            
            # 定义同步下载函数包装器
            def run_download():
                """在子线程中执行下载"""
                try:
                    if content_type == 'song':
                        return downloader.download_song(
                            content_id, download_dir, 
                            progress_callback=sync_progress_callback
                        )
                    elif content_type == 'album':
                        return downloader.download_album(
                            content_id, download_dir,
                            progress_callback=sync_progress_callback
                        )
                    elif content_type == 'playlist':
                        return downloader.download_playlist(
                            content_id, download_dir,
                            progress_callback=sync_progress_callback
                        )
                    else:
                        return {'success': False, 'error': f'不支持的类型: {content_type}'}
                except Exception as e:
                    logger.error(f"下载线程异常: {e}")
                    import traceback
                    logger.error(traceback.format_exc())
                    return {'success': False, 'error': str(e)}
            
            # 在线程池中执行下载
            logger.info(f"🚀 开始下载: {content_type} - {content_id}")
            result = await asyncio.to_thread(run_download)
            logger.info(f"✅ 下载完成，结果: {result.get('success', False)}")
            
            # 等待一小段时间，确保最后的进度更新完成，避免 Telegram API 限制
            await asyncio.sleep(0.5)
            
            # 处理结果
            if result.get('success'):
                logger.info(f"📝 准备更新完成消息...")
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
                    
                    # 获取文件名用于显示
                    filepath = result.get('filepath', '')
                    import os
                    display_filename = os.path.basename(filepath) if filepath else f"{result.get('song_title', '未知')} - {result.get('song_artist', '未知')}"
                    size_mb = result.get('size_mb', 0)
                    
                    # 创建进度条 (20个字符，100%完成)
                    progress_bar = '█' * 20
                    
                    # 参考原项目格式 - 单曲下载完成（进度条风格）
                    success_msg = (
                        f"✅ 下载完成！\n\n"
                        f"{platform_icon} 音乐：{display_filename}\n"
                        f"💾 大小：{size_mb:.2f}MB\n"
                        f"📊 进度：{progress_bar} (100.0%)"
                    )
                    try:
                        await progress_msg.edit_text(success_msg)
                        logger.info(f"✅ 消息更新成功")
                    except Exception as e:
                        logger.error(f"❌ 编辑消息失败: {e}")
                elif content_type in ['album', 'playlist']:
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
                    
                    # 创建进度条 (20个字符，100%完成)
                    progress_bar = '█' * 20
                    
                    # 构建完整消息 - 参考原项目格式（进度条风格）
                    summary = f"{platform_icon} {type_label}：{title}\n"
                    summary += f"💾 大小：{total_size_mb:.1f}MB\n"
                    summary += f"⚡ 速度：完成\n"
                    summary += f"⏳ 预计剩余：0秒\n"
                    summary += f"📊 进度：{progress_bar} (100.0%)\n\n"
                    
                    # 添加统计信息
                    summary += f"🎵 歌曲数量：{result.get('total_songs', len(success_songs))} 首\n"
                    summary += f"✅ 成功下载：{len(success_songs)} 首\n"
                    
                    if failed_songs:
                        summary += f"❌ 失败数量：{len(failed_songs)} 首\n"
                    
                    # 添加歌曲列表
                    if song_lines:
                        summary += "\n🎵 歌曲列表：\n" + "\n".join(song_lines)
                    
                    # 如果有失败的歌曲，列出失败原因
                    if failed_songs and len(failed_songs) <= 5:
                        summary += "\n\n❌ 下载失败的歌曲：\n"
                        for song in failed_songs[:5]:
                            song_name = song.get('song_title', '未知')
                            error = song.get('error', '未知错误')
                            summary += f"  • {song_name}: {error}\n"
                    
                    try:
                        await progress_msg.edit_text(summary)
                        logger.info(f"✅ 专辑/歌单消息更新成功")
                    except Exception as e:
                        logger.error(f"❌ 编辑专辑/歌单消息失败: {e}")
                else:
                    try:
                        await progress_msg.edit_text(self._format_success_message(result, content_type))
                    except Exception as e:
                        logger.error(f"❌ 编辑消息失败: {e}")
            else:
                try:
                    await progress_msg.edit_text(f"❌ 下载失败\n{result.get('error', '未知错误')}")
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
