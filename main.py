#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Music Bot - 精简版音乐下载机器人
专注于音乐下载功能，支持网易云音乐、Apple Music、YouTube Music
"""

import os
import sys
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
            "/settings - 配置设置\n"
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
    
    async def send_audio_file(self, update: Update, filepath: str, song_title: str, song_artist: str):
        """发送音频文件到 Telegram"""
        try:
            file_size = os.path.getsize(filepath)
            max_size = 50 * 1024 * 1024  # 50MB Telegram Bot API 限制
            
            if file_size > max_size:
                # 文件太大，尝试使用 Telethon（如果配置了 Session）
                session_string = self.config.get('telegram_session_string', '')
                if session_string:
                    try:
                        from telethon import TelegramClient
                        from telethon.sessions import StringSession
                        
                        api_id = int(self.config.get('telegram_api_id', 0))
                        api_hash = self.config.get('telegram_api_hash', '')
                        
                        if api_id and api_hash:
                            client = TelegramClient(StringSession(session_string), api_id, api_hash)
                            await client.connect()
                            
                            await client.send_file(
                                update.effective_chat.id,
                                filepath,
                                caption=f"🎵 {song_title}\n🎤 {song_artist}",
                                attributes=[]
                            )
                            
                            await client.disconnect()
                            logger.info(f"✅ 通过 Telethon 发送大文件成功: {filepath}")
                            return True
                    except Exception as e:
                        logger.error(f"Telethon 发送失败: {e}")
                
                await update.message.reply_text(
                    f"⚠️ 文件过大 ({file_size / (1024*1024):.1f} MB)，无法通过 Bot API 发送\n"
                    f"💡 请配置 Telegram Session 以支持大文件发送"
                )
                return False
            
            # 使用 Bot API 发送
            with open(filepath, 'rb') as audio:
                await update.message.reply_audio(
                    audio=audio,
                    title=song_title,
                    performer=song_artist,
                    caption=f"🎵 {song_title}\n🎤 {song_artist}"
                )
            
            logger.info(f"✅ 发送音频文件成功: {filepath}")
            return True
            
        except TelegramError as e:
            logger.error(f"发送音频失败: {e}")
            await update.message.reply_text(f"⚠️ 发送文件失败: {e}")
            return False
        except Exception as e:
            logger.error(f"发送音频异常: {e}")
            return False
    
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
        
        # 发送处理中消息
        progress_msg = await update.message.reply_text(
            f"🎵 正在处理 {downloader_name} 链接...\n请稍候..."
        )
        
        try:
            # 解析 URL
            parsed = downloader.parse_url(url)
            if not parsed:
                await progress_msg.edit_text("❌ 无法解析链接")
                return
            
            content_type = parsed.get('type')
            content_id = parsed.get('id')
            
            # 更新进度消息
            await progress_msg.edit_text(
                f"🎵 正在下载 {content_type}...\n"
                f"📍 平台: {downloader_name}\n"
                f"🔗 ID: {content_id}"
            )
            
            # 下载
            download_dir = os.path.join(self.download_path, downloader_name.replace('_', '/'))
            
            if content_type == 'song':
                result = downloader.download_song(content_id, download_dir)
            elif content_type == 'album':
                result = downloader.download_album(content_id, download_dir)
            elif content_type == 'playlist':
                result = downloader.download_playlist(content_id, download_dir)
            else:
                result = {'success': False, 'error': f'不支持的类型: {content_type}'}
            
            # 发送结果
            if result.get('success'):
                # 发送音频文件
                if content_type == 'song':
                    filepath = result.get('filepath')
                    if filepath and os.path.exists(filepath):
                        await progress_msg.edit_text("📤 正在发送文件...")
                        sent = await self.send_audio_file(
                            update, 
                            filepath, 
                            result.get('song_title', '未知'),
                            result.get('song_artist', '未知')
                        )
                        if sent:
                            await progress_msg.edit_text(self._format_success_message(result, content_type))
                            # 可选：删除本地文件
                            # os.remove(filepath)
                        else:
                            await progress_msg.edit_text(
                                f"{self._format_success_message(result, content_type)}\n\n"
                                f"📂 文件已保存到服务器"
                            )
                    else:
                        await progress_msg.edit_text(self._format_success_message(result, content_type))
                
                elif content_type in ['album', 'playlist']:
                    # 专辑/歌单：发送所有成功下载的文件
                    songs = result.get('songs', [])
                    sent_count = 0
                    failed_count = 0
                    
                    await progress_msg.edit_text(f"📤 正在发送 {len(songs)} 个文件...")
                    
                    for song in songs:
                        if song.get('success'):
                            filepath = song.get('filepath')
                            if filepath and os.path.exists(filepath):
                                sent = await self.send_audio_file(
                                    update,
                                    filepath,
                                    song.get('song_title', '未知'),
                                    song.get('song_artist', '未知')
                                )
                                if sent:
                                    sent_count += 1
                                else:
                                    failed_count += 1
                                # 避免发送过快
                                await asyncio.sleep(1)
                    
                    summary = (
                        f"✅ 下载完成！\n\n"
                        f"📀 {result.get('album_name', result.get('playlist_title', '未知'))}\n"
                        f"📊 已下载: {result.get('downloaded_songs', 0)}/{result.get('total_songs', 0)} 首\n"
                        f"📤 已发送: {sent_count} 首"
                    )
                    if failed_count > 0:
                        summary += f"\n⚠️ 发送失败: {failed_count} 首"
                    
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
    
    def _format_success_message(self, result: Dict[str, Any], content_type: str) -> str:
        """格式化成功消息"""
        if content_type == 'song':
            return (
                f"✅ 下载完成！\n\n"
                f"🎵 {result.get('song_title', '未知')}\n"
                f"🎤 {result.get('song_artist', '未知')}\n"
                f"💾 {result.get('size_mb', 0):.2f} MB"
            )
        elif content_type in ['album', 'playlist']:
            return (
                f"✅ 下载完成！\n\n"
                f"📀 {result.get('album_name', result.get('playlist_title', '未知'))}\n"
                f"📊 {result.get('downloaded_songs', 0)}/{result.get('total_songs', 0)} 首"
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
