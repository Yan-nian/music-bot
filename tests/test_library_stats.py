# -*- coding: utf-8 -*-
"""曲库容量扫盘统计

这块直接决定仪表盘上「曲库容量」是不是 0，之前踩过两个坑：
1. 只看历史表的字节数，而历史里可能存的是曲目数、或者压根没写；
2. download_path 是 netease_download_path 的父目录，父子一起扫会算两遍。
"""

import os

import pytest

from config import library_stats


@pytest.fixture(autouse=True)
def _clear_cache():
    library_stats.clear_cache()
    yield
    library_stats.clear_cache()


def _make(path: str, size: int):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'wb') as f:
        f.truncate(size)


def test_counts_audio_files_only(tmp_path):
    music = str(tmp_path / 'music')
    _make(os.path.join(music, '周杰伦', '叶惠美', '稻香.flac'), 1024)
    _make(os.path.join(music, '周杰伦', '叶惠美', 'cover.jpg'), 2048)
    _make(os.path.join(music, 'README.txt'), 4096)

    result = library_stats.scan_audio_size([music], force=True)

    assert result['files'] == 1
    assert result['bytes'] == 1024


def test_nested_roots_not_double_counted(tmp_path):
    """父目录 + 子目录同时配置时只能算一次（真实默认配置就是这种结构）"""
    root = str(tmp_path / 'downloads')
    child = os.path.join(root, 'netease')
    _make(os.path.join(child, 'a.mp3'), 100)
    _make(os.path.join(child, 'b.mp3'), 200)

    once = library_stats.scan_audio_size([child], force=True)
    both = library_stats.scan_audio_size([child, root], force=True)

    assert once['files'] == 2
    assert both['files'] == 2, '父子目录叠加时重复计数了'
    assert both['bytes'] == once['bytes'] == 300


def test_missing_dir_is_ignored(tmp_path):
    result = library_stats.scan_audio_size([str(tmp_path / 'not-exist')], force=True)
    assert result['files'] == 0
    assert result['bytes'] == 0


def test_cache_hit_then_force(tmp_path):
    music = str(tmp_path / 'music')
    _make(os.path.join(music, 'a.flac'), 10)

    first = library_stats.scan_audio_size([music])
    assert first['cached'] is False

    # 新增文件后不 force 应该还拿缓存
    _make(os.path.join(music, 'b.flac'), 20)
    cached = library_stats.scan_audio_size([music])
    assert cached['cached'] is True
    assert cached['files'] == 1

    forced = library_stats.scan_audio_size([music], force=True)
    assert forced['files'] == 2
    assert forced['bytes'] == 30
