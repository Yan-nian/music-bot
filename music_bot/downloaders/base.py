#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
基础下载器类
所有音乐下载器的基类
"""

import os
import re
import logging
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Optional, Dict, Any, Callable

logger = logging.getLogger(__name__)


class BaseDownloader(ABC):
    """音乐下载器基类"""
    
    def __init__(self, config_manager=None):
        """
        初始化下载器
        
        Args:
            config_manager: 配置管理器实例
        """
        self.config_manager = config_manager
        self.download_stats = {
            'total_files': 0,
            'downloaded_files': 0,
            'total_size': 0,
            'downloaded_songs': []
        }
    
    @abstractmethod
    def download_song(self, song_id: str, download_dir: str, 
                     quality: str = 'standard',
                     progress_callback: Optional[Callable] = None) -> Dict[str, Any]:
        """
        下载单曲
        
        Args:
            song_id: 歌曲ID
            download_dir: 下载目录
            quality: 音质
            progress_callback: 进度回调函数
            
        Returns:
            下载结果字典
        """
        pass
    
    @abstractmethod
    def download_album(self, album_id: str, download_dir: str,
                      quality: str = 'standard',
                      progress_callback: Optional[Callable] = None) -> Dict[str, Any]:
        """
        下载专辑
        
        Args:
            album_id: 专辑ID
            download_dir: 下载目录
            quality: 音质
            progress_callback: 进度回调函数
            
        Returns:
            下载结果字典
        """
        pass
    
    @abstractmethod
    def download_playlist(self, playlist_id: str, download_dir: str,
                         quality: str = 'standard',
                         progress_callback: Optional[Callable] = None) -> Dict[str, Any]:
        """
        下载歌单/播放列表
        
        Args:
            playlist_id: 歌单ID
            download_dir: 下载目录
            quality: 音质
            progress_callback: 进度回调函数
            
        Returns:
            下载结果字典
        """
        pass
    
    @abstractmethod
    def parse_url(self, url: str) -> Optional[Dict[str, Any]]:
        """
        解析URL，提取类型和ID
        
        Args:
            url: 音乐平台URL
            
        Returns:
            解析结果字典，包含 type 和 id
        """
        pass
    
    @abstractmethod
    def is_supported_url(self, url: str) -> bool:
        """
        检查URL是否支持
        
        Args:
            url: 待检查的URL
            
        Returns:
            是否支持
        """
        pass
    
    def clean_filename(self, filename: str) -> str:
        """
        清理文件名中的非法字符
        
        Args:
            filename: 原始文件名
            
        Returns:
            清理后的文件名
        """
        if not filename:
            return "unknown"
        
        # 移除或替换非法字符
        illegal_chars = ['<', '>', ':', '"', '/', '\\', '|', '?', '*']
        for char in illegal_chars:
            filename = filename.replace(char, '_')
        
        # 移除首尾空格和点
        filename = filename.strip(' .')
        
        # 限制长度
        if len(filename) > 200:
            filename = filename[:200]
        
        return filename or "unknown"
    
    def ensure_dir(self, dir_path: str) -> Path:
        """
        确保目录存在
        
        Args:
            dir_path: 目录路径
            
        Returns:
            Path对象
        """
        path = Path(dir_path)
        path.mkdir(parents=True, exist_ok=True)
        return path
    
    def get_config(self, key: str, default: Any = None) -> Any:
        """
        从配置管理器获取配置
        
        Args:
            key: 配置键
            default: 默认值
            
        Returns:
            配置值
        """
        if self.config_manager:
            return self.config_manager.get_config(key, default)
        return default
    
    def format_size(self, size_bytes: int) -> str:
        """
        格式化文件大小
        
        Args:
            size_bytes: 字节数
            
        Returns:
            格式化的大小字符串
        """
        if size_bytes < 1024:
            return f"{size_bytes} B"
        elif size_bytes < 1024 * 1024:
            return f"{size_bytes / 1024:.2f} KB"
        elif size_bytes < 1024 * 1024 * 1024:
            return f"{size_bytes / (1024 * 1024):.2f} MB"
        else:
            return f"{size_bytes / (1024 * 1024 * 1024):.2f} GB"
    
    def reset_stats(self):
        """重置下载统计"""
        self.download_stats = {
            'total_files': 0,
            'downloaded_files': 0,
            'total_size': 0,
            'downloaded_songs': []
        }
