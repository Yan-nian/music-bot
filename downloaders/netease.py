#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
网易云音乐下载器
使用公开 API 实现
"""

import os
import re
import json
import time
import base64
import logging
import requests
import hashlib
import binascii
from pathlib import Path
from typing import Optional, Dict, List, Any, Callable

from Crypto.Cipher import AES

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
    }
    
    # 音质映射
    QUALITY_MAP = {
        '标准': 'standard',
        '较高': 'exhigh',
        '极高': 'lossless',
        '无损': 'hires',
        'standard': 'standard',
        'exhigh': 'exhigh',
        'lossless': 'lossless',
        'hires': 'hires',
    }
    
    def __init__(self, config_manager=None):
        super().__init__(config_manager)
        
        self.session = requests.Session()
        
        # 设置请求头
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36',
            'Accept': '*/*',
            'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
            'Connection': 'keep-alive',
            'Content-Type': 'application/x-www-form-urlencoded',
            'Origin': 'https://music.163.com',
            'Referer': 'https://music.163.com/',
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
        self.song_file_format = self.get_config('netease_song_file_format', '{SongName}')
        
        logger.info(f"📝 网易云配置: 音质={self.quality}, 歌词={self.download_lyrics}")

    def _load_cookies(self):
        """加载 cookies"""
        cookies_str = self.get_config('netease_cookies', '')
        
        if cookies_str:
            self._parse_cookies(cookies_str)
            return
        
        cookies_env = os.getenv('NCM_COOKIES', '')
        if cookies_env:
            self._parse_cookies(cookies_env)
            return
        
        cookie_paths = [
            '/app/cookies/ncm_cookies.txt',
            './cookies/ncm_cookies.txt',
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
                cookies_dict = json.loads(cookies_str)
                for name, value in cookies_dict.items():
                    self.session.cookies.set(name, str(value), domain='.music.163.com')
            else:
                for cookie in cookies_str.split(';'):
                    if '=' in cookie:
                        name, value = cookie.strip().split('=', 1)
                        self.session.cookies.set(name.strip(), value.strip(), domain='.music.163.com')
            
            logger.info(f"✅ 已加载 {len(self.session.cookies)} 个 cookies")
        except Exception as e:
            logger.error(f"❌ 解析 cookies 失败: {e}")

    # ============ 加密相关 ============
    
    def _aes_encrypt(self, text: str, key: str) -> str:
        """AES CBC 加密"""
        iv = '0102030405060708'
        cipher = AES.new(key.encode('utf-8'), AES.MODE_CBC, iv.encode('utf-8'))
        pad_len = 16 - len(text.encode('utf-8')) % 16
        text = text + chr(pad_len) * pad_len
        encrypted = cipher.encrypt(text.encode('utf-8'))
        return base64.b64encode(encrypted).decode('utf-8')
    
    def _rsa_encrypt(self, text: str) -> str:
        """RSA 加密"""
        e = '010001'
        n = '00e0b509f6259df8642dbc35662901477df22677ec152b5ff68ace615bb7b725152b3ab17a876aea8a5aa76d2e417629ec4ee341f56135fccf695280104e0312ecbda92557c93870114af6c9d05c4f7f0c3685b7a46bee255932575cce10b424d813cfe4875d3e82047b97ddef52741d546b8e289dc6935b3ece0462db0a22b8e7'
        text = text[::-1]
        rs = pow(int(binascii.hexlify(text.encode('utf-8')), 16), int(e, 16), int(n, 16))
        return format(rs, 'x').zfill(256)
    
    def _weapi_encrypt(self, data: dict) -> dict:
        """weapi 加密"""
        secret_key = '0CoJUm6Qyw8W8jud'
        random_key = 'F3nws0dj1G5HfLKx'  # 固定随机密钥
        
        text = json.dumps(data)
        params = self._aes_encrypt(text, secret_key)
        params = self._aes_encrypt(params, random_key)
        enc_sec_key = self._rsa_encrypt(random_key)
        
        return {
            'params': params,
            'encSecKey': enc_sec_key
        }

    # ============ URL 解析 ============
    
    def is_supported_url(self, url: str) -> bool:
        """检查是否为支持的网易云 URL"""
        if not url:
            return False
        netease_domains = ['music.163.com', '163cn.tv']
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

    # ============ API 调用 ============
    
    def get_song_info(self, song_id: str) -> Optional[Dict[str, Any]]:
        """获取歌曲详情"""
        try:
            url = 'https://music.163.com/weapi/v3/song/detail'
            data = {'c': json.dumps([{'id': song_id}]), 'ids': f'[{song_id}]'}
            encrypted = self._weapi_encrypt(data)
            
            response = self.session.post(url, data=encrypted, timeout=30)
            result = response.json()
            
            if result.get('code') == 200 and result.get('songs'):
                song = result['songs'][0]
                return {
                    'id': str(song['id']),
                    'name': song['name'],
                    'artist': '/'.join([ar['name'] for ar in song.get('ar', [])]),
                    'album': song.get('al', {}).get('name', ''),
                    'album_id': song.get('al', {}).get('id'),
                    'cover': song.get('al', {}).get('picUrl', ''),
                    'duration': song.get('dt', 0) // 1000,
                }
            
            logger.warning(f"⚠️ 获取歌曲信息失败: code={result.get('code')}, msg={result.get('msg', '')}")
            return None
            
        except Exception as e:
            logger.error(f"❌ 获取歌曲信息失败: {e}")
            return None
    
    def get_song_url(self, song_id: str, quality: str = None) -> Optional[Dict[str, Any]]:
        """获取歌曲下载链接"""
        try:
            level = self.QUALITY_MAP.get(quality or self.quality, 'lossless')
            
            url = 'https://music.163.com/weapi/song/enhance/player/url/v1'
            data = {
                'ids': [int(song_id)],
                'level': level,
                'encodeType': 'flac',
                'csrf_token': ''
            }
            encrypted = self._weapi_encrypt(data)
            
            response = self.session.post(url, data=encrypted, timeout=30)
            result = response.json()
            
            if result.get('code') == 200 and result.get('data'):
                song_data = result['data'][0]
                if song_data.get('url'):
                    return {
                        'url': song_data['url'],
                        'size': song_data.get('size', 0),
                        'type': song_data.get('type', 'mp3'),
                        'level': song_data.get('level', level),
                        'bitrate': song_data.get('br', 0),
                    }
                else:
                    logger.warning(f"⚠️ 歌曲无下载链接，可能需要VIP: code={song_data.get('code')}")
            
            return None
            
        except Exception as e:
            logger.error(f"❌ 获取歌曲URL失败: {e}")
            return None
    
    def get_lyrics(self, song_id: str) -> Optional[str]:
        """获取歌词"""
        try:
            url = 'https://music.163.com/weapi/song/lyric'
            data = {'id': song_id, 'lv': -1, 'tv': -1, 'csrf_token': ''}
            encrypted = self._weapi_encrypt(data)
            
            response = self.session.post(url, data=encrypted, timeout=30)
            result = response.json()
            
            if result.get('code') == 200:
                lrc = result.get('lrc', {}).get('lyric', '')
                tlyric = result.get('tlyric', {}).get('lyric', '')
                
                if self.lyrics_merge and tlyric:
                    return f"{lrc}\n\n--- 翻译 ---\n\n{tlyric}"
                return lrc
            
            return None
            
        except Exception as e:
            logger.error(f"❌ 获取歌词失败: {e}")
            return None

    # ============ 下载功能 ============
    
    def download_song(self, song_id: str, download_dir: str,
                     quality: str = None,
                     progress_callback: Optional[Callable] = None) -> Dict[str, Any]:
        """下载单曲"""
        try:
            song_info = self.get_song_info(song_id)
            if not song_info:
                return {'success': False, 'error': '无法获取歌曲信息'}
            
            song_url_info = self.get_song_url(song_id, quality)
            if not song_url_info:
                return {'success': False, 'error': '无法获取下载链接，可能需要VIP'}
            
            # 构建文件名和目录
            filename = self._build_filename(song_info, song_url_info.get('type', 'mp3'))
            save_dir = self._build_directory(download_dir, song_info)
            self.ensure_dir(save_dir)
            filepath = os.path.join(save_dir, filename)
            
            if progress_callback:
                progress_callback({
                    'status': 'downloading',
                    'song': song_info['name'],
                    'artist': song_info['artist'],
                })
            
            # 下载文件
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
        filename = self.song_file_format.replace('{SongName}', song_info.get('name', 'Unknown'))
        filename = filename.replace('{ArtistName}', song_info.get('artist', 'Unknown'))
        filename = self.clean_filename(filename)
        return f"{filename}.{ext}"
    
    def _build_directory(self, base_dir: str, song_info: Dict) -> str:
        """构建保存目录"""
        path = self.dir_format.replace('{ArtistName}', self.clean_filename(song_info.get('artist', 'Unknown')))
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

    # ============ 专辑/歌单 ============
    
    def get_album_songs(self, album_id: str) -> List[Dict[str, Any]]:
        """获取专辑歌曲列表"""
        try:
            url = 'https://music.163.com/weapi/v1/album/' + album_id
            data = {'csrf_token': ''}
            encrypted = self._weapi_encrypt(data)
            
            response = self.session.post(url, data=encrypted, timeout=30)
            result = response.json()
            
            if result.get('code') == 200:
                album_info = result.get('album', {})
                songs = result.get('songs', [])
                return [
                    {
                        'id': str(song['id']),
                        'name': song['name'],
                        'artist': '/'.join([ar['name'] for ar in song.get('ar', [])]),
                        'album': album_info.get('name', ''),
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
            
            time.sleep(0.5)  # 避免请求过快
        
        return results
    
    def get_playlist_songs(self, playlist_id: str) -> List[Dict[str, Any]]:
        """获取歌单歌曲列表"""
        try:
            url = 'https://music.163.com/weapi/v6/playlist/detail'
            data = {'id': playlist_id, 'n': 1000, 'csrf_token': ''}
            encrypted = self._weapi_encrypt(data)
            
            response = self.session.post(url, data=encrypted, timeout=30)
            result = response.json()
            
            if result.get('code') == 200:
                playlist = result.get('playlist', {})
                tracks = playlist.get('tracks', [])
                
                if not tracks:
                    # 如果 tracks 为空，需要单独获取歌曲详情
                    track_ids = playlist.get('trackIds', [])
                    songs = []
                    for track in track_ids[:100]:  # 限制100首
                        song_info = self.get_song_info(str(track['id']))
                        if song_info:
                            songs.append(song_info)
                        time.sleep(0.1)
                    return songs
                
                return [
                    {
                        'id': str(song['id']),
                        'name': song['name'],
                        'artist': '/'.join([ar['name'] for ar in song.get('ar', [])]),
                        'album': song.get('al', {}).get('name', ''),
                    }
                    for song in tracks
                ]
            
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
            
            time.sleep(0.5)
        
        return results
