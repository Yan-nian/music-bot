#!/usr/bin/env python3
"""测试专辑下载功能"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from downloaders.netease import NeteaseDownloader

def test_album_download():
    """测试专辑链接解析和下载"""
    
    # 初始化下载器
    downloader = NeteaseDownloader()
    
    # 测试链接
    test_url = "https://music.163.com/#/album?id=28558"
    
    print(f"🔗 测试链接: {test_url}")
    
    # 1. 测试 URL 支持检测
    is_supported = downloader.is_supported_url(test_url)
    print(f"✅ 链接支持检测: {is_supported}")
    
    if not is_supported:
        print("❌ 链接不被支持")
        return
    
    # 2. 测试 URL 解析
    parsed = downloader.parse_url(test_url)
    print(f"✅ URL 解析结果: {parsed}")
    
    if not parsed:
        print("❌ URL 解析失败")
        return
    
    if parsed.get('type') != 'album':
        print(f"❌ 类型不正确: {parsed.get('type')}, 期望: album")
        return
    
    album_id = parsed.get('id')
    print(f"✅ 专辑 ID: {album_id}")
    
    # 3. 测试获取专辑歌曲
    songs = downloader.get_album_songs(album_id)
    print(f"✅ 获取专辑歌曲: {len(songs)} 首")
    
    if songs:
        print(f"   专辑名: {songs[0].get('album')}")
        print(f"   艺术家: {songs[0].get('album_artist')}")
        print(f"   示例歌曲: {songs[0].get('name')}")
        print(f"   曲目编号: {songs[0].get('track_number')}/{songs[0].get('total_tracks')}")
    else:
        print("❌ 获取专辑歌曲失败")
        return
    
    print("\n✅ 专辑下载功能检测通过！")

if __name__ == "__main__":
    test_album_download()
