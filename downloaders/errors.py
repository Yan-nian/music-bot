#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""下载器公共异常。

`DownloadCancelled` 存在的唯一目的：让"用户点了取消"这件事能从下载线程
一路冒泡回下载队列。

在此之前取消只是个标记位——`DownloadQueue.cancel()` 把 `task.cancelled`
置 True，但真正在跑的是 `asyncio.to_thread(...)` 里的同步下载，没人看这个
标记，于是用户点了取消，进度条照旧往前跑，直到整张专辑下完。现在下载
循环每写一个 chunk 就查一次取消标记，命中就抛这个异常。
"""


class DownloadCancelled(Exception):
    """用户主动取消。不是错误，不重试、不记失败。"""

    def __init__(self, message: str = '已取消'):
        super().__init__(message)
