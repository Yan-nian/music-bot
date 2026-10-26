#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""曲库容量统计——直接扫盘。

为什么不再用下载历史去算「曲库容量」：

1. 老版本给专辑/歌单写进 file_size 的是「曲目数」而不是字节数，读出来只能是 0；
2. Web 后台触发的同步、重试任务从来不写 download_history（只有 Bot 那条链路写），
   用 Web 下载的用户历史表是空的，容量永远是 0；
3. 用户手动删过文件、或者把文件挪走之后，历史表里的字节数就不代表磁盘现状了。

仪表盘那张卡片底下写的是「全部音频文件」，那就以磁盘为准：走一遍配置里的
下载目录，累加音频文件大小。扫盘有成本，所以带一个短 TTL 缓存。
"""

import os
import threading
import time
from typing import Any, Dict, Iterable, List

# 常见音频后缀。多列几个不吃亏——漏一个后缀，用户就会看到容量对不上。
AUDIO_EXTS = {
    '.mp3', '.flac', '.m4a', '.aac', '.ogg', '.oga', '.opus', '.wav',
    '.ape', '.wma', '.aiff', '.aif', '.alac', '.dsf', '.dff', '.mp4',
    '.mgg', '.mgg0', '.mgg1', '.mgga', '.mflac', '.mflac0',
}

# 这些目录里的东西不是用户曲库，进不进都无所谓，但进去会明显拖慢扫描
SKIP_DIR_NAMES = {'.git', '.svn', 'node_modules', '#recycle', '@eaDir',
                  '__pycache__', '.Trash', 'lost+found'}

DEFAULT_TTL = 60.0

_cache_lock = threading.Lock()
_cache: Dict[Any, tuple] = {}


def _iter_audio_files(root: str):
    """遍历一个根目录下的音频文件，产出 (path, size)。

    用 os.scandir 而不是 os.walk：scandir 的 DirEntry 自带 stat 缓存，
    在几十万文件的曲库上能快好几倍。符号链接不跟随，避免软链成环。
    """
    stack: List[str] = [root]
    while stack:
        current = stack.pop()
        try:
            with os.scandir(current) as it:
                for entry in it:
                    try:
                        if entry.is_dir(follow_symlinks=False):
                            if entry.name in SKIP_DIR_NAMES or entry.name.startswith('.'):
                                continue
                            stack.append(entry.path)
                        elif entry.is_file(follow_symlinks=False):
                            ext = os.path.splitext(entry.name)[1].lower()
                            if ext in AUDIO_EXTS:
                                yield entry.path, entry.stat(follow_symlinks=False).st_size
                    except OSError:
                        # 单个文件权限问题/正在被删除，跳过就好，别让整次扫描失败
                        continue
        except OSError:
            continue


def scan_audio_size(roots: Iterable[str], ttl: float = DEFAULT_TTL,
                    force: bool = False) -> Dict[str, Any]:
    """统计若干目录下的音频文件总大小。

    Args:
        roots: 下载目录列表（重复项会自动去掉）
        ttl: 缓存有效期（秒）。仪表盘 8 秒轮询一次，没有缓存就太浪费 I/O
        force: 忽略缓存，强制重扫

    Returns:
        {'bytes': int, 'files': int, 'roots': [...], 'missing': [...],
         'scanned_at': float, 'cached': bool, 'elapsed': float}
    """
    # 去重 + 归一化。
    # 注意这里是两层去重：完全相同的路径要去掉；**互相嵌套的目录也要去掉子目录**——
    # 默认配置里 netease_download_path 就是 download_path 的子目录，
    # 只做字符串去重会让同一批文件被走两遍，容量直接翻倍。
    seen = set()
    normalized: List[str] = []
    for raw in roots:
        if not raw:
            continue
        path = os.path.abspath(os.path.expanduser(str(raw)))
        if path in seen:
            continue
        seen.add(path)
        normalized.append(path)

    # 短路径优先，被包含的子目录统统丢掉
    normalized.sort(key=len)
    kept: List[str] = []
    for path in normalized:
        if any(path == parent or path.startswith(parent + os.sep) for parent in kept):
            continue
        kept.append(path)
    normalized = sorted(kept)

    key = tuple(normalized)
    now = time.time()

    if not force:
        with _cache_lock:
            hit = _cache.get(key)
        if hit and now - hit[0] < ttl:
            result = dict(hit[1])
            result['cached'] = True
            return result

    started = now
    total_bytes = 0
    files = 0
    missing: List[str] = []
    scanned: List[str] = []

    for root in normalized:
        if not os.path.isdir(root):
            missing.append(root)
            continue
        scanned.append(root)
        for _path, size in _iter_audio_files(root):
            total_bytes += size
            files += 1

    result = {
        'bytes': total_bytes,
        'files': files,
        'roots': scanned,
        'missing': missing,
        'scanned_at': now,
        'cached': False,
        'elapsed': round(time.time() - started, 3),
    }

    with _cache_lock:
        _cache[key] = (now, dict(result))
        # 目录配置频繁变化时别把缓存表撑大
        if len(_cache) > 32:
            for stale in sorted(_cache, key=lambda k: _cache[k][0])[:16]:
                _cache.pop(stale, None)

    return result


def clear_cache():
    with _cache_lock:
        _cache.clear()
