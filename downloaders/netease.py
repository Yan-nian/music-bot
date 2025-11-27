#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
网易云音乐下载器
使用官方 API 实现（参考原项目 renlixing87/savextube）
"""

import os
import re
import json
import time
import logging
import requests
from pathlib import Path
from typing import Optional, Dict, List, Any, Callable

from .base import BaseDownloader

logger = logging.getLogger(__name__)


class NeteaseDownloader(BaseDownloader):
    """网易云音乐下载器 - 使用官方 API"""
    
    # URL 正则模式
    URL_PATTERNS = {
        'song': [
            r'music\.163\.com.*[#/]song\?id=(\d+)',
            r'music\.163\.com.*song/(\d+)',
            r'163cn\.tv/([a-zA-Z0-9]+)',
        ],
        'album': [
            r'music\.163\.com.*[#/]album\?id=(\d+)',
            r'music\.163\.com.*album/(\d+)',
        ],
        'playlist': [
            r'music\.163\.com.*[#/]playlist\?id=(\d+)',
            r'music\.163\.com.*playlist/(\d+)',
        ],
    }
    
    # 音质映射 - 网易云 API 参数
    QUALITY_MAP = {
        '标准': 128000,
        '较高': 192000,
        '极高': 320000,
        '无损': 999000,
        '128k': 128000,
        '192k': 192000,
        '320k': 320000,
        'flac': 999000,
        'lossless': 999000,
    }
    
    # 音质降级顺序
    QUALITY_FALLBACK = ['flac', '320k', '192k', '128k']
    
    def __init__(self, config_manager=None):
        super().__init__(config_manager)
        
        self.session = requests.Session()
        
        # 网易云音乐官方 API 配置
        self.api_url = "https://music.163.com"
        
        # 设置请求头
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
            'Referer': 'https://music.163.com/',
            'Accept': 'application/json, text/plain, */*',
            'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
            'Connection': 'keep-alive',
        })
        
        # 加载配置
        self._load_config()
        
        # 加载 cookies
        self._load_cookies()
        
        logger.info("✅ 网易云音乐下载器初始化完成 (官方 API)")
    
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
        # 优先从配置获取
        cookies_str = self.get_config('netease_cookies', '')
        
        if cookies_str:
            self._parse_cookies(cookies_str)
            return
        
        # 从环境变量获取
        cookies_env = os.getenv('NCM_COOKIES', '')
        if cookies_env:
            self._parse_cookies(cookies_env)
            return
        
        # 从文件获取
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
                    self.session.cookies.set(name, str(value), domain='.music.163.com')
            else:
                # 字符串格式: name=value; name2=value2
                for cookie in cookies_str.split(';'):
                    if '=' in cookie:
                        name, value = cookie.strip().split('=', 1)
                        self.session.cookies.set(name.strip(), value.strip(), domain='.music.163.com')
            
            logger.info(f"✅ 已加载 {len(self.session.cookies)} 个 cookies")
        except Exception as e:
            logger.error(f"❌ 解析 cookies 失败: {e}")

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
        
        # 如果是短链接，先解析
        if '163cn.tv' in url:
            resolved = self._resolve_short_url(url)
            if resolved:
                return resolved
        
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
    
    def _resolve_short_url(self, short_url: str) -> Optional[Dict[str, Any]]:
        """解析网易云短链接"""
        try:
            logger.info(f"🔗 解析短链接: {short_url}")
            
            response = self.session.get(short_url, allow_redirects=True, timeout=10)
            final_url = response.url
            
            logger.info(f"🔗 重定向到: {final_url}")
            
            # 从最终 URL 提取信息 - 支持多种格式
            if 'music.163.com' in final_url:
                # 提取 id 参数（通用方式）
                id_match = re.search(r'[?&]id=(\d+)', final_url)
                
                if id_match:
                    content_id = id_match.group(1)
                    
                    # 判断类型
                    if '/song' in final_url:
                        return {'type': 'song', 'id': content_id, 'url': final_url}
                    elif '/album' in final_url:
                        return {'type': 'album', 'id': content_id, 'url': final_url}
                    elif '/playlist' in final_url:
                        return {'type': 'playlist', 'id': content_id, 'url': final_url}
                
                # 备选：从 # 后的参数获取
                hash_match = re.search(r'#/(song|album|playlist)\?id=(\d+)', final_url)
                if hash_match:
                    return {'type': hash_match.group(1), 'id': hash_match.group(2), 'url': final_url}
            
            return None
            
        except Exception as e:
            logger.error(f"❌ 解析短链接失败: {e}")
            return None

    # ============ 官方 API 调用 ============
    
    def search_songs(self, keyword: str, limit: int = 20) -> List[Dict[str, Any]]:
        """搜索歌曲"""
        try:
            url = f"{self.api_url}/api/search/get/web"
            params = {
                'csrf_token': '',
                's': keyword,
                'type': '1',  # 1=歌曲, 10=专辑, 1000=歌单
                'offset': '0',
                'total': 'true',
                'limit': str(limit)
            }
            
            logger.info(f"🔍 搜索歌曲: {keyword}")
            
            response = self.session.get(url, params=params, timeout=15)
            response.raise_for_status()
            data = response.json()
            
            if data.get('code') == 200 and data.get('result'):
                songs = data['result'].get('songs', [])
                result = []
                for song in songs:
                    result.append({
                        'id': str(song.get('id')),
                        'name': song.get('name', 'Unknown'),
                        'artist': ', '.join([a.get('name', '') for a in song.get('artists', [])]),
                        'album': song.get('album', {}).get('name', 'Unknown'),
                        'duration': song.get('duration', 0) // 1000,
                        'cover': song.get('album', {}).get('picUrl', ''),
                    })
                logger.info(f"✅ 搜索到 {len(result)} 首歌曲")
                return result
            
            logger.warning(f"⚠️ 搜索失败: {data.get('msg', '未知错误')}")
            return []
            
        except Exception as e:
            logger.error(f"❌ 搜索歌曲失败: {e}")
            return []
    
    def get_song_info(self, song_id: str) -> Optional[Dict[str, Any]]:
        """获取歌曲详情"""
        try:
            url = f"{self.api_url}/api/song/detail"
            params = {'ids': f'[{song_id}]'}
            
            response = self.session.get(url, params=params, timeout=15)
            response.raise_for_status()
            data = response.json()
            
            if data.get('code') == 200 and data.get('songs'):
                song = data['songs'][0]
                return {
                    'id': str(song['id']),
                    'name': song['name'],
                    'artist': ', '.join([a['name'] for a in song.get('artists', [])]),
                    'album': song.get('album', {}).get('name', ''),
                    'album_id': song.get('album', {}).get('id'),
                    'cover': song.get('album', {}).get('picUrl', ''),
                    'duration': song.get('duration', 0) // 1000,
                }
            
            logger.warning(f"⚠️ 获取歌曲信息失败: {song_id}")
            return None
            
        except Exception as e:
            logger.error(f"❌ 获取歌曲信息失败: {e}")
            return None
    
    def get_song_url(self, song_id: str, quality: str = None) -> Optional[Dict[str, Any]]:
        """获取歌曲下载链接 - 使用官方 API"""
        try:
            br = self.QUALITY_MAP.get(quality or self.quality, 999000)
            
            url = f"{self.api_url}/api/song/enhance/player/url"
            params = {
                'ids': f'[{song_id}]',
                'br': br,
            }
            
            logger.info(f"🔗 请求音乐链接: {song_id} (音质参数: {br})")
            
            response = self.session.get(url, params=params, timeout=15)
            response.raise_for_status()
            data = response.json()
            
            if data.get('code') == 200 and data.get('data'):
                song_data = data['data'][0]
                music_url = song_data.get('url')
                
                if music_url:
                    file_format = self._extract_format_from_url(music_url)
                    logger.info(f"✅ 获取音乐链接成功: {song_id}, 格式: {file_format}")
                    return {
                        'url': music_url,
                        'size': song_data.get('size', 0),
                        'type': file_format,
                        'br': song_data.get('br', 0),
                    }
                else:
                    logger.warning(f"⚠️ 音乐链接为空，可能需要 VIP 或版权限制: {song_id}")
            
            return None
            
        except Exception as e:
            logger.error(f"❌ 获取歌曲URL失败: {e}")
            return None
    
    def get_song_url_with_fallback(self, song_id: str, preferred_quality: str = None) -> Optional[Dict[str, Any]]:
        """获取歌曲下载链接，支持音质降级"""
        if not preferred_quality:
            preferred_quality = self.quality
        
        # 确定起始位置
        start_idx = 0
        quality_key = preferred_quality.lower().replace('无损', 'flac').replace('极高', '320k').replace('较高', '192k').replace('标准', '128k')
        
        if quality_key in self.QUALITY_FALLBACK:
            start_idx = self.QUALITY_FALLBACK.index(quality_key)
        
        # 按降级顺序尝试
        for quality in self.QUALITY_FALLBACK[start_idx:]:
            result = self.get_song_url(song_id, quality)
            if result and result.get('url'):
                logger.info(f"✅ 使用音质: {quality}")
                return result
            time.sleep(0.3)
        
        logger.warning(f"⚠️ 所有音质都无法获取: {song_id}")
        return None
    
    def _extract_format_from_url(self, url: str) -> str:
        """从 URL 推断文件格式"""
        url_lower = url.lower()
        if '.flac' in url_lower:
            return 'flac'
        elif '.mp3' in url_lower:
            return 'mp3'
        elif '.m4a' in url_lower:
            return 'm4a'
        elif '.wav' in url_lower:
            return 'wav'
        return 'mp3'
    
    def get_lyrics(self, song_id: str) -> Optional[str]:
        """获取歌词"""
        try:
            url = f"{self.api_url}/api/song/lyric"
            params = {
                'id': song_id,
                'lv': 1,
                'tv': 1,
                'rv': 1,
            }
            
            response = self.session.get(url, params=params, timeout=15)
            response.raise_for_status()
            data = response.json()
            
            if data.get('code') == 200:
                lrc = data.get('lrc', {}).get('lyric', '')
                tlyric = data.get('tlyric', {}).get('lyric', '')
                
                if self.lyrics_merge and tlyric:
                    return f"{lrc}\n\n--- 翻译 ---\n\n{tlyric}"
                return lrc
            
            return None
            
        except Exception as e:
            logger.error(f"❌ 获取歌词失败: {e}")
            return None

    # ============ 专辑/歌单 API ============
    
    def get_album_songs(self, album_id: str) -> List[Dict[str, Any]]:
        """获取专辑歌曲列表 - 参考原项目实现"""
        try:
            # 使用原项目的 API: /api/album/{id}
            url = f"{self.api_url}/api/album/{album_id}"
            logger.info(f"💿 获取专辑歌曲: {url}")
            
            response = self.session.get(url, timeout=15)
            response.raise_for_status()
            data = response.json()
            
            logger.info(f"💿 API响应: code={data.get('code')}")
            
            if data.get('code') == 200 and data.get('album'):
                album_info = data['album']
                # 关键修复: songs 在 album 对象内部，而不是顶层
                songs = album_info.get('songs', [])
                album_name = album_info.get('name', '')
                album_cover = album_info.get('picUrl', '')
                
                logger.info(f"💿 专辑: {album_name}, 歌曲数: {len(songs)}")
                
                if songs:
                    result = []
                    for i, song in enumerate(songs, 1):
                        # 原项目使用 'artists' 字段
                        artists = song.get('artists', [])
                        if artists:
                            # 只取第一个艺术家，避免多艺术家问题
                            artist_name = artists[0].get('name', '未知')
                        else:
                            artist_name = '未知'
                        
                        result.append({
                            'id': str(song['id']),
                            'name': song.get('name', '未知'),
                            'artist': artist_name,
                            'album': album_name,
                            'track_number': song.get('no', i),  # 使用曲目编号
                            'cover': album_cover,
                            'duration': song.get('duration', 0) // 1000,  # 转换为秒
                        })
                    
                    logger.info(f"✅ 获取专辑歌曲成功: {len(result)} 首")
                    return result
                else:
                    logger.warning(f"⚠️ 专辑 {album_name} 中没有歌曲")
            else:
                logger.error(f"❌ API返回错误: {data.get('msg', data.get('message', '未知'))}")
            
            return []
            
        except Exception as e:
            logger.error(f"❌ 获取专辑歌曲失败: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return []
    
    def get_playlist_songs(self, playlist_id: str) -> List[Dict[str, Any]]:
        """获取歌单歌曲列表"""
        try:
            url = f"{self.api_url}/api/playlist/detail"
            params = {
                'id': playlist_id,
                'csrf_token': ''
            }
            
            response = self.session.get(url, params=params, timeout=15)
            response.raise_for_status()
            data = response.json()
            
            if data.get('code') == 200 and data.get('result'):
                playlist = data['result']
                tracks = playlist.get('tracks', [])
                
                result = []
                for i, song in enumerate(tracks, 1):
                    artists = song.get('artists', []) or song.get('ar', [])
                    album = song.get('album', {}) or song.get('al', {})
                    
                    result.append({
                        'id': str(song['id']),
                        'name': song['name'],
                        'artist': ', '.join([a['name'] for a in artists]) if artists else '未知',
                        'album': album.get('name', '未知'),
                        'track_number': i,
                        'cover': album.get('picUrl', ''),
                    })
                
                logger.info(f"✅ 获取歌单歌曲: {len(result)} 首")
                return result
            
            return []
            
        except Exception as e:
            logger.error(f"❌ 获取歌单歌曲失败: {e}")
            return []

    # ============ 下载功能 ============
    
    def download_song(self, song_id: str, download_dir: str,
                     quality: str = None,
                     progress_callback: Optional[Callable] = None) -> Dict[str, Any]:
        """下载单曲"""
        try:
            # 获取歌曲信息
            song_info = self.get_song_info(song_id)
            if not song_info:
                return {'success': False, 'error': '无法获取歌曲信息'}
            
            # 获取下载链接（支持降级）
            song_url_info = self.get_song_url_with_fallback(song_id, quality)
            if not song_url_info or not song_url_info.get('url'):
                return {'success': False, 'error': '无法获取下载链接，可能需要 VIP 或配置 cookies'}
            
            # 构建文件名和目录
            filename = self._build_filename(song_info, song_url_info.get('type', 'mp3'))
            save_dir = self._build_directory(download_dir, song_info)
            self.ensure_dir(save_dir)
            filepath = os.path.join(save_dir, filename)
            
            # 检查是否已存在
            if os.path.exists(filepath):
                file_size = os.path.getsize(filepath)
                logger.info(f"📁 文件已存在: {filename}")
                return {
                    'success': True,
                    'song_title': song_info['name'],
                    'song_artist': song_info['artist'],
                    'filepath': filepath,
                    'size_mb': file_size / (1024 * 1024),
                    'message': '文件已存在',
                }
            
            if progress_callback:
                progress_callback({
                    'status': 'downloading',
                    'song': song_info['name'],
                    'artist': song_info['artist'],
                })
            
            # 下载文件
            success = self._download_file(song_url_info['url'], filepath, progress_callback)
            
            if success:
                file_size = os.path.getsize(filepath) if os.path.exists(filepath) else 0
                
                # 下载歌词
                if self.download_lyrics:
                    lyrics = self.get_lyrics(song_id)
                    if lyrics:
                        lrc_path = os.path.splitext(filepath)[0] + '.lrc'
                        with open(lrc_path, 'w', encoding='utf-8') as f:
                            f.write(lyrics)
                        logger.info(f"✅ 歌词已保存: {lrc_path}")
                
                # 下载封面
                if self.download_cover and song_info.get('cover'):
                    cover_path = os.path.join(save_dir, 'cover.jpg')
                    if not os.path.exists(cover_path):
                        self._download_file(song_info['cover'], cover_path)
                
                # 计算时长格式
                duration_sec = song_info.get('duration', 0)
                if duration_sec:
                    minutes = duration_sec // 60
                    seconds = duration_sec % 60
                    duration_str = f"{minutes}:{seconds:02d}"
                else:
                    duration_str = '未知'
                
                # 获取码率和音质信息
                br = song_url_info.get('br', 0)
                bitrate_str = f"{br // 1000}kbps" if br else '未知'
                file_type = song_url_info.get('type', 'mp3').upper()
                quality_name = self._get_quality_name(br)
                
                return {
                    'success': True,
                    'song_title': song_info['name'],
                    'song_artist': song_info['artist'],
                    'filepath': filepath,
                    'size_mb': file_size / (1024 * 1024),
                    'quality': quality_name,
                    'bitrate': bitrate_str,
                    'duration': duration_str,
                    'file_format': file_type,
                }
            
            return {'success': False, 'error': '下载失败'}
            
        except Exception as e:
            logger.error(f"❌ 下载歌曲失败: {e}")
            return {'success': False, 'error': str(e)}
    
    def download_album(self, album_id: str, download_dir: str,
                      quality: str = None,
                      progress_callback: Optional[Callable] = None) -> Dict[str, Any]:
        """下载专辑"""
        songs = self.get_album_songs(album_id)
        
        if not songs:
            return {'success': False, 'error': '无法获取专辑歌曲'}
        
        # 获取专辑信息
        album_name = songs[0].get('album', '') if songs else '未知专辑'
        artist_name = songs[0].get('artist', '未知艺术家') if songs else '未知艺术家'
        
        results = {
            'success': True,
            'album_name': album_name,
            'artist': artist_name,
            'total_songs': len(songs),
            'downloaded_songs': 0,
            'songs': [],
            'quality_name': self.quality,
            'bitrate': '未知',
            'file_format': 'MP3',
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
                # 更新码率和格式信息
                if result.get('bitrate') and results.get('bitrate') == '未知':
                    results['bitrate'] = result.get('bitrate')
                if result.get('file_format'):
                    results['file_format'] = result.get('file_format')
            
            time.sleep(0.5)  # 避免请求过快
        
        return results
    
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
            'playlist_title': '歌单',  # 如有歌单名称可在此获取
            'total_songs': len(songs),
            'downloaded_songs': 0,
            'songs': [],
            'quality_name': self.quality,
            'bitrate': '未知',
            'file_format': 'MP3',
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
                # 更新码率和格式信息
                if result.get('bitrate') and results.get('bitrate') == '未知':
                    results['bitrate'] = result.get('bitrate')
                if result.get('file_format'):
                    results['file_format'] = result.get('file_format')
            
            time.sleep(0.5)
        
        return results
    
    def _get_quality_name(self, bitrate: int) -> str:
        """根据码率返回音质名称"""
        if bitrate >= 900000:
            return '无损'
        elif bitrate >= 320000:
            return '极高'
        elif bitrate >= 192000:
            return '较高'
        elif bitrate >= 128000:
            return '标准'
        else:
            return '未知'
    
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
            
            # 确保目录存在
            os.makedirs(os.path.dirname(filepath), exist_ok=True)
            
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
            
            logger.info(f"✅ 下载完成: {filepath}")
            return True
            
        except Exception as e:
            logger.error(f"❌ 下载文件失败: {e}")
            return False
