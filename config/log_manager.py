#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
日志管理 Mixin - 处理应用日志
"""

import sqlite3
import logging
import json
from typing import Dict, List, Any, Optional

logger = logging.getLogger(__name__)


class LogManager:
    """日志管理 Mixin"""
    
    def add_log(self, level: str, message: str, logger_name: str = None, 
                category: str = 'general', extra_data: Dict = None) -> bool:
        """添加日志记录"""
        try:
            with self._connect() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    INSERT INTO app_logs (level, logger_name, message, category, extra_data)
                    VALUES (?, ?, ?, ?, ?)
                """, (level, logger_name, message, category, 
                      json.dumps(extra_data) if extra_data else None))
                conn.commit()
                return True
        except Exception as e:
            # 不用 logger，避免递归
            print(f"添加日志失败: {e}")
            return False

    def cleanup_old_logs(self, keep_days: int = 30, keep_max: int = 100000) -> int:
        """清理老日志，防止 app_logs 无限增长拖垮长跑。

        - 删除 keep_days 天前的日志
        - 若剩余仍超过 keep_max，删除最旧的至 keep_max 条
        """
        try:
            with self._connect() as conn:
                cur = conn.cursor()
                cur.execute(
                    "DELETE FROM app_logs WHERE timestamp < datetime('now', ?)",
                    (f'-{keep_days} days',)
                )
                cur.execute(
                    "DELETE FROM app_logs WHERE id NOT IN "
                    "(SELECT id FROM app_logs ORDER BY id DESC LIMIT ?)",
                    (keep_max,)
                )
                conn.commit()
                return cur.rowcount
        except Exception:
            return 0

    def get_logs(self, limit: int = 100, offset: int = 0, level: str = None,
                 category: str = None, search: str = None, 
                 start_time: str = None, end_time: str = None) -> List[Dict]:
        """获取日志列表"""
        try:
            with self._connect() as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()
                
                query = "SELECT * FROM app_logs WHERE 1=1"
                params = []
                
                if level:
                    query += " AND level = ?"
                    params.append(level)
                
                if category:
                    query += " AND category = ?"
                    params.append(category)
                
                if search:
                    query += " AND message LIKE ?"
                    params.append(f"%{search}%")
                
                if start_time:
                    query += " AND timestamp >= ?"
                    params.append(start_time)
                
                if end_time:
                    query += " AND timestamp <= ?"
                    params.append(end_time)
                
                query += " ORDER BY timestamp DESC LIMIT ? OFFSET ?"
                params.extend([limit, offset])
                
                cursor.execute(query, params)
                rows = cursor.fetchall()
                
                return [dict(row) for row in rows]
        except Exception as e:
            logger.error(f"❌ 获取日志失败: {e}")
            return []
    
    def get_log_count(self, level: str = None, category: str = None, 
                      search: str = None, start_time: str = None, 
                      end_time: str = None) -> int:
        """获取日志数量"""
        try:
            with self._connect() as conn:
                cursor = conn.cursor()
                
                query = "SELECT COUNT(*) FROM app_logs WHERE 1=1"
                params = []
                
                if level:
                    query += " AND level = ?"
                    params.append(level)
                
                if category:
                    query += " AND category = ?"
                    params.append(category)
                
                if search:
                    query += " AND message LIKE ?"
                    params.append(f"%{search}%")
                
                if start_time:
                    query += " AND timestamp >= ?"
                    params.append(start_time)
                
                if end_time:
                    query += " AND timestamp <= ?"
                    params.append(end_time)
                
                cursor.execute(query, params)
                return cursor.fetchone()[0]
        except Exception as e:
            logger.error(f"❌ 获取日志数量失败: {e}")
            return 0
    
    def get_log_categories(self) -> List[str]:
        """获取所有日志类别"""
        try:
            with self._connect() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT DISTINCT category FROM app_logs ORDER BY category")
                return [row[0] for row in cursor.fetchall() if row[0]]
        except Exception as e:
            logger.error(f"❌ 获取日志类别失败: {e}")
            return []
    
    def clear_logs(self, before_date: str = None, category: str = None) -> int:
        """清理日志"""
        try:
            with self._connect() as conn:
                cursor = conn.cursor()
                
                query = "DELETE FROM app_logs WHERE 1=1"
                params = []
                
                if before_date:
                    query += " AND timestamp < ?"
                    params.append(before_date)
                
                if category:
                    query += " AND category = ?"
                    params.append(category)
                
                cursor.execute(query, params)
                deleted = cursor.rowcount
                conn.commit()
                return deleted
        except Exception as e:
            logger.error(f"❌ 清理日志失败: {e}")
            return 0
    
    def export_logs(self, category: str = None, level: str = None,
                    start_time: str = None, end_time: str = None,
                    format: str = 'json') -> str:
        """导出日志"""
        logs = self.get_logs(
            limit=10000,  # 导出时限制最大条数
            category=category,
            level=level,
            start_time=start_time,
            end_time=end_time
        )
        
        if format == 'json':
            return json.dumps(logs, ensure_ascii=False, indent=2)
        elif format == 'csv':
            import csv
            import io
            output = io.StringIO()
            if logs:
                writer = csv.DictWriter(output, fieldnames=logs[0].keys())
                writer.writeheader()
                writer.writerows(logs)
            return output.getvalue()
        elif format == 'txt':
            lines = []
            for log in logs:
                line = f"[{log.get('timestamp', '')}] [{log.get('level', '')}] [{log.get('category', '')}] {log.get('message', '')}"
                lines.append(line)
            return '\n'.join(lines)
        else:
            return json.dumps(logs, ensure_ascii=False, indent=2)
