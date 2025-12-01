#!/usr/bin/env python3
"""测试元数据流程"""

import os
import logging

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')

from downloaders.netease import NeteaseDownloader

def test_metadata_flow():
    """测试元数据流程"""
    print("=" * 60)
    print("测试元数据流程")
    print("=" * 60)
    
    downloader = NeteaseDownloader()
    
    print(f"\n1. 元数据管理器状态: {downloader.metadata_manager}")
    if downloader.metadata_manager:
        print(f"   可用库: {downloader.metadata_manager.available_libraries}")
    
    # 测试获取专辑信息
    # G.E.M专辑ID: 74570951 (心之焰)
    album_id = "74570951"
    print(f"\n2. 获取专辑歌曲信息: {album_id}")
    
    songs = downloader.get_album_songs(album_id)
    if songs:
        print(f"   获取到 {len(songs)} 首歌曲")
        print(f"\n3. 检查第一首歌曲的元数据字段:")
        first_song = songs[0]
        for key, value in first_song.items():
            print(f"   {key}: {value}")
        
        # 检查关键字段
        print(f"\n4. 关键元数据字段检查:")
        print(f"   track_number: {first_song.get('track_number', '无')}")
        print(f"   total_tracks: {first_song.get('total_tracks', '无')}")
        print(f"   album_artist: {first_song.get('album_artist', '无')}")
        print(f"   disc_number: {first_song.get('disc_number', '无')}")
        print(f"   publish_time: {first_song.get('publish_time', '无')}")
    else:
        print("   未能获取专辑歌曲信息")
    
    print("\n" + "=" * 60)
    print("测试完成")
    print("=" * 60)

if __name__ == "__main__":
    test_metadata_flow()
