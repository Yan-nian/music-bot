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

# 检查元数据模块是否可用
try:
    from .metadata import MusicMetadataManager
    METADATA_AVAILABLE = True
    logger.info("✅ 成功导入音乐元数据模块")
except ImportError as e:
    MusicMetadataManager = None
    METADATA_AVAILABLE = False
    logger.warning(f"⚠️ 音乐元数据模块不可用: {e}")


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
        
        # 检查 cookies 有效性
        self._check_login_status()
        
        # 初始化音乐元数据管理器
        if METADATA_AVAILABLE:
            try:
                self.metadata_manager = MusicMetadataManager()
                logger.info("✅ 音乐元数据管理器初始化成功")
                logger.info(f"🔧 可用的音频标签库: {', '.join(self.metadata_manager.available_libraries) if self.metadata_manager.available_libraries else '无'}")
            except Exception as e:
                logger.error(f"❌ 音乐元数据管理器初始化失败: {e}")
                self.metadata_manager = None
        else:
            self.metadata_manager = None
            logger.warning("⚠️ 音乐元数据管理器不可用")
        
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

    def reload_config(self):
        """重新加载配置与 cookies（Web 修改后无需重启即生效）

        下载器是单例，__init__ 时把 cookies 读进 session.cookies、
        把音质等读进实例属性。Web 改了配置后必须主动 reload，
        否则要重启容器才生效——这是“网页保存 cookie 不生效”的根因。
        cookies 用 hash 判断变化，未变则跳过重载以减少开销。
        """
        self._load_config()
        cookies_str = self.get_config('netease_cookies', '')
        if hash(cookies_str) != getattr(self, '_cookies_hash', None):
            self._cookies_hash = hash(cookies_str)
            self.session.cookies.clear()
            self._load_cookies()
            logger.info("🔄 网易云 cookies 已重新加载")

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
            
            # 检查关键 cookie 是否存在
            cookie_names = [c.name for c in self.session.cookies]
            has_music_u = 'MUSIC_U' in cookie_names
            logger.info(f"✅ 已加载 {len(self.session.cookies)} 个 cookies (MUSIC_U: {'有' if has_music_u else '无'})")
            if not has_music_u:
                logger.warning("⚠️ 缺少关键 cookie MUSIC_U，可能无法下载付费/VIP 歌曲")
        except Exception as e:
            logger.error(f"❌ 解析 cookies 失败: {e}")
    
    def _check_login_status(self):
        """检查登录状态和 cookies 有效性"""
        try:
            url = f"{self.api_url}/api/login/status"
            response = self.session.get(url, timeout=10)
            data = response.json()
            
            if data.get('code') == 200:
                profile = data.get('profile')
                if profile:
                    nickname = profile.get('nickname', '未知')
                    vip_type = profile.get('vipType', 0)
                    vip_str = '黑胶VIP' if vip_type == 11 else ('普通VIP' if vip_type > 0 else '普通用户')
                    logger.info(f"✅ 网易云登录状态有效: {nickname} ({vip_str})")
                    self.logged_in = True
                    self.user_info = profile
                    return True
                else:
                    logger.warning("⚠️ 网易云 cookies 已失效，需要重新登录获取 cookies")
                    self.logged_in = False
                    self.user_info = None
            else:
                logger.warning(f"⚠️ 检查登录状态失败: {data.get('msg', '未知错误')}")
                self.logged_in = False
                
        except Exception as e:
            logger.debug(f"检查登录状态时出错: {e}")
            self.logged_in = False
        
        return False

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
    
    def get_songs_info_batch(self, song_ids: List[str]) -> Dict[str, Dict[str, Any]]:
        """批量获取歌曲详情（用于歌单下载时获取完整元数据）
        
        Args:
            song_ids: 歌曲ID列表
            
        Returns:
            Dict[song_id, song_info]: 歌曲ID到详情的映射
        """
        result = {}
        
        # 网易云 API 支持批量查询，每次最多 1000 首
        batch_size = 500
        
        for i in range(0, len(song_ids), batch_size):
            batch_ids = song_ids[i:i + batch_size]
            
            try:
                url = f"{self.api_url}/api/song/detail"
                ids_str = ','.join(batch_ids)
                params = {'ids': f'[{ids_str}]'}
                
                logger.info(f"📝 批量获取歌曲详情: {len(batch_ids)} 首 (批次 {i // batch_size + 1})")
                
                response = self.session.get(url, params=params, timeout=30)
                response.raise_for_status()
                data = response.json()
                
                if data.get('code') == 200 and data.get('songs'):
                    for song in data['songs']:
                        song_id = str(song['id'])
                        artists = song.get('artists', [])
                        album = song.get('album', {})
                        
                        # 提取专辑艺术家（第一个艺术家作为专辑艺术家）
                        album_artist = artists[0]['name'] if artists else '未知'
                        
                        result[song_id] = {
                            'id': song_id,
                            'name': song['name'],
                            'artist': ', '.join([a['name'] for a in artists]) if artists else '未知',
                            'album': album.get('name', '未知'),
                            'album_id': album.get('id'),
                            'album_artist': album_artist,
                            'cover': album.get('picUrl', ''),
                            'duration': song.get('duration', 0) // 1000,
                            'publish_time': album.get('publishTime'),
                        }
                
                time.sleep(0.3)  # 避免请求过快
                
            except Exception as e:
                logger.error(f"❌ 批量获取歌曲详情失败: {e}")
        
        logger.info(f"✅ 批量获取完成: {len(result)}/{len(song_ids)} 首")
        return result
    
    def get_album_track_info(self, album_id: str) -> Dict[str, Dict[str, Any]]:
        """获取专辑曲目信息（用于获取正确的 track_number 和 total_tracks）
        
        Args:
            album_id: 专辑ID
            
        Returns:
            Dict[song_id, track_info]: 歌曲ID到曲目信息的映射
        """
        try:
            url = f"{self.api_url}/api/album/{album_id}"
            response = self.session.get(url, timeout=15)
            response.raise_for_status()
            data = response.json()
            
            if data.get('code') == 200 and data.get('album'):
                album_info = data['album']
                songs = album_info.get('songs', [])
                total_tracks = len(songs)
                album_artist = album_info.get('artists', [{}])[0].get('name', '未知')
                publish_time = album_info.get('publishTime')
                
                result = {}
                for song in songs:
                    song_id = str(song['id'])
                    track_no = song.get('no', 0)
                    if not track_no or track_no == 0:
                        # 如果没有曲目号，根据位置推断
                        track_no = songs.index(song) + 1
                    
                    result[song_id] = {
                        'track_number': track_no,
                        'total_tracks': total_tracks,
                        'album_artist': album_artist,
                        'disc_number': song.get('cd', '1') or '1',
                        'publish_time': publish_time,
                    }
                
                return result
            
            return {}
            
        except Exception as e:
            logger.error(f"❌ 获取专辑曲目信息失败 (album_id={album_id}): {e}")
            return {}
    
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
                song_code = song_data.get('code', 0)  # 歌曲级别的状态码
                
                if music_url:
                    file_format = self._extract_format_from_url(music_url)
                    logger.info(f"✅ 获取音乐链接成功: {song_id}, 格式: {file_format}, 码率: {song_data.get('br', 0)}")
                    return {
                        'url': music_url,
                        'size': song_data.get('size', 0),
                        'type': file_format,
                        'br': song_data.get('br', 0),
                    }
                else:
                    # 详细的失败原因分析
                    fee = song_data.get('fee', 0)  # 0=免费, 1=VIP, 4=购买专辑, 8=低音质免费
                    payed = song_data.get('payed', 0)  # 是否已购买
                    
                    if song_code == -110:
                        logger.warning(f"⚠️ 歌曲 {song_id} 需要 VIP 权限 (fee={fee}, payed={payed})")
                    elif song_code == 404:
                        logger.warning(f"⚠️ 歌曲 {song_id} 不存在或已下架")
                    elif fee == 1 and not payed:
                        logger.warning(f"⚠️ 歌曲 {song_id} 需要 VIP 订阅 (fee={fee})")
                    elif fee == 4 and not payed:
                        logger.warning(f"⚠️ 歌曲 {song_id} 需要购买专辑 (fee={fee})")
                    else:
                        logger.warning(f"⚠️ 歌曲 {song_id} 链接为空 (code={song_code}, fee={fee}, payed={payed}) - 请检查 cookies 是否有效")
            else:
                logger.warning(f"⚠️ API 返回异常: code={data.get('code')}, msg={data.get('msg', '无')}")
            
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
                # 获取专辑艺术家（用于统一专辑识别）
                album_artists = album_info.get('artists', [])
                album_artist = album_artists[0].get('name', '未知') if album_artists else '未知'
                # 获取专辑发布时间
                album_publish_time = album_info.get('publishTime', '')
                # 专辑总曲目数（关键：用于 Plex 识别同一专辑）
                total_tracks = len(songs)
                
                logger.info(f"💿 专辑: {album_name}, 艺术家: {album_artist}, 歌曲数: {total_tracks}")
                
                if songs:
                    result = []
                    for i, song in enumerate(songs, 1):
                        # 原项目使用 'artists' 字段
                        artists = song.get('artists', [])
                        if artists:
                            # 保留完整艺术家列表用于显示，但专辑艺术家统一
                            artist_name = ', '.join([a.get('name', '') for a in artists])
                        else:
                            artist_name = album_artist
                        
                        # 获取曲目编号，优先使用 API 返回的 no 字段，否则使用索引
                        track_no = song.get('no', 0)
                        if not track_no or track_no == 0:
                            track_no = i
                        
                        result.append({
                            'id': str(song['id']),
                            'name': song.get('name', '未知'),
                            'artist': artist_name,
                            'album': album_name,
                            'album_artist': album_artist,  # 关键：统一的专辑艺术家
                            'track_number': track_no,  # 使用曲目编号
                            'total_tracks': total_tracks,  # 关键：专辑总曲目数
                            'disc_number': song.get('cd', '1') or '1',  # 碟片编号
                            'cover': album_cover,
                            'duration': song.get('duration', 0) // 1000,  # 转换为秒
                            'publish_time': album_publish_time,  # 专辑发布时间
                        })
                    
                    logger.info(f"✅ 获取专辑歌曲成功: {len(result)} 首, 示例: track_number={result[0].get('track_number')}, total_tracks={result[0].get('total_tracks')}")
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
    
    def get_playlist_songs(self, playlist_id: str) -> tuple:
        """获取歌单歌曲列表
        
        Returns:
            tuple: (songs_list, playlist_name)
        """
        try:
            # 使用 v6 API 获取完整歌单信息（包含所有 trackIds）
            url = f"{self.api_url}/api/v6/playlist/detail"
            params = {
                'id': playlist_id,
                'n': 100000,  # 请求尽可能多的歌曲
                's': 0,  # 不需要收藏者信息
                'csrf_token': ''
            }
            
            response = self.session.get(url, params=params, timeout=30)
            response.raise_for_status()
            data = response.json()
            
            if data.get('code') == 200 and data.get('playlist'):
                playlist = data['playlist']
                playlist_name = playlist.get('name', '未知歌单')
                tracks = playlist.get('tracks', [])
                
                # 获取完整的歌曲ID列表
                # v6 API 的 trackIds 在 playlist 对象里
                track_ids = playlist.get('trackIds', [])
                
                # 优先使用 trackIds（包含完整列表）
                if track_ids:
                    all_song_ids = [str(t['id']) for t in track_ids]
                else:
                    all_song_ids = [str(song['id']) for song in tracks]
                
                logger.info(f"📋 歌单 '{playlist_name}' 共 {len(all_song_ids)} 首歌曲 (trackIds: {len(track_ids)}, tracks: {len(tracks)})")
                
                if not all_song_ids:
                    logger.warning(f"⚠️ 歌单 {playlist_name} 没有歌曲")
                    return [], playlist_name
                
                # 如果 tracks 数量少于 all_song_ids，需要额外获取歌曲详情
                if len(tracks) < len(all_song_ids):
                    logger.info(f"📝 歌单歌曲不完整 ({len(tracks)}/{len(all_song_ids)})，正在获取全部歌曲详情...")
                    # 分批获取歌曲详情（每批最多 500 首）
                    all_tracks = []
                    batch_size = 500
                    for i in range(0, len(all_song_ids), batch_size):
                        batch_ids = all_song_ids[i:i + batch_size]
                        logger.info(f"📝 获取第 {i//batch_size + 1} 批歌曲详情 ({len(batch_ids)} 首)...")
                        batch_tracks = self._get_songs_detail(batch_ids)
                        all_tracks.extend(batch_tracks)
                        if i + batch_size < len(all_song_ids):
                            time.sleep(0.3)  # 避免请求过快
                    tracks = all_tracks
                    logger.info(f"✅ 获取到全部 {len(tracks)} 首歌曲详情")
                
                # 收集所有专辑ID
                album_ids = set()
                
                # 从 tracks 中提取基本信息和专辑ID
                basic_info = {}
                for song in tracks:
                    song_id = str(song['id'])
                    artists = song.get('artists', []) or song.get('ar', [])
                    album = song.get('album', {}) or song.get('al', {})
                    album_id = album.get('id')
                    
                    if album_id:
                        album_ids.add(str(album_id))
                    
                    # 提取专辑艺术家
                    album_artist = artists[0]['name'] if artists else '未知'
                    
                    basic_info[song_id] = {
                        'id': song_id,
                        'name': song['name'],
                        'artist': ', '.join([a['name'] for a in artists]) if artists else '未知',
                        'album': album.get('name', '未知'),
                        'album_id': album_id,
                        'album_artist': album_artist,
                        'cover': album.get('picUrl', ''),
                    }
                
                logger.info(f"📋 歌单涉及 {len(album_ids)} 张专辑")
                
                # 获取所有相关专辑的曲目信息
                album_track_info = {}
                logger.info(f"📝 正在获取专辑曲目信息...")
                for album_id in album_ids:
                    if album_id:
                        track_info = self.get_album_track_info(album_id)
                        album_track_info.update(track_info)
                        time.sleep(0.2)  # 避免请求过快
                
                logger.info(f"✅ 获取到 {len(album_track_info)} 首歌曲的专辑曲目信息")
                
                # 构建完整的歌曲列表（保持原始顺序）
                result = []
                for song_id in all_song_ids:
                    song_data = basic_info.get(song_id, {})
                    if not song_data:
                        # 如果没有详情，可能是获取失败的歌曲
                        logger.warning(f"⚠️ 歌曲 {song_id} 详情获取失败，跳过")
                        continue
                    
                    track_data = album_track_info.get(song_id, {})
                    
                    # 合并基本信息和曲目信息
                    result.append({
                        'id': song_id,
                        'name': song_data.get('name', '未知'),
                        'artist': song_data.get('artist', '未知'),
                        'album': song_data.get('album', '未知'),
                        'album_id': song_data.get('album_id'),
                        'album_artist': track_data.get('album_artist') or song_data.get('album_artist', '未知'),
                        'cover': song_data.get('cover', ''),
                        'track_number': track_data.get('track_number', 1),
                        'total_tracks': track_data.get('total_tracks', 1),
                        'disc_number': track_data.get('disc_number', '1'),
                        'publish_time': track_data.get('publish_time'),
                    })
                
                logger.info(f"✅ 歌单歌曲列表构建完成: {len(result)} 首")
                return result, playlist_name
            
            # v6 API 失败，尝试旧版 API
            logger.warning("⚠️ v6 API 获取失败，尝试使用旧版 API...")
            return self._get_playlist_songs_legacy(playlist_id)
            
        except Exception as e:
            logger.error(f"❌ 获取歌单歌曲失败: {e}")
            import traceback
            logger.error(traceback.format_exc())
            # 尝试旧版 API
            try:
                return self._get_playlist_songs_legacy(playlist_id)
            except:
                return [], '未知歌单'
    
    def _get_playlist_songs_legacy(self, playlist_id: str) -> tuple:
        """使用旧版 API 获取歌单歌曲（备用方案）"""
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
                playlist_name = playlist.get('name', '未知歌单')
                tracks = playlist.get('tracks', [])
                track_ids = playlist.get('trackIds', [])
                
                logger.info(f"📋 [旧版API] 歌单 '{playlist_name}' trackIds: {len(track_ids)}, tracks: {len(tracks)}")
                
                # 如果有 trackIds 但 tracks 不完整
                if track_ids and len(tracks) < len(track_ids):
                    all_song_ids = [str(t['id']) for t in track_ids]
                    logger.info(f"📝 [旧版API] 获取全部 {len(all_song_ids)} 首歌曲详情...")
                    
                    all_tracks = []
                    batch_size = 500
                    for i in range(0, len(all_song_ids), batch_size):
                        batch_ids = all_song_ids[i:i + batch_size]
                        batch_tracks = self._get_songs_detail(batch_ids)
                        all_tracks.extend(batch_tracks)
                        if i + batch_size < len(all_song_ids):
                            time.sleep(0.3)
                    tracks = all_tracks
                
                # 构建简化的歌曲列表
                result = []
                for song in tracks:
                    song_id = str(song['id'])
                    artists = song.get('artists', []) or song.get('ar', [])
                    album = song.get('album', {}) or song.get('al', {})
                    
                    result.append({
                        'id': song_id,
                        'name': song.get('name', '未知'),
                        'artist': ', '.join([a['name'] for a in artists]) if artists else '未知',
                        'album': album.get('name', '未知'),
                        'album_id': album.get('id'),
                        'album_artist': artists[0]['name'] if artists else '未知',
                        'cover': album.get('picUrl', ''),
                        'track_number': 1,
                        'total_tracks': 1,
                        'disc_number': '1',
                    })
                
                return result, playlist_name
            
            return [], '未知歌单'
            
        except Exception as e:
            logger.error(f"❌ [旧版API] 获取歌单失败: {e}")
            return [], '未知歌单'
    
    def _get_songs_detail(self, song_ids: List[str]) -> List[Dict]:
        """批量获取歌曲详情
        
        Args:
            song_ids: 歌曲ID列表
            
        Returns:
            歌曲详情列表
        """
        try:
            # 使用 POST 请求避免 URL 长度限制
            url = f"{self.api_url}/api/v3/song/detail"
            
            # 构建请求数据
            c_param = [{"id": int(sid)} for sid in song_ids]
            data = {
                'c': json.dumps(c_param),
                'csrf_token': ''
            }
            
            response = self.session.post(url, data=data, timeout=30)
            response.raise_for_status()
            result = response.json()
            
            if result.get('code') == 200:
                songs = result.get('songs', [])
                logger.debug(f"✅ 批量获取 {len(songs)} 首歌曲详情")
                return songs
            else:
                logger.warning(f"⚠️ 获取歌曲详情返回错误: {result.get('code')}, {result.get('message', '')}")
            
            return []
            
        except Exception as e:
            logger.warning(f"⚠️ 批量获取歌曲详情失败: {e}")
            import traceback
            logger.debug(traceback.format_exc())
            return []

    # ============ 下载功能 ============
    
    def download_song(self, song_id: str, download_dir: str,
                     quality: str = None,
                     progress_callback: Optional[Callable] = None,
                     extra_metadata: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """下载单曲
        
        Args:
            song_id: 歌曲ID
            download_dir: 下载目录
            quality: 音质
            progress_callback: 进度回调
            extra_metadata: 额外元数据（用于专辑下载时传递track_number等）
        """
        try:
            # 获取歌曲信息
            song_info = self.get_song_info(song_id)
            if not song_info:
                return {'success': False, 'error': '无法获取歌曲信息'}
            
            # 合并额外元数据（来自专辑/歌单等，包含track_number, total_tracks等）
            if extra_metadata:
                logger.info(f"📝 合并额外元数据: track={extra_metadata.get('track_number')}, total={extra_metadata.get('total_tracks')}, album_artist={extra_metadata.get('album_artist')}")
                song_info.update(extra_metadata)
            
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
            
            # 下载文件 - 传递文件名用于显示
            display_name = f"{song_info['name']} - {song_info['artist']}"
            success = self._download_file(song_url_info['url'], filepath, progress_callback, display_name)
            
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
                
                # 为音乐文件添加元数据标签（用于Plex刮削）
                self._add_metadata_to_file(
                    filepath,
                    song_info,
                    cover_url=song_info.get('cover')
                )
                
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
                    'album': album_name,
                })
            
            # 构建额外元数据（从专辑获取的完整信息）
            extra_metadata = {
                'track_number': song.get('track_number', i),
                'total_tracks': song.get('total_tracks', len(songs)),
                'album_artist': song.get('album_artist', artist_name),
                'disc_number': song.get('disc_number', '1'),
                'publish_time': song.get('publish_time'),
            }
            
            # 创建包装的进度回调，添加专辑进度信息
            def make_album_progress_callback(song_index, total_songs, song_name, album):
                def wrapped_callback(progress_info):
                    if progress_callback:
                        # 如果是文件下载进度，添加专辑上下文
                        if progress_info.get('status') == 'file_progress':
                            progress_info['album_context'] = {
                                'current': song_index,
                                'total': total_songs,
                                'song': song_name,
                                'album': album,
                            }
                        progress_callback(progress_info)
                return wrapped_callback
            
            album_callback = make_album_progress_callback(i, len(songs), song['name'], album_name)
            result = self.download_song(song['id'], download_dir, quality, album_callback, extra_metadata)
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
        songs, playlist_name = self.get_playlist_songs(playlist_id)
        
        if not songs:
            return {'success': False, 'error': '无法获取歌单歌曲'}
        
        results = {
            'success': True,
            'playlist_id': playlist_id,
            'playlist_title': playlist_name,
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
                    'playlist': playlist_name,
                })
            
            # 构建额外元数据（从歌单获取的完整专辑信息）
            extra_metadata = {
                'track_number': song.get('track_number', 1),
                'total_tracks': song.get('total_tracks', 1),
                'album_artist': song.get('album_artist', song.get('artist', '未知')),
                'disc_number': song.get('disc_number', '1'),
                'publish_time': song.get('publish_time'),
            }
            
            # 创建包装的进度回调，添加歌单进度信息
            def make_playlist_progress_callback(song_index, total_songs, song_name, playlist_title):
                def wrapped_callback(progress_info):
                    if progress_callback:
                        # 如果是文件下载进度，添加歌单上下文
                        if progress_info.get('status') == 'file_progress':
                            progress_info['playlist_context'] = {
                                'current': song_index,
                                'total': total_songs,
                                'song': song_name,
                                'playlist': playlist_title,
                            }
                        progress_callback(progress_info)
                return wrapped_callback
            
            playlist_callback = make_playlist_progress_callback(i, len(songs), song['name'], playlist_name)
            result = self.download_song(song['id'], download_dir, quality, playlist_callback, extra_metadata)
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
    
    def download_playlist_incremental(self, playlist_id: str, download_dir: str,
                                      quality: str = None,
                                      progress_callback: Optional[Callable] = None) -> Dict[str, Any]:
        """增量下载歌单（只下载新增歌曲）
        
        Args:
            playlist_id: 歌单ID
            download_dir: 下载目录
            quality: 音质
            progress_callback: 进度回调
            
        Returns:
            下载结果字典，包含新增歌曲数、下载数等信息
        """
        logger.info(f"🔄 开始增量检查歌单: {playlist_id}")
        
        # 获取歌单当前所有歌曲
        songs, playlist_name = self.get_playlist_songs(playlist_id)
        
        if not songs:
            return {'success': False, 'error': '无法获取歌单歌曲'}
        
        # 获取已下载的歌曲ID（从配置管理器）
        downloaded_song_ids = set()
        downloaded_records = []
        if self.config_manager:
            downloaded_records = self.config_manager.get_playlist_songs(playlist_id, downloaded_only=True)
            downloaded_song_ids = {record['song_id'] for record in downloaded_records}
        
        # 当前歌单中的所有歌曲ID
        current_song_ids = {song['id'] for song in songs}
        
        # 找出需要删除的歌曲（本地有但歌单中已移除）
        removed_song_ids = downloaded_song_ids - current_song_ids
        if removed_song_ids:
            logger.info(f"🗑️ 检测到 {len(removed_song_ids)} 首歌曲已从歌单中移除，准备删除本地文件")
            for removed_id in removed_song_ids:
                # 从数据库记录中查找歌曲信息
                removed_song_info = next((r for r in downloaded_records if r['song_id'] == removed_id), None)
                if removed_song_info:
                    self._delete_song_file(download_dir, removed_song_info)
                # 从数据库中删除记录
                if self.config_manager:
                    self.config_manager.remove_playlist_song(playlist_id, removed_id)
        
        # 获取所有失败记录（包括未下载的）
        all_records = []
        if self.config_manager:
            all_records = self.config_manager.get_playlist_songs(playlist_id, downloaded_only=False)
        all_song_ids = {record['song_id'] for record in all_records}
        
        # 找出新增歌曲和需要重试的歌曲
        new_songs = []
        skipped_permanent_fails = 0
        
        for song in songs:
            song_id = song['id']
            
            # 已下载成功的歌曲，跳过
            if song_id in downloaded_song_ids:
                continue
            
            # 检查是否是永久性失败的歌曲
            if song_id in all_song_ids:
                if self.config_manager and self.config_manager.is_song_permanently_failed(playlist_id, song_id):
                    skipped_permanent_fails += 1
                    logger.info(f"⏭️ 跳过永久性失败歌曲: {song.get('name')} - {song.get('artist')}")
                    continue
                else:
                    # 可以重试的失败歌曲
                    logger.info(f"🔄 重试下载失败歌曲: {song.get('name')} - {song.get('artist')}")
            
            # 新歌曲或可重试的失败歌曲
            new_songs.append(song)
            
            # 如果是新歌曲，记录到数据库
            if song_id not in all_song_ids and self.config_manager:
                self.config_manager.add_playlist_song(
                    playlist_id=playlist_id,
                    song_id=song_id,
                    song_name=song.get('name'),
                    artist=song.get('artist'),
                    album=song.get('album'),
                    downloaded=False
                )
        
        if skipped_permanent_fails > 0:
            logger.info(f"📋 歌单 '{playlist_name}' 共 {len(songs)} 首，需下载 {len(new_songs)} 首，跳过永久性失败 {skipped_permanent_fails} 首")
        else:
            logger.info(f"📋 歌单 '{playlist_name}' 共 {len(songs)} 首，需下载 {len(new_songs)} 首")
        
        results = {
            'success': True,
            'playlist_id': playlist_id,
            'playlist_title': playlist_name,
            'total_songs': len(songs),
            'new_songs': len(new_songs),
            'downloaded_songs': 0,
            'skipped_songs': len(songs) - len(new_songs),
            'skipped_permanent_fails': skipped_permanent_fails,
            'songs': [],
            'quality_name': self.quality,
            'bitrate': '未知',
            'file_format': 'MP3',
        }
        
        if not new_songs:
            logger.info(f"✅ 歌单 '{playlist_name}' 没有新增歌曲")
            results['message'] = '没有新增歌曲'
            
            # 即使没有新歌曲也要更新检查时间，避免重复检查
            if self.config_manager:
                self.config_manager.update_subscribed_playlist(
                    playlist_id=playlist_id,
                    last_check_time=time.strftime('%Y-%m-%d %H:%M:%S'),
                    last_song_count=len(songs)
                )
            return results
        
        # 下载新增歌曲
        for i, song in enumerate(new_songs, 1):
            if progress_callback:
                progress_callback({
                    'status': 'playlist_progress',
                    'current': i,
                    'total': len(new_songs),
                    'song': song['name'],
                    'playlist': playlist_name,
                    'is_incremental': True,
                })
            
            # 构建额外元数据
            extra_metadata = {
                'track_number': song.get('track_number', 1),
                'total_tracks': song.get('total_tracks', 1),
                'album_artist': song.get('album_artist', song.get('artist', '未知')),
                'disc_number': song.get('disc_number', '1'),
                'publish_time': song.get('publish_time'),
            }
            
            # 创建包装的进度回调
            def make_incremental_callback(song_index, total_songs, song_name, playlist_title):
                def wrapped_callback(progress_info):
                    if progress_callback:
                        if progress_info.get('status') == 'file_progress':
                            progress_info['playlist_context'] = {
                                'current': song_index,
                                'total': total_songs,
                                'song': song_name,
                                'playlist': playlist_title,
                                'is_incremental': True,
                            }
                        progress_callback(progress_info)
                return wrapped_callback
            
            incremental_callback = make_incremental_callback(i, len(new_songs), song['name'], playlist_name)
            result = self.download_song(song['id'], download_dir, quality, incremental_callback, extra_metadata)
            results['songs'].append(result)
            
            if result.get('success'):
                results['downloaded_songs'] += 1
                # 标记歌曲已下载
                if self.config_manager:
                    self.config_manager.mark_song_downloaded(playlist_id, song['id'])
                
                # 更新码率和格式信息
                if result.get('bitrate') and results.get('bitrate') == '未知':
                    results['bitrate'] = result.get('bitrate')
                if result.get('file_format'):
                    results['file_format'] = result.get('file_format')
            else:
                # 记录下载失败原因
                fail_reason = result.get('error', '未知错误')
                if self.config_manager:
                    self.config_manager.mark_song_failed(playlist_id, song['id'], fail_reason)
                results['failed_songs'] = results.get('failed_songs', 0) + 1
            
            time.sleep(0.5)
        
        # 更新歌单统计
        if self.config_manager:
            self.config_manager.update_subscribed_playlist(
                playlist_id=playlist_id,
                last_check_time=time.strftime('%Y-%m-%d %H:%M:%S'),
                last_song_count=len(songs),
                total_downloaded=results['downloaded_songs'] + results['skipped_songs']
            )
        
        logger.info(f"✅ 歌单 '{playlist_name}' 增量下载完成: {results['downloaded_songs']}/{len(new_songs)}")
        return results
    
    def sync_playlist(self, playlist_id: str, download_dir: str,
                     quality: str = None,
                     progress_callback: Optional[Callable] = None) -> Dict[str, Any]:
        """同步歌单（检查并下载新歌曲的别名方法）"""
        return self.download_playlist_incremental(playlist_id, download_dir, quality, progress_callback)
    
    def refresh_metadata(self, download_dir: str, 
                        progress_callback: Optional[Callable] = None) -> Dict[str, Any]:
        """刷新下载目录中所有音乐文件的元数据（不下载，只更新标签）
        
        扫描下载目录，根据文件名解析歌曲信息，从 API 获取最新元数据并更新文件标签。
        
        Args:
            download_dir: 下载目录
            progress_callback: 进度回调
            
        Returns:
            刷新结果统计
        """
        logger.info(f"🔄 开始刷新元数据: {download_dir}")
        
        results = {
            'success': True,
            'total_files': 0,
            'updated_files': 0,
            'skipped_files': 0,
            'failed_files': 0,
            'details': []
        }
        
        # 支持的音频格式
        audio_extensions = {'.mp3', '.flac', '.m4a', '.wav', '.aac'}
        
        # 递归扫描目录中的音频文件
        audio_files = []
        download_path = Path(download_dir)
        
        if not download_path.exists():
            return {'success': False, 'error': f'目录不存在: {download_dir}'}
        
        for file_path in download_path.rglob('*'):
            if file_path.suffix.lower() in audio_extensions:
                audio_files.append(file_path)
        
        results['total_files'] = len(audio_files)
        logger.info(f"📁 发现 {len(audio_files)} 个音频文件")
        
        if not audio_files:
            results['message'] = '没有找到音频文件'
            return results
        
        # 处理每个文件
        for i, file_path in enumerate(audio_files, 1):
            if progress_callback:
                progress_callback({
                    'status': 'metadata_refresh',
                    'current': i,
                    'total': len(audio_files),
                    'file': file_path.name,
                })
            
            try:
                # 尝试从现有元数据或文件名解析歌曲信息
                song_info = self._extract_song_info_from_file(file_path)
                
                if not song_info:
                    logger.warning(f"⚠️ 无法解析文件信息: {file_path.name}")
                    results['skipped_files'] += 1
                    results['details'].append({
                        'file': str(file_path),
                        'status': 'skipped',
                        'reason': '无法解析歌曲信息'
                    })
                    continue
                
                # 如果有歌曲ID，从 API 获取最新信息
                if song_info.get('song_id'):
                    api_info = self.get_song_info(song_info['song_id'])
                    if api_info:
                        # 获取专辑详细信息（包含 track_number）
                        if api_info.get('album_id'):
                            track_info = self.get_album_track_info(str(api_info['album_id']))
                            if song_info['song_id'] in track_info:
                                api_info.update(track_info[song_info['song_id']])
                        song_info.update(api_info)
                
                # 更新元数据
                success = self._add_metadata_to_file(
                    str(file_path),
                    song_info,
                    cover_url=song_info.get('cover')
                )
                
                if success:
                    results['updated_files'] += 1
                    results['details'].append({
                        'file': str(file_path),
                        'status': 'updated',
                        'song': song_info.get('name', '未知')
                    })
                    logger.info(f"✅ 更新元数据: {file_path.name}")
                else:
                    results['failed_files'] += 1
                    results['details'].append({
                        'file': str(file_path),
                        'status': 'failed',
                        'reason': '元数据写入失败'
                    })
                
            except Exception as e:
                logger.error(f"❌ 处理文件失败 {file_path.name}: {e}")
                results['failed_files'] += 1
                results['details'].append({
                    'file': str(file_path),
                    'status': 'failed',
                    'reason': str(e)
                })
            
            time.sleep(0.2)  # 避免 API 请求过快
        
        logger.info(f"✅ 元数据刷新完成: 更新 {results['updated_files']}, 跳过 {results['skipped_files']}, 失败 {results['failed_files']}")
        return results
    
    def _extract_song_info_from_file(self, file_path: Path) -> Optional[Dict[str, Any]]:
        """从音频文件提取歌曲信息（优先读取现有标签，其次解析文件名）
        
        Args:
            file_path: 音频文件路径
            
        Returns:
            歌曲信息字典，包含 name, artist, album, song_id 等
        """
        song_info = {}
        
        # 1. 尝试从现有元数据读取
        try:
            from mutagen import File as MutagenFile
            audio = MutagenFile(str(file_path), easy=True)
            
            if audio:
                # 提取基本标签
                if audio.get('title'):
                    song_info['name'] = audio['title'][0]
                if audio.get('artist'):
                    song_info['artist'] = audio['artist'][0]
                if audio.get('album'):
                    song_info['album'] = audio['album'][0]
                if audio.get('albumartist'):
                    song_info['album_artist'] = audio['albumartist'][0]
                    
        except Exception as e:
            logger.debug(f"读取现有元数据失败: {e}")
        
        # 2. 如果元数据不完整，尝试从文件名解析
        if not song_info.get('name'):
            filename = file_path.stem  # 不含扩展名
            
            # 常见文件名格式: "歌曲名" 或 "歌曲名 - 艺术家"
            if ' - ' in filename:
                parts = filename.split(' - ', 1)
                song_info['name'] = parts[0].strip()
                if len(parts) > 1:
                    song_info['artist'] = parts[1].strip()
            else:
                song_info['name'] = filename
        
        # 3. 从目录结构推断专辑和艺术家
        # 假设目录格式: {ArtistName}/{AlbumName}/song.mp3
        try:
            parent = file_path.parent
            if parent.name and not song_info.get('album'):
                song_info['album'] = parent.name
            
            grandparent = parent.parent
            if grandparent.name and not song_info.get('artist'):
                # 检查是否是下载根目录
                if grandparent.name not in ['downloads', 'netease', 'music']:
                    song_info['artist'] = grandparent.name
        except Exception:
            pass
        
        # 4. 尝试通过歌曲名搜索获取ID
        if song_info.get('name') and not song_info.get('song_id'):
            search_query = song_info['name']
            if song_info.get('artist'):
                search_query += f" {song_info['artist']}"
            
            search_results = self.search_songs(search_query, limit=5)
            
            # 查找最匹配的结果
            for result in search_results:
                if self._is_song_match(song_info, result):
                    song_info['song_id'] = result['id']
                    # 更新其他信息
                    song_info['name'] = result['name']
                    song_info['artist'] = result['artist']
                    song_info['album'] = result.get('album', song_info.get('album', ''))
                    song_info['cover'] = result.get('cover', '')
                    break
        
        return song_info if song_info.get('name') else None
    
    def _is_song_match(self, local_info: Dict, api_info: Dict) -> bool:
        """判断本地歌曲信息与API结果是否匹配"""
        local_name = local_info.get('name', '').lower().strip()
        api_name = api_info.get('name', '').lower().strip()
        
        # 歌曲名必须相似
        if local_name not in api_name and api_name not in local_name:
            # 检查是否有大部分重叠
            if len(set(local_name) & set(api_name)) < min(len(local_name), len(api_name)) * 0.5:
                return False
        
        # 如果有艺术家信息，也检查匹配
        local_artist = local_info.get('artist', '').lower().strip()
        api_artist = api_info.get('artist', '').lower().strip()
        
        if local_artist and api_artist:
            # 检查艺术家是否有部分匹配
            local_artists = set(local_artist.replace('、', ',').replace('/', ',').split(','))
            api_artists = set(api_artist.replace('、', ',').replace('/', ',').split(','))
            
            local_artists = {a.strip() for a in local_artists}
            api_artists = {a.strip() for a in api_artists}
            
            if not local_artists & api_artists:
                # 没有完全匹配，检查部分包含
                matched = False
                for la in local_artists:
                    for aa in api_artists:
                        if la in aa or aa in la:
                            matched = True
                            break
                    if matched:
                        break
                if not matched:
                    return False
        
        return True
    
    def refresh_playlist_metadata(self, playlist_id: str, download_dir: str,
                                  progress_callback: Optional[Callable] = None) -> Dict[str, Any]:
        """刷新歌单中已下载歌曲的元数据
        
        只更新数据库中标记为已下载的歌曲，从 API 获取最新元数据并更新文件标签。
        
        Args:
            playlist_id: 歌单ID
            download_dir: 下载目录
            progress_callback: 进度回调
            
        Returns:
            刷新结果统计
        """
        logger.info(f"🔄 开始刷新歌单元数据: {playlist_id}")
        
        # 获取歌单中已下载的歌曲记录
        if not self.config_manager:
            return {'success': False, 'error': '配置管理器不可用'}
        
        downloaded_songs = self.config_manager.get_playlist_songs(playlist_id, downloaded_only=True)
        
        if not downloaded_songs:
            return {
                'success': True,
                'message': '没有已下载的歌曲',
                'total_songs': 0,
                'updated_songs': 0
            }
        
        # 获取歌单最新信息（包含完整的元数据）
        songs, playlist_name = self.get_playlist_songs(playlist_id)
        songs_dict = {s['id']: s for s in songs}
        
        results = {
            'success': True,
            'playlist_id': playlist_id,
            'playlist_name': playlist_name,
            'total_songs': len(downloaded_songs),
            'updated_songs': 0,
            'skipped_songs': 0,
            'failed_songs': 0,
            'details': []
        }
        
        for i, record in enumerate(downloaded_songs, 1):
            song_id = record['song_id']
            
            if progress_callback:
                progress_callback({
                    'status': 'metadata_refresh',
                    'current': i,
                    'total': len(downloaded_songs),
                    'song': record.get('song_name', '未知'),
                })
            
            try:
                # 从歌单数据获取歌曲信息
                song_info = songs_dict.get(song_id)
                
                if not song_info:
                    # 如果歌单中找不到，单独获取
                    song_info = self.get_song_info(song_id)
                    if song_info and song_info.get('album_id'):
                        track_info = self.get_album_track_info(str(song_info['album_id']))
                        if song_id in track_info:
                            song_info.update(track_info[song_id])
                
                if not song_info:
                    results['skipped_songs'] += 1
                    results['details'].append({
                        'song_id': song_id,
                        'status': 'skipped',
                        'reason': '无法获取歌曲信息'
                    })
                    continue
                
                # 查找本地文件
                file_path = self._find_song_file(download_dir, song_info)
                
                if not file_path:
                    results['skipped_songs'] += 1
                    results['details'].append({
                        'song_id': song_id,
                        'song_name': song_info.get('name', '未知'),
                        'status': 'skipped',
                        'reason': '本地文件不存在'
                    })
                    continue
                
                # 更新元数据
                success = self._add_metadata_to_file(
                    str(file_path),
                    song_info,
                    cover_url=song_info.get('cover')
                )
                
                if success:
                    results['updated_songs'] += 1
                    results['details'].append({
                        'song_id': song_id,
                        'song_name': song_info.get('name', '未知'),
                        'file': str(file_path),
                        'status': 'updated'
                    })
                    logger.info(f"✅ 更新元数据: {song_info.get('name')}")
                else:
                    results['failed_songs'] += 1
                    results['details'].append({
                        'song_id': song_id,
                        'song_name': song_info.get('name', '未知'),
                        'status': 'failed',
                        'reason': '元数据写入失败'
                    })
                
            except Exception as e:
                logger.error(f"❌ 处理歌曲失败 {song_id}: {e}")
                results['failed_songs'] += 1
                results['details'].append({
                    'song_id': song_id,
                    'status': 'failed',
                    'reason': str(e)
                })
            
            time.sleep(0.3)
        
        logger.info(f"✅ 歌单元数据刷新完成: 更新 {results['updated_songs']}, 跳过 {results['skipped_songs']}, 失败 {results['failed_songs']}")
        return results
    
    def _find_song_file(self, download_dir: str, song_info: Dict) -> Optional[Path]:
        """在下载目录中查找歌曲文件
        
        Args:
            download_dir: 下载目录
            song_info: 歌曲信息
            
        Returns:
            找到的文件路径，未找到返回 None
        """
        audio_extensions = ['.flac', '.mp3', '.m4a', '.wav', '.aac']
        
        # 方法1: 根据配置的目录格式查找
        for ext in audio_extensions:
            filename = self._build_filename(song_info, ext.lstrip('.'))
            save_dir = self._build_directory(download_dir, song_info)
            file_path = Path(save_dir) / filename
            
            if file_path.exists():
                return file_path
        
        # 方法2: 递归搜索匹配文件名
        song_name = self.clean_filename(song_info.get('name', ''))
        if song_name:
            download_path = Path(download_dir)
            for ext in audio_extensions:
                # 搜索包含歌曲名的文件
                for file_path in download_path.rglob(f'*{song_name}*{ext}'):
                    return file_path
        
        return None
    
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
    
    def _delete_song_file(self, download_dir: str, song_info: Dict) -> bool:
        """删除歌曲文件及相关文件（歌词、封面）
        
        Args:
            download_dir: 下载目录
            song_info: 歌曲信息字典（包含 song_name, artist, album 等）
        
        Returns:
            是否成功删除
        """
        try:
            # 构建歌曲文件路径
            song_name = song_info.get('song_name', '')
            artist = song_info.get('artist', '')
            album = song_info.get('album', '')
            
            if not song_name:
                logger.warning(f"⚠️ 歌曲信息不完整，无法删除文件")
                return False
            
            # 构建文件可能所在的目录
            save_dir = self._build_directory(download_dir, {
                'name': song_name,
                'artist': artist,
                'album': album
            })
            
            # 构建可能的文件名模式
            clean_name = self.clean_filename(song_name)
            clean_artist = self.clean_filename(artist)
            
            deleted_files = []
            file_patterns = [
                f"{clean_name}.*",  # 歌曲名
                f"{clean_name} - {clean_artist}.*",  # 歌曲名 - 艺术家
                f"{clean_artist} - {clean_name}.*",  # 艺术家 - 歌曲名
            ]
            
            from pathlib import Path
            save_path = Path(save_dir)
            
            if save_path.exists():
                # 搜索并删除匹配的文件
                for pattern in file_patterns:
                    for file_path in save_path.glob(pattern):
                        try:
                            file_path.unlink()
                            deleted_files.append(str(file_path))
                            logger.info(f"🗑️ 已删除文件: {file_path.name}")
                        except Exception as e:
                            logger.error(f"❌ 删除文件失败 {file_path}: {e}")
                
                if deleted_files:
                    logger.info(f"✅ 成功删除 {len(deleted_files)} 个文件（{song_name}）")
                    return True
                else:
                    logger.warning(f"⚠️ 未找到匹配的文件: {song_name}")
                    return False
            else:
                logger.warning(f"⚠️ 目录不存在: {save_dir}")
                return False
                
        except Exception as e:
            logger.error(f"❌ 删除歌曲文件失败: {e}")
            import traceback
            logger.error(f"   详细错误: {traceback.format_exc()}")
            return False
    
    def _download_file(self, url: str, filepath: str,
                      progress_callback: Optional[Callable] = None,
                      display_name: str = None) -> bool:
        """下载文件"""
        try:
            response = self.session.get(url, stream=True, timeout=300)
            response.raise_for_status()
            
            total_size = int(response.headers.get('content-length', 0))
            downloaded = 0
            start_time = time.time()
            
            # 确保目录存在
            os.makedirs(os.path.dirname(filepath), exist_ok=True)
            
            # 用于显示的文件名
            filename = display_name or os.path.basename(filepath)
            
            with open(filepath, 'wb') as f:
                for chunk in response.iter_content(chunk_size=8192):
                    if chunk:
                        f.write(chunk)
                        downloaded += len(chunk)
                        
                        if progress_callback and total_size > 0:
                            progress = (downloaded / total_size) * 100
                            elapsed = time.time() - start_time
                            speed = downloaded / elapsed if elapsed > 0 else 0
                            eta = (total_size - downloaded) / speed if speed > 0 else 0
                            
                            progress_callback({
                                'status': 'file_progress',
                                'percent': progress,
                                'downloaded': downloaded,
                                'total': total_size,
                                'speed': speed,
                                'eta': eta,
                                'filename': filename,
                            })
            
            logger.info(f"✅ 下载完成: {filepath}")
            return True
            
        except Exception as e:
            logger.error(f"❌ 下载文件失败: {e}")
            return False

    def _add_metadata_to_file(
        self,
        file_path: str,
        song_info: Dict,
        album_info: Optional[Dict] = None,
        cover_url: Optional[str] = None
    ) -> bool:
        """
        为下载的音乐文件添加元数据标签（用于Plex等媒体库刮削）
        
        Args:
            file_path: 音乐文件路径
            song_info: 歌曲信息字典
            album_info: 专辑信息字典（可选）
            cover_url: 封面图片URL（可选）
            
        Returns:
            bool: 是否成功添加元数据
        """
        logger.info(f"🏷️ 开始为音乐文件添加元数据: {Path(file_path).name}")
        
        if not self.metadata_manager:
            logger.warning("⚠️ 元数据管理器不可用，尝试使用内置回退方式写入元数据")
        
        try:
            # 安全提取发布时间
            def _extract_year(publish_time_value) -> str:
                """提取年份"""
                if not publish_time_value:
                    return ''
                try:
                    if isinstance(publish_time_value, int):
                        from datetime import datetime
                        dt = datetime.fromtimestamp(publish_time_value / 1000)
                        return str(dt.year)
                    s = str(publish_time_value)
                    return s[:4] if len(s) >= 4 else s
                except Exception:
                    return ''
            
            def _extract_release_date(publish_time_value) -> str:
                """提取完整发布日期"""
                if not publish_time_value:
                    return ''
                try:
                    if isinstance(publish_time_value, int):
                        from datetime import datetime
                        dt = datetime.fromtimestamp(publish_time_value / 1000)
                        return dt.strftime('%Y-%m-%d')
                    s = str(publish_time_value)
                    if len(s) >= 8:
                        return s
                    return ''
                except Exception:
                    return ''
            
            # 智能处理发布时间
            song_release_date = _extract_release_date(song_info.get('publish_time'))
            song_publish_year = _extract_year(song_info.get('publish_time'))
            
            # 智能处理专辑艺术家
            song_album_artist = song_info.get('album_artist', '')
            if not song_album_artist:
                artist_str = song_info.get('artist', '')
                # 从多艺术家字符串中提取第一个
                for sep in [', ', '、', '/', ' feat. ', ' ft. ', ' & ']:
                    if sep in artist_str:
                        song_album_artist = artist_str.split(sep)[0].strip()
                        break
                else:
                    song_album_artist = artist_str
            
            # 准备元数据
            metadata = {
                'title': song_info.get('name', ''),
                'artist': song_info.get('artist', ''),
                'album': song_info.get('album', ''),
                'album_artist': song_album_artist,
                'track_number': str(song_info.get('track_number', '')),
                'total_tracks': str(song_info.get('total_tracks', '')) if song_info.get('total_tracks') else '',
                'disc_number': str(song_info.get('disc_number', '1')),
                'genre': '流行'
            }
            
            # 记录关键元数据字段
            logger.info(f"🏷️ 元数据: 曲目={metadata['track_number']}, 总数={metadata['total_tracks']}, 专辑艺术家={metadata['album_artist']}")
            
            # 智能处理时间字段
            if song_release_date and len(song_release_date) > 4:
                metadata['date'] = song_publish_year
                metadata['releasetime'] = song_release_date
                logger.debug(f"🗓️ 同时写入年份: {song_publish_year} 和完整发布时间: {song_release_date}")
            elif song_publish_year:
                metadata['date'] = song_publish_year
                logger.debug(f"📅 只写入发布年份: {song_publish_year}")
            
            # 如果有专辑信息，优先使用专辑信息
            if album_info:
                metadata['album'] = album_info.get('name', metadata['album'])
                metadata['album_artist'] = album_info.get('artist', metadata['album_artist'])
                album_release_date = _extract_release_date(album_info.get('publish_time'))
                album_publish_year = _extract_year(album_info.get('publish_time'))
                
                if album_release_date and len(album_release_date) > 4:
                    metadata['date'] = album_publish_year or metadata.get('date', '')
                    metadata['releasetime'] = album_release_date
                elif album_publish_year:
                    metadata['date'] = album_publish_year
                    metadata.pop('releasetime', None)
            
            # 获取封面URL
            final_cover_url = cover_url or song_info.get('cover') or song_info.get('pic_url')
            if album_info:
                final_cover_url = final_cover_url or album_info.get('pic_url')
            
            logger.info(f"🏷️ 元数据详情:")
            logger.debug(f"  标题: {metadata['title']}")
            logger.debug(f"  艺术家: {metadata['artist']}")
            logger.debug(f"  专辑: {metadata['album']}")
            logger.debug(f"  专辑艺术家: {metadata['album_artist']}")
            logger.debug(f"  曲目: {metadata['track_number']}")
            logger.debug(f"  年份: {metadata.get('date', '')}")
            
            # 使用元数据管理器写入
            if self.metadata_manager:
                success = self.metadata_manager.add_metadata_to_file(
                    file_path=file_path,
                    metadata=metadata,
                    cover_url=final_cover_url
                )
            else:
                # 使用回退方案写入元数据
                success = self._embed_metadata_fallback(file_path, metadata, final_cover_url)
            
            if success:
                logger.info(f"✅ 成功添加元数据: {Path(file_path).name}")
            else:
                logger.warning(f"⚠️ 添加元数据失败: {Path(file_path).name}")
            
            return success
            
        except Exception as e:
            logger.error(f"❌ 添加元数据时出错: {e}")
            return False

    def _embed_metadata_fallback(
        self,
        file_path: str,
        metadata: Dict,
        cover_url: Optional[str]
    ) -> bool:
        """
        当外部元数据管理器不可用时，使用mutagen直接写入元数据
        仅依赖 mutagen，可选使用 requests 下载封面
        """
        try:
            from mutagen import File
            from mutagen.id3 import (
                ID3, ID3NoHeaderError, TIT2, TPE1, TALB, TPE2, 
                TRCK, TCON, APIC, TDRC, TYER, TPOS
            )
            from mutagen.flac import FLAC, Picture
        except ImportError as e:
            logger.warning(f"⚠️ 回退元数据写入不可用（缺少mutagen）: {e}")
            return False
        
        try:
            path_obj = Path(file_path)
            suffix = path_obj.suffix.lower()
            
            title = metadata.get('title', '')
            artist = metadata.get('artist', '')
            album = metadata.get('album', '')
            album_artist = metadata.get('album_artist', artist)
            track_number = str(metadata.get('track_number', '') or '')
            total_tracks = str(metadata.get('total_tracks', '') or '')
            disc_number = str(metadata.get('disc_number', '1') or '1')
            genre = metadata.get('genre', '流行')
            
            # 下载封面
            cover_data: Optional[bytes] = None
            cover_mime = 'image/jpeg'
            if cover_url:
                try:
                    resp = self.session.get(cover_url, timeout=15)
                    resp.raise_for_status()
                    cover_data = resp.content
                    ctype = resp.headers.get('content-type', '').lower()
                    if 'png' in ctype:
                        cover_mime = 'image/png'
                except Exception as ce:
                    logger.warning(f"⚠️ 下载专辑封面失败，跳过封面: {ce}")
            
            if suffix == '.mp3':
                try:
                    try:
                        tags = ID3(file_path)
                    except ID3NoHeaderError:
                        tags = ID3()
                    
                    tags.add(TIT2(encoding=3, text=title))
                    tags.add(TPE1(encoding=3, text=artist))
                    tags.add(TALB(encoding=3, text=album))
                    tags.add(TPE2(encoding=3, text=album_artist))
                    if track_number:
                        # 格式化曲目号：track/total
                        trck_value = f"{track_number}/{total_tracks}" if total_tracks else track_number
                        tags.add(TRCK(encoding=3, text=trck_value))
                    tags.add(TCON(encoding=3, text=genre))
                    
                    # 处理时间字段
                    if metadata.get('date'):
                        try:
                            tags.add(TYER(encoding=3, text=metadata['date']))
                        except:
                            tags.add(TDRC(encoding=3, text=metadata['date']))
                    
                    if metadata.get('releasetime'):
                        tags.add(TDRC(encoding=3, text=metadata['releasetime']))
                    
                    # 碟片编号
                    try:
                        tpos_value = f"{disc_number}/1" if disc_number else "1/1"
                        tags.add(TPOS(encoding=3, text=tpos_value))
                    except Exception:
                        pass
                    
                    if cover_data:
                        tags.add(APIC(encoding=3, mime=cover_mime, type=3, desc='Cover', data=cover_data))
                    
                    tags.save(file_path)
                    logger.info(f"✅ 回退方式为MP3写入元数据成功: {path_obj.name}")
                    return True
                except Exception as e:
                    logger.error(f"❌ 回退方式写入MP3元数据失败: {e}")
                    return False
            
            elif suffix == '.flac':
                try:
                    audio = FLAC(file_path)
                    audio['TITLE'] = title
                    audio['ARTIST'] = artist
                    audio['ALBUM'] = album
                    audio['ALBUMARTIST'] = album_artist
                    if track_number:
                        audio['TRACKNUMBER'] = track_number
                    if total_tracks:
                        audio['TOTALTRACKS'] = total_tracks
                        audio['TRACKTOTAL'] = total_tracks
                    
                    if metadata.get('date'):
                        audio['DATE'] = metadata['date']
                    
                    if metadata.get('releasetime'):
                        audio['RELEASETIME'] = metadata['releasetime']
                        audio['RELEASEDATE'] = metadata['releasetime']
                    
                    # 碟片编号
                    audio['DISCNUMBER'] = disc_number
                    audio['DISCTOTAL'] = '1'
                    audio['TOTALDISCS'] = '1'
                    audio['DISC'] = disc_number
                    audio['PART'] = disc_number
                    audio['PARTOFSET'] = f'{disc_number}/1'
                    audio['PART_OF_SET'] = f'{disc_number}/1'
                    audio['GENRE'] = genre
                    
                    if cover_data:
                        pic = Picture()
                        pic.data = cover_data
                        pic.type = 3
                        pic.mime = cover_mime
                        pic.desc = 'Cover'
                        audio.clear_pictures()
                        audio.add_picture(pic)
                    
                    audio.save()
                    logger.info(f"✅ 回退方式为FLAC写入元数据成功: {path_obj.name}")
                    return True
                except Exception as e:
                    logger.error(f"❌ 回退方式写入FLAC元数据失败: {e}")
                    return False
            
            elif suffix in ['.m4a', '.mp4', '.aac']:
                try:
                    from mutagen.mp4 import MP4, MP4Cover
                    
                    audio = MP4(file_path)
                    audio['\xa9nam'] = title
                    audio['\xa9ART'] = artist
                    audio['\xa9alb'] = album
                    audio['aART'] = album_artist
                    
                    if metadata.get('date'):
                        audio['\xa9day'] = metadata['date']
                    
                    if track_number:
                        try:
                            # M4A 的 trkn 格式: (track_number, total_tracks)
                            total = int(total_tracks) if total_tracks else 0
                            audio['trkn'] = [(int(track_number), total)]
                        except (ValueError, TypeError):
                            pass
                    
                    audio['\xa9gen'] = genre
                    
                    try:
                        audio['disk'] = [(int(disc_number), 1)]
                    except (ValueError, TypeError):
                        pass
                    
                    if cover_data:
                        audio['covr'] = [MP4Cover(cover_data, imageformat=MP4Cover.FORMAT_JPEG)]
                    
                    audio.save()
                    logger.info(f"✅ 回退方式为M4A写入元数据成功: {path_obj.name}")
                    return True
                except Exception as e:
                    logger.error(f"❌ 回退方式写入M4A元数据失败: {e}")
                    return False
            
            else:
                logger.warning(f"⚠️ 暂不支持的音频格式，无法写入元数据: {suffix}")
                return False
        
        except Exception as e:
            logger.error(f"❌ 回退方式写入元数据异常: {e}")
            return False
