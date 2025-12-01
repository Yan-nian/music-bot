#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
数据库日志处理器
将日志写入 SQLite 数据库，支持按类别分类
"""

import logging
import re
from typing import Optional


class DatabaseLogHandler(logging.Handler):
    """将日志写入数据库的 Handler"""
    
    def __init__(self, config_manager):
        super().__init__()
        self.config_manager = config_manager
        
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
        """写入日志记录"""
        try:
            # 格式化消息
            message = self.format(record)
            
            # 检测类别
            category = self._detect_category(message, record.name)
            
            # 构建额外数据
            extra_data = None
            if hasattr(record, 'extra_data'):
                extra_data = record.extra_data
            
            # 写入数据库
            self.config_manager.add_log(
                level=record.levelname,
                message=message,
                logger_name=record.name,
                category=category,
                extra_data=extra_data
            )
        except Exception:
            # 避免日志处理异常导致程序崩溃
            pass


def setup_database_logging(config_manager, level: int = logging.INFO):
    """设置数据库日志记录"""
    # 创建数据库 handler
    db_handler = DatabaseLogHandler(config_manager)
    db_handler.setLevel(level)
    
    # 设置格式
    formatter = logging.Formatter('%(message)s')
    db_handler.setFormatter(formatter)
    
    # 添加到根 logger
    root_logger = logging.getLogger()
    root_logger.addHandler(db_handler)
    
    # 同时添加到关键模块的 logger
    loggers_to_add = [
        'music_bot',
        'downloaders.metadata',
        'downloaders.netease',
        'downloaders.apple_music',
        'downloaders.youtube_music',
        'web.app',
        'web.tg_setup',
    ]
    
    for logger_name in loggers_to_add:
        logger = logging.getLogger(logger_name)
        logger.addHandler(db_handler)
    
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
