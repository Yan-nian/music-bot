#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SQLite 配置管理器 - 增强版
支持 Web 配置管理和完整的配置项

已重构：将不同职责拆分到 config/ 目录下的独立模块
- config/playlist_manager.py: 歌单管理
- config/history_manager.py: 下载历史
- config/log_manager.py: 日志管理
"""

import sqlite3
import json
import os
import time
import logging
import threading
from pathlib import Path
from typing import Dict, Any, Optional, List
from datetime import datetime

# 导入 Mixin 类
from config.playlist_manager import PlaylistManager
from config.history_manager import HistoryManager
from config.log_manager import LogManager

logger = logging.getLogger(__name__)


def _resolve_default_db_path() -> str:
    """决定默认数据库位置。

    老的默认值是写死的 `/app/db/music_bot.db`（Docker 镜像里的路径）。
    在 Windows / macOS 上直接跑源码时，`/app/db` 会被解释成 `C:\\app\\db`
    （或根目录下新建 `/app`），数据库就落在一个既意外又难找的地方，
    而且和 `--db-path` 指定的库不是同一个，表现为「配置改了没生效」。

    现在的规则：
    1. `MUSIC_BOT_DB_PATH` 环境变量优先；
    2. 容器内（存在 `/.dockerenv`）沿用 `/app/db/music_bot.db`，兼容老部署的 volume 挂载；
    3. 本地跑源码时放到代码目录下的 `db/music_bot.db`；
    4. 如果新位置还没有库、但老位置的库存在，就继续用老的，
       免得升级后用户发现「配置和下载记录全没了」。
    """
    env_path = os.environ.get('MUSIC_BOT_DB_PATH')
    if env_path:
        return env_path

    in_docker = os.path.exists('/.dockerenv') or (
        os.name != 'nt' and os.path.isdir('/app/db')
    )
    if in_docker:
        return '/app/db/music_bot.db'

    local_default = Path(__file__).resolve().parent / 'db' / 'music_bot.db'
    legacy = Path('/app/db/music_bot.db')
    try:
        if not local_default.exists() and legacy.exists():
            return str(legacy)
    except OSError:
        pass
    return str(local_default)


DEFAULT_DB_PATH = _resolve_default_db_path()

class ConfigManager(PlaylistManager, HistoryManager, LogManager):
    """配置管理器 - 使用 SQLite 存储配置
    
    通过多继承混入以下功能：
    - PlaylistManager: 歌单订阅管理
    - HistoryManager: 下载历史管理
    - LogManager: 日志管理
    """
    
    # 默认配置
    DEFAULT_CONFIG = {
        # Telegram 配置
        "telegram_bot_token": "",
        "telegram_api_id": "",
        "telegram_api_hash": "",
        "telegram_allowed_users": "",
        "telegram_session_string": "",
        
        # Telegram 通知配置
        "telegram_notify_enabled": True,
        "telegram_notify_progress": True,
        "telegram_notify_complete": True,
        "telegram_notify_error": True,
        "telegram_progress_interval": 1.0,
        "telegram_send_file": False,
        "telegram_file_size_limit": 50,
        
        # 代理配置
        "proxy_enabled": False,
        "proxy_host": "",
        
        # 通用下载配置
        "download_path": "/downloads",
        "auto_download_enabled": True,
        "send_to_telegram": False,
        
        # 网易云音乐配置
        "netease_enabled": True,
        "netease_download_path": "/downloads/netease",
        "netease_quality": "无损",
        "netease_download_lyrics": True,
        "netease_download_cover": True,
        "netease_lyrics_merge": False,
        "netease_dir_format": "{ArtistName}/{AlbumName}",
        "netease_album_folder_format": "{AlbumName}({ReleaseDate})",
        "netease_song_file_format": "{SongName}",
        "netease_cookies": "",
        # 登录态 / Cookie 自动维护
        "netease_auto_refresh_enabled": True,
        "netease_cookie_check_interval": 1800,
        "netease_cookie_notify_on_expire": True,
        "netease_cookie_meta": {},
        
        # Apple Music 配置
        "apple_music_enabled": True,
        "apple_music_download_path": "/downloads/apple_music",
        "apple_music_quality": "lossless",
        "apple_music_download_lyrics": True,
        "apple_music_download_cover": True,
        "apple_music_region": "cn",
        "apple_music_decrypt_host": "",
        "apple_music_get_host": "",
        "apple_music_cookies": "",

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
        
        # 下载队列配置
        "download_max_concurrent": 3,
        "download_max_retries": 3,
        "download_retry_delay_base": 2.0,
        "download_timeout": 1800,
        "handler_timeout": 60,

        # 管理后台的公网/局域网地址，填了之后 TG 里会出现「打开管理后台」按钮
        "web_ui_url": "",
    }
    
    def __init__(self, db_path: str = None):
        """
        初始化配置管理器

        Args:
            db_path: SQLite 数据库文件路径；为空时使用默认路径（见 DEFAULT_DB_PATH）
        """
        self.db_path = Path(db_path or DEFAULT_DB_PATH)

        # 确保数据库目录存在
        try:
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            logger.warning(f"无法创建数据库目录 {self.db_path.parent}: {e}")
            # 回退到当前目录
            self.db_path = Path("./music_bot.db")

        # 统一成绝对路径，避免不同进程 CWD 不同导致指向两个库
        try:
            self.db_path = self.db_path.resolve()
        except OSError:
            pass
        
        self._cache: Dict[str, Any] = {}
        self._cache_at: float = 0.0
        self._cache_ttl: float = 3.0
        self._cache_lock = threading.RLock()

        # 初始化数据库
        self._init_database()

    def _refresh_cache(self, force: bool = False) -> None:
        """把 config 表整体读进内存。

        读取配置是热路径（每条消息、每次下载都会调），原来每次都要新开
        一个 SQLite 连接 + 3 条 PRAGMA，开销远大于数据本身。这里按 TTL
        做整体刷新：同一个进程内直接命中内存；跨进程（--web-only 与
        --bot-only 分开跑）时最多 stale 一个 TTL 周期。
        """
        now = time.time()
        with self._cache_lock:
            if not force and self._cache and (now - self._cache_at) < self._cache_ttl:
                return
            try:
                with self._connect() as conn:
                    cursor = conn.cursor()
                    cursor.execute("SELECT key, value FROM config")
                    data: Dict[str, Any] = {}
                    for key, value in cursor.fetchall():
                        try:
                            data[key] = json.loads(value)
                        except (TypeError, json.JSONDecodeError):
                            data[key] = value
                self._cache = data
                self._cache_at = now
            except Exception as e:
                logger.error(f"❌ 加载配置缓存失败: {e}")

    def invalidate_cache(self) -> None:
        """使缓存立即失效（写入配置后调用）"""
        with self._cache_lock:
            self._cache_at = 0.0

    def _connect(self):
        """统一数据库连接：开启 WAL、设置忙等待与同步策略
        """
        conn = sqlite3.connect(self.db_path, timeout=30)
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.execute("PRAGMA busy_timeout=30000")
        except sqlite3.Error as e:
            logger.warning(f"设置数据库 PRAGMA 失败: {e}")
        return conn

    def _init_database(self):
        """初始化数据库表"""
        try:
            with self._connect() as conn:
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
                
                # 创建订阅歌单表
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
                
                # 创建歌单歌曲记录表
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
                
                # 创建日志表
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

            # 启动时清理老日志
            try:
                self.cleanup_old_logs()
            except Exception:
                pass

        except Exception as e:
            logger.error(f"❌ 数据库初始化失败: {e}")
            raise
    
    def _migrate_database(self, conn):
        """数据库迁移：为旧表添加新列"""
        cursor = conn.cursor()
        
        # 定义需要迁移的列
        migrations = [
            ("playlist_songs", "fail_reason", "TEXT"),
            ("playlist_songs", "fail_time", "TIMESTAMP"),
            ("playlist_songs", "retry_count", "INTEGER DEFAULT 0"),
        ]
        
        for table, column, definition in migrations:
            try:
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
        """获取配置值（优先读内存缓存）"""
        try:
            self._refresh_cache()
            with self._cache_lock:
                if key in self._cache:
                    return self._cache[key]
            return default if default is not None else self.DEFAULT_CONFIG.get(key)
        except Exception as e:
            logger.error(f"❌ 获取配置失败 [{key}]: {e}")
            return default if default is not None else self.DEFAULT_CONFIG.get(key)

    def get(self, key: str, default: Any = None) -> Any:
        """dict 风格别名。

        早期下载器（如 apple_music.py）按 `config_manager.get(key, default)`
        调用，而 ConfigManager 只有 get_config，导致 __init__ 里直接抛
        AttributeError 被上层吞掉，Apple Music 下载器因此从未初始化成功。
        """
        return self.get_config(key, default)

    def get_audio_roots(self) -> List[str]:
        """收集所有可能的曲库目录（去重由扫描器负责）。

        平台的下载目录可能被配置成 `download_path` 的子目录，也可能完全在别处；
        三个都交给扫描器，让它按绝对路径去重。
        """
        keys = (
            'netease_download_path',
            'apple_music_download_path',
            'download_path',
        )
        roots: List[str] = []
        for key in keys:
            value = self.get_config(key, '')
            if value and str(value).strip():
                roots.append(str(value).strip())
        return roots

    def get_library_stats(self, ttl: float = 60.0, force: bool = False) -> Dict[str, Any]:
        """曲库容量（扫盘）。见 config/library_stats.py 的说明。"""
        from config.library_stats import scan_audio_size
        return scan_audio_size(self.get_audio_roots(), ttl=ttl, force=force)

    def set_config(self, key: str, value: Any) -> bool:
        """设置配置值"""
        try:
            with self._connect() as conn:
                cursor = conn.cursor()
                value_type = type(value).__name__
                
                cursor.execute("""
                    INSERT INTO config (key, value, value_type) VALUES (?, ?, ?)
                    ON CONFLICT(key) DO UPDATE SET value = ?, value_type = ?
                """, (key, json.dumps(value), value_type, json.dumps(value), value_type))
                
                conn.commit()
                logger.info(f"✅ 配置已更新: {key}")
            with self._cache_lock:
                self._cache[key] = value
            return True
                
        except Exception as e:
            logger.error(f"❌ 设置配置失败 [{key}]: {e}")
            self.invalidate_cache()
            return False
    
    def get_all_config(self) -> Dict[str, Any]:
        """获取所有配置（优先读内存缓存）"""
        try:
            self._refresh_cache()
            with self._cache_lock:
                if self._cache:
                    return dict(self._cache)
            return self.DEFAULT_CONFIG.copy()
        except Exception as e:
            logger.error(f"❌ 获取所有配置失败: {e}")
            return self.DEFAULT_CONFIG.copy()
    
    def update_config_batch(self, config_dict: Dict[str, Any]) -> bool:
        """批量更新配置"""
        try:
            with self._connect() as conn:
                cursor = conn.cursor()
                
                for key, value in config_dict.items():
                    # 跳过敏感配置的掩码占位值
                    if value == '******':
                        continue
                    value_type = type(value).__name__
                    cursor.execute("""
                        INSERT INTO config (key, value, value_type) VALUES (?, ?, ?)
                        ON CONFLICT(key) DO UPDATE SET value = ?, value_type = ?
                    """, (key, json.dumps(value), value_type, json.dumps(value), value_type))
                
                conn.commit()
                logger.info(f"✅ 批量更新配置成功: {len(config_dict)} 项")
            self.invalidate_cache()
            return True
                
        except Exception as e:
            logger.error(f"❌ 批量更新配置失败: {e}")
            return False
    
    def reset_to_default(self) -> bool:
        """重置为默认配置"""
        try:
            with self._connect() as conn:
                cursor = conn.cursor()
                cursor.execute("DELETE FROM config")
                self._insert_default_config(cursor)
                conn.commit()
                logger.info("✅ 配置已重置为默认值")
            self.invalidate_cache()
            return True
                
        except Exception as e:
            logger.error(f"❌ 重置配置失败: {e}")
            return False
    
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
    """获取配置管理器单例。

    注意：单例一旦建立，后续再传一个**不同**的 db_path 会被忽略——这正是
    `--db-path` 曾经完全失效的原因（先有人用默认路径建了实例，Web 再传
    自定义路径时被静默丢弃，于是 Web 读写的是另一个库，表现为「配置改了
    却不生效」「下载记录看不到」）。这里显式检测路径变化并重建单例。
    """
    global _config_manager
    if _config_manager is None:
        _config_manager = ConfigManager(db_path) if db_path else ConfigManager()
        return _config_manager

    if db_path:
        try:
            want = Path(db_path).resolve()
        except OSError:
            want = Path(db_path)
        if _config_manager.db_path != want:
            logger.warning(
                "配置管理器数据库路径变化：%s -> %s，重建单例",
                _config_manager.db_path, want,
            )
            _config_manager = ConfigManager(db_path)
    return _config_manager


def reset_config_manager() -> None:
    """丢弃单例（测试 / 重新初始化时用）。"""
    global _config_manager
    _config_manager = None
