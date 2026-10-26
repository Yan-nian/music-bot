#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Telegram Session 生成器
优化版 - 支持异步操作、两步验证、更好的错误处理
"""

import os
import sys
import json
import asyncio
import logging
from typing import Optional, Dict, Any, Tuple
from urllib.parse import urlparse
from flask import Blueprint, jsonify, request, render_template, redirect

logger = logging.getLogger(__name__)

# Telethon 导入
try:
    from telethon import TelegramClient
    from telethon.sessions import StringSession
    from telethon.errors import (
        PhoneCodeInvalidError,
        PhoneCodeExpiredError,
        FloodWaitError,
        SessionPasswordNeededError,
        PasswordHashInvalidError,
        PhoneNumberInvalidError,
        ApiIdInvalidError,
    )
    TELETHON_AVAILABLE = True
except ImportError:
    TELETHON_AVAILABLE = False
    logger.warning("⚠️ Telethon 未安装，Telegram Session 功能不可用")


# 配置管理器引用
_config_manager = None


def init_tg_setup(config_manager):
    """初始化 tg_setup 模块"""
    global _config_manager
    _config_manager = config_manager


def get_proxy_config() -> Optional[Tuple[str, str, int]]:
    """
    获取代理配置
    
    Returns:
        代理配置元组 (scheme, host, port) 或 None
    """
    proxy_url = None
    
    # 尝试从配置管理器获取
    if _config_manager:
        try:
            if _config_manager.get_config('proxy_enabled', False):
                proxy_url = _config_manager.get_config('proxy_host', '')
        except Exception as e:
            logger.warning(f"读取代理配置失败: {e}")
    
    # 回退到环境变量
    if not proxy_url:
        proxy_url = os.getenv("PROXY_HOST", "")
    
    # 解析代理 URL
    if proxy_url and proxy_url.strip():
        try:
            parsed = urlparse(proxy_url.strip())
            if parsed.scheme and parsed.hostname and parsed.port:
                return (parsed.scheme, parsed.hostname, parsed.port)
        except Exception as e:
            logger.warning(f"解析代理 URL 失败: {e}")
    
    return None


class TelegramSessionManager:
    """Telegram Session 管理器"""
    
    def __init__(self, api_id: int, api_hash: str, proxy: Optional[Tuple] = None):
        """
        初始化 Session 管理器
        
        Args:
            api_id: Telegram API ID
            api_hash: Telegram API Hash
            proxy: 代理配置 (scheme, host, port)
        """
        self.api_id = api_id
        self.api_hash = api_hash
        self.proxy = proxy
        self.client: Optional[TelegramClient] = None
        self.phone_code_hash: Optional[str] = None
    
    async def connect(self, session_string: str = "") -> bool:
        """
        连接到 Telegram
        
        Args:
            session_string: 现有的 session string（可选）
        
        Returns:
            是否连接成功
        """
        try:
            self.client = TelegramClient(
                StringSession(session_string),
                self.api_id,
                self.api_hash,
                proxy=self.proxy,
                connection_retries=3,
                timeout=30,
            )
            await self.client.connect()
            return True
        except Exception as e:
            logger.error(f"连接 Telegram 失败: {e}")
            raise
    
    async def disconnect(self):
        """断开连接"""
        if self.client:
            try:
                await self.client.disconnect()
            except Exception:
                pass
            self.client = None
    
    async def send_code(self, phone: str) -> Dict[str, Any]:
        """
        发送验证码
        
        Args:
            phone: 手机号（带国际区号）
        
        Returns:
            包含 phone_code_hash 和 session_string 的字典
        """
        if not self.client:
            raise RuntimeError("未连接到 Telegram")
        
        try:
            result = await self.client.send_code_request(phone)
            self.phone_code_hash = result.phone_code_hash
            
            return {
                "ok": True,
                "phone_code_hash": result.phone_code_hash,
                "temp_session_string": self.client.session.save(),
                "message": f"验证码已发送到 {phone}",
            }
        except PhoneNumberInvalidError:
            return {"ok": False, "error": "手机号格式无效，请使用国际格式（如 +8613800138000）"}
        except ApiIdInvalidError:
            return {"ok": False, "error": "API ID 或 API Hash 无效"}
        except FloodWaitError as e:
            return {"ok": False, "error": f"请求过于频繁，请等待 {e.seconds} 秒后重试"}
        except Exception as e:
            logger.error(f"发送验证码失败: {e}")
            return {"ok": False, "error": str(e)}
    
    async def sign_in(self, phone: str, code: str, phone_code_hash: str, 
                      password: Optional[str] = None) -> Dict[str, Any]:
        """
        验证登录
        
        Args:
            phone: 手机号
            code: 验证码
            phone_code_hash: 验证码哈希
            password: 两步验证密码（可选）
        
        Returns:
            包含 session_string 的字典
        """
        if not self.client:
            raise RuntimeError("未连接到 Telegram")
        
        try:
            # 尝试使用验证码登录
            await self.client.sign_in(phone, code, phone_code_hash=phone_code_hash)
            
            return {
                "ok": True,
                "session_string": self.client.session.save(),
                "message": "登录成功",
            }
        
        except SessionPasswordNeededError:
            # 需要两步验证
            if password:
                try:
                    await self.client.sign_in(password=password)
                    return {
                        "ok": True,
                        "session_string": self.client.session.save(),
                        "message": "登录成功",
                    }
                except PasswordHashInvalidError:
                    return {"ok": False, "error": "两步验证密码错误", "need_2fa": True}
            else:
                return {
                    "ok": False, 
                    "error": "此账号启用了两步验证，请输入密码",
                    "need_2fa": True,
                }
        
        except PhoneCodeInvalidError:
            return {"ok": False, "error": "验证码错误"}
        except PhoneCodeExpiredError:
            return {"ok": False, "error": "验证码已过期，请重新获取"}
        except FloodWaitError as e:
            return {"ok": False, "error": f"请等待 {e.seconds} 秒后重试"}
        except Exception as e:
            logger.error(f"登录失败: {e}")
            return {"ok": False, "error": str(e)}
    
    def get_session_string(self) -> Optional[str]:
        """获取当前 session string"""
        if self.client:
            return self.client.session.save()
        return None


def run_async(coro):
    """
    运行异步协程（兼容不同环境）
    """
    try:
        # 尝试获取当前事件循环
        loop = asyncio.get_event_loop()
        if loop.is_running():
            # 如果循环正在运行，创建新线程执行
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor() as executor:
                future = executor.submit(asyncio.run, coro)
                return future.result(timeout=120)
        else:
            return loop.run_until_complete(coro)
    except RuntimeError:
        # 没有事件循环，创建新的
        return asyncio.run(coro)


def create_tg_setup_blueprint() -> Blueprint:
    """创建 Telegram 设置蓝图"""
    bp = Blueprint("tg_setup", __name__, url_prefix="/tg")

    @bp.before_request
    def _require_login():
        """整个蓝图都需要登录。

        这块能拿到 Telegram 账号的完整登录凭据，原来没有任何鉴权，
        任何人访问 /tg/setup 都能生成别人的 Session。
        """
        from flask import session, request as _req
        if not session.get("logged_in"):
            if _req.method == "GET":
                return redirect("/login")
            return jsonify({"ok": False, "error": "未登录"}), 401
        return None

    @bp.route("/setup")
    def setup_page():
        """Telegram 设置页面"""
        return render_template("setup.html")
    
    @bp.post("/send_code")
    def send_code():
        """发送验证码 API"""
        if not TELETHON_AVAILABLE:
            return jsonify({"ok": False, "error": "Telethon 未安装"}), 500
        
        try:
            data = request.get_json() or {}
            api_id = data.get("api_id")
            api_hash = data.get("api_hash")
            phone = data.get("phone")
            
            # 参数验证
            if not api_id:
                return jsonify({"ok": False, "error": "请输入 API ID"}), 400
            if not api_hash:
                return jsonify({"ok": False, "error": "请输入 API Hash"}), 400
            if not phone:
                return jsonify({"ok": False, "error": "请输入手机号"}), 400
            
            try:
                api_id = int(api_id)
            except ValueError:
                return jsonify({"ok": False, "error": "API ID 必须是数字"}), 400
            
            # 格式化手机号
            phone = phone.strip().replace(" ", "")
            if not phone.startswith("+"):
                phone = "+" + phone
            
            logger.info(f"📤 发送验证码到: {phone}")
            
            # 获取代理配置
            proxy = get_proxy_config()
            if proxy:
                logger.info(f"🌐 使用代理: {proxy[0]}://{proxy[1]}:{proxy[2]}")
            
            # 执行异步操作
            async def do_send_code():
                manager = TelegramSessionManager(api_id, api_hash, proxy)
                try:
                    await manager.connect()
                    result = await manager.send_code(phone)
                    return result
                finally:
                    await manager.disconnect()
            
            result = run_async(do_send_code())
            
            if result.get("ok"):
                return jsonify(result)
            else:
                return jsonify(result), 400
        
        except Exception as e:
            logger.error(f"❌ 发送验证码失败: {e}")
            return jsonify({"ok": False, "error": str(e)}), 500
    
    @bp.post("/confirm_code")
    def confirm_code():
        """确认验证码 API"""
        if not TELETHON_AVAILABLE:
            return jsonify({"ok": False, "error": "Telethon 未安装"}), 500
        
        try:
            data = request.get_json() or {}
            api_id = data.get("api_id")
            api_hash = data.get("api_hash")
            phone = data.get("phone")
            code = data.get("code")
            phone_code_hash = data.get("phone_code_hash")
            temp_session_string = data.get("temp_session_string", "")
            password = data.get("password")  # 两步验证密码
            
            # 参数验证
            if not all([api_id, api_hash, phone, code, phone_code_hash]):
                return jsonify({"ok": False, "error": "缺少必要参数"}), 400
            
            try:
                api_id = int(api_id)
            except ValueError:
                return jsonify({"ok": False, "error": "API ID 必须是数字"}), 400
            
            # 格式化手机号
            phone = phone.strip().replace(" ", "")
            if not phone.startswith("+"):
                phone = "+" + phone
            
            logger.info(f"🔐 确认验证码: {phone}")
            
            # 获取代理配置
            proxy = get_proxy_config()
            
            # 执行异步操作
            async def do_confirm_code():
                manager = TelegramSessionManager(api_id, api_hash, proxy)
                try:
                    await manager.connect(temp_session_string)
                    result = await manager.sign_in(phone, code, phone_code_hash, password)
                    return result
                finally:
                    await manager.disconnect()
            
            result = run_async(do_confirm_code())
            
            if result.get("ok"):
                return jsonify(result)
            else:
                status_code = 400
                if result.get("need_2fa"):
                    status_code = 401  # 需要两步验证
                return jsonify(result), status_code
        
        except Exception as e:
            logger.error(f"❌ 确认验证码失败: {e}")
            return jsonify({"ok": False, "error": str(e)}), 500
    
    @bp.post("/verify_2fa")
    def verify_2fa():
        """两步验证 API"""
        if not TELETHON_AVAILABLE:
            return jsonify({"ok": False, "error": "Telethon 未安装"}), 500
        
        try:
            data = request.get_json() or {}
            api_id = data.get("api_id")
            api_hash = data.get("api_hash")
            phone = data.get("phone")
            code = data.get("code")
            phone_code_hash = data.get("phone_code_hash")
            temp_session_string = data.get("temp_session_string", "")
            password = data.get("password")
            
            # 参数验证
            if not all([api_id, api_hash, phone, code, phone_code_hash, password]):
                return jsonify({"ok": False, "error": "缺少必要参数"}), 400
            
            try:
                api_id = int(api_id)
            except ValueError:
                return jsonify({"ok": False, "error": "API ID 必须是数字"}), 400
            
            # 格式化手机号
            phone = phone.strip().replace(" ", "")
            if not phone.startswith("+"):
                phone = "+" + phone
            
            logger.info(f"🔐 两步验证: {phone}")
            
            # 获取代理配置
            proxy = get_proxy_config()
            
            # 执行异步操作
            async def do_verify_2fa():
                manager = TelegramSessionManager(api_id, api_hash, proxy)
                try:
                    await manager.connect(temp_session_string)
                    result = await manager.sign_in(phone, code, phone_code_hash, password)
                    return result
                finally:
                    await manager.disconnect()
            
            result = run_async(do_verify_2fa())
            
            if result.get("ok"):
                return jsonify(result)
            else:
                return jsonify(result), 400
        
        except Exception as e:
            logger.error(f"❌ 两步验证失败: {e}")
            return jsonify({"ok": False, "error": str(e)}), 500
    
    @bp.post("/save_session")
    def save_session():
        """保存 Session 到配置"""
        try:
            data = request.get_json() or {}
            session_string = data.get("session_string")
            api_id = data.get("api_id")
            api_hash = data.get("api_hash")
            
            if not session_string:
                return jsonify({"ok": False, "error": "缺少 session_string"}), 400
            
            session_string = session_string.strip()
            
            # 保存到配置管理器
            if _config_manager:
                success = True
                success &= _config_manager.set_config("telegram_session_string", session_string)
                
                # 同时保存 API ID 和 API Hash
                if api_id:
                    success &= _config_manager.set_config("telegram_api_id", str(api_id))
                if api_hash:
                    success &= _config_manager.set_config("telegram_api_hash", api_hash)
                
                if success:
                    logger.info("✅ Telegram Session 已保存到配置")
                else:
                    return jsonify({"ok": False, "error": "保存到数据库失败"}), 500
            
            # 同时保存到文件（备份）
            session_dir = os.environ.get("SESSION_DIR", "/app/cookies")
            try:
                os.makedirs(session_dir, exist_ok=True)
                session_file = os.path.join(session_dir, "telethon_session.txt")
                with open(session_file, "w", encoding="utf-8") as f:
                    f.write(session_string)
                logger.info(f"✅ Session 已保存到文件: {session_file}")
            except Exception as e:
                logger.warning(f"保存 Session 文件失败（非致命错误）: {e}")
            
            return jsonify({"ok": True, "message": "Session 已保存"})
        
        except Exception as e:
            logger.error(f"❌ 保存 Session 失败: {e}")
            return jsonify({"ok": False, "error": str(e)}), 500
    
    @bp.get("/status")
    def get_status():
        """获取 Telegram 配置状态"""
        try:
            status = {
                "telethon_available": TELETHON_AVAILABLE,
                "session_configured": False,
                "api_configured": False,
                "proxy_enabled": False,
            }
            
            if _config_manager:
                session_string = _config_manager.get_config("telegram_session_string", "")
                api_id = _config_manager.get_config("telegram_api_id", "")
                api_hash = _config_manager.get_config("telegram_api_hash", "")
                proxy_enabled = _config_manager.get_config("proxy_enabled", False)
                
                status["session_configured"] = bool(session_string)
                status["api_configured"] = bool(api_id and api_hash)
                status["proxy_enabled"] = proxy_enabled
            
            return jsonify({"ok": True, "data": status})
        
        except Exception as e:
            logger.error(f"获取状态失败: {e}")
            return jsonify({"ok": False, "error": str(e)}), 500
    
    @bp.post("/test_session")
    def test_session():
        """测试 Session 是否有效"""
        if not TELETHON_AVAILABLE:
            return jsonify({"ok": False, "error": "Telethon 未安装"}), 500
        
        try:
            data = request.get_json() or {}
            session_string = data.get("session_string")
            
            # 如果没有提供 session_string，从配置读取
            if not session_string and _config_manager:
                session_string = _config_manager.get_config("telegram_session_string", "")
            
            if not session_string:
                return jsonify({"ok": False, "error": "没有可用的 Session"}), 400
            
            # 获取 API 凭证
            api_id = data.get("api_id")
            api_hash = data.get("api_hash")
            
            if not api_id and _config_manager:
                api_id = _config_manager.get_config("telegram_api_id", "")
            if not api_hash and _config_manager:
                api_hash = _config_manager.get_config("telegram_api_hash", "")
            
            if not api_id or not api_hash:
                return jsonify({"ok": False, "error": "缺少 API ID 或 API Hash"}), 400
            
            try:
                api_id = int(api_id)
            except ValueError:
                return jsonify({"ok": False, "error": "API ID 必须是数字"}), 400
            
            proxy = get_proxy_config()
            
            # 测试连接
            async def do_test():
                manager = TelegramSessionManager(api_id, api_hash, proxy)
                try:
                    await manager.connect(session_string)
                    me = await manager.client.get_me()
                    return {
                        "ok": True,
                        "message": "Session 有效",
                        "user": {
                            "id": me.id,
                            "first_name": me.first_name,
                            "last_name": me.last_name or "",
                            "username": me.username or "",
                            "phone": me.phone or "",
                        }
                    }
                except Exception as e:
                    return {"ok": False, "error": f"Session 无效: {e}"}
                finally:
                    await manager.disconnect()
            
            result = run_async(do_test())
            
            if result.get("ok"):
                return jsonify(result)
            else:
                return jsonify(result), 400
        
        except Exception as e:
            logger.error(f"❌ 测试 Session 失败: {e}")
            return jsonify({"ok": False, "error": str(e)}), 500
    
    return bp
