#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
网易云音乐 API 模块 - 封装所有 API 调用
"""

import re
import time
import logging
import requests
from typing import Dict, List, Any, Optional

logger = logging.getLogger(__name__)


class NeteaseAPI:
    """网易云音乐 API 封装"""
    
    # 音质映射
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
    
    def __init__(self, session: requests.Session, api_url: str = "https://music.163.com"):
        """
        初始化 API
        
        Args:
            session: requests Session（共享下载器的 session）
            api_url: API 基础 URL
        """
        self.session = session
        self.api_url = api_url
    
    def search_songs(self, keyword: str, limit: int = 20) -> List[Dict[str, Any]]:
        """搜索歌曲"""
        try:
            url = f"{self.api_url}/api/search/get/web"
            params = {
                'csrf_token': '',
                's': keyword,
                'type': '1',
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
        """批量获取歌曲详情"""
        result = {}
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
                
                time.sleep(0.3)
                
            except Exception as e:
                logger.error(f"❌ 批量获取歌曲详情失败: {e}")
        
        logger.info(f"✅ 批量获取完成: {len(result)}/{len(song_ids)} 首")
        return result
    
    def get_album_track_info(self, album_id: str) -> Dict[str, Dict[str, Any]]:
        """获取专辑曲目信息"""
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
    
    def get_song_url(self, song_id: str, quality: str = None, 
                     quality_map: Dict = None) -> Optional[Dict[str, Any]]:
        """获取歌曲下载链接"""
        try:
            br = (quality_map or self.QUALITY_MAP).get(quality or '无损', 999000)
            
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
                song_code = song_data.get('code', 0)
                
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
                    fee = song_data.get('fee', 0)
                    payed = song_data.get('payed', 0)
                    
                    if song_code == -110:
                        logger.warning(f"⚠️ 歌曲 {song_id} 需要 VIP 权限")
                    elif song_code == 404:
                        logger.warning(f"⚠️ 歌曲 {song_id} 不存在或已下架")
                    elif fee == 1 and not payed:
                        logger.warning(f"⚠️ 歌曲 {song_id} 需要 VIP 订阅")
                    elif fee == 4 and not payed:
                        logger.warning(f"⚠️ 歌曲 {song_id} 需要购买专辑")
                    else:
                        logger.warning(f"⚠️ 歌曲 {song_id} 链接为空 (code={song_code})")
            else:
                logger.warning(f"⚠️ API 返回异常: code={data.get('code')}")
            
            return None
            
        except Exception as e:
            logger.error(f"❌ 获取歌曲URL失败: {e}")
            return None
    
    def get_song_url_with_fallback(self, song_id: str, preferred_quality: str = None,
                                   quality_fallback: List[str] = None,
                                   quality_map: Dict = None) -> Optional[Dict[str, Any]]:
        """获取歌曲下载链接，支持音质降级"""
        if not preferred_quality:
            preferred_quality = 'flac'
        
        quality_fallback = quality_fallback or self.QUALITY_FALLBACK
        quality_map = quality_map or self.QUALITY_MAP
        
        start_idx = 0
        quality_key = preferred_quality.lower()
        
        if quality_key in quality_fallback:
            start_idx = quality_fallback.index(quality_key)
        
        for quality in quality_fallback[start_idx:]:
            result = self.get_song_url(song_id, quality, quality_map)
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
    
    def get_lyrics(self, song_id: str, lyrics_merge: bool = False) -> Optional[str]:
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
                
                if lyrics_merge and tlyric:
                    return f"{lrc}\n\n--- 翻译 ---\n\n{tlyric}"
                return lrc
            
            return None
            
        except Exception as e:
            logger.error(f"❌ 获取歌词失败: {e}")
            return None
    
    def get_album_songs(self, album_id: str) -> List[Dict[str, Any]]:
        """获取专辑歌曲列表"""
        try:
            url = f"{self.api_url}/api/album/{album_id}"
            logger.info(f"💿 获取专辑歌曲: {url}")
            
            response = self.session.get(url, timeout=15)
            response.raise_for_status()
            data = response.json()
            
            if data.get('code') == 200 and data.get('album'):
                album_info = data['album']
                songs = album_info.get('songs', [])
                album_name = album_info.get('name', '')
                album_cover = album_info.get('picUrl', '')
                album_artists = album_info.get('artists', [])
                album_artist = album_artists[0].get('name', '未知') if album_artists else '未知'
                album_publish_time = album_info.get('publishTime', '')
                total_tracks = len(songs)
                
                logger.info(f"💿 专辑: {album_name}, 艺术家: {album_artist}, 歌曲数: {total_tracks}")
                
                if songs:
                    result = []
                    for i, song in enumerate(songs, 1):
                        artists = song.get('artists', [])
                        artist_name = ', '.join([a.get('name', '') for a in artists]) if artists else album_artist
                        
                        track_no = song.get('no', 0)
                        if not track_no or track_no == 0:
                            track_no = i
                        
                        result.append({
                            'id': str(song['id']),
                            'name': song.get('name', '未知'),
                            'artist': artist_name,
                            'album': album_name,
                            'album_artist': album_artist,
                            'track_number': track_no,
                            'total_tracks': total_tracks,
                            'disc_number': song.get('cd', '1') or '1',
                            'cover': album_cover,
                            'duration': song.get('duration', 0) // 1000,
                            'publish_time': album_publish_time,
                        })
                    
                    logger.info(f"✅ 获取专辑歌曲成功: {len(result)} 首")
                    return result
            
            logger.error(f"❌ API返回错误: {data.get('msg', '未知')}")
            return []
            
        except Exception as e:
            logger.error(f"❌ 获取专辑歌曲失败: {e}")
            return []
