#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Web 配置服务
提供 Web UI 界面管理配置
"""

import os
import sys
import logging
import hashlib
import secrets
from functools import wraps
from pathlib import Path
from flask import Flask, Blueprint, jsonify, request, render_template, send_from_directory, session, redirect, url_for

# 添加父目录到路径
sys.path.insert(0, str(Path(__file__).parent.parent))

from config_manager import ConfigManager, get_config_manager

logger = logging.getLogger(__name__)

# 创建 Flask 应用
app = Flask(__name__, 
           template_folder='templates',
           static_folder='static')

# 设置 session 密钥（从环境变量读取或使用固定值，避免每次重启生成新密钥导致 session 失效）
app.secret_key = os.environ.get('FLASK_SECRET_KEY', 'music-bot-default-secret-key-change-in-production')

# 配置管理器实例
config_manager: ConfigManager = None

# 默认管理员账号
DEFAULT_USERNAME = 'admin'
DEFAULT_PASSWORD = 'admin'


def hash_password(password: str) -> str:
    """哈希密码"""
    return hashlib.sha256(password.encode()).hexdigest()


def login_required(f):
    """登录验证装饰器"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get('logged_in'):
            # 如果是 API 请求，返回 JSON
            if request.path.startswith('/api/'):
                return jsonify({'success': False, 'error': '未登录', 'code': 401}), 401
            # 否则重定向到登录页
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function


def init_app(db_path: str = None):
    """初始化应用"""
    global config_manager
    config_manager = get_config_manager(db_path)
    
    # 初始化默认管理员账号（如果不存在）
    if not config_manager.get_config('admin_username'):
        config_manager.set_config('admin_username', DEFAULT_USERNAME)
        config_manager.set_config('admin_password', hash_password(DEFAULT_PASSWORD))
    
    # 注册 Telegram Setup Blueprint
    from .tg_setup import create_tg_setup_blueprint, init_tg_setup
    tg_setup_bp = create_tg_setup_blueprint()
    init_tg_setup(config_manager)
    app.register_blueprint(tg_setup_bp)
    
    return app


# ==================== 健康检查 ====================

@app.route('/api/health', methods=['GET'])
def health_check():
    """健康检查端点（不需要登录）"""
    return jsonify({
        'success': True,
        'status': 'healthy',
        'service': 'music-bot'
    })


# ==================== 认证 API ====================

