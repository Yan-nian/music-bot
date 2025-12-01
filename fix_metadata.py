#!/usr/bin/env python3
"""
修复已下载音乐文件的元数据
用于解决 Plex 刮削显示一首歌一个专辑的问题

主要功能：
1. 扫描指定目录中的音乐文件
2. 根据文件夹结构推断专辑信息
3. 添加缺失的 TRACKNUMBER、TOTALTRACKS 等元数据

使用方法：
python fix_metadata.py <音乐目录>
例如：python fix_metadata.py "d:/AI/savextube/G.E.M"
"""

import os
import sys
from pathlib import Path
from mutagen.flac import FLAC
from mutagen.mp3 import MP3
from mutagen.mp4 import MP4
from mutagen.id3 import TRCK, TALB, TPE2


def fix_flac_metadata(file_path: Path, track_number: int, total_tracks: int, album_artist: str = None):
    """修复 FLAC 文件元数据"""
    try:
        audio = FLAC(str(file_path))
        
        # 添加曲目编号
        audio['TRACKNUMBER'] = str(track_number)
        audio['TOTALTRACKS'] = str(total_tracks)
        audio['TRACKTOTAL'] = str(total_tracks)
        
        # 如果提供了专辑艺术家且当前没有，则添加
        if album_artist and 'ALBUMARTIST' not in audio:
            audio['ALBUMARTIST'] = album_artist
        
        audio.save()
        print(f"  ✅ 已修复: {file_path.name}")
        print(f"     曲目: {track_number}/{total_tracks}")
        return True
    except Exception as e:
        print(f"  ❌ 修复失败: {file_path.name} - {e}")
        return False


def fix_mp3_metadata(file_path: Path, track_number: int, total_tracks: int, album_artist: str = None):
    """修复 MP3 文件元数据"""
    try:
        audio = MP3(str(file_path))
        
        # 确保有 ID3 标签
        if audio.tags is None:
            audio.add_tags()
        
        # 添加曲目编号 (格式: track/total)
        audio.tags.add(TRCK(encoding=3, text=f"{track_number}/{total_tracks}"))
        
        # 如果提供了专辑艺术家
        if album_artist:
            audio.tags.add(TPE2(encoding=3, text=album_artist))
        
        audio.save()
        print(f"  ✅ 已修复: {file_path.name}")
        print(f"     曲目: {track_number}/{total_tracks}")
        return True
    except Exception as e:
        print(f"  ❌ 修复失败: {file_path.name} - {e}")
        return False


def fix_m4a_metadata(file_path: Path, track_number: int, total_tracks: int, album_artist: str = None):
    """修复 M4A 文件元数据"""
    try:
        audio = MP4(str(file_path))
        
        # 添加曲目编号 (格式: [(track, total)])
        audio['trkn'] = [(track_number, total_tracks)]
        
        # 如果提供了专辑艺术家
        if album_artist:
            audio['aART'] = [album_artist]
        
        audio.save()
        print(f"  ✅ 已修复: {file_path.name}")
        print(f"     曲目: {track_number}/{total_tracks}")
        return True
    except Exception as e:
        print(f"  ❌ 修复失败: {file_path.name} - {e}")
        return False


def fix_album_folder(folder_path: str):
    """修复整个专辑文件夹中的音乐文件"""
    folder = Path(folder_path)
    
    if not folder.exists():
        print(f"❌ 目录不存在: {folder_path}")
        return
    
    # 收集所有音乐文件
    music_extensions = {'.flac', '.mp3', '.m4a', '.mp4'}
    music_files = sorted([f for f in folder.iterdir() 
                         if f.is_file() and f.suffix.lower() in music_extensions])
    
    if not music_files:
        print(f"⚠️ 未找到音乐文件: {folder_path}")
        return
    
    total_tracks = len(music_files)
    print(f"\n📁 处理目录: {folder.name}")
    print(f"   找到 {total_tracks} 个音乐文件")
    print("-" * 50)
    
    # 尝试获取专辑艺术家（从第一个文件）
    album_artist = None
    first_file = music_files[0]
    try:
        if first_file.suffix.lower() == '.flac':
            audio = FLAC(str(first_file))
            album_artist = audio.get('ALBUMARTIST', [None])[0]
            if not album_artist:
                album_artist = audio.get('ARTIST', [None])[0]
        elif first_file.suffix.lower() == '.mp3':
            audio = MP3(str(first_file))
            if audio.tags:
                tpe2 = audio.tags.get('TPE2')
                if tpe2:
                    album_artist = str(tpe2.text[0])
                else:
                    tpe1 = audio.tags.get('TPE1')
                    if tpe1:
                        album_artist = str(tpe1.text[0])
    except Exception:
        pass
    
    if album_artist:
        print(f"   专辑艺术家: {album_artist}")
    
    # 修复每个文件
    fixed = 0
    for i, file_path in enumerate(music_files, 1):
        suffix = file_path.suffix.lower()
        
        if suffix == '.flac':
            if fix_flac_metadata(file_path, i, total_tracks, album_artist):
                fixed += 1
        elif suffix == '.mp3':
            if fix_mp3_metadata(file_path, i, total_tracks, album_artist):
                fixed += 1
        elif suffix in ['.m4a', '.mp4']:
            if fix_m4a_metadata(file_path, i, total_tracks, album_artist):
                fixed += 1
    
    print("-" * 50)
    print(f"✅ 完成! 已修复 {fixed}/{total_tracks} 个文件")


def main():
    if len(sys.argv) < 2:
        print("用法: python fix_metadata.py <音乐目录>")
        print("例如: python fix_metadata.py \"d:/AI/savextube/G.E.M\"")
        sys.exit(1)
    
    folder_path = sys.argv[1]
    fix_album_folder(folder_path)
    
    print("\n💡 提示: 修复完成后，请在 Plex 中刷新该专辑的元数据")


if __name__ == "__main__":
    main()
