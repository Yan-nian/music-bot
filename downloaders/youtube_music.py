#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
YouTube Music 下载器
基于 yt-dlp 实现
"""

import os
import re
import logging
from pathlib import Path
from typing import Optional, Dict, List, Any, Callable, Tuple

from .base import BaseDownloader

logger = logging.getLogger(__name__)

# 检查元数据管理器是否可用
try:
    from .metadata import MusicMetadataManager
    METADATA_AVAILABLE = True
    logger.info("✅ 元数据管理器已加载")
except ImportError:
    METADATA_AVAILABLE = False
    logger.warning("⚠️ 元数据管理器不可用")

# 检查 yt-dlp 是否可用
try:
    import yt_dlp
    YT_DLP_AVAILABLE = True
    logger.info(f"✅ yt-dlp 可用，版本: {yt_dlp.version.__version__}")
except ImportError:
    YT_DLP_AVAILABLE = False
    logger.warning("⚠️ yt-dlp 不可用")


class YouTubeMusicDownloader(BaseDownloader):
    """YouTube Music 下载器"""
    
    URL_PATTERNS = {
        'song': [
            r'music\.youtube\.com/watch\?v=([a-zA-Z0-9_-]+)',
            r'youtube\.com/watch\?v=([a-zA-Z0-9_-]+)',
            r'youtu\.be/([a-zA-Z0-9_-]+)',
        ],
        'playlist': [
            r'music\.youtube\.com.*[&?]list=([a-zA-Z0-9_-]+)',
            r'youtube\.com.*[&?]list=([a-zA-Z0-9_-]+)',
        ],
        'album': [
            r'music\.youtube\.com/playlist\?list=([a-zA-Z0-9_-]+)',
        ],
    }
    
    def __init__(self, config_manager=None):
        super().__init__(config_manager)
        
        if not YT_DLP_AVAILABLE:
            logger.error("❌ yt-dlp 未安装，YouTube Music 下载器不可用")
            return
        
        # 加载配置
        self._load_config()
        
        # 查找 cookies 文件
        self.cookies_path = self._find_cookies()
        
        # 初始化元数据管理器
        if METADATA_AVAILABLE:
            self.metadata_manager = MusicMetadataManager()
            logger.info("✅ 元数据管理器已初始化")
        else:
            self.metadata_manager = None
        
        logger.info("✅ YouTube Music 下载器初始化完成")
    
    def _load_config(self):
        """加载配置"""
        self.quality = self.get_config('youtube_music_quality', 'best')
        self.format = self.get_config('youtube_music_format', 'm4a')
        self.download_cover = self.get_config('youtube_music_download_cover', True)
        
        logger.info(f"📝 YouTube Music 配置: 音质={self.quality}, 格式={self.format}")

    def reload_config(self):
        """重新加载配置与 cookies 路径（Web 修改后无需重启即生效）"""
        self._load_config()
        self.cookies_path = self._find_cookies()

    def _find_cookies(self) -> Optional[str]:
        """查找 cookies 文件"""
        possible_paths = [
            '/app/cookies/youtube_cookies.txt',
            './cookies/youtube_cookies.txt',
            './youtube_cookies.txt',
        ]
        
        for path in possible_paths:
            if os.path.exists(path):
                logger.info(f"✅ 找到 YouTube cookies: {path}")
                return path
        
        logger.warning("⚠️ 未找到 YouTube cookies，部分内容可能无法下载")
        return None
    
    def is_supported_url(self, url: str) -> bool:
        """检查是否为支持的 YouTube Music URL"""
        if not url:
            return False
        
        youtube_domains = ['youtube.com', 'youtu.be', 'music.youtube.com']
        return any(domain in url.lower() for domain in youtube_domains)
    
    def parse_url(self, url: str) -> Optional[Dict[str, Any]]:
        """解析 URL"""
        if not self.is_supported_url(url):
            return None
        
        # 优先检查是否为播放列表
        for pattern in self.URL_PATTERNS['playlist']:
            match = re.search(pattern, url)
            if match:
                return {
                    'type': 'playlist',
                    'id': match.group(1),
                    'url': url
                }
        
        # 检查单曲
        for pattern in self.URL_PATTERNS['song']:
            match = re.search(pattern, url)
            if match:
                return {
                    'type': 'song',
                    'id': match.group(1),
                    'url': url
                }
        
        return None
    
    def _create_ydl_opts(self, output_dir: str, filename_template: str = None) -> Dict[str, Any]:
        """创建 yt-dlp 配置"""
        if filename_template is None:
            filename_template = '%(title).100s.%(ext)s'
        
        # 音频格式选择
        if self.format == 'm4a':
            format_selector = 'bestaudio[ext=m4a]/bestaudio'
            postprocessors = [{
                'key': 'FFmpegExtractAudio',
                'preferredcodec': 'm4a',
                'preferredquality': '0',
            }]
        else:
            format_selector = 'bestaudio'
            postprocessors = [{
                'key': 'FFmpegExtractAudio',
                'preferredcodec': 'mp3',
                'preferredquality': '320' if self.quality == 'best' else self.quality.replace('k', ''),
            }]
        
        ydl_opts = {
            'format': format_selector,
            'outtmpl': os.path.join(output_dir, filename_template),
            'writeinfojson': False,
            'ignoreerrors': False,
            'no_warnings': True,
            'socket_timeout': 300,
            'retries': 3,
            'continuedl': True,
            'noplaylist': True,
            'geo_bypass': True,
            'postprocessors': postprocessors,
        }
        
        if self.cookies_path:
            ydl_opts['cookiefile'] = self.cookies_path
        
        return ydl_opts
    
    def get_song_info(self, url: str) -> Optional[Dict[str, Any]]:
        """获取歌曲信息"""
        try:
            ydl_opts = {
                'quiet': True,
                'no_warnings': True,
                'extract_flat': False,
            }
            
            if self.cookies_path:
                ydl_opts['cookiefile'] = self.cookies_path
            
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=False)
                
                if info:
                    return {
                        'id': info.get('id', ''),
                        'name': info.get('title', ''),
                        'artist': info.get('artist', info.get('uploader', '')),
                        'album': info.get('album', ''),
                        'duration': info.get('duration', 0),
                        'cover': info.get('thumbnail', ''),
                    }
            
            return None
            
        except Exception as e:
            logger.error(f"❌ 获取歌曲信息失败: {e}")
            return None
    
    def download_song(self, song_id: str, download_dir: str,
                     quality: str = None,
                     progress_callback: Optional[Callable] = None) -> Dict[str, Any]:
        """下载单曲"""
        if not YT_DLP_AVAILABLE:
            return {'success': False, 'error': 'yt-dlp 不可用'}
        
        try:
            url = f"https://music.youtube.com/watch?v={song_id}"
            if not song_id.startswith('http'):
                url = f"https://music.youtube.com/watch?v={song_id}"
            else:
                url = song_id
            
            # 获取歌曲信息
            song_info = self.get_song_info(url)
            
            # 创建下载配置
            ydl_opts = self._create_ydl_opts(download_dir)
            
            # 添加进度回调 - 复刻原项目通知格式
            if progress_callback:
                def progress_hook(d):
                    if d['status'] == 'downloading':
                        downloaded = d.get('downloaded_bytes', 0)
                        total = d.get('total_bytes') or d.get('total_bytes_estimate', 0)
                        speed = d.get('speed', 0)
                        eta = d.get('eta', 0)
                        filename = d.get('filename', '')
                        
                        # 获取显示文件名
                        display_filename = os.path.basename(filename) if filename else ''
                        if not display_filename and song_info:
                            display_filename = song_info.get('name', '未知')
                        if not display_filename:
                            display_filename = '未知'
                        
                        if total > 0:
                            # 使用 file_progress 状态（原项目格式）
                            progress_callback({
                                'status': 'file_progress',
                                'percent': (downloaded / total) * 100,
                                'downloaded': downloaded,
                                'total': total,
                                'speed': speed or 0,
                                'eta': eta or 0,
                                'filename': display_filename,
                            })
                        else:
                            # 没有总大小信息时也发送进度
                            progress_callback({
                                'status': 'downloading',
                                'downloaded_bytes': downloaded,
                                'total_bytes': total,
                                'speed': speed or 0,
                                'eta': eta or 0,
                                'filename': display_filename,
                            })
                    elif d['status'] == 'finished':
                        filename = d.get('filename', '')
                        display_filename = os.path.basename(filename) if filename else ''
                        if not display_filename and song_info:
                            display_filename = song_info.get('name', '未知')
                        
                        # 获取文件大小
                        total_bytes = 0
                        if filename and os.path.exists(filename):
                            total_bytes = os.path.getsize(filename)
                        
                        progress_callback({
                            'status': 'finished',
                            'filename': display_filename,
                            'total_bytes': total_bytes,
                        })
                
                ydl_opts['progress_hooks'] = [progress_hook]
            
            # 下载
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=True)
                
                if info:
                    # 获取下载的文件路径
                    filename = ydl.prepare_filename(info)
                    # 替换为实际的音频扩展名
                    actual_file = Path(filename).with_suffix(f'.{self.format}')
                    
                    if actual_file.exists():
                        # 计算时长格式
                        duration_sec = info.get('duration', 0)
                        if duration_sec:
                            minutes = int(duration_sec) // 60
                            seconds = int(duration_sec) % 60
                            duration_str = f"{minutes}:{seconds:02d}"
                        else:
                            duration_str = '未知'
                        
                        # 获取码率信息
                        abr = info.get('abr', 0)
                        bitrate_str = f"{int(abr)}kbps" if abr else '320kbps'
                        
                        # 写入元数据
                        self._add_metadata_to_file(str(actual_file), info)
                        
                        return {
                            'success': True,
                            'song_title': info.get('title', ''),
                            'song_artist': info.get('artist', info.get('uploader', '')),
                            'filepath': str(actual_file),
                            'size_mb': actual_file.stat().st_size / (1024 * 1024),
                            'quality': '高品质' if self.quality == 'best' else self.quality,
                            'bitrate': bitrate_str,
                            'duration': duration_str,
                            'file_format': self.format.upper(),
                        }
            
            return {'success': False, 'error': '下载失败'}
            
        except Exception as e:
            logger.error(f"❌ 下载歌曲失败: {e}")
            return {'success': False, 'error': str(e)}
    
    def get_playlist_info(self, url: str) -> Optional[Dict[str, Any]]:
        """获取播放列表信息"""
        try:
            ydl_opts = {
                'quiet': True,
                'no_warnings': True,
                'extract_flat': True,
            }
            
            if self.cookies_path:
                ydl_opts['cookiefile'] = self.cookies_path
            
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=False)
                
                if info and info.get('_type') == 'playlist':
                    entries = info.get('entries', [])
                    return {
                        'id': info.get('id', ''),
                        'title': info.get('title', ''),
                        'uploader': info.get('uploader', ''),
                        'total_videos': len(entries),
                        'entries': entries,
                    }
            
            return None
            
        except Exception as e:
            logger.error(f"❌ 获取播放列表信息失败: {e}")
            return None
    
    def download_album(self, album_id: str, download_dir: str,
                      quality: str = None,
                      progress_callback: Optional[Callable] = None) -> Dict[str, Any]:
        """下载专辑（播放列表）"""
        return self.download_playlist(album_id, download_dir, quality, progress_callback)
    
    def download_playlist(self, playlist_id: str, download_dir: str,
                         quality: str = None,
                         progress_callback: Optional[Callable] = None) -> Dict[str, Any]:
        """下载播放列表"""
        if not YT_DLP_AVAILABLE:
            return {'success': False, 'error': 'yt-dlp 不可用'}
        
        try:
            url = playlist_id if playlist_id.startswith('http') else f"https://music.youtube.com/playlist?list={playlist_id}"
            
            # 获取播放列表信息
            playlist_info = self.get_playlist_info(url)
            
            if not playlist_info:
                return {'success': False, 'error': '无法获取播放列表信息'}
            
            results = {
                'success': True,
                'playlist_title': playlist_info.get('title', ''),
                'total_songs': playlist_info.get('total_videos', 0),
                'downloaded_songs': 0,
                'songs': [],
                'quality_name': '高品质' if self.quality == 'best' else self.quality,
                'bitrate': '320kbps',
                'file_format': self.format.upper(),
            }
            
            entries = playlist_info.get('entries', [])
            
            for i, entry in enumerate(entries, 1):
                video_id = entry.get('id', entry.get('url', ''))
                song_name = entry.get('title', '未知')
                
                if progress_callback:
                    # 原项目格式 - 播放列表进度
                    progress_callback({
                        'status': 'playlist_progress',
                        'current': i,
                        'total': len(entries),
                        'song': song_name,
                        'playlist': playlist_info.get('title', '未知'),
                    })
                
                # 创建带专辑/歌单信息的进度回调包装器
                def create_wrapped_callback(song_idx, total_songs, song_title, playlist_title):
                    def wrapped_callback(progress_info):
                        if progress_callback:
                            # 添加专辑/歌单上下文信息
                            enhanced_info = progress_info.copy()
                            enhanced_info['playlist'] = playlist_title
                            
                            if progress_info.get('status') == 'file_progress':
                                # 单曲下载进度 - 附加专辑进度信息
                                enhanced_info['song_downloaded'] = progress_info.get('downloaded', 0)
                                enhanced_info['song_total'] = progress_info.get('total', 0)
                                enhanced_info['song_speed'] = progress_info.get('speed', 0)
                                enhanced_info['song_eta'] = progress_info.get('eta', 0)
                                enhanced_info['status'] = 'playlist_progress'
                                enhanced_info['current'] = song_idx
                                enhanced_info['total'] = total_songs
                                enhanced_info['song'] = song_title
                                enhanced_info['album'] = playlist_title
                            
                            progress_callback(enhanced_info)
                    return wrapped_callback
                
                wrapped_progress = create_wrapped_callback(i, len(entries), song_name, playlist_info.get('title', ''))
                
                result = self.download_song(video_id, download_dir, quality, wrapped_progress)
                results['songs'].append(result)
                
                if result.get('success'):
                    results['downloaded_songs'] += 1
                    # 更新码率和格式信息
                    if result.get('bitrate'):
                        results['bitrate'] = result.get('bitrate')
                    if result.get('file_format'):
                        results['file_format'] = result.get('file_format')
            
            return results
            
        except Exception as e:
            logger.error(f"❌ 下载播放列表失败: {e}")
            return {'success': False, 'error': str(e)}
    
    def _add_metadata_to_file(self, file_path: str, info: Dict[str, Any]) -> bool:
        """
        为下载的音频文件添加元数据标签
        
        Args:
            file_path: 音频文件路径
            info: yt-dlp 返回的视频/音频信息
            
        Returns:
            是否成功写入元数据
        """
        try:
            # 提取元数据信息
            title = info.get('title', '')
            artist = info.get('artist', info.get('uploader', ''))
            album = info.get('album', '')
            
            # 尝试从 release_year 或 upload_date 获取年份
            year = info.get('release_year', '')
            if not year and info.get('upload_date'):
                upload_date = str(info.get('upload_date', ''))
                if len(upload_date) >= 4:
                    year = upload_date[:4]
            
            # 获取封面
            cover_url = info.get('thumbnail', '')
            cover_data = None
            
            if cover_url:
                try:
                    import requests
                    response = requests.get(cover_url, timeout=30)
                    if response.status_code == 200:
                        cover_data = response.content
                except Exception as e:
                    logger.warning(f"⚠️ 获取封面失败: {e}")
            
            # 获取曲目号（如果在播放列表中）
            track_number = info.get('playlist_index', '')
            total_tracks = info.get('playlist_count', '')
            
            # 准备元数据字典
            metadata = {
                'title': title,
                'artist': artist,
                'album': album if album else info.get('playlist_title', ''),
                'album_artist': info.get('album_artist', artist),
                'date': year,
                'genre': info.get('genre', ''),
                'total_tracks': total_tracks,  # 添加总曲目数
            }
            
            if track_number:
                metadata['track_number'] = str(track_number)
            
            # 使用元数据管理器写入
            if self.metadata_manager:
                success = self.metadata_manager.add_metadata_to_file(
                    file_path=file_path,
                    title=metadata.get('title'),
                    artist=metadata.get('artist'),
                    album=metadata.get('album'),
                    album_artist=metadata.get('album_artist'),
                    track_number=metadata.get('track_number'),
                    date=metadata.get('date'),
                    genre=metadata.get('genre'),
                    cover_data=cover_data,
                    mime_type='image/jpeg' if cover_data else None
                )
                
                if success:
                    logger.info(f"✅ 元数据写入成功: {title}")
                    return True
                else:
                    logger.warning(f"⚠️ 元数据管理器写入失败，尝试备用方法")
            
            # 备用方法：直接使用 mutagen
            return self._embed_metadata_fallback(file_path, metadata, cover_data)
            
        except Exception as e:
            logger.error(f"❌ 写入元数据失败: {e}")
            return False
    
    def _embed_metadata_fallback(self, file_path: str, metadata: Dict[str, Any], cover_data: bytes = None) -> bool:
        """
        备用元数据写入方法，直接使用 mutagen
        """
        try:
            file_ext = Path(file_path).suffix.lower()
            
            if file_ext == '.mp3':
                return self._embed_mp3_metadata(file_path, metadata, cover_data)
            elif file_ext == '.flac':
                return self._embed_flac_metadata(file_path, metadata, cover_data)
            elif file_ext in ['.m4a', '.aac', '.mp4']:
                return self._embed_m4a_metadata(file_path, metadata, cover_data)
            else:
                logger.warning(f"⚠️ 不支持的音频格式: {file_ext}")
                return False
                
        except Exception as e:
            logger.error(f"❌ 备用元数据写入失败: {e}")
            return False
    
    def _embed_mp3_metadata(self, file_path: str, metadata: Dict[str, Any], cover_data: bytes = None) -> bool:
        """写入 MP3 元数据"""
        try:
            from mutagen.mp3 import MP3
            from mutagen.id3 import ID3, TIT2, TPE1, TALB, TPE2, TDRC, TRCK, TCON, APIC
            
            audio = MP3(file_path)
            
            if audio.tags is None:
                audio.add_tags()
            
            tags = audio.tags
            
            if metadata.get('title'):
                tags['TIT2'] = TIT2(encoding=3, text=metadata['title'])
            if metadata.get('artist'):
                tags['TPE1'] = TPE1(encoding=3, text=metadata['artist'])
            if metadata.get('album'):
                tags['TALB'] = TALB(encoding=3, text=metadata['album'])
            if metadata.get('album_artist'):
                tags['TPE2'] = TPE2(encoding=3, text=metadata['album_artist'])
            if metadata.get('date'):
                tags['TDRC'] = TDRC(encoding=3, text=str(metadata['date']))
            if metadata.get('track_number'):
                track_str = str(metadata['track_number'])
                if metadata.get('total_tracks'):
                    track_str = f"{track_str}/{metadata['total_tracks']}"
                tags['TRCK'] = TRCK(encoding=3, text=track_str)
            if metadata.get('genre'):
                tags['TCON'] = TCON(encoding=3, text=metadata['genre'])
            
            if cover_data:
                tags['APIC'] = APIC(
                    encoding=3,
                    mime='image/jpeg',
                    type=3,  # Front cover
                    desc='Cover',
                    data=cover_data
                )
            
            audio.save()
            logger.info(f"✅ MP3 元数据写入成功")
            return True
            
        except Exception as e:
            logger.error(f"❌ MP3 元数据写入失败: {e}")
            return False
    
    def _embed_flac_metadata(self, file_path: str, metadata: Dict[str, Any], cover_data: bytes = None) -> bool:
        """写入 FLAC 元数据"""
        try:
            from mutagen.flac import FLAC, Picture
            
            audio = FLAC(file_path)
            
            if metadata.get('title'):
                audio['TITLE'] = metadata['title']
            if metadata.get('artist'):
                audio['ARTIST'] = metadata['artist']
            if metadata.get('album'):
                audio['ALBUM'] = metadata['album']
            if metadata.get('album_artist'):
                audio['ALBUMARTIST'] = metadata['album_artist']
            if metadata.get('date'):
                audio['DATE'] = str(metadata['date'])
            if metadata.get('track_number'):
                audio['TRACKNUMBER'] = str(metadata['track_number'])
            if metadata.get('total_tracks'):
                audio['TOTALTRACKS'] = str(metadata['total_tracks'])
                audio['TRACKTOTAL'] = str(metadata['total_tracks'])
            if metadata.get('genre'):
                audio['GENRE'] = metadata['genre']
            
            if cover_data:
                picture = Picture()
                picture.type = 3  # Front cover
                picture.mime = 'image/jpeg'
                picture.desc = 'Cover'
                picture.data = cover_data
                audio.add_picture(picture)
            
            audio.save()
            logger.info(f"✅ FLAC 元数据写入成功")
            return True
            
        except Exception as e:
            logger.error(f"❌ FLAC 元数据写入失败: {e}")
            return False
    
    def _embed_m4a_metadata(self, file_path: str, metadata: Dict[str, Any], cover_data: bytes = None) -> bool:
        """写入 M4A/AAC 元数据"""
        try:
            from mutagen.mp4 import MP4, MP4Cover
            
            audio = MP4(file_path)
            
            if metadata.get('title'):
                audio['\xa9nam'] = metadata['title']
            if metadata.get('artist'):
                audio['\xa9ART'] = metadata['artist']
            if metadata.get('album'):
                audio['\xa9alb'] = metadata['album']
            if metadata.get('album_artist'):
                audio['aART'] = metadata['album_artist']
            if metadata.get('date'):
                audio['\xa9day'] = str(metadata['date'])
            if metadata.get('genre'):
                audio['\xa9gen'] = metadata['genre']
            
            # 处理曲目号
            if metadata.get('track_number'):
                track_str = str(metadata['track_number'])
                if '/' in track_str:
                    track, total = track_str.split('/')
                    audio['trkn'] = [(int(track), int(total))]
                else:
                    # 尝试获取 total_tracks
                    total = int(metadata.get('total_tracks', 0)) if metadata.get('total_tracks') else 0
                    audio['trkn'] = [(int(track_str), total)]
            
            if cover_data:
                audio['covr'] = [MP4Cover(cover_data, imageformat=MP4Cover.FORMAT_JPEG)]
            
            audio.save()
            logger.info(f"✅ M4A 元数据写入成功")
            return True
            
        except Exception as e:
            logger.error(f"❌ M4A 元数据写入失败: {e}")
            return False
