#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Telegram 通知模块
统一管理消息发送、进度通知、格式化等功能
"""

import os
import asyncio
import logging
import time
from typing import Optional, Dict, Any, Callable, Union
from dataclasses import dataclass, field
from enum import Enum

logger = logging.getLogger(__name__)


class NotifyType(Enum):
    """通知类型"""
    INFO = "info"
    SUCCESS = "success"
    WARNING = "warning"
    ERROR = "error"
    PROGRESS = "progress"


class DownloadType(Enum):
    """下载类型"""
    SONG = "song"
    ALBUM = "album"
    PLAYLIST = "playlist"


@dataclass
class ProgressInfo:
    """下载进度信息"""
    # 基本信息
    filename: str = ""
    percent: float = 0.0
    downloaded_bytes: int = 0
    total_bytes: int = 0
    speed: float = 0.0  # bytes/s
    eta: float = 0.0  # 秒
    
    # 专辑/歌单上下文
    download_type: DownloadType = DownloadType.SONG
    collection_name: str = ""  # 专辑/歌单名称
    current_index: int = 0
    total_count: int = 0
    current_song: str = ""
    
    # 状态
    status: str = "downloading"  # downloading, finished, error
    error_message: str = ""


@dataclass
class DownloadResult:
    """下载结果"""
    success: bool = False
    download_type: DownloadType = DownloadType.SONG
    platform: str = ""
    
    # 单曲信息
    title: str = ""
    artist: str = ""
    filepath: str = ""
    size_mb: float = 0.0
    quality: str = ""
    bitrate: str = ""
    duration: str = ""
    
    # 专辑/歌单信息
    collection_name: str = ""
    total_songs: int = 0
    success_count: int = 0
    failed_count: int = 0
    total_size_mb: float = 0.0
    songs: list = field(default_factory=list)
    failed_songs: list = field(default_factory=list)
    
    # 错误信息
    error: str = ""


class ProgressFormatter:
    """进度格式化器"""
    
    # 进度条配置
    BAR_LENGTH = 20
    BAR_FILLED = '█'
    BAR_EMPTY = '░'
    
    # 平台图标
    PLATFORM_ICONS = {
        'netease': '🎵',
        'apple_music': '🍎',
    }
    
    # 类型图标
    TYPE_ICONS = {
        'song': '🎵',
        'album': '📀',
        'playlist': '📋',
    }
    
    @classmethod
    def create_progress_bar(cls, percent: float) -> str:
        """创建进度条"""
        percent = max(0, min(100, percent))
        filled_length = int(cls.BAR_LENGTH * percent / 100)
        bar = cls.BAR_FILLED * filled_length + cls.BAR_EMPTY * (cls.BAR_LENGTH - filled_length)
        return f"{bar} ({percent:.1f}%)"
    
    @classmethod
    def format_size(cls, bytes_size: int) -> str:
        """格式化文件大小"""
        if bytes_size < 0:
            return "0.00MB"
        mb = bytes_size / (1024 * 1024)
        if mb < 0.01:
            kb = bytes_size / 1024
            return f"{kb:.2f}KB"
        return f"{mb:.2f}MB"
    
    @classmethod
    def format_speed(cls, bytes_per_sec: float) -> str:
        """格式化下载速度"""
        if bytes_per_sec <= 0:
            return "0.00MB/s"
        mb_per_sec = bytes_per_sec / (1024 * 1024)
        if mb_per_sec < 0.01:
            kb_per_sec = bytes_per_sec / 1024
            return f"{kb_per_sec:.2f}KB/s"
        return f"{mb_per_sec:.2f}MB/s"
    
    @classmethod
    def format_eta(cls, seconds: float) -> str:
        """格式化预计剩余时间"""
        if seconds <= 0:
            return "计算中..."
        seconds = int(seconds)
        if seconds >= 3600:
            hours, remainder = divmod(seconds, 3600)
            mins, secs = divmod(remainder, 60)
            return f"{hours}小时{mins}分{secs}秒"
        elif seconds >= 60:
            mins, secs = divmod(seconds, 60)
            return f"{mins}分{secs}秒"
        else:
            return f"{seconds}秒"
    
    @classmethod
    def truncate_name(cls, name: str, max_length: int = 30) -> str:
        """智能截断名称"""
        if not name:
            return "未知"
        if len(name) <= max_length:
            return name
        return name[:max_length - 3] + "..."
    
    @classmethod
    def get_platform_icon(cls, platform: str) -> str:
        """获取平台图标"""
        return cls.PLATFORM_ICONS.get(platform.lower(), '🎵')
    
    @classmethod
    def get_type_icon(cls, download_type: Union[str, DownloadType]) -> str:
        """获取类型图标"""
        if isinstance(download_type, DownloadType):
            download_type = download_type.value
        return cls.TYPE_ICONS.get(download_type, '🎵')


class MessageTemplates:
    """消息模板管理"""
    
    @staticmethod
    def download_started(platform: str, content_type: str, content_id: str, is_redownload: bool = False) -> str:
        """下载开始消息"""
        icon = ProgressFormatter.get_platform_icon(platform)
        action = "重新下载" if is_redownload else "正在下载"
        return (
            f"{icon} {action} {content_type}...\n"
            f"📍 平台: {platform}\n"
            f"🔗 ID: {content_id}"
        )
    
    @staticmethod
    def song_progress(info: ProgressInfo) -> str:
        """单曲下载进度消息"""
        filename = ProgressFormatter.truncate_name(info.filename, 35)
        downloaded = ProgressFormatter.format_size(info.downloaded_bytes)
        total = ProgressFormatter.format_size(info.total_bytes) if info.total_bytes > 0 else "计算中..."
        speed = ProgressFormatter.format_speed(info.speed)
        eta = ProgressFormatter.format_eta(info.eta)
        progress_bar = ProgressFormatter.create_progress_bar(info.percent)
        
        return (
            f"🎵 音乐：{filename}\n"
            f"💾 大小：{downloaded} / {total}\n"
            f"⚡ 速度：{speed}\n"
            f"⏳ 预计剩余：{eta}\n"
            f"📊 进度：{progress_bar}"
        )
    
    @staticmethod
    def album_progress(info: ProgressInfo) -> str:
        """专辑下载进度消息"""
        album_name = ProgressFormatter.truncate_name(info.collection_name, 25)
        song_name = ProgressFormatter.truncate_name(info.current_song or info.filename, 30)
        downloaded = ProgressFormatter.format_size(info.downloaded_bytes)
        total = ProgressFormatter.format_size(info.total_bytes) if info.total_bytes > 0 else "计算中..."
        speed = ProgressFormatter.format_speed(info.speed)
        eta = ProgressFormatter.format_eta(info.eta)
        progress_bar = ProgressFormatter.create_progress_bar(info.percent)
        
        return (
            f"📀 专辑：{album_name}\n"
            f"📝 进度：{info.current_index}/{info.total_count} 首\n\n"
            f"🎵 音乐：{song_name}\n"
            f"💾 大小：{downloaded} / {total}\n"
            f"⚡ 速度：{speed}\n"
            f"⏳ 预计剩余：{eta}\n"
            f"📊 进度：{progress_bar}"
        )
    
    @staticmethod
    def playlist_progress(info: ProgressInfo) -> str:
        """歌单下载进度消息"""
        playlist_name = ProgressFormatter.truncate_name(info.collection_name, 25)
        song_name = ProgressFormatter.truncate_name(info.current_song or info.filename, 30)
        downloaded = ProgressFormatter.format_size(info.downloaded_bytes)
        total = ProgressFormatter.format_size(info.total_bytes) if info.total_bytes > 0 else "计算中..."
        speed = ProgressFormatter.format_speed(info.speed)
        eta = ProgressFormatter.format_eta(info.eta)
        progress_bar = ProgressFormatter.create_progress_bar(info.percent)
        
        return (
            f"📋 歌单：{playlist_name}\n"
            f"📝 进度：{info.current_index}/{info.total_count} 首\n\n"
            f"🎵 音乐：{song_name}\n"
            f"💾 大小：{downloaded} / {total}\n"
            f"⚡ 速度：{speed}\n"
            f"⏳ 预计剩余：{eta}\n"
            f"📊 进度：{progress_bar}"
        )
    
    @staticmethod
    def preparing_download(info: ProgressInfo) -> str:
        """准备下载消息（开始下载新歌曲时）"""
        if info.download_type == DownloadType.ALBUM:
            type_label = "专辑"
            type_icon = "📀"
        elif info.download_type == DownloadType.PLAYLIST:
            type_label = "歌单"
            type_icon = "📋"
        else:
            return f"🎵 准备下载：{ProgressFormatter.truncate_name(info.filename, 30)}"
        
        collection_name = ProgressFormatter.truncate_name(info.collection_name, 25)
        song_name = ProgressFormatter.truncate_name(info.current_song, 30)
        
        return (
            f"{type_icon} {type_label}：{collection_name}\n"
            f"📝 进度：{info.current_index}/{info.total_count} 首\n\n"
            f"🎵 准备下载：{song_name}\n"
            f"💾 大小：获取中...\n"
            f"⚡ 速度：--\n"
            f"⏳ 预计剩余：计算中...\n"
            f"📊 进度：{ProgressFormatter.BAR_EMPTY * ProgressFormatter.BAR_LENGTH} (0.0%)"
        )
    
    @staticmethod
    def song_completed(result: DownloadResult) -> str:
        """单曲下载完成消息"""
        icon = ProgressFormatter.get_platform_icon(result.platform)
        filename = ProgressFormatter.truncate_name(os.path.basename(result.filepath) if result.filepath else f"{result.title} - {result.artist}", 35)
        progress_bar = ProgressFormatter.create_progress_bar(100)
        
        return (
            f"✅ 下载完成！\n\n"
            f"{icon} 音乐：{filename}\n"
            f"💾 大小：{result.size_mb:.2f}MB\n"
            f"📊 进度：{progress_bar}"
        )
    
    @staticmethod
    def collection_completed(result: DownloadResult) -> str:
        """专辑/歌单下载完成消息"""
        icon = ProgressFormatter.get_platform_icon(result.platform)
        type_label = "专辑" if result.download_type == DownloadType.ALBUM else "歌单"
        type_icon = "📀" if result.download_type == DownloadType.ALBUM else "📋"
        progress_bar = ProgressFormatter.create_progress_bar(100)
        
        lines = [
            f"{type_icon} {type_label}：{result.collection_name}",
            f"💾 大小：{result.total_size_mb:.1f}MB",
            f"⚡ 速度：完成",
            f"⏳ 预计剩余：0秒",
            f"📊 进度：{progress_bar}",
            "",
            f"🎵 歌曲数量：{result.total_songs} 首",
            f"✅ 成功下载：{result.success_count} 首",
        ]
        
        if result.failed_count > 0:
            lines.append(f"❌ 失败数量：{result.failed_count} 首")
        
        # 添加歌曲列表
        if result.songs:
            lines.append("")
            lines.append("🎵 歌曲列表：")
            for i, song in enumerate(result.songs[:15], 1):
                song_title = song.get('song_title', '未知')
                song_size = song.get('size_mb', 0)
                lines.append(f"{i:02d}. {song_title} ({song_size:.1f}MB)")
            
            if len(result.songs) > 15:
                lines.append(f"... 还有 {len(result.songs) - 15} 首歌曲")
        
        # 添加失败歌曲信息
        if result.failed_songs and len(result.failed_songs) <= 5:
            lines.append("")
            lines.append("❌ 下载失败的歌曲：")
            for song in result.failed_songs[:5]:
                song_name = song.get('song_title', '未知')
                error = song.get('error', '未知错误')
                lines.append(f"  • {song_name}: {error}")
        
        return "\n".join(lines)
    
    @staticmethod
    def download_error(error_message: str) -> str:
        """下载错误消息"""
        return f"❌ 下载失败\n{error_message}"
    
    # ==================== 歌单订阅通知模板 ====================
    
    @staticmethod
    def playlist_sync_started(playlist_name: str, playlist_id: str, is_auto: bool = False) -> str:
        """歌单同步开始通知"""
        sync_type = "🔄 自动同步" if is_auto else "📥 手动同步"
        return (
            f"{sync_type}歌单...\n\n"
            f"📋 歌单：{playlist_name}\n"
            f"🔗 ID：{playlist_id}\n"
            f"⏳ 正在检查更新..."
        )
    
    @staticmethod
    def playlist_check_result(playlist_name: str, total_songs: int, new_songs: int, 
                              skipped_songs: int) -> str:
        """歌单检查结果"""
        if new_songs == 0:
            return (
                f"✅ 歌单已是最新\n\n"
                f"📋 歌单：{playlist_name}\n"
                f"🎵 总歌曲：{total_songs} 首\n"
                f"📦 已下载：{skipped_songs} 首\n"
                f"🆕 新增：0 首"
            )
        return (
            f"🆕 发现新歌曲！\n\n"
            f"📋 歌单：{playlist_name}\n"
            f"🎵 总歌曲：{total_songs} 首\n"
            f"📦 已下载：{skipped_songs} 首\n"
            f"🆕 新增：{new_songs} 首\n\n"
            f"⏳ 开始下载新歌曲..."
        )
    
    @staticmethod
    def playlist_sync_progress(playlist_name: str, current: int, total: int,
                               current_song: str, downloaded: int, failed: int) -> str:
        """歌单同步进度"""
        progress_bar = ProgressFormatter.create_progress_bar(current / total * 100 if total > 0 else 0)
        
        lines = [
            f"📥 正在同步歌单...\n",
            f"📋 歌单：{ProgressFormatter.truncate_name(playlist_name, 25)}",
            f"📝 进度：{current}/{total} 首",
            f"🎵 当前：{ProgressFormatter.truncate_name(current_song, 30)}",
            f"📊 {progress_bar}",
            "",
            f"✅ 已下载：{downloaded} 首",
        ]
        
        if failed > 0:
            lines.append(f"❌ 失败：{failed} 首")
        
        return "\n".join(lines)
    
    @staticmethod
    def playlist_sync_completed(playlist_name: str, total_songs: int, new_songs: int,
                                downloaded: int, failed: int, skipped: int,
                                failed_songs_list: list = None) -> str:
        """歌单同步完成通知"""
        status_icon = "✅" if failed == 0 else "⚠️"
        progress_bar = ProgressFormatter.create_progress_bar(100)
        
        lines = [
            f"{status_icon} 歌单同步完成！\n",
            f"📋 歌单：{playlist_name}",
            f"📊 {progress_bar}",
            "",
            f"🎵 歌单总数：{total_songs} 首",
            f"🆕 本次新增：{new_songs} 首",
            f"✅ 下载成功：{downloaded} 首",
            f"⏭️ 已跳过：{skipped} 首",
        ]
        
        if failed > 0:
            lines.append(f"❌ 下载失败：{failed} 首")
            
            # 显示失败歌曲列表（最多5首）
            if failed_songs_list:
                lines.append("")
                lines.append("❌ 失败歌曲：")
                for song in failed_songs_list[:5]:
                    song_name = song.get('name', song.get('song_title', '未知'))
                    error = song.get('error', song.get('fail_reason', '未知错误'))
                    # 截断错误信息
                    if len(error) > 30:
                        error = error[:27] + "..."
                    lines.append(f"  • {ProgressFormatter.truncate_name(song_name, 20)}")
                
                if len(failed_songs_list) > 5:
                    lines.append(f"  ... 还有 {len(failed_songs_list) - 5} 首")
        
        return "\n".join(lines)
    
    @staticmethod
    def playlist_sync_error(playlist_name: str, error: str) -> str:
        """歌单同步失败通知"""
        return (
            f"❌ 歌单同步失败\n\n"
            f"📋 歌单：{playlist_name}\n"
            f"💥 错误：{error}"
        )
    
    @staticmethod
    def all_playlists_sync_started(total: int) -> str:
        """全部歌单同步开始"""
        return (
            f"🔄 开始同步所有订阅歌单\n\n"
            f"📋 共 {total} 个歌单\n"
            f"⏳ 正在处理..."
        )
    
    @staticmethod
    def all_playlists_sync_completed(total: int, synced: int, total_new: int, 
                                      total_downloaded: int, total_failed: int,
                                      results: list = None) -> str:
        """全部歌单同步完成"""
        status_icon = "✅" if total_failed == 0 else "⚠️"
        
        lines = [
            f"{status_icon} 全部歌单同步完成！\n",
            f"📋 处理歌单：{synced}/{total} 个",
            f"🆕 发现新歌：{total_new} 首",
            f"✅ 下载成功：{total_downloaded} 首",
        ]
        
        if total_failed > 0:
            lines.append(f"❌ 下载失败：{total_failed} 首")
        
        # 显示各歌单概要
        if results:
            lines.append("")
            lines.append("📊 各歌单统计：")
            for r in results[:8]:
                name = ProgressFormatter.truncate_name(r.get('playlist_name', '未知'), 15)
                new_count = r.get('new_songs', 0)
                dl_count = r.get('downloaded', 0)
                if r.get('success'):
                    if new_count > 0:
                        lines.append(f"  📋 {name}: +{new_count}首, ✅{dl_count}首")
                    else:
                        lines.append(f"  📋 {name}: 无更新")
                else:
                    lines.append(f"  📋 {name}: ❌失败")
            
            if len(results) > 8:
                lines.append(f"  ... 还有 {len(results) - 8} 个歌单")
        
        return "\n".join(lines)
    
    @staticmethod
    def song_download_failed(song_name: str, artist: str, error: str, 
                             playlist_name: str = None) -> str:
        """单曲下载失败通知（在歌单同步中）"""
        lines = [
            f"⚠️ 歌曲下载失败\n",
            f"🎵 歌曲：{ProgressFormatter.truncate_name(song_name, 30)}",
            f"👤 歌手：{ProgressFormatter.truncate_name(artist, 20)}",
        ]
        
        if playlist_name:
            lines.append(f"📋 歌单：{ProgressFormatter.truncate_name(playlist_name, 20)}")
        
        lines.append(f"💥 原因：{error}")
        
        return "\n".join(lines)


class TelegramNotifier:
    """Telegram 通知器"""
    
    def __init__(self, update_interval: float = 1.0):
        """
        初始化通知器
        
        Args:
            update_interval: 进度更新最小间隔（秒）
        """
        self.update_interval = update_interval
        self._last_update_time = 0
        self._current_message = None
        self._main_loop = None
    
    def set_main_loop(self, loop: asyncio.AbstractEventLoop):
        """设置主事件循环（用于从子线程调用）"""
        self._main_loop = loop
    
    def set_message(self, message):
        """设置当前要更新的消息对象"""
        self._current_message = message
    
    async def update_message(self, text: str, force: bool = False) -> bool:
        """
        更新消息内容
        
        Args:
            text: 新的消息文本
            force: 是否强制更新（忽略时间间隔限制）
        
        Returns:
            是否成功更新
        """
        if not self._current_message:
            return False
        
        current_time = time.time()
        if not force and current_time - self._last_update_time < self.update_interval:
            return False
        
        try:
            await self._current_message.edit_text(text)
            self._last_update_time = current_time
            return True
        except Exception as e:
            logger.debug(f"更新消息失败: {e}")
            return False
    
    def update_message_sync(self, text: str, force: bool = False):
        """
        同步方式更新消息（从子线程调用）
        
        Args:
            text: 新的消息文本
            force: 是否强制更新
        """
        if not self._main_loop or not self._current_message:
            return
        
        current_time = time.time()
        if not force and current_time - self._last_update_time < self.update_interval:
            return
        
        try:
            # 使用 run_coroutine_threadsafe 并处理返回的 Future
            future = asyncio.run_coroutine_threadsafe(
                self._current_message.edit_text(text),
                self._main_loop
            )
            self._last_update_time = current_time
            
            # 添加回调来处理结果，避免 Future 泄漏
            def on_complete(f):
                try:
                    f.result(timeout=0)  # 不阻塞，只是消费结果
                except Exception:
                    pass  # 忽略错误，已在日志中记录
            
            future.add_done_callback(on_complete)
        except Exception as e:
            logger.debug(f"同步更新消息失败: {e}")
    
    def create_progress_callback(self, download_type: DownloadType = DownloadType.SONG,
                                  collection_name: str = "",
                                  total_count: int = 0) -> Callable[[Dict[str, Any]], None]:
        """
        创建进度回调函数
        
        Args:
            download_type: 下载类型
            collection_name: 专辑/歌单名称
            total_count: 总歌曲数
        
        Returns:
            进度回调函数
        """
        context = {
            'download_type': download_type,
            'collection_name': collection_name,
            'total_count': total_count,
            'current_index': 0,
            'current_song': '',
        }
        
        def progress_callback(progress_info: Dict[str, Any]):
            """处理进度回调"""
            status = progress_info.get('status', '')
            
            # 更新上下文
            if 'album_context' in progress_info:
                ctx = progress_info['album_context']
                context['download_type'] = DownloadType.ALBUM
                context['collection_name'] = ctx.get('album', collection_name)
                context['current_index'] = ctx.get('current', 0)
                context['total_count'] = ctx.get('total', total_count)
                context['current_song'] = ctx.get('song', '')
            elif 'playlist_context' in progress_info:
                ctx = progress_info['playlist_context']
                context['download_type'] = DownloadType.PLAYLIST
                context['collection_name'] = ctx.get('playlist', collection_name)
                context['current_index'] = ctx.get('current', 0)
                context['total_count'] = ctx.get('total', total_count)
                context['current_song'] = ctx.get('song', '')
            
            # 构建进度信息
            info = ProgressInfo(
                filename=progress_info.get('filename', '未知文件'),
                percent=progress_info.get('percent', 0),
                downloaded_bytes=progress_info.get('downloaded', progress_info.get('downloaded_bytes', 0)),
                total_bytes=progress_info.get('total', progress_info.get('total_bytes', 0)) or progress_info.get('total_bytes_estimate', 0),
                speed=progress_info.get('speed', 0) or 0,
                eta=progress_info.get('eta', 0) or 0,
                download_type=context['download_type'],
                collection_name=context['collection_name'],
                current_index=context['current_index'],
                total_count=context['total_count'],
                current_song=context['current_song'],
                status=status,
            )
            
            # 根据状态生成消息
            if status == 'file_progress':
                # 单文件下载进度
                if info.download_type == DownloadType.ALBUM:
                    text = MessageTemplates.album_progress(info)
                elif info.download_type == DownloadType.PLAYLIST:
                    text = MessageTemplates.playlist_progress(info)
                else:
                    text = MessageTemplates.song_progress(info)
                self.update_message_sync(text)
            
            elif status == 'downloading':
                # yt-dlp 格式的进度
                if info.total_bytes > 0:
                    info.percent = (info.downloaded_bytes / info.total_bytes) * 100
                    # 计算 ETA
                    if info.speed > 0 and info.total_bytes > info.downloaded_bytes:
                        info.eta = (info.total_bytes - info.downloaded_bytes) / info.speed
                
                # 获取文件名
                filename = progress_info.get('filename', '')
                if filename:
                    info.filename = os.path.basename(filename)
                
                text = MessageTemplates.song_progress(info)
                self.update_message_sync(text)
            
            elif status == 'finished':
                # 单文件下载完成
                info.percent = 100
                filename = progress_info.get('filename', '')
                if filename:
                    info.filename = os.path.basename(filename)
                info.total_bytes = progress_info.get('total_bytes', 0)
                info.downloaded_bytes = info.total_bytes
                
                text = MessageTemplates.song_progress(info)
                self.update_message_sync(text, force=True)
            
            elif status in ['album_progress', 'playlist_progress']:
                # 开始下载新歌曲
                context['current_index'] = progress_info.get('current', 0)
                context['current_song'] = progress_info.get('song', '')
                if status == 'album_progress':
                    context['download_type'] = DownloadType.ALBUM
                    context['collection_name'] = progress_info.get('album', collection_name)
                else:
                    context['download_type'] = DownloadType.PLAYLIST
                    context['collection_name'] = progress_info.get('playlist', collection_name)
                context['total_count'] = progress_info.get('total', total_count)
                
                info.download_type = context['download_type']
                info.collection_name = context['collection_name']
                info.current_index = context['current_index']
                info.total_count = context['total_count']
                info.current_song = context['current_song']
                
                text = MessageTemplates.preparing_download(info)
                self.update_message_sync(text, force=True)
        
        return progress_callback
    
    @staticmethod
    def format_result(result: Dict[str, Any], content_type: str, platform: str) -> str:
        """
        格式化下载结果为消息文本
        
        Args:
            result: 下载结果字典
            content_type: 内容类型
            platform: 平台名称
        
        Returns:
            格式化后的消息文本
        """
        if not result.get('success'):
            return MessageTemplates.download_error(result.get('error', '未知错误'))
        
        # 构建结果对象
        download_result = DownloadResult(
            success=True,
            platform=platform,
        )
        
        if content_type == 'song':
            download_result.download_type = DownloadType.SONG
            download_result.title = result.get('song_title', '未知')
            download_result.artist = result.get('song_artist', '未知')
            download_result.filepath = result.get('filepath', '')
            download_result.size_mb = result.get('size_mb', 0)
            download_result.quality = result.get('quality', '')
            download_result.bitrate = result.get('bitrate', '')
            return MessageTemplates.song_completed(download_result)
        
        elif content_type in ['album', 'playlist']:
            download_result.download_type = DownloadType.ALBUM if content_type == 'album' else DownloadType.PLAYLIST
            download_result.collection_name = result.get('album_name', result.get('playlist_title', '未知'))
            download_result.total_songs = result.get('total_songs', 0)
            
            songs_list = result.get('songs', [])
            success_songs = [s for s in songs_list if s.get('success')]
            failed_songs = [s for s in songs_list if not s.get('success')]
            
            download_result.success_count = len(success_songs)
            download_result.failed_count = len(failed_songs)
            download_result.total_size_mb = sum(s.get('size_mb', 0) for s in success_songs if s.get('size_mb'))
            download_result.songs = success_songs
            download_result.failed_songs = failed_songs
            
            return MessageTemplates.collection_completed(download_result)
        
        return "✅ 下载完成！"


# ==================== 独立的 TG 消息发送功能 ====================

def send_telegram_notification(config_manager, message: str, parse_mode: str = None) -> bool:
    """
    发送 Telegram 通知给所有配置的用户（使用 HTTP API 直接发送）
    
    Args:
        config_manager: 配置管理器实例
        message: 消息文本
        parse_mode: 解析模式 (None, 'Markdown', 'HTML')
    
    Returns:
        是否至少成功发送给一个用户
    """
    import requests
    
    try:
        logger.info("📤 send_telegram_notification 被调用")
        
        # 检查是否启用通知
        notify_enabled = config_manager.get_config('telegram_notify_enabled', True)
        logger.info(f"📤 telegram_notify_enabled = {notify_enabled}")
        if not notify_enabled:
            logger.info("📤 TG 通知未启用")
            return False
        
        bot_token = config_manager.get_config('telegram_bot_token', '')
        logger.info(f"📤 bot_token 是否存在: {bool(bot_token)}, 长度: {len(bot_token) if bot_token else 0}")
        if not bot_token:
            logger.warning("📤 未配置 Bot Token")
            return False
        
        allowed_users = config_manager.get_config('telegram_allowed_users', '')
        logger.info(f"📤 allowed_users = '{allowed_users}'")
        if not allowed_users:
            logger.warning("📤 未配置允许的用户")
            return False
        
        # 获取代理配置
        proxies = None
        proxy_enabled = config_manager.get_config('proxy_enabled', False)
        if proxy_enabled:
            proxy_host = config_manager.get_config('proxy_host', '')
            if proxy_host:
                proxies = {
                    'http': proxy_host,
                    'https': proxy_host
                }
                logger.info(f"📤 使用代理: {proxy_host}")
        
        # Telegram API URL
        api_url = f"https://api.telegram.org/bot{bot_token[:10]}...{bot_token[-5:]}/sendMessage"
        logger.info(f"📤 API URL (部分): {api_url}")
        
        # 实际 API URL
        real_api_url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
        
        # 发送给所有用户
        success_count = 0
        for user_id in allowed_users.split(','):
            user_id = user_id.strip()
            if user_id:
                try:
                    logger.info(f"📤 准备发送给用户: {user_id}")
                    payload = {
                        'chat_id': int(user_id),
                        'text': message,
                    }
                    if parse_mode:
                        payload['parse_mode'] = parse_mode
                    
                    logger.info(f"📤 发送请求中...")
                    response = requests.post(real_api_url, json=payload, proxies=proxies, timeout=30)
                    logger.info(f"📤 响应状态码: {response.status_code}")
                    
                    if response.status_code == 200:
                        result = response.json()
                        logger.info(f"📤 响应内容: ok={result.get('ok')}")
                        if result.get('ok'):
                            success_count += 1
                            logger.info(f"✅ 已发送 TG 通知给用户 {user_id}")
                        else:
                            logger.warning(f"⚠️ TG API 返回错误: {result.get('description', '未知错误')}")
                    else:
                        logger.warning(f"⚠️ TG API 请求失败: HTTP {response.status_code}, 响应: {response.text[:200]}")
                        
                except ValueError:
                    logger.warning(f"⚠️ 无效的用户 ID: {user_id}")
                except Exception as e:
                    logger.error(f"⚠️ 发送给用户 {user_id} 失败: {e}")
                    import traceback
                    logger.error(traceback.format_exc())
        
        if success_count > 0:
            logger.info(f"✅ TG 通知已发送给 {success_count} 个用户")
        else:
            logger.warning(f"⚠️ TG 通知发送失败，没有成功发送给任何用户")
        
        return success_count > 0
        
    except Exception as e:
        logger.error(f"❌ 发送 TG 通知失败: {e}")
        import traceback
        logger.debug(traceback.format_exc())
        return False


def notify_playlist_sync_result(config_manager, result: Dict[str, Any], 
                                playlist_name: str = None, is_auto: bool = False):
    """
    发送歌单同步结果通知
    
    Args:
        config_manager: 配置管理器实例
        result: 同步结果字典
        playlist_name: 歌单名称（可选，会从 result 中获取）
        is_auto: 是否是自动同步
    """
    try:
        logger.info(f"📨 notify_playlist_sync_result 被调用: {playlist_name}")
        
        # 检查是否启用完成通知
        notify_complete = config_manager.get_config('telegram_notify_complete', True)
        logger.info(f"📨 telegram_notify_complete = {notify_complete}")
        if not notify_complete:
            logger.info("📨 完成通知未启用，跳过")
            return
        
        name = playlist_name or result.get('playlist_title', '未知歌单')
        total = result.get('total_songs', 0)
        new_songs = result.get('new_songs', 0)
        downloaded = result.get('downloaded_songs', 0)
        skipped = result.get('skipped_songs', 0)
        failed = result.get('failed_songs', 0)
        
        logger.info(f"📨 歌单同步结果: total={total}, new={new_songs}, downloaded={downloaded}, failed={failed}")
        
        # 获取失败歌曲列表
        songs = result.get('songs', [])
        failed_songs_list = [s for s in songs if not s.get('success')]
        
        # 生成通知消息
        message = MessageTemplates.playlist_sync_completed(
            playlist_name=name,
            total_songs=total,
            new_songs=new_songs,
            downloaded=downloaded,
            failed=failed,
            skipped=skipped,
            failed_songs_list=failed_songs_list
        )
        
        logger.info(f"📨 生成的消息长度: {len(message)} 字符")
        
        # 发送通知
        success = send_telegram_notification(config_manager, message)
        logger.info(f"📨 发送结果: {success}")
        
    except Exception as e:
        logger.error(f"❌ 发送歌单同步通知失败: {e}")
        import traceback
        logger.error(traceback.format_exc())


def notify_all_playlists_sync_result(config_manager, total: int, results: list):
    """
    发送全部歌单同步结果通知
    
    Args:
        config_manager: 配置管理器实例
        total: 歌单总数
        results: 各歌单同步结果列表
    """
    try:
        if not config_manager.get_config('telegram_notify_complete', True):
            return
        
        synced = len([r for r in results if r.get('success')])
        total_new = sum(r.get('new_songs', 0) for r in results)
        total_downloaded = sum(r.get('downloaded', 0) for r in results)
        total_failed = sum(r.get('failed', 0) for r in results if r.get('failed'))
        
        message = MessageTemplates.all_playlists_sync_completed(
            total=total,
            synced=synced,
            total_new=total_new,
            total_downloaded=total_downloaded,
            total_failed=total_failed,
            results=results
        )
        
        send_telegram_notification(config_manager, message)
        
    except Exception as e:
        logger.error(f"❌ 发送全部歌单同步通知失败: {e}")
