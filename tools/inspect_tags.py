#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""查看音频文件的标签信息

用途：排查「播放器/Plex 把一张专辑拆成 N 张」这类问题——多半是
TRACKNUMBER / TOTALTRACKS / ALBUMARTIST 缺失或不一致。

用法：
    python tools/inspect_tags.py <目录或文件> [更多路径...]

旧版本把目录写死成 d:\\AI\\savextube\\...,换个机器就跑不起来。
"""

import argparse
import os
import sys

AUDIO_EXTS = {
    '.mp3', '.flac', '.m4a', '.aac', '.ogg', '.oga', '.opus', '.wav',
    '.ape', '.wma', '.aiff', '.aif', '.alac', '.mgg', '.mflac',
}

KEYS = ('TITLE', 'ARTIST', 'ALBUM', 'ALBUMARTIST', 'TRACKNUMBER',
        'TOTALTRACKS', 'DISCNUMBER', 'DATE')


def iter_audio(paths):
    for raw in paths:
        path = os.path.abspath(os.path.expanduser(raw))
        if os.path.isfile(path):
            yield path
            continue
        for root, _dirs, files in os.walk(path):
            for name in sorted(files):
                if os.path.splitext(name)[1].lower() in AUDIO_EXTS:
                    yield os.path.join(root, name)


def describe(path: str) -> None:
    try:
        from mutagen import File
    except ImportError:
        print('缺少 mutagen，先执行：pip install mutagen', file=sys.stderr)
        raise SystemExit(2)

    audio = File(path)
    print(f'=== {os.path.relpath(path)} ===')
    if audio is None:
        print('  无法识别（不是受支持的音频格式）\n')
        return

    info = getattr(audio, 'info', None)
    if info is not None:
        bitrate = getattr(info, 'bitrate', 0) or 0
        print(f'  时长 {getattr(info, "length", 0):.1f}s · '
              f'{bitrate / 1000:.0f} kbps · {path.rsplit(".", 1)[-1].upper()}')

    tags = getattr(audio, 'tags', None)
    if not tags:
        print('  ⚠️ 没有任何标签\n')
        return

    for key in KEYS:
        value = tags.get(key)
        if value is None:
            print(f'  {key:<12} —')
        else:
            text = value[0] if isinstance(value, (list, tuple)) and value else value
            print(f'  {key:<12} {text}')
    print()


def main() -> int:
    parser = argparse.ArgumentParser(description='查看音频文件标签')
    parser.add_argument('paths', nargs='+', help='目录或音频文件')
    args = parser.parse_args()

    files = list(iter_audio(args.paths))
    if not files:
        print('没找到音频文件', file=sys.stderr)
        return 1

    for path in files:
        describe(path)
    print(f'共 {len(files)} 个文件')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
