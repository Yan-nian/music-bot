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
