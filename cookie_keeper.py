#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
网易云 Cookie 守护线程

做三件事：
1. 定期检测登录态（阈值可配，默认 30 分钟）
2. 登录态有效时自动调 /api/login/token/refresh 续期，并把新 cookie 写回配置
3. 失效时标记状态 + 发一次 TG 通知（6 小时内不重复轰炸），等用户扫码

登录态和 cookie 元信息都落在 config 表里，Web 端直接读：
    netease_cookie_meta = {
        state: valid | expired | empty | unknown
        nickname, user_id, vip_label, avatar,
        expires_at (毫秒), checked_at (秒), refreshed_at (秒), last_error
    }
"""

import json
import logging
import threading
import time
from typing import Any, Dict, Optional

from downloaders.netease_auth import NeteaseAuth

logger = logging.getLogger('music_bot.cookie_keeper')

META_KEY = 'netease_cookie_meta'
COOKIE_KEY = 'netease_cookies'
NOTIFY_COOLDOWN = 6 * 3600


class CookieKeeper:
    """后台守护线程：检测 + 续期 + 失效通知"""

    def __init__(self, config_manager, on_cookies_updated=None):
        """
        Args:
            config_manager: 配置管理器
            on_cookies_updated: cookie 变更后的回调（用于热重载下载器）
        """
        self.config_manager = config_manager
        self.auth = NeteaseAuth(config_manager)
        self.on_cookies_updated = on_cookies_updated
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._last_check: float = 0.0
        self._lock = threading.RLock()

    # ---------- 生命周期 ----------

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._loop, name='netease-cookie-keeper', daemon=True)
        self._thread.start()
        logger.info("🌱 网易云 Cookie 守护已启动")

    def stop(self):
        self._stop_event.set()

    def _loop(self):
        # 启动后先等一会，别和 Bot 初始化抢网络
        self._stop_event.wait(8)
        while not self._stop_event.is_set():
            try:
                self.check_once(auto=True)
            except Exception as e:  # noqa: BLE001
                logger.warning(f"Cookie 守护执行异常: {e}")

            interval = self._interval()
            # 分段等待，保证 stop() 能及时退出
            waited = 0
            while waited < interval and not self._stop_event.is_set():
                self._stop_event.wait(min(30, interval - waited))
                waited += 30

    def _interval(self) -> int:
        try:
            value = int(self.config_manager.get_config('netease_cookie_check_interval', 1800))
        except (TypeError, ValueError):
            value = 1800
        return max(120, value)

    # ---------- 状态读写 ----------

    def get_meta(self) -> Dict[str, Any]:
        meta = self.config_manager.get_config(META_KEY, {})
        if not isinstance(meta, dict):
            meta = {}
        return meta

    def _save_meta(self, **kwargs):
        meta = self.get_meta()
        meta.update(kwargs)
        self.config_manager.set_config(META_KEY, meta)
        return meta

    def get_cookies(self) -> str:
        return self.config_manager.get_config(COOKIE_KEY, '') or ''

    def save_cookies(self, cookie_str: str, expires_at: Optional[int] = None,
                     account: Optional[Dict[str, Any]] = None, source: str = 'manual'):
        """写入新 cookie，并通知下载器热重载"""
        cookie_str = (cookie_str or '').strip()
        self.config_manager.set_config(COOKIE_KEY, cookie_str)

        updates: Dict[str, Any] = {
            'checked_at': int(time.time()),
            'refreshed_at': int(time.time()),
            'source': source,
            'last_error': '',
        }
        if expires_at:
            updates['expires_at'] = int(expires_at)
        if account:
            updates.update({
                'state': 'valid' if account.get('logged_in') else 'expired',
                'nickname': account.get('nickname', ''),
                'user_id': account.get('userId'),
                'vip_label': account.get('vip_label', ''),
                'avatar': account.get('avatar', ''),
            })
        else:
            updates['state'] = 'unknown'
        self._save_meta(**updates)

        self._notify_downloader()
        logger.info(f"🍪 网易云 Cookie 已更新（来源: {source}）")
        return True

    def clear_cookies(self):
        self.config_manager.set_config(COOKIE_KEY, '')
        self._save_meta(state='empty', nickname='', vip_label='', expires_at=None,
                        checked_at=int(time.time()), last_error='')
        self._notify_downloader()
        logger.info("🧹 网易云 Cookie 已清除")

    def _notify_downloader(self):
        if not self.on_cookies_updated:
            return
        try:
            self.on_cookies_updated()
        except Exception as e:  # noqa: BLE001
            logger.warning(f"通知下载器重载失败: {e}")

    # ---------- 主流程 ----------

    def check_once(self, auto: bool = False) -> Dict[str, Any]:
        """执行一次检测（可选续期）

        Returns:
            最新 meta
        """
        with self._lock:
            self._last_check = time.time()
            cookies = self.get_cookies()
            auto_refresh = bool(self.config_manager.get_config('netease_auto_refresh_enabled', True))

            if not cookies:
                meta = self._save_meta(state='empty', checked_at=int(time.time()),
                                       nickname='', vip_label='', last_error='')
                return meta

            info = self.auth.check(cookies)
            if info.get('logged_in'):
                updates: Dict[str, Any] = {
                    'state': 'valid',
                    'checked_at': int(time.time()),
                    'nickname': info.get('nickname', ''),
                    'user_id': info.get('userId'),
                    'vip_label': info.get('vip_label', ''),
                    'avatar': info.get('avatar', ''),
                    'last_error': '',
                }
                meta = self._save_meta(**updates)

                if auto_refresh:
                    result = self.auth.refresh(cookies)
                    if result.get('ok'):
                        if result.get('changed'):
                            self.config_manager.set_config(COOKIE_KEY, result['cookies'])
                            self._notify_downloader()
                        self._save_meta(
                            refreshed_at=int(time.time()),
                            expires_at=result.get('expires_at') or meta.get('expires_at'),
                            last_refresh_message=result.get('message', ''),
                        )
                        logger.info("♻️ 网易云会话已续期")
                    else:
                        logger.info(f"ℹ️ 会话续期跳过: {result.get('message')}")
                return self.get_meta()

            # 登录态失效：先尝试续期（有时 refresh 能把会话救回来）
            result = self.auth.refresh(cookies)
            if result.get('ok') and result.get('cookies'):
                self.config_manager.set_config(COOKIE_KEY, result['cookies'])
                self._notify_downloader()
                info = self.auth.check(result['cookies'])
                if info.get('logged_in'):
                    logger.info("♻️ 会话失效后通过 refresh 恢复")
                    return self._save_meta(
                        state='valid', checked_at=int(time.time()),
                        nickname=info.get('nickname', ''),
                        vip_label=info.get('vip_label', ''),
                        expires_at=result.get('expires_at'),
                        last_error='',
                    )

            meta = self._save_meta(
                state='expired',
                checked_at=int(time.time()),
                expires_at=None,
                last_error=result.get('message') or 'MUSIC_U 已失效',
            )
            logger.warning("⚠️ 网易云登录态已失效，请在 Web 账号页扫码重新登录")
            self._maybe_notify(meta)
            return meta

    def _maybe_notify(self, meta: Dict[str, Any]):
        """失效通知（带冷却，避免每次轮询都发）"""
        if not self.config_manager.get_config('netease_cookie_notify_on_expire', True):
            return
        last = int(meta.get('notified_at') or 0)
        if time.time() - last < NOTIFY_COOLDOWN:
            return
        try:
            from web.tg_notifier import send_telegram_notification
            message = (
                "⚠️ 网易云登录态已失效\n\n"
                "受影响的曲目：付费 / VIP / 无损\n"
                "处理方式：打开 Web 管理后台 → 账号 → 扫码登录\n\n"
                f"时间：{time.strftime('%Y-%m-%d %H:%M:%S')}"
            )
            send_telegram_notification(self.config_manager, message)
        except Exception as e:  # noqa: BLE001
            logger.debug(f"发送失效通知失败: {e}")
        self._save_meta(notified_at=int(time.time()))


# 进程内共享实例：Web 与 Bot 都通过 get_keeper() 拿同一个，
# 这样扫码登录写回的 cookie 能立刻热重载到正在用的下载器上，
# 也不会出现两个守护线程同时检测、重复续期的情况。
_keeper_instance: Optional[CookieKeeper] = None
_keeper_lock = threading.Lock()


def get_keeper(config_manager=None) -> CookieKeeper:
    """取（必要时创建）进程内唯一的 CookieKeeper。

    start() 本身是幂等的，所以调用方可以放心每次启动都 start()。
    """
    global _keeper_instance
    with _keeper_lock:
        if _keeper_instance is None:
            if config_manager is None:
                from config_manager import get_config_manager
                config_manager = get_config_manager()
            _keeper_instance = CookieKeeper(config_manager)
            logger.info("🌱 已创建 Cookie 守护实例")
        return _keeper_instance
