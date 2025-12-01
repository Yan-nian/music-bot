#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SQLite 配置管理器 - 增强版
支持 Web 配置管理和完整的配置项
"""

import sqlite3
import json
import os
import logging
from pathlib import Path
from typing import Dict, Any, Optional, List
from datetime import datetime

logger = logging.getLogger(__name__)


class ConfigManager:
    """配置管理器 - 使用 SQLite 存储配置"""
    
    # 默认配置
    DEFAULT_CONFIG = {
        # Telegram 配置
        "telegram_bot_token": "",
        "telegram_api_id": "",
        "telegram_api_hash": "",
        "telegram_allowed_users": "",
        "telegram_session_string": "",
        
        # Telegram 通知配置
        "telegram_notify_enabled": True,         # 是否启用 TG 通知
        "telegram_notify_progress": True,        # 是否发送进度通知
        "telegram_notify_complete": True,        # 是否发送完成通知
        "telegram_notify_error": True,           # 是否发送错误通知
        "telegram_progress_interval": 1.0,       # 进度更新间隔（秒）
        "telegram_send_file": False,             # 是否发送文件到 Telegram
        "telegram_file_size_limit": 50,          # 发送文件大小限制（MB）
        
        # 代理配置
        "proxy_enabled": False,
        "proxy_host": "",
        
        # 通用下载配置
        "download_path": "/downloads",
        "auto_download_enabled": True,
        "send_to_telegram": False,  # 是否发送到 Telegram（旧配置，保留兼容）
        
        # 网易云音乐配置
        "netease_enabled": True,
        "netease_download_path": "/downloads/netease",  # 网易云单独下载路径
        "netease_quality": "无损",  # 标准/较高/极高/无损
        "netease_download_lyrics": True,
        "netease_download_cover": True,
        "netease_lyrics_merge": False,
        "netease_dir_format": "{ArtistName}/{AlbumName}",
        "netease_album_folder_format": "{AlbumName}({ReleaseDate})",
        "netease_song_file_format": "{SongName}",
        "netease_cookies": "",
        
        # Apple Music 配置
        "apple_music_enabled": True,
        "apple_music_download_path": "/downloads/apple_music",  # Apple Music 单独下载路径
        "apple_music_quality": "lossless",  # aac/lossless/atmos
        "apple_music_download_lyrics": True,
        "apple_music_download_cover": True,
        "apple_music_region": "cn",
        "apple_music_decrypt_host": "",
        "apple_music_get_host": "",
        "apple_music_cookies": "",
        
        # YouTube Music 配置
        "youtube_music_enabled": True,
        "youtube_music_download_path": "/downloads/youtube_music",  # YouTube Music 单独下载路径
        "youtube_music_quality": "best",  # best/320k/256k/128k
        "youtube_music_format": "m4a",  # m4a/mp3
        "youtube_music_download_cover": True,
        "youtube_music_cookies": "",
        
        # qBittorrent 配置
        "qbittorrent_enabled": False,
        "qbittorrent_host": "",
        "qbittorrent_port": 8080,
        "qbittorrent_username": "",
        "qbittorrent_password": "",
        
        # 日志配置
        "log_level": "INFO",
        "log_to_file": True,
        "log_to_console": True,
    }
    
    def __init__(self, db_path: str = "/app/db/music_bot.db"):
        """
        初始化配置管理器
        
        Args:
            db_path: SQLite 数据库文件路径
        """
        self.db_path = Path(db_path)
        
        # 确保数据库目录存在
        try:
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            logger.warning(f"无法创建数据库目录 {self.db_path.parent}: {e}")
            # 回退到当前目录
            self.db_path = Path("./music_bot.db")
        
        # 初始化数据库
        self._init_database()
    
    def _init_database(self):
        """初始化数据库表"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                
                # 创建配置表
                cursor.execute('''
                    CREATE TABLE IF NOT EXISTS config (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        key TEXT UNIQUE NOT NULL,
                        value TEXT NOT NULL,
                        value_type TEXT DEFAULT 'string',
                        description TEXT,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                ''')
                
                # 创建更新时间触发器
                cursor.execute('''
                    CREATE TRIGGER IF NOT EXISTS update_config_timestamp 
                    AFTER UPDATE ON config
                    BEGIN
                        UPDATE config SET updated_at = CURRENT_TIMESTAMP WHERE id = NEW.id;
                    END
                ''')
                
                # 创建下载历史表
                cursor.execute('''
                    CREATE TABLE IF NOT EXISTS download_history (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        platform TEXT NOT NULL,
                        content_type TEXT NOT NULL,
                        content_id TEXT NOT NULL,
                        title TEXT,
                        artist TEXT,
                        file_path TEXT,
                        file_size INTEGER,
                        quality TEXT,
                        status TEXT DEFAULT 'completed',
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                ''')
                
                # 创建订阅歌单表（用于增量更新）
                cursor.execute('''
                    CREATE TABLE IF NOT EXISTS subscribed_playlists (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        platform TEXT NOT NULL DEFAULT 'netease',
                        playlist_id TEXT NOT NULL,
                        playlist_name TEXT,
                        playlist_url TEXT,
                        auto_download BOOLEAN DEFAULT 1,
                        check_interval INTEGER DEFAULT 3600,
                        last_check_time TIMESTAMP,
                        last_song_count INTEGER DEFAULT 0,
                        total_downloaded INTEGER DEFAULT 0,
                        enabled BOOLEAN DEFAULT 1,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        UNIQUE(platform, playlist_id)
                    )
                ''')
                
                # 创建歌单歌曲记录表（记录已下载的歌曲）
                cursor.execute('''
                    CREATE TABLE IF NOT EXISTS playlist_songs (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        playlist_id TEXT NOT NULL,
                        song_id TEXT NOT NULL,
                        song_name TEXT,
                        artist TEXT,
                        album TEXT,
                        downloaded BOOLEAN DEFAULT 0,
                        download_time TIMESTAMP,
                        fail_reason TEXT,
                        fail_time TIMESTAMP,
                        retry_count INTEGER DEFAULT 0,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        UNIQUE(playlist_id, song_id)
                    )
                ''')
                
                # 创建日志表（用于存储应用日志）
                cursor.execute('''
                    CREATE TABLE IF NOT EXISTS app_logs (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        level TEXT NOT NULL,
                        logger_name TEXT,
                        message TEXT NOT NULL,
                        category TEXT DEFAULT 'general',
                        extra_data TEXT,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                ''')
                
                # 为日志表创建索引
                cursor.execute('CREATE INDEX IF NOT EXISTS idx_logs_timestamp ON app_logs(timestamp)')
                cursor.execute('CREATE INDEX IF NOT EXISTS idx_logs_level ON app_logs(level)')
                cursor.execute('CREATE INDEX IF NOT EXISTS idx_logs_category ON app_logs(category)')
                
                conn.commit()
                
                # 数据库迁移：为旧表添加新列
                self._migrate_database(conn)
                
                logger.info(f"✅ 配置数据库初始化成功: {self.db_path}")
                
                # 如果表为空，插入默认配置
                cursor.execute("SELECT COUNT(*) FROM config")
                count = cursor.fetchone()[0]
                if count == 0:
                    self._insert_default_config(cursor)
                    conn.commit()
                    logger.info("✅ 默认配置已插入数据库")
                    
        except Exception as e:
            logger.error(f"❌ 数据库初始化失败: {e}")
            raise
    
    def _migrate_database(self, conn):
        """数据库迁移：为旧表添加新列"""
        cursor = conn.cursor()
        
        # 定义需要迁移的列 (表名, 列名, 列定义)
        migrations = [
            # playlist_songs 表的新列
            ("playlist_songs", "fail_reason", "TEXT"),
            ("playlist_songs", "fail_time", "TIMESTAMP"),
            ("playlist_songs", "retry_count", "INTEGER DEFAULT 0"),
        ]
        
        for table, column, definition in migrations:
            try:
                # 检查列是否存在
                cursor.execute(f"PRAGMA table_info({table})")
                columns = [row[1] for row in cursor.fetchall()]
                
                if column not in columns:
                    cursor.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
                    logger.info(f"✅ 数据库迁移: 添加列 {table}.{column}")
            except Exception as e:
                logger.warning(f"⚠️ 数据库迁移警告 ({table}.{column}): {e}")
        
        conn.commit()
    
    def _insert_default_config(self, cursor):
        """插入默认配置到数据库"""
        config_descriptions = {
            "telegram_bot_token": "Telegram Bot Token",
            "telegram_api_id": "Telegram API ID",
            "telegram_api_hash": "Telegram API Hash",
            "telegram_allowed_users": "允许使用的用户ID，多个用逗号分隔",
            "telegram_session_string": "Telegram Session String",
            "proxy_enabled": "是否启用代理",
            "proxy_host": "代理服务器地址",
            "download_path": "下载文件保存路径",
            "auto_download_enabled": "是否自动下载",
            "netease_enabled": "启用网易云音乐下载",
            "netease_quality": "网易云音乐下载音质",
            "netease_download_lyrics": "下载歌词",
            "netease_download_cover": "下载封面",
            "netease_lyrics_merge": "合并歌词到音频文件",
            "netease_dir_format": "目录格式",
            "netease_album_folder_format": "专辑文件夹格式",
            "netease_song_file_format": "歌曲文件名格式",
            "netease_cookies": "网易云音乐 Cookies",
            "apple_music_enabled": "启用 Apple Music 下载",
            "apple_music_quality": "Apple Music 下载音质",
            "apple_music_download_lyrics": "下载歌词",
            "apple_music_download_cover": "下载封面",
            "apple_music_region": "Apple Music 地区",
            "apple_music_decrypt_host": "解密服务地址",
            "apple_music_get_host": "获取服务地址",
            "youtube_music_enabled": "启用 YouTube Music 下载",
            "youtube_music_quality": "YouTube Music 下载音质",
            "youtube_music_format": "YouTube Music 输出格式",
            "youtube_music_download_cover": "下载封面",
            "qbittorrent_enabled": "启用 qBittorrent",
            "qbittorrent_host": "qBittorrent 主机地址",
            "qbittorrent_port": "qBittorrent 端口",
            "qbittorrent_username": "qBittorrent 用户名",
            "qbittorrent_password": "qBittorrent 密码",
            "log_level": "日志级别",
            "log_to_file": "日志输出到文件",
            "log_to_console": "日志输出到控制台",
        }
        
        for key, value in self.DEFAULT_CONFIG.items():
            value_type = type(value).__name__
            description = config_descriptions.get(key, "")
            cursor.execute(
                "INSERT INTO config (key, value, value_type, description) VALUES (?, ?, ?, ?)",
                (key, json.dumps(value), value_type, description)
            )
    
    def get_config(self, key: str, default: Any = None) -> Any:
        """获取配置值"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT value, value_type FROM config WHERE key = ?", (key,))
                row = cursor.fetchone()
                
                if row:
                    value, value_type = row
                    return json.loads(value)
                
                return default if default is not None else self.DEFAULT_CONFIG.get(key)
                
        except Exception as e:
            logger.error(f"❌ 获取配置失败 [{key}]: {e}")
            return default if default is not None else self.DEFAULT_CONFIG.get(key)
    
    def set_config(self, key: str, value: Any) -> bool:
        """设置配置值"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                value_type = type(value).__name__
                
                cursor.execute("""
                    INSERT INTO config (key, value, value_type) VALUES (?, ?, ?)
                    ON CONFLICT(key) DO UPDATE SET value = ?, value_type = ?
                """, (key, json.dumps(value), value_type, json.dumps(value), value_type))
                
                conn.commit()
                logger.info(f"✅ 配置已更新: {key}")
                return True
                
        except Exception as e:
            logger.error(f"❌ 设置配置失败 [{key}]: {e}")
            return False
    
    def get_all_config(self) -> Dict[str, Any]:
        """获取所有配置"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT key, value FROM config")
                rows = cursor.fetchall()
                
                config = {}
                for key, value in rows:
                    try:
                        config[key] = json.loads(value)
                    except json.JSONDecodeError:
                        config[key] = value
                
                return config
                
        except Exception as e:
            logger.error(f"❌ 获取所有配置失败: {e}")
            return self.DEFAULT_CONFIG.copy()
    
    def update_config_batch(self, config_dict: Dict[str, Any]) -> bool:
        """批量更新配置"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                
                for key, value in config_dict.items():
                    value_type = type(value).__name__
                    cursor.execute("""
                        INSERT INTO config (key, value, value_type) VALUES (?, ?, ?)
                        ON CONFLICT(key) DO UPDATE SET value = ?, value_type = ?
                    """, (key, json.dumps(value), value_type, json.dumps(value), value_type))
                
                conn.commit()
                logger.info(f"✅ 批量更新配置成功: {len(config_dict)} 项")
                return True
                
        except Exception as e:
            logger.error(f"❌ 批量更新配置失败: {e}")
            return False
    
    def reset_to_default(self) -> bool:
        """重置为默认配置"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute("DELETE FROM config")
                self._insert_default_config(cursor)
                conn.commit()
                logger.info("✅ 配置已重置为默认值")
                return True
                
        except Exception as e:
            logger.error(f"❌ 重置配置失败: {e}")
            return False
    
    def add_download_history(self, platform: str, content_type: str, content_id: str,
                            title: str = None, artist: str = None, file_path: str = None,
                            file_size: int = None, quality: str = None) -> bool:
        """添加下载历史记录"""
        try:
            with sqlite3.connect(self.db_path) as conn:
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
            with sqlite3.connect(self.db_path) as conn:
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
            with sqlite3.connect(self.db_path) as conn:
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

    def get_config_by_category(self, category: str) -> Dict[str, Any]:
        """按类别获取配置"""
        all_config = self.get_all_config()
        prefix = f"{category}_"
        return {k: v for k, v in all_config.items() if k.startswith(prefix)}
    
    def export_config(self) -> str:
        """导出配置为 JSON 字符串"""
        config = self.get_all_config()
        return json.dumps(config, ensure_ascii=False, indent=2)
    
    def import_config(self, config_json: str) -> bool:
        """从 JSON 字符串导入配置"""
        try:
            config = json.loads(config_json)
            return self.update_config_batch(config)
        except json.JSONDecodeError as e:
            logger.error(f"❌ JSON 解析失败: {e}")
            return False
    
    # ==================== 订阅歌单管理 ====================
    
    def add_subscribed_playlist(self, playlist_id: str, playlist_name: str = None,
                                playlist_url: str = None, platform: str = 'netease',
                                check_interval: int = 3600) -> bool:
        """添加订阅歌单"""
        try:
            with sqlite3.connect(self.db_path) as conn:
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
    
    def remove_subscribed_playlist(self, playlist_id: str, platform: str = 'netease') -> bool:
        """移除订阅歌单"""
        try:
            with sqlite3.connect(self.db_path) as conn:
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
                return True
        except Exception as e:
            logger.error(f"❌ 移除订阅歌单失败: {e}")
            return False
    
    def get_subscribed_playlists(self, platform: str = None, enabled_only: bool = False) -> List[Dict[str, Any]]:
        """获取订阅歌单列表"""
        try:
            with sqlite3.connect(self.db_path) as conn:
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
            with sqlite3.connect(self.db_path) as conn:
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
            
            with sqlite3.connect(self.db_path) as conn:
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
    
    def add_playlist_song(self, playlist_id: str, song_id: str, song_name: str = None,
                         artist: str = None, album: str = None, downloaded: bool = False,
                         fail_reason: str = None) -> bool:
        """添加歌单歌曲记录"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                download_time = datetime.now().isoformat() if downloaded else None
                fail_time = datetime.now().isoformat() if fail_reason else None
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
            with sqlite3.connect(self.db_path) as conn:
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
            with sqlite3.connect(self.db_path) as conn:
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
            with sqlite3.connect(self.db_path) as conn:
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
            with sqlite3.connect(self.db_path) as conn:
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
            with sqlite3.connect(self.db_path) as conn:
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
    
    def get_failed_songs(self, playlist_id: str) -> List[Dict[str, Any]]:
        """获取歌单中下载失败的歌曲"""
        try:
            with sqlite3.connect(self.db_path) as conn:
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
            with sqlite3.connect(self.db_path) as conn:
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
            with sqlite3.connect(self.db_path) as conn:
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
            with sqlite3.connect(self.db_path) as conn:
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
    
    # ==================== 日志管理 ====================
    
    def add_log(self, level: str, message: str, logger_name: str = None, 
                category: str = 'general', extra_data: Dict = None) -> bool:
        """添加日志记录"""
        try:
            with sqlite3.connect(self.db_path) as conn:
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
    
    def get_logs(self, limit: int = 100, offset: int = 0, level: str = None,
                 category: str = None, search: str = None, 
                 start_time: str = None, end_time: str = None) -> List[Dict]:
        """获取日志列表"""
        try:
            with sqlite3.connect(self.db_path) as conn:
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
            with sqlite3.connect(self.db_path) as conn:
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
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT DISTINCT category FROM app_logs ORDER BY category")
                return [row[0] for row in cursor.fetchall() if row[0]]
        except Exception as e:
            logger.error(f"❌ 获取日志类别失败: {e}")
            return []
    
    def clear_logs(self, before_date: str = None, category: str = None) -> int:
        """清理日志"""
        try:
            with sqlite3.connect(self.db_path) as conn:
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


# 全局配置管理器实例
_config_manager: Optional[ConfigManager] = None


def get_config_manager(db_path: str = None) -> ConfigManager:
    """获取配置管理器单例"""
    global _config_manager
    if _config_manager is None:
        if db_path:
            _config_manager = ConfigManager(db_path)
        else:
            _config_manager = ConfigManager()
    return _config_manager
