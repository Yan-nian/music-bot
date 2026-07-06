#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
歌单管理 Mixin - 处理订阅歌单和歌曲记录
"""

import sqlite3
import logging
from typing import Dict, List, Any, Optional

logger = logging.getLogger(__name__)


class PlaylistManager:
    """歌单管理 Mixin - 处理订阅歌单和歌曲记录"""
    
    # ==================== 订阅歌单管理 ====================
    
    def add_subscribed_playlist(self, playlist_id: str, playlist_name: str = None,
                                playlist_url: str = None, platform: str = 'netease',
                                check_interval: int = 3600) -> bool:
        """添加订阅歌单"""
        try:
            with self._connect() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    INSERT INTO subscribed_playlists 
                    (platform, playlist_id, playlist_name, playlist_url, check_interval)
                    VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(platform, playlist_id) DO UPDATE SET 
                        playlist_name = COALESCE(?, playlist_name),
                        playlist_url = COALESCE(?, playlist_url),
                        check_interval = ?,
                        updated_at = CURRENT_TIMESTAMP
                """, (platform, playlist_id, playlist_name, playlist_url, check_interval,
                      playlist_name, playlist_url, check_interval))
                conn.commit()
                logger.info(f"✅ 添加订阅歌单: {playlist_name or playlist_id}")
                return True
        except Exception as e:
            logger.error(f"❌ 添加订阅歌单失败: {e}")
            return False
    
    def get_playlist_download_dir(self, playlist_id: str, platform: str = 'netease') -> str:
        """获取歌单的下载目录"""
        try:
            with self._connect() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT download_dir FROM subscribed_playlists
                    WHERE platform = ? AND playlist_id = ?
                """, (platform, playlist_id))
                row = cursor.fetchone()
                return row[0] if row else None
        except Exception as e:
            logger.error(f"❌ 获取歌单下载目录失败: {e}")
            return None
    
    def remove_subscribed_playlist(self, playlist_id: str, platform: str = 'netease', delete_files: bool = False) -> bool:
        """移除订阅歌单
        
        Args:
            playlist_id: 歌单ID
            platform: 平台
            delete_files: 是否同时删除本地文件
        """
        try:
            # 如果需要删除文件，先获取下载目录
            download_dir = None
            if delete_files:
                download_dir = self.get_playlist_download_dir(playlist_id, platform)
            
            with self._connect() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    DELETE FROM subscribed_playlists 
                    WHERE platform = ? AND playlist_id = ?
                """, (platform, playlist_id))
                # 同时删除该歌单的歌曲记录
                cursor.execute("""
                    DELETE FROM playlist_songs WHERE playlist_id = ?
                """, (playlist_id,))
                conn.commit()
                logger.info(f"✅ 移除订阅歌单: {playlist_id}")
            
            # 删除本地文件
            if delete_files and download_dir:
                self._delete_playlist_files(download_dir)
            
            return True
        except Exception as e:
            logger.error(f"❌ 移除订阅歌单失败: {e}")
            return False
    
    def _delete_playlist_files(self, download_dir: str) -> bool:
        """删除歌单的本地文件"""
        import shutil
        from pathlib import Path
        
        try:
            path = Path(download_dir)
            if path.exists() and path.is_dir():
                shutil.rmtree(path)
                logger.info(f"🗑️ 已删除歌单目录: {download_dir}")
                return True
            else:
                logger.warning(f"⚠️ 歌单目录不存在: {download_dir}")
                return False
        except Exception as e:
            logger.error(f"❌ 删除歌单文件失败: {e}")
            return False
    
    def get_subscribed_playlists(self, platform: str = None, enabled_only: bool = False) -> List[Dict[str, Any]]:
        """获取订阅歌单列表"""
        try:
            with self._connect() as conn:
                cursor = conn.cursor()
                
                query = "SELECT * FROM subscribed_playlists WHERE 1=1"
                params = []
                
                if platform:
                    query += " AND platform = ?"
                    params.append(platform)
                
                if enabled_only:
                    query += " AND enabled = 1"
                
                query += " ORDER BY created_at DESC"
                
                cursor.execute(query, params)
                columns = [description[0] for description in cursor.description]
                rows = cursor.fetchall()
                
                return [dict(zip(columns, row)) for row in rows]
        except Exception as e:
            logger.error(f"❌ 获取订阅歌单失败: {e}")
            return []
    
    def get_subscribed_playlist(self, playlist_id: str, platform: str = 'netease') -> Optional[Dict[str, Any]]:
        """获取单个订阅歌单信息"""
        try:
            with self._connect() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT * FROM subscribed_playlists 
                    WHERE platform = ? AND playlist_id = ?
                """, (platform, playlist_id))
                
                row = cursor.fetchone()
                if row:
                    columns = [description[0] for description in cursor.description]
                    return dict(zip(columns, row))
                return None
        except Exception as e:
            logger.error(f"❌ 获取订阅歌单失败: {e}")
            return None
    
    def update_subscribed_playlist(self, playlist_id: str, platform: str = 'netease', **kwargs) -> bool:
        """更新订阅歌单信息"""
        try:
            allowed_fields = ['playlist_name', 'auto_download', 'check_interval', 
                             'last_check_time', 'last_song_count', 'total_downloaded', 'enabled']
            
            updates = []
            values = []
            for key, value in kwargs.items():
                if key in allowed_fields:
                    updates.append(f"{key} = ?")
                    values.append(value)
            
            if not updates:
                return False
            
            values.extend([platform, playlist_id])
            
            with self._connect() as conn:
                cursor = conn.cursor()
                cursor.execute(f"""
                    UPDATE subscribed_playlists 
                    SET {', '.join(updates)}, updated_at = CURRENT_TIMESTAMP
                    WHERE platform = ? AND playlist_id = ?
                """, values)
                conn.commit()
                return True
        except Exception as e:
            logger.error(f"❌ 更新订阅歌单失败: {e}")
            return False
    
    # ==================== 歌单歌曲管理 ====================
    
    def add_playlist_song(self, playlist_id: str, song_id: str, song_name: str = None,
                         artist: str = None, album: str = None, downloaded: bool = False,
                         fail_reason: str = None) -> bool:
        """添加歌单歌曲记录"""
        try:
            with self._connect() as conn:
                cursor = conn.cursor()
                download_time = logging.Formatter().formatTime(None) if downloaded else None
                fail_time = logging.Formatter().formatTime(None) if fail_reason else None
                cursor.execute("""
                    INSERT INTO playlist_songs 
                    (playlist_id, song_id, song_name, artist, album, downloaded, download_time, fail_reason, fail_time)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(playlist_id, song_id) DO UPDATE SET
                        song_name = COALESCE(?, song_name),
                        artist = COALESCE(?, artist),
                        album = COALESCE(?, album),
                        downloaded = ?,
                        download_time = CASE WHEN ? THEN COALESCE(download_time, CURRENT_TIMESTAMP) ELSE download_time END,
                        fail_reason = ?,
                        fail_time = CASE WHEN ? IS NOT NULL THEN CURRENT_TIMESTAMP ELSE fail_time END,
                        retry_count = CASE WHEN ? IS NOT NULL THEN retry_count + 1 ELSE retry_count END
                """, (playlist_id, song_id, song_name, artist, album, downloaded, download_time, fail_reason, fail_time,
                      song_name, artist, album, downloaded, downloaded, fail_reason, fail_reason, fail_reason))
                conn.commit()
                return True
        except Exception as e:
            logger.error(f"❌ 添加歌单歌曲记录失败: {e}")
            return False
    
    def get_playlist_songs(self, playlist_id: str, downloaded_only: bool = False) -> List[Dict[str, Any]]:
        """获取歌单中的歌曲记录"""
        try:
            with self._connect() as conn:
                cursor = conn.cursor()
                
                query = "SELECT * FROM playlist_songs WHERE playlist_id = ?"
                params = [playlist_id]
                
                if downloaded_only:
                    query += " AND downloaded = 1"
                
                query += " ORDER BY created_at"
                
                cursor.execute(query, params)
                columns = [description[0] for description in cursor.description]
                rows = cursor.fetchall()
                
                return [dict(zip(columns, row)) for row in rows]
        except Exception as e:
            logger.error(f"❌ 获取歌单歌曲失败: {e}")
            return []
    
    def get_undownloaded_songs(self, playlist_id: str) -> List[Dict[str, Any]]:
        """获取歌单中未下载的歌曲"""
        try:
            with self._connect() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT * FROM playlist_songs 
                    WHERE playlist_id = ? AND downloaded = 0
                    ORDER BY created_at
                """, (playlist_id,))
                
                columns = [description[0] for description in cursor.description]
                rows = cursor.fetchall()
                
                return [dict(zip(columns, row)) for row in rows]
        except Exception as e:
            logger.error(f"❌ 获取未下载歌曲失败: {e}")
            return []
    
    def mark_song_downloaded(self, playlist_id: str, song_id: str) -> bool:
        """标记歌曲已下载"""
        try:
            with self._connect() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    UPDATE playlist_songs 
                    SET downloaded = 1, download_time = CURRENT_TIMESTAMP
                    WHERE playlist_id = ? AND song_id = ?
                """, (playlist_id, song_id))
                conn.commit()
                return True
        except Exception as e:
            logger.error(f"❌ 标记歌曲已下载失败: {e}")
            return False
    
    def is_song_downloaded(self, playlist_id: str, song_id: str) -> bool:
        """检查歌曲是否已下载"""
        try:
            with self._connect() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT downloaded FROM playlist_songs 
                    WHERE playlist_id = ? AND song_id = ?
                """, (playlist_id, song_id))
                
                row = cursor.fetchone()
                return bool(row and row[0])
        except Exception as e:
            logger.error(f"❌ 检查歌曲下载状态失败: {e}")
            return False
    
    def mark_song_failed(self, playlist_id: str, song_id: str, fail_reason: str) -> bool:
        """标记歌曲下载失败"""
        try:
            with self._connect() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    UPDATE playlist_songs 
                    SET fail_reason = ?, fail_time = CURRENT_TIMESTAMP, retry_count = retry_count + 1
                    WHERE playlist_id = ? AND song_id = ?
                """, (fail_reason, playlist_id, song_id))
                conn.commit()
                return True
        except Exception as e:
            logger.error(f"❌ 标记歌曲失败状态失败: {e}")
            return False
    
    def remove_playlist_song(self, playlist_id: str, song_id: str) -> bool:
        """从歌单中移除歌曲记录"""
        try:
            with self._connect() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    DELETE FROM playlist_songs 
                    WHERE playlist_id = ? AND song_id = ?
                """, (playlist_id, song_id))
                conn.commit()
                logger.debug(f"✅ 移除歌曲记录: {song_id} from playlist {playlist_id}")
                return True
        except Exception as e:
            logger.error(f"❌ 移除歌曲记录失败: {e}")
            return False
    
    def is_song_permanently_failed(self, playlist_id: str, song_id: str) -> bool:
        """检查歌曲是否因永久性原因失败（版权、VIP等）"""
        try:
            with self._connect() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT fail_reason, retry_count FROM playlist_songs 
                    WHERE playlist_id = ? AND song_id = ? AND downloaded = 0
                """, (playlist_id, song_id))
                row = cursor.fetchone()
                
                if not row or not row[0]:
                    return False
                
                fail_reason = row[0].lower()
                retry_count = row[1] or 0
                
                # 永久性失败的关键词
                permanent_keywords = [
                    '版权', 'copyright', 
                    'vip', '会员',
                    '付费', 'paid',
                    '下架', 'unavailable',
                    '无法获取下载链接',
                    '不可用', 'not available'
                ]
                
                # 检查是否包含永久性失败关键词
                if any(keyword in fail_reason for keyword in permanent_keywords):
                    logger.debug(f"⏭️ 歌曲 {song_id} 因永久性原因跳过: {row[0]}")
                    return True
                
                # 重试次数过多（超过3次）也视为永久性失败
                if retry_count >= 3:
                    logger.debug(f"⏭️ 歌曲 {song_id} 重试次数过多({retry_count}次)，跳过")
                    return True
                
                return False
                
        except Exception as e:
            logger.error(f"❌ 检查歌曲失败状态失败: {e}")
            return False
    
    def get_failed_songs(self, playlist_id: str) -> List[Dict[str, Any]]:
        """获取歌单中下载失败的歌曲"""
        try:
            with self._connect() as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT * FROM playlist_songs 
                    WHERE playlist_id = ? AND downloaded = 0 AND fail_reason IS NOT NULL
                    ORDER BY fail_time DESC
                """, (playlist_id,))
                
                rows = cursor.fetchall()
                return [dict(row) for row in rows]
        except Exception as e:
            logger.error(f"❌ 获取失败歌曲列表失败: {e}")
            return []
    
    def clear_song_fail_status(self, playlist_id: str, song_id: str) -> bool:
        """清除歌曲的失败状态（用于重试）"""
        try:
            with self._connect() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    UPDATE playlist_songs 
                    SET fail_reason = NULL, fail_time = NULL
                    WHERE playlist_id = ? AND song_id = ?
                """, (playlist_id, song_id))
                conn.commit()
                return True
        except Exception as e:
            logger.error(f"❌ 清除失败状态失败: {e}")
            return False
    
    def get_all_failed_songs(self) -> List[Dict[str, Any]]:
        """获取所有歌单中下载失败的歌曲"""
        try:
            with self._connect() as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT ps.*, sp.playlist_name 
                    FROM playlist_songs ps
                    LEFT JOIN subscribed_playlists sp ON ps.playlist_id = sp.playlist_id
                    WHERE ps.downloaded = 0 AND ps.fail_reason IS NOT NULL
                    ORDER BY ps.fail_time DESC
                """, ())
                
                rows = cursor.fetchall()
                return [dict(row) for row in rows]
        except Exception as e:
            logger.error(f"❌ 获取所有失败歌曲失败: {e}")
            return []
    
    def get_playlist_stats(self, playlist_id: str) -> Dict[str, int]:
        """获取歌单统计信息"""
        try:
            with self._connect() as conn:
                cursor = conn.cursor()
                
                cursor.execute("""
                    SELECT 
                        COUNT(*) as total,
                        SUM(CASE WHEN downloaded = 1 THEN 1 ELSE 0 END) as downloaded,
                        SUM(CASE WHEN downloaded = 0 AND fail_reason IS NOT NULL THEN 1 ELSE 0 END) as failed
                    FROM playlist_songs WHERE playlist_id = ?
                """, (playlist_id,))
                
                row = cursor.fetchone()
                total = row[0] or 0
                downloaded = row[1] or 0
                failed = row[2] or 0
                return {
                    'total': total,
                    'downloaded': downloaded,
                    'failed': failed,
                    'pending': total - downloaded - failed
                }
        except Exception as e:
            logger.error(f"❌ 获取歌单统计失败: {e}")
            return {'total': 0, 'downloaded': 0, 'failed': 0, 'pending': 0}
