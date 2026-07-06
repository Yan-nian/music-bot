#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
数据库日志处理器
将日志写入 SQLite 数据库，支持按类别分类
"""

import json
import logging
import queue
import re
import threading
from typing import Optional


class DatabaseLogHandler(logging.Handler):
    """将日志写入数据库的 Handler（异步批量写）

    日志可能被高频调用（下载进度等）。若每次 emit 都同步开 SQLite 连接
    写入，会与业务线程争抢数据库锁、拖垮响应。改为：emit 仅投递到内存
    队列，由单一后台线程批量写入，既减少锁竞争又不阻塞调用方。队列满时
    丢弃日志，保证业务线程不被阻塞。
    """

    _BATCH_SIZE = 50
    _FLUSH_INTERVAL = 2.0
    _QUEUE_MAXSIZE = 2000
    _CLEANUP_EVERY = 1000  # 每写入 N 条触发一次老日志清理

    def __init__(self, config_manager):
        super().__init__()
        self.config_manager = config_manager
        self._queue: "queue.Queue" = queue.Queue(maxsize=self._QUEUE_MAXSIZE)
        self._stop_event = threading.Event()
        self._dropped = 0
        self._written_since_cleanup = 0

        # 类别关键词映射
        self.category_patterns = {
            'metadata': [
                r'元数据', r'metadata', r'mutagen', r'eyed3', r'tinytag',
                r'标签', r'tag', r'封面', r'cover', r'歌词', r'lyric',
                r'专辑', r'album', r'艺术家', r'artist', r'标题', r'title'
            ],
            'download': [
                r'下载', r'download', r'获取', r'fetch', r'保存', r'save',
                r'歌曲', r'song', r'音乐', r'music', r'文件', r'file'
            ],
            'telegram': [
                r'telegram', r'tg', r'bot', r'消息', r'message', r'通知'
            ],
            'playlist': [
                r'歌单', r'playlist', r'订阅', r'subscribe', r'同步', r'sync'
            ],
        }

        self._worker = threading.Thread(
            target=self._flush_loop, name="db-log-writer", daemon=True
        )
        self._worker.start()

    def _detect_category(self, message: str, logger_name: str) -> str:
        """根据消息内容和 logger 名称检测类别"""
        message_lower = message.lower()
        logger_lower = (logger_name or '').lower()

        # 根据 logger 名称判断
        if 'metadata' in logger_lower:
            return 'metadata'
        if 'telegram' in logger_lower or 'tg_' in logger_lower:
            return 'telegram'
        if 'download' in logger_lower:
            return 'download'

        # 根据消息内容判断
        for category, patterns in self.category_patterns.items():
            for pattern in patterns:
                if re.search(pattern, message_lower):
                    return category

        return 'general'

    def emit(self, record: logging.LogRecord):
        """投递日志到队列（非阻塞，绝不卡调用方）"""
        try:
            message = self.format(record)
            category = self._detect_category(message, record.name)
            extra_data = getattr(record, 'extra_data', None)
            self._queue.put_nowait(
                (record.levelname, message, record.name, category, extra_data)
            )
        except queue.Full:
            self._dropped += 1
        except Exception:
            pass

    def _flush_loop(self):
        """后台线程：批量写库"""
        buffer = []
        while not self._stop_event.is_set():
            try:
                item = self._queue.get(timeout=self._FLUSH_INTERVAL)
                buffer.append(item)
                while len(buffer) < self._BATCH_SIZE:
                    try:
                        buffer.append(self._queue.get_nowait())
                    except queue.Empty:
                        break
                self._write_batch(buffer)
                buffer.clear()
            except queue.Empty:
                if buffer:
                    self._write_batch(buffer)
                    buffer.clear()
            except Exception:
                buffer.clear()
        if buffer:
            try:
                self._write_batch(buffer)
            except Exception:
                pass

    def _write_batch(self, batch):
        """单连接批量写入，并按需清理老日志"""
        if not batch:
            return
        try:
            cm = self.config_manager
            with cm._connect() as conn:
                cur = conn.cursor()
                rows = [
                    (lvl, msg, name, cat,
                     json.dumps(ex, ensure_ascii=False) if ex else None)
                    for (lvl, msg, name, cat, ex) in batch
                ]
                cur.executemany(
                    "INSERT INTO app_logs (level, logger_name, message, category, extra_data) "
                    "VALUES (?, ?, ?, ?, ?)",
                    rows
                )
                conn.commit()
            self._written_since_cleanup += len(batch)
            if self._written_since_cleanup >= self._CLEANUP_EVERY:
                self._written_since_cleanup = 0
                try:
                    cm.cleanup_old_logs()
                except Exception:
                    pass
        except Exception:
            pass

    def stop(self):
        """停止后台线程"""
        self._stop_event.set()


def setup_database_logging(config_manager, level: int = logging.INFO):
    """设置数据库日志记录
    
    注意：只添加到根 logger，避免日志重复记录
    子 logger 会自动向上传播日志到根 logger
    """
    # 检查是否已经添加过数据库 handler，避免重复添加
    root_logger = logging.getLogger()
    
    # 检查是否已存在 DatabaseLogHandler
    for handler in root_logger.handlers:
        if isinstance(handler, DatabaseLogHandler):
            return handler  # 已存在，直接返回
    
    # 创建数据库 handler
    db_handler = DatabaseLogHandler(config_manager)
    db_handler.setLevel(level)
    
    # 设置格式
    formatter = logging.Formatter('%(message)s')
    db_handler.setFormatter(formatter)
    
    # 只添加到根 logger
    # 子 logger 默认 propagate=True，会自动传播到根 logger
    root_logger.addHandler(db_handler)
    
    return db_handler


class MetadataLogger:
    """元数据专用 Logger，方便追踪元数据相关问题"""
    
    def __init__(self, config_manager):
        self.config_manager = config_manager
        self.logger = logging.getLogger('downloaders.metadata')
    
    def log(self, level: str, message: str, file_path: str = None, 
            metadata: dict = None, error: str = None):
        """记录元数据日志"""
        extra_data = {}
        
        if file_path:
            extra_data['file_path'] = str(file_path)
        if metadata:
            extra_data['metadata'] = metadata
        if error:
            extra_data['error'] = error
        
        # 写入数据库
        self.config_manager.add_log(
            level=level.upper(),
            message=message,
            logger_name='metadata',
            category='metadata',
            extra_data=extra_data if extra_data else None
        )
        
        # 同时写入标准日志
        log_func = getattr(self.logger, level.lower(), self.logger.info)
        log_func(message)
    
    def info(self, message: str, **kwargs):
        self.log('INFO', message, **kwargs)
    
    def warning(self, message: str, **kwargs):
        self.log('WARNING', message, **kwargs)
    
    def error(self, message: str, **kwargs):
        self.log('ERROR', message, **kwargs)
    
    def debug(self, message: str, **kwargs):
        self.log('DEBUG', message, **kwargs)


# 全局元数据 logger 实例
_metadata_logger: Optional[MetadataLogger] = None


def get_metadata_logger(config_manager=None) -> MetadataLogger:
    """获取元数据 logger 单例"""
    global _metadata_logger
    if _metadata_logger is None and config_manager:
        _metadata_logger = MetadataLogger(config_manager)
    return _metadata_logger
