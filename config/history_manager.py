#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
下载历史管理 Mixin - 处理下载历史记录
"""

import sqlite3
import logging
from typing import Dict, List, Any, Optional

logger = logging.getLogger(__name__)


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
