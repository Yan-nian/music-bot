# 音乐下载器模块
from .base import BaseDownloader
from .netease import NeteaseDownloader
from .apple_music import AppleMusicDownloader
from .metadata import MusicMetadataManager

__all__ = [
    'BaseDownloader',
    'NeteaseDownloader',
    'AppleMusicDownloader',
    'MusicMetadataManager',
]
