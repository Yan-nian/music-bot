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
        
        logger.info("✅ YouTube Music 下载器初始化完成")
    
    def _load_config(self):
        """加载配置"""
        self.quality = self.get_config('youtube_music_quality', 'best')
        self.format = self.get_config('youtube_music_format', 'm4a')
        self.download_cover = self.get_config('youtube_music_download_cover', True)
        
        logger.info(f"📝 YouTube Music 配置: 音质={self.quality}, 格式={self.format}")
    
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
            
            # 添加进度回调
            if progress_callback:
                def progress_hook(d):
                    if d['status'] == 'downloading':
                        downloaded = d.get('downloaded_bytes', 0)
                        total = d.get('total_bytes') or d.get('total_bytes_estimate', 0)
                        if total > 0:
                            progress_callback({
                                'status': 'progress',
                                'percent': (downloaded / total) * 100,
                                'downloaded': downloaded,
                                'total': total,
                            })
                    elif d['status'] == 'finished':
                        progress_callback({
                            'status': 'finished',
                            'filename': d.get('filename', ''),
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
                        return {
                            'success': True,
                            'song_title': info.get('title', ''),
                            'song_artist': info.get('artist', info.get('uploader', '')),
                            'filepath': str(actual_file),
                            'size_mb': actual_file.stat().st_size / (1024 * 1024),
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
            }
            
            entries = playlist_info.get('entries', [])
            
            for i, entry in enumerate(entries, 1):
                video_id = entry.get('id', entry.get('url', ''))
                
                if progress_callback:
                    progress_callback({
                        'status': 'playlist_progress',
                        'current': i,
                        'total': len(entries),
                        'song': entry.get('title', ''),
                    })
                
                result = self.download_song(video_id, download_dir, quality, progress_callback)
                results['songs'].append(result)
                
                if result.get('success'):
                    results['downloaded_songs'] += 1
            
            return results
            
        except Exception as e:
            logger.error(f"❌ 下载播放列表失败: {e}")
            return {'success': False, 'error': str(e)}
