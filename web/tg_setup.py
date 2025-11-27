#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Telegram Session 生成器
简化版 - 用于生成 Telethon Session String
"""

import os
import sys
import json
import logging
import tempfile
import subprocess
from flask import Blueprint, jsonify, request, render_template

logger = logging.getLogger(__name__)


def get_proxy_from_config():
    """从配置获取代理设置"""
    try:
        # 尝试从配置管理器获取
        sys.path.insert(0, str(os.path.dirname(os.path.dirname(__file__))))
        from config_manager import get_config_manager
        config_manager = get_config_manager()
        
        if config_manager.get_config('proxy_enabled', False):
            return config_manager.get_config('proxy_host', '')
    except Exception as e:
        logger.warning(f"读取代理配置失败: {e}")
    
    # 回退到环境变量
    return os.getenv("PROXY_HOST", "")


def create_tg_setup_blueprint() -> Blueprint:
    """创建 Telegram 设置蓝图"""
    bp = Blueprint("tg_setup", __name__, url_prefix="/tg")
    
    @bp.route("/setup")
    def setup_page():
        """Telegram 设置页面"""
        return render_template("setup.html")
    
    @bp.post("/send_code")
    def send_code():
        """发送验证码"""
        try:
            data = request.get_json() or {}
            api_id = data.get("api_id")
            api_hash = data.get("api_hash")
            phone = data.get("phone")
            proxy_url = get_proxy_from_config()
            
            if not all([api_id, api_hash, phone]):
                return jsonify({"ok": False, "error": "缺少必要参数"}), 400
            
            logger.info(f"🔍 发送验证码到: {phone}")
            
            # 创建临时脚本
            script_content = f'''
import asyncio
import json
from telethon import TelegramClient
from telethon.sessions import StringSession
from urllib.parse import urlparse

async def send_code():
    try:
        proxy_config = None
        proxy_url = "{proxy_url}"
        if proxy_url and proxy_url.strip() and proxy_url != "None":
            try:
                p_url = urlparse(proxy_url.strip())
                if p_url.scheme and p_url.hostname and p_url.port:
                    proxy_config = (p_url.scheme, p_url.hostname, p_url.port)
            except:
                pass
        
        client = TelegramClient(
            StringSession(),
            {int(api_id)},
            "{api_hash}",
            proxy=proxy_config,
            connection_retries=3
        )
        
        await client.connect()
        code_result = await client.send_code_request("{phone}")
        session_string = client.session.save()
        await client.disconnect()
        
        result = {{
            "ok": True,
            "phone_code_hash": code_result.phone_code_hash,
            "temp_session_string": session_string
        }}
        print(json.dumps(result))
        
    except Exception as e:
        print(json.dumps({{"ok": False, "error": str(e)}}))

asyncio.run(send_code())
'''
            
            with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False) as f:
                f.write(script_content)
                script_path = f.name
            
            try:
                result = subprocess.run(
                    [sys.executable, script_path],
                    capture_output=True,
                    text=True,
                    timeout=60
                )
                
                if result.returncode == 0:
                    output = result.stdout.strip()
                    for line in output.split('\n'):
                        if line.strip().startswith('{'):
                            data = json.loads(line.strip())
                            if data.get("ok"):
                                return jsonify({
                                    "ok": True,
                                    "message": f"验证码已发送到 {phone}",
                                    "phone_code_hash": data.get("phone_code_hash"),
                                    "temp_session_string": data.get("temp_session_string")
                                })
                            return jsonify(data)
                    
                    return jsonify({"ok": False, "error": "无效的响应"})
                else:
                    return jsonify({"ok": False, "error": result.stderr or "执行失败"})
                    
            finally:
                try:
                    os.unlink(script_path)
                except:
                    pass
                    
        except Exception as e:
            logger.error(f"❌ 发送验证码失败: {e}")
            return jsonify({"ok": False, "error": str(e)})
    
    @bp.post("/confirm_code")
    def confirm_code():
        """确认验证码"""
        try:
            data = request.get_json() or {}
            api_id = data.get("api_id")
            api_hash = data.get("api_hash")
            phone = data.get("phone")
            code = data.get("code")
            phone_code_hash = data.get("phone_code_hash")
            temp_session_string = data.get("temp_session_string", "")
            proxy_url = get_proxy_from_config()
            
            if not all([api_id, api_hash, phone, code, phone_code_hash]):
                return jsonify({"ok": False, "error": "缺少必要参数"}), 400
            
            logger.info(f"🔍 确认验证码: {phone}")
            
            script_content = f'''
import asyncio
import json
from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.errors import PhoneCodeInvalidError, FloodWaitError
from urllib.parse import urlparse

async def confirm_code():
    try:
        proxy_config = None
        proxy_url = "{proxy_url}"
        if proxy_url and proxy_url.strip() and proxy_url != "None":
            try:
                p_url = urlparse(proxy_url.strip())
                if p_url.scheme and p_url.hostname and p_url.port:
                    proxy_config = (p_url.scheme, p_url.hostname, p_url.port)
            except:
                pass
        
        client = TelegramClient(
            StringSession("{temp_session_string}"),
            {int(api_id)},
            "{api_hash}",
            proxy=proxy_config
        )
        
        await client.connect()
        await client.sign_in("{phone}", "{code}", phone_code_hash="{phone_code_hash}")
        session_string = client.session.save()
        await client.disconnect()
        
        print(json.dumps({{"ok": True, "session_string": session_string}}))
        
    except PhoneCodeInvalidError:
        print(json.dumps({{"ok": False, "error": "验证码错误"}}))
    except FloodWaitError as e:
        print(json.dumps({{"ok": False, "error": f"请等待 {{e.seconds}} 秒后重试"}}))
    except Exception as e:
        print(json.dumps({{"ok": False, "error": str(e)}}))

asyncio.run(confirm_code())
'''
            
            with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False) as f:
                f.write(script_content)
                script_path = f.name
            
            try:
                result = subprocess.run(
                    [sys.executable, script_path],
                    capture_output=True,
                    text=True,
                    timeout=60
                )
                
                if result.returncode == 0:
                    output = result.stdout.strip()
                    for line in output.split('\n'):
                        if line.strip().startswith('{'):
                            return jsonify(json.loads(line.strip()))
                    return jsonify({"ok": False, "error": "无效的响应"})
                else:
                    return jsonify({"ok": False, "error": result.stderr or "执行失败"})
                    
            finally:
                try:
                    os.unlink(script_path)
                except:
                    pass
                    
        except Exception as e:
            logger.error(f"❌ 确认验证码失败: {e}")
            return jsonify({"ok": False, "error": str(e)})
    
    @bp.post("/save_session")
    def save_session():
        """保存 Session 到配置"""
        try:
            data = request.get_json() or {}
            session_string = data.get("session_string")
            
            if not session_string:
                return jsonify({"ok": False, "error": "缺少 session_string"}), 400
            
            # 保存到配置
            sys.path.insert(0, str(os.path.dirname(os.path.dirname(__file__))))
            from config_manager import get_config_manager
            config_manager = get_config_manager()
            
            if config_manager.set_config("telegram_session_string", session_string.strip()):
                # 同时保存到文件
                session_dir = "/app/cookies"
                os.makedirs(session_dir, exist_ok=True)
                session_file = os.path.join(session_dir, "telethon_session.txt")
                
                try:
                    with open(session_file, "w") as f:
                        f.write(session_string.strip())
                except:
                    pass
                
                return jsonify({"ok": True, "message": "Session 已保存"})
            else:
                return jsonify({"ok": False, "error": "保存失败"})
                
        except Exception as e:
            logger.error(f"❌ 保存 Session 失败: {e}")
            return jsonify({"ok": False, "error": str(e)})
    
    return bp


# 配置管理器引用
_config_manager = None


def init_tg_setup(config_manager):
    """初始化 tg_setup 模块"""
    global _config_manager
    _config_manager = config_manager
