#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
网易云音乐下载器包

已重构：将 API 调用拆分到 api.py
"""

from .api import NeteaseAPI

# 为了向后兼容，保留原来的导入路径
# 使用方仍可以从 downloaders.netease 导入 NeteaseDownloader
from .downloader import NeteaseDownloader

__all__ = ['NeteaseAPI', 'NeteaseDownloader']
