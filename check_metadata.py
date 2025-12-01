#!/usr/bin/env python3
from mutagen.flac import FLAC
import os

folder = r'd:\AI\savextube\G.E.M'
for f in os.listdir(folder):
    if f.endswith('.flac'):
        path = os.path.join(folder, f)
        audio = FLAC(path)
        print(f'=== {f} ===')
        print(f'  TITLE: {audio.get("TITLE", ["N/A"])}')
        print(f'  ARTIST: {audio.get("ARTIST", ["N/A"])}')
        print(f'  ALBUM: {audio.get("ALBUM", ["N/A"])}')
        print(f'  ALBUMARTIST: {audio.get("ALBUMARTIST", ["N/A"])}')
        print(f'  TRACKNUMBER: {audio.get("TRACKNUMBER", ["N/A"])}')
        print(f'  TOTALTRACKS: {audio.get("TOTALTRACKS", ["N/A"])}')
        print(f'  TRACKTOTAL: {audio.get("TRACKTOTAL", ["N/A"])}')
        print(f'  DISCNUMBER: {audio.get("DISCNUMBER", ["N/A"])}')
        print(f'  DATE: {audio.get("DATE", ["N/A"])}')
        print()