@app.route('/api/auth/login', methods=['POST'])
def api_login():
    """登录 API"""
    try:
        data = request.get_json()
        if not data:
            return jsonify({'success': False, 'error': '无效的请求数据'}), 400
        
        username = data.get('username', '').strip()
        password = data.get('password', '')
        remember = data.get('remember', False)
        
        if not username or not password:
            return jsonify({'success': False, 'error': '用户名和密码不能为空'}), 400
        
        # 验证账号密码
        stored_username = config_manager.get_config('admin_username') or DEFAULT_USERNAME
        stored_password = config_manager.get_config('admin_password') or hash_password(DEFAULT_PASSWORD)
        
        if username == stored_username and hash_password(password) == stored_password:
            session['logged_in'] = True
            session['username'] = username
            session.permanent = remember
            
            logger.info(f"用户 {username} 登录成功")
            return jsonify({
                'success': True,
                'message': '登录成功',
                'redirect': '/'
            })
        else:
            logger.warning(f"用户 {username} 登录失败：密码错误")
            return jsonify({'success': False, 'error': '用户名或密码错误'}), 401
            
    except Exception as e:
        logger.error(f"登录失败: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/auth/logout', methods=['POST'])
def api_logout():
    """登出 API"""
    try:
        username = session.get('username', 'unknown')
        session.clear()
        logger.info(f"用户 {username} 已登出")
        return jsonify({
            'success': True,
            'message': '已登出',
            'redirect': '/login'
        })
    except Exception as e:
        logger.error(f"登出失败: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/auth/status', methods=['GET'])
def api_auth_status():
    """获取登录状态"""
    return jsonify({
        'success': True,
        'logged_in': session.get('logged_in', False),
        'username': session.get('username', None)
    })


@app.route('/api/auth/change-password', methods=['POST'])
@login_required
def api_change_password():
    """修改密码 API"""
    try:
        data = request.get_json()
        if not data:
            return jsonify({'success': False, 'error': '无效的请求数据'}), 400
        
        current_password = data.get('current_password', '')
        new_password = data.get('new_password', '')
        confirm_password = data.get('confirm_password', '')
        
        if not current_password or not new_password:
            return jsonify({'success': False, 'error': '请填写所有字段'}), 400
        
        if new_password != confirm_password:
            return jsonify({'success': False, 'error': '两次输入的新密码不一致'}), 400
        
        if len(new_password) < 4:
            return jsonify({'success': False, 'error': '新密码长度至少4位'}), 400
        
        # 验证当前密码
        stored_password = config_manager.get_config('admin_password') or hash_password(DEFAULT_PASSWORD)
        if hash_password(current_password) != stored_password:
            return jsonify({'success': False, 'error': '当前密码错误'}), 401
        
        # 更新密码
        config_manager.set_config('admin_password', hash_password(new_password))
        logger.info(f"用户 {session.get('username')} 修改了密码")
        
        return jsonify({
            'success': True,
            'message': '密码修改成功'
        })
        
    except Exception as e:
        logger.error(f"修改密码失败: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/auth/change-username', methods=['POST'])
@login_required
def api_change_username():
    """修改用户名 API"""
    try:
        data = request.get_json()
        if not data:
            return jsonify({'success': False, 'error': '无效的请求数据'}), 400
        
        new_username = data.get('new_username', '').strip()
        password = data.get('password', '')
        
        if not new_username or not password:
            return jsonify({'success': False, 'error': '请填写所有字段'}), 400
        
        if len(new_username) < 3:
            return jsonify({'success': False, 'error': '用户名长度至少3位'}), 400
        
        # 验证密码
        stored_password = config_manager.get_config('admin_password') or hash_password(DEFAULT_PASSWORD)
        if hash_password(password) != stored_password:
            return jsonify({'success': False, 'error': '密码错误'}), 401
        
        # 更新用户名
        old_username = session.get('username')
        config_manager.set_config('admin_username', new_username)
        session['username'] = new_username
        
        logger.info(f"用户 {old_username} 将用户名修改为 {new_username}")
        
        return jsonify({
            'success': True,
            'message': '用户名修改成功'
        })
        
    except Exception as e:
        logger.error(f"修改用户名失败: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


# ==================== API 路由 ====================

@app.route('/api/config', methods=['GET'])
@login_required
def get_all_config():
    """获取所有配置"""
    try:
        config = config_manager.get_all_config()
        # 明文显示所有配置
        return jsonify({
            'success': True,
            'data': config
        })
    except Exception as e:
        logger.error(f"获取配置失败: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/config', methods=['POST'])
@login_required
def update_config():
    """更新配置"""
    try:
        data = request.get_json()
        if not data:
            return jsonify({'success': False, 'error': '无效的请求数据'}), 400
        
        if config_manager.update_config_batch(data):
            return jsonify({'success': True, 'message': '配置更新成功'})
        else:
            return jsonify({'success': False, 'error': '配置更新失败'}), 500
            
    except Exception as e:
        logger.error(f"更新配置失败: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/config/<key>', methods=['GET'])
@login_required
def get_config_item(key: str):
    """获取单个配置项"""
    try:
        value = config_manager.get_config(key)
        return jsonify({
            'success': True,
            'data': {key: value}
        })
    except Exception as e:
        logger.error(f"获取配置项失败: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/config/<key>', methods=['PUT'])
@login_required
def set_config_item(key: str):
    """设置单个配置项"""
    try:
        data = request.get_json()
        if 'value' not in data:
            return jsonify({'success': False, 'error': '缺少 value 字段'}), 400
        
        if config_manager.set_config(key, data['value']):
            return jsonify({'success': True, 'message': f'配置项 {key} 更新成功'})
        else:
            return jsonify({'success': False, 'error': '配置更新失败'}), 500
            
    except Exception as e:
        logger.error(f"设置配置项失败: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/config/category/<category>', methods=['GET'])
@login_required
def get_config_by_category(category: str):
    """按类别获取配置"""
    try:
        config = config_manager.get_config_by_category(category)
        return jsonify({
            'success': True,
            'data': config
        })
    except Exception as e:
        logger.error(f"获取分类配置失败: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/config/reset', methods=['POST'])
@login_required
def reset_config():
    """重置配置为默认值"""
    try:
        if config_manager.reset_to_default():
            return jsonify({'success': True, 'message': '配置已重置为默认值'})
        else:
            return jsonify({'success': False, 'error': '重置失败'}), 500
    except Exception as e:
        logger.error(f"重置配置失败: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/config/export', methods=['GET'])
@login_required
def export_config():
    """导出配置"""
    try:
        config_json = config_manager.export_config()
        return jsonify({
            'success': True,
            'data': config_json
        })
    except Exception as e:
        logger.error(f"导出配置失败: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/config/import', methods=['POST'])
@login_required
def import_config():
    """导入配置"""
    try:
        data = request.get_json()
        if 'config' not in data:
            return jsonify({'success': False, 'error': '缺少 config 字段'}), 400
        
        if config_manager.import_config(data['config']):
            return jsonify({'success': True, 'message': '配置导入成功'})
        else:
            return jsonify({'success': False, 'error': '配置导入失败'}), 500
            
    except Exception as e:
        logger.error(f"导入配置失败: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/history', methods=['GET'])
@login_required
def get_download_history():
    """获取下载历史"""
    try:
        limit = request.args.get('limit', 50, type=int)
        platform = request.args.get('platform', None)
        
        history = config_manager.get_download_history(limit=limit, platform=platform)
        return jsonify({
            'success': True,
            'data': history
        })
    except Exception as e:
        logger.error(f"获取下载历史失败: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/status', methods=['GET'])
@login_required
def get_status():
    """获取服务状态"""
    try:
        config = config_manager.get_all_config()
        
        status = {
            'bot_configured': bool(config.get('telegram_bot_token')),
            'netease_enabled': config.get('netease_enabled', False),
            'apple_music_enabled': config.get('apple_music_enabled', False),
            'youtube_music_enabled': config.get('youtube_music_enabled', False),
            'qbittorrent_enabled': config.get('qbittorrent_enabled', False),
            'proxy_enabled': config.get('proxy_enabled', False),
        }
        
        return jsonify({
            'success': True,
            'data': status
        })
    except Exception as e:
        logger.error(f"获取状态失败: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


# ==================== 订阅歌单 API ====================

@app.route('/api/playlists', methods=['GET'])
@login_required
def get_playlists():
    """获取所有订阅歌单"""
    try:
        platform = request.args.get('platform', None)
        enabled_only = request.args.get('enabled_only', 'false').lower() == 'true'
        
        playlists = config_manager.get_subscribed_playlists(platform=platform, enabled_only=enabled_only)
        
        # 为每个歌单添加统计信息
        for playlist in playlists:
            stats = config_manager.get_playlist_stats(playlist['playlist_id'])
            playlist['stats'] = stats
        
        return jsonify({
            'success': True,
            'data': playlists
        })
    except Exception as e:
        logger.error(f"获取订阅歌单失败: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/playlists', methods=['POST'])
@login_required
def add_playlist():
    """添加订阅歌单"""
    try:
        data = request.get_json()
        if not data:
            return jsonify({'success': False, 'error': '无效的请求数据'}), 400
        
        # 支持直接传入歌单URL或ID
        playlist_url = data.get('playlist_url', '').strip()
        playlist_id = data.get('playlist_id', '').strip()
        playlist_name = data.get('playlist_name', '').strip()
        platform = data.get('platform', 'netease')
        check_interval = data.get('check_interval', 3600)
        
        # 从URL提取歌单ID
        if playlist_url and not playlist_id:
            import re
            # 支持多种网易云歌单URL格式
            patterns = [
                r'playlist\?id=(\d+)',
                r'playlist/(\d+)',
                r'#/playlist\?id=(\d+)',
            ]
            for pattern in patterns:
                match = re.search(pattern, playlist_url)
                if match:
                    playlist_id = match.group(1)
                    break
        
        if not playlist_id:
            return jsonify({'success': False, 'error': '请提供有效的歌单ID或URL'}), 400
        
        # 如果没有提供歌单名称，尝试获取
        if not playlist_name:
            try:
                from downloaders.netease import NeteaseDownloader
                downloader = NeteaseDownloader(config_manager)
                songs, fetched_name = downloader.get_playlist_songs(playlist_id)
                if fetched_name and fetched_name != '未知歌单':
                    playlist_name = fetched_name
                else:
                    playlist_name = f'歌单 {playlist_id}'
            except Exception as e:
                logger.warning(f"获取歌单名称失败: {e}")
                playlist_name = f'歌单 {playlist_id}'
        
        # 构建完整URL
        if not playlist_url:
            playlist_url = f'https://music.163.com/#/playlist?id={playlist_id}'
        
        if config_manager.add_subscribed_playlist(
            playlist_id=playlist_id,
            playlist_name=playlist_name,
            playlist_url=playlist_url,
            platform=platform,
            check_interval=check_interval
        ):
            return jsonify({
                'success': True,
                'message': f'成功添加歌单: {playlist_name}',
                'data': {
                    'playlist_id': playlist_id,
                    'playlist_name': playlist_name
                }
            })
        else:
            return jsonify({'success': False, 'error': '添加歌单失败'}), 500
            
    except Exception as e:
        logger.error(f"添加订阅歌单失败: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/playlists/<playlist_id>', methods=['GET'])
@login_required
def get_playlist(playlist_id: str):
    """获取单个歌单详情"""
    try:
        platform = request.args.get('platform', 'netease')
        playlist = config_manager.get_subscribed_playlist(playlist_id, platform)
        
        if not playlist:
            return jsonify({'success': False, 'error': '歌单不存在'}), 404
        
        # 添加统计信息
        playlist['stats'] = config_manager.get_playlist_stats(playlist_id)
        
        # 获取歌曲列表
        playlist['songs'] = config_manager.get_playlist_songs(playlist_id)
        
        return jsonify({
            'success': True,
            'data': playlist
        })
    except Exception as e:
        logger.error(f"获取歌单详情失败: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/playlists/<playlist_id>', methods=['PUT'])
@login_required
def update_playlist(playlist_id: str):
    """更新歌单设置"""
    try:
        data = request.get_json()
        if not data:
            return jsonify({'success': False, 'error': '无效的请求数据'}), 400
        
        platform = data.pop('platform', 'netease')
        
        if config_manager.update_subscribed_playlist(playlist_id, platform, **data):
            return jsonify({
                'success': True,
                'message': '歌单设置已更新'
            })
        else:
            return jsonify({'success': False, 'error': '更新失败'}), 500
            
    except Exception as e:
        logger.error(f"更新歌单设置失败: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/playlists/<playlist_id>', methods=['DELETE'])
@login_required
def delete_playlist(playlist_id: str):
    """删除订阅歌单"""
    try:
        platform = request.args.get('platform', 'netease')
        # 支持删除本地文件选项（默认为 False，前端可传 delete_files=true）
        delete_files = request.args.get('delete_files', 'false').lower() == 'true'
        
        if config_manager.remove_subscribed_playlist(playlist_id, platform, delete_files):
            message = '歌单已删除' + ('（包括本地文件）' if delete_files else '')
            return jsonify({
                'success': True,
                'message': message
            })
        else:
            return jsonify({'success': False, 'error': '删除失败'}), 500
            
    except Exception as e:
        logger.error(f"删除歌单失败: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/playlists/<playlist_id>/sync', methods=['POST'])
@login_required
def sync_playlist(playlist_id: str):
    """同步歌单（增量下载）"""
    try:
        platform = request.args.get('platform', 'netease')
        
        # 检查歌单是否存在
        playlist = config_manager.get_subscribed_playlist(playlist_id, platform)
        if not playlist:
            return jsonify({'success': False, 'error': '歌单不存在'}), 404
        
        playlist_name = playlist.get('playlist_name', '未知歌单')
        
        # 获取下载目录
        download_dir = config_manager.get_config('netease_download_path', '/downloads/netease')
        
        # 创建下载器并执行增量下载
        from downloaders.netease import NeteaseDownloader
        downloader = NeteaseDownloader(config_manager)
        
        result = downloader.download_playlist_incremental(
            playlist_id=playlist_id,
            download_dir=download_dir
        )
        
        # 发送 TG 通知
        try:
            from web.tg_notifier import notify_playlist_sync_result
            logger.info(f"📨 准备发送歌单同步 TG 通知: {playlist_name}")
            notify_playlist_sync_result(config_manager, result, playlist_name)
        except Exception as e:
            logger.error(f"❌ 发送 TG 通知失败: {e}")
            import traceback
            logger.error(traceback.format_exc())
        
        return jsonify({
            'success': result.get('success', False),
            'data': result
        })
        
    except Exception as e:
        logger.error(f"同步歌单失败: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/playlists/<playlist_id>/check', methods=['POST'])
@login_required
def check_playlist_updates(playlist_id: str):
    """检查歌单更新（不下载，仅检查新歌曲）"""
    try:
        platform = request.args.get('platform', 'netease')
        
        # 创建下载器获取歌单信息
        from downloaders.netease import NeteaseDownloader
        downloader = NeteaseDownloader(config_manager)
        
        songs, playlist_name = downloader.get_playlist_songs(playlist_id)
        
        if not songs:
            return jsonify({'success': False, 'error': '无法获取歌单信息'}), 500
        
        # 获取已下载的歌曲
        downloaded_records = config_manager.get_playlist_songs(playlist_id, downloaded_only=True)
        downloaded_ids = {record['song_id'] for record in downloaded_records}
        
        # 统计新歌曲
        new_songs = [s for s in songs if s['id'] not in downloaded_ids]
        
        # 更新歌单信息
        config_manager.update_subscribed_playlist(
            playlist_id=playlist_id,
            playlist_name=playlist_name,
            last_check_time=None,  # 只检查不更新时间
            last_song_count=len(songs)
        )
        
        return jsonify({
            'success': True,
            'data': {
                'playlist_name': playlist_name,
                'total_songs': len(songs),
                'downloaded_songs': len(downloaded_ids),
                'new_songs': len(new_songs),
                'new_song_list': [{'id': s['id'], 'name': s['name'], 'artist': s['artist']} for s in new_songs[:20]]  # 只返回前20首
            }
        })
        
    except Exception as e:
        logger.error(f"检查歌单更新失败: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/playlists/sync-all', methods=['POST'])
@login_required
def sync_all_playlists():
    """同步所有启用的歌单"""
    try:
        playlists = config_manager.get_subscribed_playlists(enabled_only=True)
        
        if not playlists:
            return jsonify({
                'success': True,
                'message': '没有启用的订阅歌单',
                'data': {'total': 0, 'synced': 0}
            })
        
        download_dir = config_manager.get_config('netease_download_path', '/downloads/netease')
        
        from downloaders.netease import NeteaseDownloader
        downloader = NeteaseDownloader(config_manager)
        
        results = []
        total_new_songs = 0
        total_downloaded = 0
        total_failed = 0
        
        for playlist in playlists:
            try:
                result = downloader.download_playlist_incremental(
                    playlist_id=playlist['playlist_id'],
                    download_dir=download_dir
                )
                failed_count = result.get('failed_songs', 0)
                results.append({
                    'playlist_id': playlist['playlist_id'],
                    'playlist_name': playlist['playlist_name'],
                    'success': result.get('success', False),
                    'new_songs': result.get('new_songs', 0),
                    'downloaded': result.get('downloaded_songs', 0),
                    'failed': failed_count
                })
                total_new_songs += result.get('new_songs', 0)
                total_downloaded += result.get('downloaded_songs', 0)
                total_failed += failed_count
            except Exception as e:
                logger.error(f"同步歌单 {playlist['playlist_id']} 失败: {e}")
                results.append({
                    'playlist_id': playlist['playlist_id'],
                    'playlist_name': playlist['playlist_name'],
                    'success': False,
                    'error': str(e)
                })
        
        # 发送 TG 通知
        try:
            from web.tg_notifier import notify_all_playlists_sync_result
            notify_all_playlists_sync_result(config_manager, len(playlists), results)
        except Exception as e:
            logger.debug(f"发送 TG 通知失败: {e}")
        
        return jsonify({
            'success': True,
            'data': {
                'total': len(playlists),
                'results': results,
                'total_new_songs': total_new_songs,
                'total_downloaded': total_downloaded,
                'total_failed': total_failed
            }
        })
        
    except Exception as e:
        logger.error(f"同步所有歌单失败: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/playlists/<playlist_id>/failed-songs', methods=['GET'])
@login_required
def get_playlist_failed_songs(playlist_id: str):
    """获取歌单中下载失败的歌曲"""
    try:
        failed_songs = config_manager.get_failed_songs(playlist_id)
        return jsonify({
            'success': True,
            'data': failed_songs
        })
    except Exception as e:
        logger.error(f"获取失败歌曲失败: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/playlists/failed-songs', methods=['GET'])
@login_required
def get_all_failed_songs():
    """获取所有歌单中下载失败的歌曲"""
    try:
        failed_songs = config_manager.get_all_failed_songs()
        return jsonify({
            'success': True,
            'data': failed_songs
        })
    except Exception as e:
        logger.error(f"获取所有失败歌曲失败: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/playlists/<playlist_id>/retry-failed', methods=['POST'])
@login_required
def retry_failed_songs(playlist_id: str):
    """重试下载失败的歌曲"""
    try:
        # 获取失败的歌曲
        failed_songs = config_manager.get_failed_songs(playlist_id)
        
        if not failed_songs:
            return jsonify({
                'success': True,
                'message': '没有需要重试的歌曲',
                'data': {'retried': 0, 'success': 0}
            })
        
        download_dir = config_manager.get_config('netease_download_path', '/downloads/netease')
        
        from downloaders.netease import NeteaseDownloader
        downloader = NeteaseDownloader(config_manager)
        
        success_count = 0
        for song in failed_songs:
            # 清除失败状态
            config_manager.clear_song_fail_status(playlist_id, song['song_id'])
            
            # 尝试重新下载
            result = downloader.download_song(song['song_id'], download_dir)
            
            if result.get('success'):
                config_manager.mark_song_downloaded(playlist_id, song['song_id'])
                success_count += 1
            else:
                # 重新标记失败
                config_manager.mark_song_failed(playlist_id, song['song_id'], result.get('error', '重试失败'))
        
        return jsonify({
            'success': True,
            'data': {
                'retried': len(failed_songs),
                'success': success_count,
                'still_failed': len(failed_songs) - success_count
            }
        })
        
    except Exception as e:
        logger.error(f"重试下载失败: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/playlists/<playlist_id>/clear-failed', methods=['POST'])
@login_required
def clear_failed_songs(playlist_id: str):
    """清除歌单中所有失败记录（用于重新检测）"""
    try:
        failed_songs = config_manager.get_failed_songs(playlist_id)
        
        for song in failed_songs:
            config_manager.clear_song_fail_status(playlist_id, song['song_id'])
        
        return jsonify({
            'success': True,
            'message': f'已清除 {len(failed_songs)} 条失败记录'
        })
        
    except Exception as e:
        logger.error(f"清除失败记录失败: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/playlists/<playlist_id>/refresh-metadata', methods=['POST'])
@login_required
def refresh_playlist_metadata(playlist_id: str):
    """刷新歌单已下载歌曲的元数据（不重新下载）"""
    try:
        download_dir = config_manager.get_config('netease_download_path', '/downloads/netease')
        
        from downloaders.netease import NeteaseDownloader
        downloader = NeteaseDownloader(config_manager)
        
        result = downloader.refresh_playlist_metadata(
            playlist_id=playlist_id,
            download_dir=download_dir
        )
        
        return jsonify({
            'success': result.get('success', False),
            'data': result
        })
        
    except Exception as e:
        logger.error(f"刷新元数据失败: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/refresh-metadata', methods=['POST'])
@login_required
def refresh_all_metadata():
    """刷新下载目录中所有音乐文件的元数据"""
    try:
        download_dir = config_manager.get_config('netease_download_path', '/downloads/netease')
        
        from downloaders.netease import NeteaseDownloader
        downloader = NeteaseDownloader(config_manager)
        
        result = downloader.refresh_metadata(download_dir=download_dir)
        
        return jsonify({
            'success': result.get('success', False),
            'data': result
        })
        
    except Exception as e:
        logger.error(f"刷新元数据失败: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return jsonify({'success': False, 'error': str(e)}), 500


# ==================== 日志 API ====================

@app.route('/api/logs', methods=['GET'])
@login_required
def get_logs():
    """获取日志列表"""
    try:
        limit = request.args.get('limit', 100, type=int)
        offset = request.args.get('offset', 0, type=int)
        level = request.args.get('level', None)
        category = request.args.get('category', None)
        search = request.args.get('search', None)
        start_time = request.args.get('start_time', None)
        end_time = request.args.get('end_time', None)
        
        logs = config_manager.get_logs(
            limit=limit,
            offset=offset,
            level=level,
            category=category,
            search=search,
            start_time=start_time,
            end_time=end_time
        )
        
        total = config_manager.get_log_count(
            level=level,
            category=category,
            search=search,
            start_time=start_time,
            end_time=end_time
        )
        
        return jsonify({
            'success': True,
            'data': {
                'logs': logs,
                'total': total,
                'limit': limit,
                'offset': offset
            }
        })
    except Exception as e:
        logger.error(f"获取日志失败: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/logs/categories', methods=['GET'])
@login_required
def get_log_categories():
    """获取日志类别列表"""
    try:
        categories = config_manager.get_log_categories()
        return jsonify({
            'success': True,
            'data': categories
        })
    except Exception as e:
        logger.error(f"获取日志类别失败: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/logs/export', methods=['GET'])
@login_required
def export_logs():
    """导出日志"""
    try:
        category = request.args.get('category', None)
        level = request.args.get('level', None)
        start_time = request.args.get('start_time', None)
        end_time = request.args.get('end_time', None)
        format_type = request.args.get('format', 'json')
        
        content = config_manager.export_logs(
            category=category,
            level=level,
            start_time=start_time,
            end_time=end_time,
            format=format_type
        )
        
        # 设置响应类型
        if format_type == 'json':
            mimetype = 'application/json'
            filename = 'logs.json'
        elif format_type == 'csv':
            mimetype = 'text/csv'
            filename = 'logs.csv'
        else:
            mimetype = 'text/plain'
            filename = 'logs.txt'
        
        # 如果是元数据类别，在文件名中标注
        if category == 'metadata':
            filename = f'metadata_{filename}'
        
        from flask import Response
        response = Response(
            content,
            mimetype=mimetype,
            headers={'Content-Disposition': f'attachment; filename={filename}'}
        )
        return response
        
    except Exception as e:
        logger.error(f"导出日志失败: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/logs/clear', methods=['POST'])
@login_required
def clear_logs():
    """清理日志"""
    try:
        data = request.get_json() or {}
        before_date = data.get('before_date', None)
        category = data.get('category', None)
        
        deleted = config_manager.clear_logs(before_date=before_date, category=category)
        
        return jsonify({
            'success': True,
            'message': f'已清理 {deleted} 条日志',
            'deleted': deleted
        })
    except Exception as e:
        logger.error(f"清理日志失败: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/logs/stats', methods=['GET'])
@login_required
def get_log_stats():
    """获取日志统计信息"""
    try:
        # 获取各级别日志数量
        stats = {
            'total': config_manager.get_log_count(),
            'by_level': {
                'DEBUG': config_manager.get_log_count(level='DEBUG'),
                'INFO': config_manager.get_log_count(level='INFO'),
                'WARNING': config_manager.get_log_count(level='WARNING'),
                'ERROR': config_manager.get_log_count(level='ERROR'),
            },
            'categories': config_manager.get_log_categories()
        }
        
        # 获取元数据相关日志数量
        stats['metadata_count'] = config_manager.get_log_count(category='metadata')
        
        return jsonify({
            'success': True,
            'data': stats
        })
    except Exception as e:
        logger.error(f"获取日志统计失败: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


# ==================== 页面路由 ====================

@app.route('/login')
def login():
    """登录页面"""
    # 如果已登录，重定向到首页
    if session.get('logged_in'):
        return redirect(url_for('index'))
    return render_template('login.html')


@app.route('/logout')
def logout():
    """登出并重定向到登录页"""
    session.clear()
    return redirect(url_for('login'))


@app.route('/')
@login_required
def index():
    """主页"""
    return render_template('index.html')


@app.route('/settings')
@login_required
def settings():
    """设置页面"""
    return render_template('settings.html')


@app.route('/history')
@login_required
def history():
    """下载历史页面"""
    return render_template('history.html')


@app.route('/setup')
@login_required
def setup():
    """Telegram 设置页面"""
    return render_template('setup.html')


@app.route('/account')
@login_required
def account():
    """账号管理页面"""
    return render_template('account.html')


@app.route('/playlists')
@login_required
def playlists():
    """歌单订阅管理页面"""
    return render_template('playlists.html')


@app.route('/logs')
@login_required
def logs():
    """日志查看页面"""
    return render_template('logs.html')


# ==================== 静态文件 ====================

@app.route('/static/<path:filename>')
def serve_static(filename):
    """提供静态文件"""
    return send_from_directory(app.static_folder, filename)


# ==================== 错误处理 ====================

@app.errorhandler(404)
def not_found(e):
    """404 错误处理"""
    return jsonify({'success': False, 'error': 'Not Found'}), 404


@app.errorhandler(500)
def internal_error(e):
    """500 错误处理"""
    return jsonify({'success': False, 'error': 'Internal Server Error'}), 500


def run_server(host: str = '0.0.0.0', port: int = 5000, debug: bool = False):
    """运行 Web 服务器"""
    app.run(host=host, port=port, debug=debug, threaded=True)


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO)
    init_app()
    run_server(debug=True)
