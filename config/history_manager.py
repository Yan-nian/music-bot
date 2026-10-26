#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
下载历史管理 Mixin - 处理下载历史记录
"""

import os
import logging
import datetime as dt
from typing import Dict, List, Any, Optional

logger = logging.getLogger(__name__)

# 非单曲记录（专辑/歌单）的 file_size 历史上存的是「成功曲目数」，
# 新版本改存「总字节数」。用 1MB 做分界：一个专辑的音频不可能小于 1MB，
# 而曲目数不可能大于 1MB，两边都不会误判。
BYTES_THRESHOLD = 1024 * 1024


class HistoryManager:
    """下载历史管理 Mixin"""
    
    def add_download_history(self, platform: str, content_type: str, content_id: str,
                            title: str = None, artist: str = None, file_path: str = None,
                            file_size: int = None, quality: str = None) -> bool:
        """添加下载历史记录"""
        try:
            with self._connect() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    INSERT INTO download_history 
                    (platform, content_type, content_id, title, artist, file_path, file_size, quality)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """, (platform, content_type, content_id, title, artist, file_path, file_size, quality))
                conn.commit()
                return True
                
        except Exception as e:
            logger.error(f"❌ 添加下载历史失败: {e}")
            return False
    
    def record_download_result(self, platform: str, content_type: str, content_id: str,
                               result: Dict[str, Any], download_dir: str = '') -> bool:
        """把一次下载结果写进历史表（Bot 与 Web 共用）。

        以前这段逻辑只存在于 main.py 的 Bot 链路里，Web 后台触发的歌单同步、
        重试任务跑完什么都不记，结果仪表盘的「累计下载 / 今日新增 / 曲库容量」
        对只用 Web 的用户永远是 0。

        返回是否真的写入了记录（合并成一个文件的多曲目录不写，靠扫盘统计容量）。
        """
        if not isinstance(result, dict) or not result.get('success'):
            return False

        try:
            if content_type == 'song':
                size_bytes = int(float(result.get('size_mb') or 0) * 1024 * 1024)
                # size_mb 缺失时退回真实文件大小，别再写 0 进去
                if not size_bytes:
                    filepath = result.get('filepath') or ''
                    if filepath and os.path.isfile(filepath):
                        try:
                            size_bytes = os.path.getsize(filepath)
                        except OSError:
                            size_bytes = 0
                return self.add_download_history(
                    platform=platform,
                    content_type=content_type,
                    content_id=content_id,
                    title=result.get('song_title', '未知'),
                    artist=result.get('song_artist', '未知'),
                    file_path=result.get('filepath', ''),
                    file_size=size_bytes,
                    quality=result.get('quality', ''),
                )

            if content_type in ('album', 'playlist'):
                songs_list = result.get('songs') or []
                success_songs = [s for s in songs_list if isinstance(s, dict) and s.get('success')]
                title = (result.get('album_name')
                         or result.get('playlist_title')
                         or result.get('album')
                         or '未知')
                artist_name = result.get('artist', '未知艺术家')

                total_bytes = 0
                for song in success_songs:
                    size = int(float(song.get('size_mb') or 0) * 1024 * 1024)
                    if not size:
                        path = song.get('filepath') or ''
                        if path and os.path.isfile(path):
                            try:
                                size = os.path.getsize(path)
                            except OSError:
                                size = 0
                    total_bytes += size

                total_songs = result.get('total_songs') or len(songs_list)

                # 一条都没成功就别写一条「0 首」的垃圾记录
                if not success_songs:
                    return False

                return self.add_download_history(
                    platform=platform,
                    content_type=content_type,
                    content_id=content_id,
                    title=title,
                    artist=artist_name,
                    file_path=download_dir,
                    file_size=total_bytes,
                    quality=f"{len(success_songs)}/{total_songs}",
                )
        except Exception as e:  # noqa: BLE001
            logger.error(f"❌ 记录下载历史失败: {e}")
        return False

    def get_history_stats(self) -> Dict[str, int]:
        """全量统计（不按分页截断）

        仪表盘之前拿 get_download_history(limit=200) 的结果去算总数，
        历史超过 200 条后「累计下载」就永远停在 200。这里直接走 SQL 聚合。

        「今日」的边界要小心：created_at 由 CURRENT_TIMESTAMP 写入，是 UTC。
        拿本地日期字符串去比，东八区 0:00-8:00 的记录会被算进昨天，
        所以要先把本地零点换算回 UTC 再比较。
        """
        empty = {'total': 0, 'today': 0, 'total_bytes': 0,
                 'song_count': 0, 'album_count': 0, 'playlist_count': 0}
        try:
            now_local = dt.datetime.now()
            offset = now_local.astimezone().utcoffset() or dt.timedelta(0)
            midnight_local = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
            today_start_utc = (midnight_local - offset).strftime('%Y-%m-%d %H:%M:%S')

            with self._connect() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT
                        COUNT(*) AS total,
                        SUM(CASE WHEN created_at >= ? THEN 1 ELSE 0 END) AS today,
                        SUM(CASE WHEN content_type = 'song' THEN 1 ELSE 0 END) AS song_count,
                        SUM(CASE WHEN content_type = 'album' THEN 1 ELSE 0 END) AS album_count,
                        SUM(CASE WHEN content_type = 'playlist' THEN 1 ELSE 0 END) AS playlist_count,
                        SUM(
                            CASE
                                WHEN content_type = 'song' THEN COALESCE(file_size, 0)
                                WHEN COALESCE(file_size, 0) >= ? THEN file_size
                                ELSE 0
                            END
                        ) AS total_bytes
                    FROM download_history
                """, (today_start_utc, BYTES_THRESHOLD))

                row = cursor.fetchone()
                if not row:
                    return empty
                return {
                    'total': row[0] or 0,
                    'today': row[1] or 0,
                    'song_count': row[2] or 0,
                    'album_count': row[3] or 0,
                    'playlist_count': row[4] or 0,
                    'total_bytes': int(row[5] or 0),
                }
        except Exception as e:  # noqa: BLE001
            logger.error(f"❌ 获取下载统计失败: {e}")
            return empty

    def get_download_history(self, limit: int = 50, platform: str = None) -> List[Dict[str, Any]]:
        """获取下载历史"""
        try:
            with self._connect() as conn:
                cursor = conn.cursor()
                
                if platform:
                    cursor.execute("""
                        SELECT * FROM download_history 
                        WHERE platform = ?
                        ORDER BY created_at DESC LIMIT ?
                    """, (platform, limit))
                else:
                    cursor.execute("""
                        SELECT * FROM download_history 
                        ORDER BY created_at DESC LIMIT ?
                    """, (limit,))
                
                columns = [description[0] for description in cursor.description]
                rows = cursor.fetchall()
                
                return [dict(zip(columns, row)) for row in rows]
                
        except Exception as e:
            logger.error(f"❌ 获取下载历史失败: {e}")
            return []
    
    def check_download_exists(self, platform: str, content_type: str, content_id: str) -> Optional[Dict[str, Any]]:
        """检查是否已下载过此内容"""
        try:
            with self._connect() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT * FROM download_history 
                    WHERE platform = ? AND content_type = ? AND content_id = ?
                    ORDER BY created_at DESC LIMIT 1
                """, (platform, content_type, content_id))
                
                row = cursor.fetchone()
                if row:
                    columns = [description[0] for description in cursor.description]
                    return dict(zip(columns, row))
                return None
                
        except Exception as e:
            logger.error(f"❌ 检查下载历史失败: {e}")
            return None
