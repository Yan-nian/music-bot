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
                
                conn.commit()
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
