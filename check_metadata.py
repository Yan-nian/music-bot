#!/usr/bin/env python3
from mutagen.flac import FLAC
from mutagen import File
import os

# 检查 G.E.M 专辑
folder = r'd:\AI\savextube\netease\G.E.M.邓紫棋\G.E.M'
print(f"检查目录: {folder}\n")

for f in os.listdir(folder):
    if f.endswith(('.flac', '.mp3', '.m4a')):
        path = os.path.join(folder, f)
        audio = File(path)
        print(f'=== {f} ===')
        
        if hasattr(audio, 'tags') and audio.tags:
            tags = audio.tags
            # FLAC 格式
            if f.endswith('.flac'):
                print(f'  TITLE: {tags.get("TITLE", ["N/A"])}')
                print(f'  ARTIST: {tags.get("ARTIST", ["N/A"])}')
                print(f'  ALBUM: {tags.get("ALBUM", ["N/A"])}')
                print(f'  ALBUMARTIST: {tags.get("ALBUMARTIST", ["N/A"])}')
                print(f'  TRACKNUMBER: {tags.get("TRACKNUMBER", ["N/A"])}')
                print(f'  TOTALTRACKS: {tags.get("TOTALTRACKS", ["N/A"])}')
                print(f'  DATE: {tags.get("DATE", ["N/A"])}')
            else:
                print(f'  All tags: {dict(tags)}')
        else:
            print('  No tags found!')
        print()
