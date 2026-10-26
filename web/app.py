#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Web 配置服务
提供 Web UI 界面管理配置
"""

import os
import sys
import json
import time
import logging
import hashlib
import secrets
import threading
from collections import defaultdict
from functools import wraps
from pathlib import Path
from flask import (Flask, Blueprint, jsonify, request, render_template,
                   session, redirect, url_for)
from werkzeug.security import generate_password_hash, check_password_hash

# 添加父目录到路径
sys.path.insert(0, str(Path(__file__).parent.parent))

from config_manager import ConfigManager, get_config_manager

logger = logging.getLogger(__name__)

# 创建 Flask 应用
app = Flask(__name__, 
           template_folder='templates',
           static_folder='static')

# 设置 session 密钥（从环境变量读取或使用固定值，避免每次重启生成新密钥导致 session 失效）
app.secret_key = os.environ.get('FLASK_SECRET_KEY') or secrets.token_hex(32)

# 禁用模板缓存，确保修改后立即生效（防止浏览器/CDN 缓存旧版 HTML）
app.config['SEND_FILE_MAX_AGE_DEFAULT'] = 0
app.config['TEMPLATES_AUTO_RELOAD'] = True


@app.after_request
def _no_cache_headers(response):
    """为所有 HTML 响应添加 no-cache 头，防止浏览器缓存旧版页面"""
    if 'text/html' in response.content_type:
        response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
        response.headers['Pragma'] = 'no-cache'
        response.headers['Expires'] = '0'
    return response


# 配置管理器实例
config_manager: ConfigManager = None

# 默认管理员账号
DEFAULT_USERNAME = 'admin'
DEFAULT_PASSWORD = 'admin'

# 登录失败限流（内存级，进程重启即清空）
_login_failures: dict = defaultdict(lambda: {'count': 0, 'until': 0.0})
_login_lock = threading.Lock()
LOGIN_MAX_FAILURES = 5
LOGIN_LOCKOUT_SECONDS = 300


def _client_ip() -> str:
    forwarded = request.headers.get('X-Forwarded-For', '')
    if forwarded:
        return forwarded.split(',')[0].strip()
    return request.remote_addr or 'unknown'


def _check_login_locked(ip: str) -> int:
    """返回剩余锁定秒数，0 表示未锁定"""
    with _login_lock:
        record = _login_failures[ip]
        remain = record['until'] - time.time()
        if remain > 0:
            return int(remain)
        return 0


def _record_login_failure(ip: str):
    with _login_lock:
        record = _login_failures[ip]
        record['count'] += 1
        if record['count'] >= LOGIN_MAX_FAILURES:
            record['until'] = time.time() + LOGIN_LOCKOUT_SECONDS
            record['count'] = 0
            logger.warning(f"🔒 IP {ip} 登录失败次数过多，锁定 {LOGIN_LOCKOUT_SECONDS}s")


def _reset_login_failures(ip: str):
    with _login_lock:
        _login_failures.pop(ip, None)


# 需要掩码的敏感配置项（不再明文返回前端）
SENSITIVE_KEYS = {
    'telegram_bot_token', 'netease_cookies', 'apple_music_session_string',
    'qbittorrent_password', 'flask_secret_key',
    'password_salt', 'admin_password',
}


def _get_password_salt() -> str:
    """获取密码盐（首次生成并持久化到配置）"""
    salt = config_manager.get_config('password_salt') if config_manager else ''
    if not salt:
        salt = secrets.token_hex(16)
        if config_manager:
            config_manager.set_config('password_salt', salt)
    return salt


def hash_password(password: str) -> str:
    """PBKDF2 哈希密码

    旧实现是单轮 sha256(salt+password)，对离线爆破几乎没有抵抗力。
    werkzeug 已经在依赖里（Flask 自带），换成 PBKDF2 零成本。
    """
    return generate_password_hash(password, method='pbkdf2:sha256:260000')


def verify_password(password: str, stored: str) -> bool:
    """验证密码，兼容三种历史格式"""
    if not stored:
        return False
    if stored.startswith(('pbkdf2:', 'scrypt:')):
        try:
            return check_password_hash(stored, password)
        except (ValueError, TypeError):
            return False
    if stored.startswith('salted:'):
        # 旧的 sha256(salt+password)
        salt = _get_password_salt()
        return 'salted:' + hashlib.sha256((salt + password).encode()).hexdigest() == stored
    # 最老的裸 sha256
    return hashlib.sha256(password.encode()).hexdigest() == stored


def _needs_rehash(stored: str) -> bool:
    return bool(stored) and not stored.startswith(('pbkdf2:', 'scrypt:'))


def mask_sensitive(config: dict) -> dict:
    """对敏感配置项掩码，避免明文返回前端"""
    masked = dict(config)
    for key in SENSITIVE_KEYS:
        if key in masked and masked[key]:
            masked[key] = '******'
    return masked


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

    # secret_key 动态化：优先环境变量，其次持久化配置，最后随机生成并持久化
    # 避免 session 被伪造，也避免重启后所有用户被登出
    secret = os.environ.get('FLASK_SECRET_KEY') or config_manager.get_config('flask_secret_key')
    if not secret:
        secret = secrets.token_hex(32)
        config_manager.set_config('flask_secret_key', secret)
    app.secret_key = secret

    # 初始化默认管理员账号。
    # 旧版本硬编码 admin/admin 且不提示修改；现在首次启动生成随机密码，
    # 通过 stdout 打印一次（docker logs 可见），避免公网部署被直接登录。
    if not config_manager.get_config('admin_username'):
        initial_password = os.environ.get('ADMIN_PASSWORD') or secrets.token_urlsafe(9)
        config_manager.set_config('admin_username', DEFAULT_USERNAME)
        config_manager.set_config('admin_password', hash_password(initial_password))
        print('=' * 62, flush=True)
        print('  首次启动，已生成管理后台账号', flush=True)
        print(f'  用户名: {DEFAULT_USERNAME}', flush=True)
        print(f'  密  码: {initial_password}', flush=True)
        print('  登录后请立即在「账号」页修改密码', flush=True)
        print('=' * 62, flush=True)

    # 注册 Telegram Setup Blueprint
    from .tg_setup import create_tg_setup_blueprint, init_tg_setup
    tg_setup_bp = create_tg_setup_blueprint()
    init_tg_setup(config_manager)
    app.register_blueprint(tg_setup_bp)

    # 队列并发数按配置初始化。
    # 之前只有 MusicBot.__init__ 会读 download_max_concurrent，--web-only 模式下
    # 没人读，队列永远停在硬编码的 3，设置页改的值重启后就对不上了。
    try:
        from download_queue import get_download_queue
        mc = config_manager.get_config('download_max_concurrent', 3)
        get_download_queue(max_concurrent=max(1, int(mc or 3)))
    except Exception:
        logger.exception("初始化下载队列并发数失败，沿用默认值")

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

        ip = _client_ip()
        locked = _check_login_locked(ip)
        if locked:
            return jsonify({
                'success': False,
                'error': f'尝试次数过多，请 {locked} 秒后再试'
            }), 429

        # 验证账号密码
        stored_username = config_manager.get_config('admin_username') or DEFAULT_USERNAME
        stored_password = config_manager.get_config('admin_password') or hash_password(DEFAULT_PASSWORD)
        
        if username == stored_username and verify_password(password, stored_password):
            # 旧格式（sha256）密码在登录成功时自动升级为 PBKDF2
            if _needs_rehash(stored_password):
                config_manager.set_config('admin_password', hash_password(password))
            _reset_login_failures(ip)
            session['logged_in'] = True
            session['username'] = username
            session.permanent = bool(remember)
            
            logger.info(f"用户 {username} 登录成功")
            return jsonify({
                'success': True,
                'message': '登录成功',
                'redirect': '/'
            })
        else:
            _record_login_failure(ip)
            logger.warning(f"用户 {username} 登录失败：密码错误 (IP: {ip})")
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


# ==================== 页面路由 ====================

@app.route('/')
@login_required
def index():
    """仪表盘页面"""
    return render_template('index.html')


@app.route('/settings')
@login_required
def settings():
    """系统设置页面"""
    return render_template('settings.html')


@app.route('/history')
@login_required
def history():
    """下载历史页面"""
    return render_template('history.html')


@app.route('/playlists')
@login_required
def playlists():
    """歌单订阅页面"""
    return render_template('playlists.html')


@app.route('/logs')
@login_required
def logs():
    """系统日志页面"""
    return render_template('logs.html')


@app.route('/account')
@login_required
def account():
    """账号管理页面"""
    return render_template('account.html')


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
        
        if len(new_password) < 8:
            return jsonify({'success': False, 'error': '新密码长度至少 8 位'}), 400
        
        # 验证当前密码
        stored_password = config_manager.get_config('admin_password') or hash_password(DEFAULT_PASSWORD)
        if not verify_password(current_password, stored_password):
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
        if not verify_password(password, stored_password):
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
        return jsonify({
            'success': True,
            'data': mask_sensitive(config)
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
        if key in SENSITIVE_KEYS and value:
            value = '******'
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
    """导出配置（敏感项按需脱敏）

    旧实现直接返回全量明文，包含 telegram_bot_token、netease_cookies、
    qbittorrent_password——一份导出文件泄露等于整套凭据泄露。
    """
    try:
        include_secrets = request.args.get('secrets', 'false').lower() == 'true'
        config = config_manager.get_all_config()
        if not include_secrets:
            config = mask_sensitive(config)
        return jsonify({
            'success': True,
            'data': json.dumps(config, ensure_ascii=False, indent=2)
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
                from downloaders import get_downloader
                downloader = get_downloader('netease', config_manager)
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
        from downloaders import get_downloader
        downloader = get_downloader('netease', config_manager)
        
        result = downloader.download_playlist_incremental(
            playlist_id=playlist_id,
            download_dir=download_dir
        )
        config_manager.record_download_result(
            platform, 'playlist', playlist_id, result, download_dir)
        
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
        from downloaders import get_downloader
        downloader = get_downloader('netease', config_manager)
        
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
        
        from downloaders import get_downloader
        downloader = get_downloader('netease', config_manager)
        
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
                config_manager.record_download_result(
                    'netease', 'playlist', playlist['playlist_id'], result, download_dir)
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
        
        from downloaders import get_downloader
        downloader = get_downloader('netease', config_manager)
        
        success_count = 0
        for song in failed_songs:
            # 清除失败状态
            config_manager.clear_song_fail_status(playlist_id, song['song_id'])
            
            # 尝试重新下载
            result = downloader.download_song(song['song_id'], download_dir)
            config_manager.record_download_result(
                'netease', 'song', str(song['song_id']), result, download_dir)
            
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
        
        from downloaders import get_downloader
        downloader = get_downloader('netease', config_manager)
        
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
        
        from downloaders import get_downloader
        downloader = get_downloader('netease', config_manager)
        
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


# ==================== 错误处理 ====================

@app.errorhandler(404)
def not_found(e):
    """404 错误处理：API 返回 JSON，页面返回简单提示"""
    if request.path.startswith('/api/'):
        return jsonify({'success': False, 'error': 'Not Found'}), 404
    return ('<div style="font-family:system-ui;padding:60px;text-align:center;color:#4b5563">'
            '<h2 style="font-weight:600">404 · 页面不存在</h2>'
            '<p><a href="/" style="color:#4f46e5">返回首页</a></p></div>'), 404


@app.errorhandler(500)
def internal_error(e):
    """500 错误处理"""
    logger.exception(f"未捕获的服务器错误: {e}")
    if request.path.startswith('/api/'):
        return jsonify({'success': False, 'error': 'Internal Server Error'}), 500
    return ('<div style="font-family:system-ui;padding:60px;text-align:center;color:#4b5563">'
            '<h2 style="font-weight:600">500 · 服务端错误</h2>'
            '<p>详细信息请查看运行日志。</p>'
            '<p><a href="/logs" style="color:#4f46e5">查看日志</a> · '
            '<a href="/" style="color:#4f46e5">返回首页</a></p></div>'), 500


def run_server(host: str = '0.0.0.0', port: int = 5000, debug: bool = False):
    """运行 Web 服务器"""
    app.run(host=host, port=port, debug=debug, threaded=True)


# ==================== 下载队列 API ====================

@app.route('/api/queue/status', methods=['GET'])
@login_required
def api_queue_status():
    """获取下载队列状态"""
    try:
        from download_queue import get_download_queue
        return jsonify({'success': True, 'data': get_download_queue().get_status()})
    except Exception as e:
        logger.error(f"获取队列状态失败: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/queue/config', methods=['POST'])
@login_required
def api_queue_config():
    """更新队列配置（最大并发数）"""
    try:
        data = request.get_json() or {}
        mc = data.get('max_concurrent')
        if mc and isinstance(mc, int) and mc >= 1:
            from download_queue import get_download_queue
            queue = get_download_queue()
            # 信号量属于 Bot 的事件循环，必须切线程改，不能在 Flask 线程里直接动。
            # --web-only 下没有事件循环时，实现里会退回直接写内存。
            queue.set_max_concurrent_threadsafe(mc)
            config_manager.set_config('download_max_concurrent', mc)
            # 回读实际生效值：原来不管有没有生效都回一句「已设为 N」，
            # web-only 模式下其实被忽略了，前端却以为成功。
            effective = queue.max_concurrent
            return jsonify({
                'success': True,
                'message': f'最大并发数已设为 {effective}',
                'data': {'max_concurrent': effective},
            })
        return jsonify({'success': False, 'error': 'invalid max_concurrent'}), 400
    except Exception as e:
        logger.error(f"更新队列配置失败: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/queue/cancel/<task_id>', methods=['POST'])
@login_required
def api_queue_cancel(task_id: str):
    """取消排队中的任务"""
    try:
        from download_queue import get_download_queue
        ok = get_download_queue().cancel_threadsafe(task_id)
        return jsonify({'success': ok, 'message': '已请求取消' if ok else '任务不存在或已结束'})
    except Exception as e:
        logger.error(f"取消任务失败: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


# ==================== 网易云登录态 / Cookie 自动刷新 ====================

@app.route('/api/telegram/test', methods=['POST'])
@login_required
def api_telegram_test():
    """给配置里的用户发一条测试消息，验证 token / 代理 / 用户 ID 是否正确"""
    try:
        from .tg_notifier import send_telegram_notification
        message = (
            "✅ Music Bot 连通性测试\n\n"
            "如果你看到这条消息，说明 Bot Token、代理与用户 ID 都配置正确。"
        )
        ok = send_telegram_notification(config_manager, message)
        if ok:
            return jsonify({'success': True, 'message': '已发送，请查看 Telegram'})
        return jsonify({
            'success': False,
            'error': '发送失败，请检查 Bot Token、允许的用户 ID 与代理设置'
        }), 400
    except Exception as e:
        logger.error(f"发送测试消息失败: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


def _netease_keeper():
    """取共享的 Cookie 守护实例"""
    from cookie_keeper import get_keeper
    return get_keeper(config_manager)


@app.route('/api/netease/status', methods=['GET'])
@login_required
def api_netease_status():
    """网易云登录态（读缓存，不发网络请求）"""
    try:
        keeper = _netease_keeper()
        meta = keeper.get_meta()
        cookies = keeper.get_cookies()
        expires_at = meta.get('expires_at')
        days_left = None
        if expires_at:
            days_left = round((expires_at / 1000 - time.time()) / 86400, 1)

        return jsonify({
            'success': True,
            'data': {
                'state': meta.get('state', 'unknown'),
                'nickname': meta.get('nickname', ''),
                'vip_label': meta.get('vip_label', ''),
                'avatar': meta.get('avatar', ''),
                'checked_at': meta.get('checked_at'),
                'refreshed_at': meta.get('refreshed_at'),
                'expires_at': expires_at,
                'days_left': days_left,
                'last_error': meta.get('last_error', ''),
                'has_cookies': bool(cookies),
                'cookie_count': len(cookies.split(';')) if cookies else 0,
                'auto_refresh': bool(config_manager.get_config('netease_auto_refresh_enabled', True)),
                'check_interval': config_manager.get_config('netease_cookie_check_interval', 1800),
            }
        })
    except Exception as e:
        logger.error(f"获取网易云状态失败: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/netease/check', methods=['POST'])
@login_required
def api_netease_check():
    """立即检测登录态（会发一次网络请求）"""
    try:
        meta = _netease_keeper().check_once()
        return jsonify({'success': True, 'data': meta})
    except Exception as e:
        logger.error(f"检测网易云登录态失败: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/netease/refresh', methods=['POST'])
@login_required
def api_netease_refresh():
    """手动续期会话"""
    try:
        keeper = _netease_keeper()
        from downloaders.netease_auth import NeteaseAuth
        auth = NeteaseAuth(config_manager)
        result = auth.refresh(keeper.get_cookies())
        if result.get('ok'):
            keeper.save_cookies(result['cookies'], result.get('expires_at'), source='refresh')
            return jsonify({'success': True, 'message': '会话已续期', 'data': result})
        return jsonify({'success': False, 'error': result.get('message', '续期失败')}), 400
    except Exception as e:
        logger.error(f"续期失败: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/netease/cookies', methods=['POST'])
@login_required
def api_netease_cookies():
    """手动保存 cookie 字符串"""
    try:
        data = request.get_json() or {}
        cookie_str = (data.get('cookies') or '').strip()
        if not cookie_str:
            return jsonify({'success': False, 'error': 'cookie 不能为空'}), 400

        keeper = _netease_keeper()
        from downloaders.netease_auth import NeteaseAuth
        info = NeteaseAuth(config_manager).check(cookie_str)
        if not info.get('logged_in'):
            return jsonify({
                'success': False,
                'error': 'cookie 无效或已过期，请用扫码登录'
            }), 400

        keeper.save_cookies(cookie_str, None, account=info, source='manual')
        return jsonify({
            'success': True,
            'message': f"已登录为 {info.get('nickname')}",
            'data': info,
        })
    except Exception as e:
        logger.error(f"保存 cookie 失败: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/netease/logout', methods=['POST'])
@login_required
def api_netease_logout():
    """清除已保存的 cookie"""
    try:
        _netease_keeper().clear_cookies()
        return jsonify({'success': True, 'message': '已退出网易云登录'})
    except Exception as e:
        logger.error(f"清除 cookie 失败: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/netease/qr', methods=['POST'])
@login_required
def api_netease_qr_create():
    """生成扫码登录二维码"""
    try:
        from downloaders.netease_auth import NeteaseAuth
        result = NeteaseAuth(config_manager).qr_create()
        if not result.get('ok'):
            return jsonify({'success': False, 'error': result.get('message', '生成失败')}), 400
        return jsonify({'success': True, 'data': result})
    except Exception as e:
        logger.error(f"生成二维码失败: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/netease/qr/<unikey>', methods=['GET'])
@login_required
def api_netease_qr_check(unikey: str):
    """轮询扫码结果；803 时自动写回 cookie"""
    try:
        from downloaders.netease_auth import NeteaseAuth
        result = NeteaseAuth(config_manager).qr_check(unikey)
        code = result.get('code')

        if code == 803 and result.get('cookies'):
            keeper = _netease_keeper()
            keeper.save_cookies(
                result['cookies'],
                result.get('expires_at'),
                account=result.get('account') or {'logged_in': True},
                source='qrcode',
            )
            account = result.get('account') or {}
            return jsonify({
                'success': True,
                'data': {
                    'code': code,
                    'message': '登录成功',
                    'nickname': account.get('nickname', ''),
                }
            })

        return jsonify({'success': True, 'data': {'code': code, 'message': result.get('message', '')}})
    except Exception as e:
        logger.error(f"扫码轮询失败: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


# ==================== 后台任务 API ====================

def _job_payload(job):
    return job.to_dict()


@app.route('/api/jobs', methods=['GET'])
@login_required
def api_jobs():
    """任务列表"""
    from .jobs import job_manager
    kind = request.args.get('kind')
    return jsonify({'success': True, 'data': job_manager.list(kind=kind)})


@app.route('/api/jobs/<job_id>', methods=['GET'])
@login_required
def api_job_detail(job_id: str):
    """任务详情（前端轮询用）"""
    from .jobs import job_manager
    job = job_manager.get(job_id)
    if not job:
        return jsonify({'success': False, 'error': '任务不存在'}), 404
    return jsonify({'success': True, 'data': _job_payload(job)})


@app.route('/api/jobs/<job_id>/cancel', methods=['POST'])
@login_required
def api_job_cancel(job_id: str):
    from .jobs import job_manager
    ok = job_manager.cancel(job_id)
    return jsonify({'success': ok, 'message': '已请求取消' if ok else '无法取消'})


@app.route('/api/jobs/playlist-sync', methods=['POST'])
@login_required
def api_job_playlist_sync():
    """异步同步单个歌单"""
    try:
        data = request.get_json() or {}
        playlist_id = str(data.get('playlist_id', '')).strip()
        platform = data.get('platform', 'netease')
        if not playlist_id:
            return jsonify({'success': False, 'error': '缺少 playlist_id'}), 400

        from .jobs import job_manager
        from downloaders import get_downloader

        playlist = config_manager.get_subscribed_playlist(playlist_id, platform) or {}
        title = playlist.get('playlist_name') or f'歌单 {playlist_id}'

        def _run(job):
            downloader = get_downloader(platform, config_manager)
            path_key = 'netease_download_path' if platform == 'netease' else f'{platform}_download_path'
            download_dir = config_manager.get_config(path_key, '/downloads')
            job.update(progress=1, total=3, message='拉取歌单信息')
            result = downloader.download_playlist_incremental(playlist_id, download_dir)
            job.update(progress=3, message='写入记录')
            # Web 触发的同步必须自己写历史，否则仪表盘的累计/今日/容量永远是 0
            config_manager.record_download_result(
                platform, 'playlist', playlist_id, result, download_dir)
            return {'success': bool(result.get('success')), 'detail': result}

        job = job_manager.submit('playlist_sync', f'同步歌单 · {title}', _run, total=3)
        return jsonify({'success': True, 'data': _job_payload(job)})
    except Exception as e:
        logger.exception(f"提交歌单同步任务失败: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/jobs/playlist-sync-all', methods=['POST'])
@login_required
def api_job_playlist_sync_all():
    """异步同步全部启用的歌单"""
    try:
        from .jobs import job_manager
        from downloaders import get_downloader

        playlists = config_manager.get_subscribed_playlists(enabled_only=True)
        if not playlists:
            return jsonify({'success': False, 'error': '没有启用的订阅歌单'}), 400

        def _run(job):
            job.update(total=len(playlists), progress=0)
            downloader = get_downloader('netease', config_manager)
            download_dir = config_manager.get_config('netease_download_path', '/downloads/netease')
            ok = 0
            for index, item in enumerate(playlists, 1):
                if job.cancelled:
                    break
                name = item.get('playlist_name') or item['playlist_id']
                job.update(progress=index - 1, message=f'({index}/{len(playlists)}) {name}')
                try:
                    result = downloader.download_playlist_incremental(
                        item['playlist_id'], download_dir)
                    config_manager.record_download_result(
                        'netease', 'playlist', item['playlist_id'], result, download_dir)
                    ok += 1 if result.get('success') else 0
                except Exception as e:  # noqa: BLE001
                    logger.warning(f"同步歌单 {name} 失败: {e}")
                job.update(progress=index)
            return {'synced': ok, 'total': len(playlists)}

        job = job_manager.submit('playlist_sync_all', f'同步全部歌单（{len(playlists)} 个）',
                                 _run, total=len(playlists))
        return jsonify({'success': True, 'data': _job_payload(job)})
    except Exception as e:
        logger.exception(f"提交全量同步任务失败: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/jobs/retry-failed', methods=['POST'])
@login_required
def api_job_retry_failed():
    """异步重试某个歌单的失败曲目"""
    try:
        data = request.get_json() or {}
        playlist_id = str(data.get('playlist_id', '')).strip()
        if not playlist_id:
            return jsonify({'success': False, 'error': '缺少 playlist_id'}), 400

        from .jobs import job_manager
        from downloaders import get_downloader

        failed = config_manager.get_failed_songs(playlist_id)
        if not failed:
            return jsonify({'success': False, 'error': '没有失败的曲目'}), 400

        def _run(job):
            downloader = get_downloader('netease', config_manager)
            download_dir = config_manager.get_config('netease_download_path', '/downloads/netease')
            job.update(total=len(failed), progress=0)
            done = 0
            for index, song in enumerate(failed, 1):
                if job.cancelled:
                    break
                job.update(progress=index - 1,
                           message=f"({index}/{len(failed)}) {song.get('song_name')}")
                try:
                    song_result = downloader.download_song(song['song_id'], download_dir)
                    config_manager.record_download_result(
                        'netease', 'song', str(song['song_id']), song_result, download_dir)
                    config_manager.mark_song_downloaded(playlist_id, song['song_id'])
                    config_manager.clear_song_fail_status(playlist_id, song['song_id'])
                    done += 1
                except Exception as e:  # noqa: BLE001
                    logger.warning(f"重试失败曲目出错: {e}")
                job.update(progress=index)
            return {'retried': done, 'total': len(failed)}

        job = job_manager.submit('retry_failed', f'重试失败曲目（{len(failed)} 首）',
                                 _run, total=len(failed))
        return jsonify({'success': True, 'data': _job_payload(job)})
    except Exception as e:
        logger.exception(f"提交重试任务失败: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/jobs/refresh-metadata', methods=['POST'])
@login_required
def api_job_refresh_metadata():
    """异步刷新已下载文件的元数据"""
    try:
        data = request.get_json() or {}
        playlist_id = str(data.get('playlist_id', '')).strip()

        from .jobs import job_manager
        from downloaders import get_downloader

        def _run(job):
            downloader = get_downloader('netease', config_manager)
            download_dir = config_manager.get_config('netease_download_path', '/downloads/netease')
            job.update(total=1, progress=0, message='扫描并修复元数据')
            if playlist_id:
                result = downloader.refresh_playlist_metadata(playlist_id, download_dir)
            else:
                result = downloader.refresh_metadata(download_dir)
            return result

        job = job_manager.submit('refresh_metadata', '刷新元数据', _run, total=1)
        return jsonify({'success': True, 'data': _job_payload(job)})
    except Exception as e:
        logger.exception(f"提交元数据任务失败: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


# ==================== 仪表盘聚合接口 ====================

@app.route('/api/dashboard', methods=['GET'])
@login_required
def api_dashboard():
    """首页一次拿齐：统计 + 队列 + 最近下载 + 登录态

    旧首页 4 个统计卡片是写死的假数据（1234 / 1180 / 3 / 56），
    这里把真实数据一次性聚合出来，省得前端发 4 个请求。

    注意：统计必须走 SQL 聚合（get_history_stats），不能拿 history 列表
    的长度去数——之前用 get_download_history(limit=200) 算总数，
    历史超过 200 条之后「累计下载」就永远停在 200 不再增长。
    """
    try:
        from download_queue import get_download_queue

        stats = config_manager.get_history_stats()
        # 曲库容量以磁盘为准：历史表里的字节数既可能缺失（老数据存的是曲目数），
        # 也可能压根没写过（Web 触发的下载历史上不写历史）。扫不到文件时才回退到历史。
        library = config_manager.get_library_stats()
        size_bytes = library['bytes'] if library['files'] else stats['total_bytes']
        queue = get_download_queue().get_status()
        playlists = config_manager.get_subscribed_playlists()
        meta = _netease_keeper().get_meta()

        return jsonify({
            'success': True,
            'data': {
                'stats': {
                    'total_downloads': stats['total'],
                    'today_downloads': stats['today'],
                    'total_size': size_bytes,
                    'size_source': 'disk' if library['files'] else 'history',
                    'library_files': library['files'],
                    'library_missing': library['missing'],
                    'library_scanned_at': library['scanned_at'],
                    'history_bytes': stats['total_bytes'],
                    'playlist_count': len(playlists),
                    'active_playlists': sum(1 for p in playlists if p.get('enabled')),
                    'song_count': stats['song_count'],
                    'album_count': stats['album_count'],
                    'bundle_count': stats['playlist_count'],
                },
                'queue': queue,
                'netease': {
                    'state': meta.get('state', 'unknown'),
                    'nickname': meta.get('nickname', ''),
                    'vip_label': meta.get('vip_label', ''),
                },
                # 最近 8 条单独查，不要把整张表捞出来再切
                'recent': config_manager.get_download_history(limit=8),
            }
        })
    except Exception as e:
        logger.exception(f"获取仪表盘数据失败: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/library/rescan', methods=['POST'])
@login_required
def api_library_rescan():
    """强制重扫曲库目录，返回真实的音频文件数与总大小。"""
    try:
        result = config_manager.get_library_stats(force=True)
        return jsonify({
            'success': True,
            'data': {
                'bytes': result['bytes'],
                'files': result['files'],
                'roots': result['roots'],
                'missing': result['missing'],
                'elapsed': result.get('elapsed', 0),
            }
        })
    except Exception as e:
        logger.exception(f"扫描曲库失败: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO)
    init_app()
    run_server(debug=True)
