#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Web 配置服务
提供 Web UI 界面管理配置
"""

import os
import sys
import logging
from pathlib import Path
from flask import Flask, Blueprint, jsonify, request, render_template, send_from_directory

# 添加父目录到路径
sys.path.insert(0, str(Path(__file__).parent.parent))

from config_manager import ConfigManager, get_config_manager

logger = logging.getLogger(__name__)

# 创建 Flask 应用
app = Flask(__name__, 
           template_folder='templates',
           static_folder='static')

# 配置管理器实例
config_manager: ConfigManager = None


def init_app(db_path: str = None):
    """初始化应用"""
    global config_manager
    config_manager = get_config_manager(db_path)
    
    # 注册 Telegram Setup Blueprint
    from .tg_setup import create_tg_setup_blueprint, init_tg_setup
    tg_setup_bp = create_tg_setup_blueprint()
    init_tg_setup(config_manager)
    app.register_blueprint(tg_setup_bp)
    
    return app


# ==================== API 路由 ====================

@app.route('/api/config', methods=['GET'])
def get_all_config():
    """获取所有配置"""
    try:
        config = config_manager.get_all_config()
        # 隐藏敏感信息
        safe_config = config.copy()
        sensitive_keys = ['telegram_bot_token', 'telegram_api_hash', 'telegram_session_string',
                         'netease_cookies', 'qbittorrent_password']
        for key in sensitive_keys:
            if key in safe_config and safe_config[key]:
                safe_config[key] = '******' if safe_config[key] else ''
        
        return jsonify({
            'success': True,
            'data': safe_config
        })
    except Exception as e:
        logger.error(f"获取配置失败: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/config', methods=['POST'])
def update_config():
    """更新配置"""
    try:
        data = request.get_json()
        if not data:
            return jsonify({'success': False, 'error': '无效的请求数据'}), 400
        
        # 过滤掉隐藏的敏感字段（值为 ******）
        filtered_data = {k: v for k, v in data.items() if v != '******'}
        
        if config_manager.update_config_batch(filtered_data):
            return jsonify({'success': True, 'message': '配置更新成功'})
        else:
            return jsonify({'success': False, 'error': '配置更新失败'}), 500
            
    except Exception as e:
        logger.error(f"更新配置失败: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/config/<key>', methods=['GET'])
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

@app.route('/')
def index():
    """主页"""
    return render_template('index.html')


@app.route('/settings')
def settings():
    """设置页面"""
    return render_template('settings.html')


@app.route('/history')
def history():
    """下载历史页面"""
    return render_template('history.html')


@app.route('/setup')
def setup():
    """Telegram 设置页面"""
    return render_template('setup.html')


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
