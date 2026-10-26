#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
网易云登录态管理（Cookie 自动维护）

为什么单独一个模块：
    原实现把"检查登录状态"写在 NeteaseDownloader.__init__ 里，而且用的是
    已经下线的 /api/login/status（实测固定返回 404），所以 self.logged_in
    永远是 False。登录态这件事现在统一在这里做，下载器只负责下载。

实测验证过的接口（2026-10）：
    GET  /api/nuser/account/get            匿名返回 account=null；带 cookie 返回 profile
    POST /api/login/qrcode/unikey          {type:1} -> {code:200, unikey}
    POST /api/login/qrcode/client/login    {key, type:1} -> 801 待扫 / 802 已扫 / 803 成功
    POST /api/login/token/refresh          未登录 -> {code:301}；登录后 -> 200 并下发新 cookie
    二维码内容 = https://music.163.com/login?codekey=<unikey>

注意：扫码相关接口走的是普通 /api/ 前缀，不需要 weapi 加密，所以这里
没有引入 AES/RSA 那套东西，只用 requests。
"""

import io
import logging
import time
from typing import Dict, Any, Optional, Tuple

import requests

logger = logging.getLogger('music_bot.netease_auth')

BASE_URL = 'https://music.163.com'
DEFAULT_UA = (
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
    '(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
)

# 扫码轮询返回码
QR_EXPIRED = 800
QR_WAITING = 801
QR_SCANNED = 802
QR_SUCCESS = 803

QR_MESSAGES = {
    QR_EXPIRED: '二维码已过期，请刷新',
    QR_WAITING: '等待扫码',
    QR_SCANNED: '已扫码，请在手机上确认',
    QR_SUCCESS: '登录成功',
}

# 必须保留的关键 cookie，其余（NMTID 之类）可丢
KEY_COOKIES = ('MUSIC_U', '__csrf', 'MUSIC_A', 'NMTID', '__remember_me')


def parse_cookie_string(cookie_str: str) -> Dict[str, str]:
    """把 'a=1; b=2' / JSON 两种格式统一解析成 dict"""
    result: Dict[str, str] = {}
    if not cookie_str:
        return result
    text = cookie_str.strip()
    if text.startswith('{'):
        import json
        try:
            data = json.loads(text)
            if isinstance(data, dict):
                return {str(k): str(v) for k, v in data.items()}
        except Exception:  # noqa: BLE001
            pass
    for part in text.split(';'):
        if '=' in part:
            name, value = part.strip().split('=', 1)
            if name:
                result[name.strip()] = value.strip()
    return result


def dump_cookie_string(cookies: Dict[str, str]) -> str:
    """把 dict 还原成 'a=1; b=2'（保持与旧配置格式一致）"""
    return '; '.join(f'{k}={v}' for k, v in cookies.items() if v)


class NeteaseAuth:
    """网易云登录态操作：检测 / 续期 / 扫码登录"""

    def __init__(self, config_manager=None, proxy: Optional[str] = None):
        self.config_manager = config_manager
        self.proxy = proxy

    # ---------- 基础设施 ----------

    @property
    def proxies(self) -> Optional[Dict[str, str]]:
        if self.proxy:
            return {'http': self.proxy, 'https': self.proxy}
        if self.config_manager:
            try:
                if self.config_manager.get_config('proxy_enabled', False):
                    host = self.config_manager.get_config('proxy_host', '')
                    if host:
                        return {'http': host, 'https': host}
            except Exception:  # noqa: BLE001
                pass
        return None

    def build_session(self, cookie_str: str = "") -> requests.Session:
        session = requests.Session()
        session.headers.update({
            'User-Agent': DEFAULT_UA,
            'Referer': f'{BASE_URL}/',
            'Origin': BASE_URL,
            'Accept': 'application/json, text/plain, */*',
            'Accept-Language': 'zh-CN,zh;q=0.9',
        })
        # 网页版必需的两个标记，缺了容易被判定为非浏览器请求
        session.cookies.set('os', 'pc', domain='.music.163.com')
        session.cookies.set('appver', '8.9.70', domain='.music.163.com')
        for name, value in parse_cookie_string(cookie_str).items():
            session.cookies.set(name, value, domain='.music.163.com')
        return session

    def _request(self, session: requests.Session, method: str, path: str,
                 **kwargs) -> Optional[requests.Response]:
        url = BASE_URL + path
        kwargs.setdefault('timeout', kwargs.pop('timeout', 12))
        try:
            if method.upper() == 'POST':
                return session.post(url, proxies=self.proxies, **kwargs)
            return session.get(url, proxies=self.proxies, **kwargs)
        except requests.RequestException as e:
            logger.warning(f"网易云请求失败 {path}: {e}")
            return None

    # ---------- 登录态检测 ----------

    def fetch_account(self, cookie_str: str = "") -> Dict[str, Any]:
        """读取当前账号信息。

        Returns:
            {'logged_in': bool, 'nickname', 'userId', 'avatar', 'vip_type', 'vip_label'}
        """
        session = self.build_session(cookie_str)
        resp = self._request(session, 'GET', '/api/nuser/account/get')
        if resp is None or resp.status_code != 200:
            return {'logged_in': False, 'error': '请求失败'}

        try:
            data = resp.json()
        except ValueError:
            return {'logged_in': False, 'error': '响应不是 JSON'}

        profile = data.get('profile') or {}
        account = data.get('account') or {}
        if not profile and not account:
            return {'logged_in': False}

        vip_type = int(profile.get('vipType') or 0)
        if vip_type == 11:
            vip_label = '黑胶VIP'
        elif vip_type > 0:
            vip_label = '音乐包'
        else:
            vip_label = '普通用户'

        return {
            'logged_in': True,
            'nickname': profile.get('nickname') or '未知用户',
            'userId': account.get('id') or profile.get('userId'),
            'avatar': profile.get('avatarUrl') or '',
            'vip_type': vip_type,
            'vip_label': vip_label,
            'create_time': profile.get('createTime'),
        }

    def check(self, cookie_str: str = "") -> Dict[str, Any]:
        """完整检测：账号信息 + cookie 是否包含关键字段"""
        cookies = parse_cookie_string(cookie_str)
        result = self.fetch_account(cookie_str)
        result['has_music_u'] = 'MUSIC_U' in cookies
        result['cookie_count'] = len(cookies)
        result['checked_at'] = int(time.time())
        return result

    # ---------- 续期 ----------

    def refresh(self, cookie_str: str = "") -> Dict[str, Any]:
        """尝试续期会话。

        /api/login/token/refresh 会用当前会话下发一份新的 cookie，能把
        MUSIC_U 的有效期往后推。没登录时返回 code=301。

        Returns:
            {'ok': bool, 'cookies': str, 'changed': bool, 'message': str, 'expires_at': int|None}
        """
        if not parse_cookie_string(cookie_str).get('MUSIC_U'):
            return {'ok': False, 'changed': False, 'message': '未配置 MUSIC_U，无法续期'}

        session = self.build_session(cookie_str)
        resp = self._request(session, 'POST', '/api/login/token/refresh', data={})
        if resp is None:
            return {'ok': False, 'changed': False, 'message': '请求失败'}

        try:
            data = resp.json()
        except ValueError:
            data = {}
        if data.get('code') != 200:
            return {
                'ok': False,
                'changed': False,
                'message': data.get('message') or f'续期失败 (code={data.get("code")})',
                'code': data.get('code'),
            }

        merged, expires_at = self._merge_session_cookies(session, cookie_str)
        changed = merged != parse_cookie_string(cookie_str)
        return {
            'ok': True,
            'cookies': dump_cookie_string(merged),
            'changed': changed,
            'message': '会话已续期',
            'expires_at': expires_at,
        }

    def _merge_session_cookies(self, session: requests.Session,
                               cookie_str: str) -> Tuple[Dict[str, str], Optional[int]]:
        """把响应带回的新 cookie 合并进原有 cookie 串"""
        merged = parse_cookie_string(cookie_str)
        expires_at: Optional[int] = None

        for cookie in session.cookies:
            if cookie.name in ('os', 'appver'):
                continue
            if cookie.value:
                merged[cookie.name] = cookie.value
            # MUSIC_U 的有效期最有参考价值
            if cookie.name == 'MUSIC_U' and cookie.expires:
                expires_at = int(cookie.expires) * 1000
        return merged, expires_at

    # ---------- 扫码登录 ----------

    def qr_create(self) -> Dict[str, Any]:
        """申请二维码。

        Returns:
            {'ok', 'unikey', 'qrurl', 'qr_svg', 'message'}
        """
        session = self.build_session()
        resp = self._request(session, 'POST', '/api/login/qrcode/unikey', data={'type': 1})
        if resp is None:
            return {'ok': False, 'message': '请求失败'}

        try:
            data = resp.json()
        except ValueError:
            return {'ok': False, 'message': '响应不是 JSON'}

        unikey = data.get('unikey')
        if data.get('code') != 200 or not unikey:
            return {'ok': False, 'message': data.get('message') or '获取二维码失败'}

        qrurl = f'{BASE_URL}/login?codekey={unikey}'
        return {
            'ok': True,
            'unikey': unikey,
            'qrurl': qrurl,
            'qr_svg': self._render_qr_svg(qrurl),
            'message': '请使用网易云音乐 App 扫码',
        }

    @staticmethod
    def _render_qr_svg(content: str) -> str:
        """把二维码渲染成 SVG；没装 qrcode 库时返回空串，前端降级成链接"""
        try:
            import qrcode
            import qrcode.image.svg as svg_factory
        except ImportError:
            logger.info('未安装 qrcode，二维码将以链接形式提供')
            return ''
        try:
            img = qrcode.make(content, image_factory=svg_factory.SvgPathImage, box_size=10, border=2)
            buf = io.BytesIO()
            img.save(buf)
            return buf.getvalue().decode('utf-8')
        except Exception as e:  # noqa: BLE001
            logger.warning(f'生成二维码失败: {e}')
            return ''

    def qr_check(self, unikey: str) -> Dict[str, Any]:
        """轮询扫码结果。

        Returns:
            {'code': 800/801/802/803, 'message', 'cookies'?: str, 'account'?: dict}
        """
        if not unikey:
            return {'code': QR_EXPIRED, 'message': '缺少 unikey'}

        session = self.build_session()
        resp = self._request(session, 'POST', '/api/login/qrcode/client/login',
                            data={'key': unikey, 'type': 1})
        if resp is None:
            return {'code': QR_WAITING, 'message': '网络异常，稍后重试'}

        try:
            data = resp.json()
        except ValueError:
            return {'code': QR_WAITING, 'message': '响应异常'}

        code = int(data.get('code') or QR_WAITING)
        result = {
            'code': code,
            'message': data.get('message') or QR_MESSAGES.get(code, '未知状态'),
        }

        if code == QR_SUCCESS:
            merged, expires_at = self._merge_session_cookies(session, '')
            cookie_str = dump_cookie_string(merged)
            account = self.fetch_account(cookie_str)
            result.update({
                'cookies': cookie_str,
                'expires_at': expires_at,
                'account': account,
            })
        return result
