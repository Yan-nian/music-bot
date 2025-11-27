#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
网易云音乐下载器
精简版 - 专注于音乐下载功能
"""

import os
import re
import json
import time
import logging
import requests
from pathlib import Path
from typing import Optional, Dict, List, Any, Callable
from hashlib import md5

from .base import BaseDownloader

logger = logging.getLogger(__name__)


class NeteaseDownloader(BaseDownloader):
    """网易云音乐下载器"""
    
    # URL 正则模式
    URL_PATTERNS = {
        'song': [
            r'music\.163\.com.*song\?id=(\d+)',
            r'music\.163\.com.*song/(\d+)',
            r'163cn\.tv/([a-zA-Z0-9]+)',
        ],
        'album': [
            r'music\.163\.com.*album\?id=(\d+)',
            r'music\.163\.com.*album/(\d+)',
        ],
        'playlist': [
            r'music\.163\.com.*playlist\?id=(\d+)',
            r'music\.163\.com.*playlist/(\d+)',
        ],
        'artist': [
            r'music\.163\.com.*artist\?id=(\d+)',
            r'music\.163\.com.*artist/(\d+)',
        ],
    }
    
    # 音质映射
    QUALITY_MAP = {
        '标准': 'standard',
        '较高': 'higher',
        '极高': 'exhigh',
        '无损': 'lossless',
        'standard': 'standard',
        'higher': 'higher',
        'exhigh': 'exhigh',
        'lossless': 'lossless',
        '128k': 'standard',
        '320k': 'higher',
        'flac': 'lossless',
    }
    
    QUALITY_LEVELS = {
        'standard': {'level': 'standard', 'bitrate': 128000},
        'higher': {'level': 'higher', 'bitrate': 320000},
        'exhigh': {'level': 'exhigh', 'bitrate': 320000},
        'lossless': {'level': 'lossless', 'bitrate': 0},
    }
    
    def __init__(self, config_manager=None):
        super().__init__(config_manager)
        
        self.session = requests.Session()
        self.api_url = "https://music.163.com"
        
        # 设置请求头
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Referer': 'https://music.163.com/',
            'Accept': 'application/json, text/plain, */*',
            'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
            'Connection': 'keep-alive',
        })
        
        # 加载配置
        self._load_config()
        
        # 加载 cookies
        self._load_cookies()
        
        logger.info("✅ 网易云音乐下载器初始化完成")
    
    def _load_config(self):
        """加载配置"""
        self.quality = self.get_config('netease_quality', '无损')
        self.download_lyrics = self.get_config('netease_download_lyrics', True)
        self.download_cover = self.get_config('netease_download_cover', True)
        self.lyrics_merge = self.get_config('netease_lyrics_merge', False)
        self.dir_format = self.get_config('netease_dir_format', '{ArtistName}/{AlbumName}')
        self.album_folder_format = self.get_config('netease_album_folder_format', '{AlbumName}({ReleaseDate})')
        self.song_file_format = self.get_config('netease_song_file_format', '{SongName}')
        
        logger.info(f"📝 网易云配置: 音质={self.quality}, 歌词={self.download_lyrics}, 封面={self.download_cover}")
    
    def _load_cookies(self):
        """加载 cookies"""
        # 从配置获取 cookies
        cookies_str = self.get_config('netease_cookies', '')
        
        if cookies_str:
            self._parse_cookies(cookies_str)
            return
        
        # 尝试从环境变量获取
        cookies_env = os.getenv('NCM_COOKIES', '')
        if cookies_env:
            self._parse_cookies(cookies_env)
            return
        
        # 尝试从文件加载
        cookie_paths = [
            '/app/cookies/ncm_cookies.txt',
            './cookies/ncm_cookies.txt',
            './ncm_cookies.txt',
        ]
        
        for path in cookie_paths:
            if os.path.exists(path):
                try:
                    with open(path, 'r', encoding='utf-8') as f:
                        content = f.read().strip()
                    self._parse_cookies(content)
                    logger.info(f"✅ 从文件加载 cookies: {path}")
                    return
                except Exception as e:
                    logger.warning(f"⚠️ 读取 cookies 失败: {e}")
        
        logger.warning("⚠️ 未配置网易云 cookies，部分功能可能受限")
    
    def _parse_cookies(self, cookies_str: str):
        """解析 cookies 字符串"""
        try:
            if cookies_str.startswith('{'):
                # JSON 格式
                cookies_dict = json.loads(cookies_str)
                for name, value in cookies_dict.items():
                    self.session.cookies.set(name, value, domain='.music.163.com')
            else:
                # 字符串格式
                for cookie in cookies_str.split(';'):
                    if '=' in cookie:
                        name, value = cookie.strip().split('=', 1)
                        self.session.cookies.set(name.strip(), value.strip(), domain='.music.163.com')
            
            logger.info(f"✅ 已加载 {len(self.session.cookies)} 个 cookies")
        except Exception as e:
            logger.error(f"❌ 解析 cookies 失败: {e}")
    
    def is_supported_url(self, url: str) -> bool:
        """检查是否为支持的网易云 URL"""
        if not url:
            return False
        
        netease_domains = ['music.163.com', '163cn.tv', 'y.music.163.com']
        return any(domain in url.lower() for domain in netease_domains)
    
    def parse_url(self, url: str) -> Optional[Dict[str, Any]]:
        """解析网易云 URL"""
        if not self.is_supported_url(url):
            return None
        
        for content_type, patterns in self.URL_PATTERNS.items():
            for pattern in patterns:
                match = re.search(pattern, url)
                if match:
                    return {
                        'type': content_type,
                        'id': match.group(1),
                        'url': url
                    }
        
        return None
    
    def get_song_info(self, song_id: str) -> Optional[Dict[str, Any]]:
        """获取歌曲详情"""
        try:
            url = f"{self.api_url}/api/v3/song/detail"
            params = {'c': json.dumps([{'id': song_id}])}
            
            response = self.session.get(url, params=params, timeout=30)
            data = response.json()
            
            if data.get('code') == 200 and data.get('songs'):
                song = data['songs'][0]
                return {
                    'id': str(song['id']),
                    'name': song['name'],
                    'artist': '/'.join([ar['name'] for ar in song.get('ar', [])]),
                    'artist_id': song['ar'][0]['id'] if song.get('ar') else None,
                    'album': song.get('al', {}).get('name', ''),
                    'album_id': song.get('al', {}).get('id'),
                    'cover': song.get('al', {}).get('picUrl', ''),
                    'duration': song.get('dt', 0) // 1000,
                    'publish_time': song.get('publishTime'),
                }
            
            return None
            
        except Exception as e:
            logger.error(f"❌ 获取歌曲信息失败: {e}")
            return None
    
    def get_song_url(self, song_id: str, quality: str = None) -> Optional[Dict[str, Any]]:
        """获取歌曲下载链接"""
        try:
            quality = quality or self.quality
            level = self.QUALITY_MAP.get(quality, 'lossless')
            
            url = f"{self.api_url}/api/song/enhance/player/url/v1"
            params = {
                'ids': f'[{song_id}]',
                'level': level,
                'encodeType': 'flac' if level == 'lossless' else 'mp3',
            }
            
            response = self.session.get(url, params=params, timeout=30)
            data = response.json()
            
            if data.get('code') == 200 and data.get('data'):
                song_data = data['data'][0]
                if song_data.get('url'):
                    return {
                        'url': song_data['url'],
                        'size': song_data.get('size', 0),
                        'type': song_data.get('type', 'mp3'),
                        'level': song_data.get('level', level),
                        'bitrate': song_data.get('br', 0),
                    }
            
            return None
            
        except Exception as e:
            logger.error(f"❌ 获取歌曲URL失败: {e}")
            return None
    
    def get_lyrics(self, song_id: str) -> Optional[str]:
        """获取歌词"""
        try:
            url = f"{self.api_url}/api/song/lyric"
            params = {'id': song_id, 'lv': 1, 'tv': 1}
            
            response = self.session.get(url, params=params, timeout=30)
            data = response.json()
            
            if data.get('code') == 200:
                lrc = data.get('lrc', {}).get('lyric', '')
                tlyric = data.get('tlyric', {}).get('lyric', '')
                
                if self.lyrics_merge and tlyric:
                    return self._merge_lyrics(lrc, tlyric)
                return lrc
            
            return None
            
        except Exception as e:
            logger.error(f"❌ 获取歌词失败: {e}")
            return None
    
    def _merge_lyrics(self, lrc: str, tlyric: str) -> str:
        """合并中英文歌词"""
        if not tlyric:
            return lrc
        
        # 简单合并实现
        return f"{lrc}\n\n--- 翻译 ---\n\n{tlyric}"
    
    def download_song(self, song_id: str, download_dir: str,
                     quality: str = None,
                     progress_callback: Optional[Callable] = None) -> Dict[str, Any]:
        """下载单曲"""
        try:
            # 获取歌曲信息
            song_info = self.get_song_info(song_id)
            if not song_info:
                return {'success': False, 'error': '无法获取歌曲信息'}
            
            # 获取下载链接
            song_url_info = self.get_song_url(song_id, quality)
            if not song_url_info:
                return {'success': False, 'error': '无法获取下载链接，可能需要会员'}
            
            # 构建文件名
            filename = self._build_filename(song_info, song_url_info.get('type', 'mp3'))
            
            # 构建目录
            save_dir = self._build_directory(download_dir, song_info)
            self.ensure_dir(save_dir)
            
            filepath = os.path.join(save_dir, filename)
            
            # 下载文件
            if progress_callback:
                progress_callback({
                    'status': 'downloading',
                    'song': song_info['name'],
                    'artist': song_info['artist'],
                })
            
            success = self._download_file(song_url_info['url'], filepath, progress_callback)
            
            if success:
                # 下载歌词
                if self.download_lyrics:
                    lyrics = self.get_lyrics(song_id)
                    if lyrics:
                        lrc_path = os.path.splitext(filepath)[0] + '.lrc'
                        with open(lrc_path, 'w', encoding='utf-8') as f:
                            f.write(lyrics)
                
                # 下载封面
                if self.download_cover and song_info.get('cover'):
                    cover_path = os.path.splitext(filepath)[0] + '.jpg'
                    self._download_file(song_info['cover'], cover_path)
                
                return {
                    'success': True,
                    'song_title': song_info['name'],
                    'song_artist': song_info['artist'],
                    'filepath': filepath,
                    'size_mb': os.path.getsize(filepath) / (1024 * 1024),
                    'quality': song_url_info.get('level', ''),
                }
            
            return {'success': False, 'error': '下载失败'}
            
        except Exception as e:
            logger.error(f"❌ 下载歌曲失败: {e}")
            return {'success': False, 'error': str(e)}
    
    def _build_filename(self, song_info: Dict, ext: str) -> str:
        """构建文件名"""
        template = self.song_file_format
        
        filename = template.replace('{SongName}', song_info.get('name', 'Unknown'))
        filename = filename.replace('{ArtistName}', song_info.get('artist', 'Unknown'))
        
        filename = self.clean_filename(filename)
        return f"{filename}.{ext}"
    
    def _build_directory(self, base_dir: str, song_info: Dict) -> str:
        """构建保存目录"""
        template = self.dir_format
        
        path = template.replace('{ArtistName}', self.clean_filename(song_info.get('artist', 'Unknown')))
        path = path.replace('{AlbumName}', self.clean_filename(song_info.get('album', 'Unknown')))
        
        return os.path.join(base_dir, path)
    
    def _download_file(self, url: str, filepath: str,
                      progress_callback: Optional[Callable] = None) -> bool:
        """下载文件"""
        try:
            response = self.session.get(url, stream=True, timeout=300)
            response.raise_for_status()
            
            total_size = int(response.headers.get('content-length', 0))
            downloaded = 0
            
            with open(filepath, 'wb') as f:
                for chunk in response.iter_content(chunk_size=8192):
                    if chunk:
                        f.write(chunk)
                        downloaded += len(chunk)
                        
                        if progress_callback and total_size > 0:
                            progress = (downloaded / total_size) * 100
                            progress_callback({
                                'status': 'progress',
                                'percent': progress,
                                'downloaded': downloaded,
                                'total': total_size,
                            })
            
            return True
            
        except Exception as e:
            logger.error(f"❌ 下载文件失败: {e}")
            return False
    
    def get_album_songs(self, album_id: str) -> List[Dict[str, Any]]:
        """获取专辑歌曲列表"""
        try:
            url = f"{self.api_url}/api/v1/album/{album_id}"
            
            response = self.session.get(url, timeout=30)
            data = response.json()
            
            if data.get('code') == 200:
                songs = data.get('songs', [])
                return [
                    {
                        'id': str(song['id']),
                        'name': song['name'],
                        'artist': '/'.join([ar['name'] for ar in song.get('ar', [])]),
                        'album': data.get('album', {}).get('name', ''),
                        'publish_time': data.get('album', {}).get('publishTime'),
                    }
                    for song in songs
                ]
            
            return []
            
        except Exception as e:
            logger.error(f"❌ 获取专辑歌曲失败: {e}")
            return []
    
    def download_album(self, album_id: str, download_dir: str,
                      quality: str = None,
                      progress_callback: Optional[Callable] = None) -> Dict[str, Any]:
        """下载专辑"""
        songs = self.get_album_songs(album_id)
        
        if not songs:
            return {'success': False, 'error': '无法获取专辑歌曲'}
        
        results = {
            'success': True,
            'album_name': songs[0].get('album', ''),
            'total_songs': len(songs),
            'downloaded_songs': 0,
            'songs': [],
        }
        
        for i, song in enumerate(songs, 1):
            if progress_callback:
                progress_callback({
                    'status': 'album_progress',
                    'current': i,
                    'total': len(songs),
                    'song': song['name'],
                })
            
            result = self.download_song(song['id'], download_dir, quality, progress_callback)
            results['songs'].append(result)
            
            if result.get('success'):
                results['downloaded_songs'] += 1
        
        return results
    
    def get_playlist_songs(self, playlist_id: str) -> List[Dict[str, Any]]:
        """获取歌单歌曲列表"""
        try:
            url = f"{self.api_url}/api/v6/playlist/detail"
            params = {'id': playlist_id}
            
            response = self.session.get(url, params=params, timeout=30)
            data = response.json()
            
            if data.get('code') == 200:
                playlist = data.get('playlist', {})
                track_ids = playlist.get('trackIds', [])
                
                # 获取歌曲详情
                songs = []
                for track in track_ids[:200]:  # 限制最多200首
                    song_info = self.get_song_info(str(track['id']))
                    if song_info:
                        songs.append(song_info)
                
                return songs
            
            return []
            
        except Exception as e:
            logger.error(f"❌ 获取歌单歌曲失败: {e}")
            return []
    
    def download_playlist(self, playlist_id: str, download_dir: str,
                         quality: str = None,
                         progress_callback: Optional[Callable] = None) -> Dict[str, Any]:
        """下载歌单"""
        songs = self.get_playlist_songs(playlist_id)
        
        if not songs:
            return {'success': False, 'error': '无法获取歌单歌曲'}
        
        results = {
            'success': True,
            'playlist_id': playlist_id,
            'total_songs': len(songs),
            'downloaded_songs': 0,
            'songs': [],
        }
        
        for i, song in enumerate(songs, 1):
            if progress_callback:
                progress_callback({
                    'status': 'playlist_progress',
                    'current': i,
                    'total': len(songs),
                    'song': song['name'],
                })
            
            result = self.download_song(song['id'], download_dir, quality, progress_callback)
            results['songs'].append(result)
            
            if result.get('success'):
                results['downloaded_songs'] += 1
        
        return results
