#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Music Bot - 精简版音乐下载机器人
专注于音乐下载功能，支持网易云音乐、Apple Music
"""

import os
import sys
import time
import uuid
import logging
import asyncio
import threading
from pathlib import Path
from typing import Optional, Dict, Any, List

# 设置环境变量
os.environ['PYTHONWARNINGS'] = 'ignore:Unverified HTTPS request'

import warnings
warnings.filterwarnings('ignore')

# 版本信息
BOT_VERSION = "1.1.0"

# 设置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger('music_bot')

# 导入配置管理器
from config_manager import ConfigManager, get_config_manager

# 导入下载器（具体平台由 downloaders 注册表按配置构建，这里不需要逐个引用）
from downloaders import get_all_downloaders
from downloaders.errors import DownloadCancelled

# 导入 Web 服务
from web.app import app as web_app, init_app as init_web_app

# 导入数据库日志处理器
from web.db_logger import setup_database_logging, get_metadata_logger

# TG 消息渲染层（统一转义 / 安全发送 / 进度卡片）
from web import tg_ui as ui

# 导入下载队列
from download_queue import get_download_queue, DownloadTask

# Cookie 守护（网易云登录态自动维护）
from cookie_keeper import CookieKeeper, get_keeper

# Telegram 相关导入
try:
    from telegram import (
        Update, Bot, InlineKeyboardButton, InlineKeyboardMarkup, InputFile, BotCommand
    )
    from telegram.ext import (
        Application, CommandHandler, MessageHandler,
        filters, ContextTypes, CallbackQueryHandler
    )
    TELEGRAM_AVAILABLE = True
except ImportError:
    TELEGRAM_AVAILABLE = False
    logger.warning("⚠️ python-telegram-bot 未安装")


class MusicBot:
    """音乐下载机器人"""
    
    def __init__(self, db_path: str = None):
        """初始化机器人"""
        # 初始化配置管理器
        self.config_manager = get_config_manager(db_path)
        
        # 加载配置
        self.config = self.config_manager.get_all_config()
        
        # 设置数据库日志记录
        setup_database_logging(self.config_manager)
        
        # 初始化下载器
        self.downloaders = {}
        self._init_downloaders()

        # Telegram 应用
        self.app: Optional[Application] = None

        # 下载路径
        self.download_path = self.config.get('download_path', '/downloads')

        # 下载队列（控制并发 + 重试）
        max_concurrent = self._cfg('download_max_concurrent', 3)
        self.download_queue = get_download_queue(max_concurrent=max_concurrent)
        
        # 下载重试配置
        self.download_max_retries = self._cfg('download_max_retries', 3)
        self.download_retry_delay_base = self._cfg('download_retry_delay_base', 2.0)

        # 后台下载任务（持有引用，避免被 GC）
        self._bg_tasks: "set[asyncio.Task]" = set()

        # 回调按钮的短码表。
        # Telegram 的 callback_data 上限 64 字节，而 task_id / 链接里可能带长 ID；
        # 另外把「按钮含义」和「业务参数」解耦后，改按钮文案不用动解析逻辑。
        self._cb_actions: Dict[str, tuple] = {}
        self._cb_lock = threading.Lock()

        # 「没看到链接」提示的节流表（user_id → 上次提示时间）
        self._hint_at: Dict[int, float] = {}

        # Cookie 守护（网易云登录态自动维护）
        self.cookie_keeper: Optional[CookieKeeper] = None

        logger.info(f"🎵 Music Bot v{BOT_VERSION} 初始化完成")

    def _cfg(self, key: str, default=None):
        """实时读取配置（热加载）。

        取代直接读 self.config.get(key)——后者是启动期快照，
        Web 界面修改后不会生效，导致“改了设置必须重启容器”。
        所有运行期需要读取的配置都应走这里。
        """
        try:
            return self.config_manager.get_config(key, default)
        except Exception:
            return self.config.get(key, default) if self.config else default

    def _init_downloaders(self):
        """初始化下载器

        走 downloaders 注册表，让 Bot 与 Web 共用同一份实例——
        Web 侧改完 cookie / 音质后 Bot 立刻用新的，反之亦然。
        """
        try:
            self.downloaders = get_all_downloaders(self.config_manager)
        except Exception as e:
            logger.exception(f"❌ 下载器初始化失败: {e}")
            self.downloaders = {}
        for name in self.downloaders:
            logger.info(f"✅ 已启用下载器: {name}")
    
    def get_downloader_for_url(self, url: str) -> Optional[tuple]:
        """根据 URL 获取对应的下载器"""
        for name, downloader in self.downloaders.items():
            if downloader.is_supported_url(url):
                return name, downloader
        return None
    
    # ==================== 回调数据短码 ====================

    def _register_action(self, action: str, payload: Any = None) -> str:
        """生成 callback_data。格式 `<首字母>:<10位hex>`，最长 12 字节。"""
        token = uuid.uuid4().hex[:10]
        with self._cb_lock:
            self._cb_actions[token] = (action, payload)
            # 按钮点过就没人再用，但历史消息里的按钮可能还被点。
            # 保留最近 800 个，够覆盖几十条消息。
            if len(self._cb_actions) > 800:
                for stale in list(self._cb_actions)[:200]:
                    self._cb_actions.pop(stale, None)
        return f'{action[:1]}:{token}'

    def _resolve_action(self, data: str):
        """解析 callback_data，返回 (action, payload)；无效返回 (None, None)。"""
        if not data or ':' not in data:
            return None, None
        prefix, _, token = data.partition(':')
        with self._cb_lock:
            entry = self._cb_actions.get(token)
        if not entry:
            return None, None
        return entry

    # ==================== 键盘 ====================

    def _main_keyboard(self):
        """首页快捷键盘。比以前纯文本好点：少打字，也能直接跳后台。"""
        rows = [
            [
                InlineKeyboardButton('📊 状态', callback_data=self._register_action('status')),
                InlineKeyboardButton('📥 队列', callback_data=self._register_action('queue')),
            ],
            [
                InlineKeyboardButton('📜 历史', callback_data=self._register_action('history', 1)),
                InlineKeyboardButton('🍪 账号', callback_data=self._register_action('cookie')),
            ],
        ]
        web_url = str(self._cfg('web_ui_url', '') or '').strip()
        if web_url.startswith('http'):
            rows.append([InlineKeyboardButton('🌐 打开管理后台', url=web_url)])
        return InlineKeyboardMarkup(rows)

    def _refresh_keyboard(self, action: str, payload: Any = None):
        return InlineKeyboardMarkup([[
            InlineKeyboardButton('🔄 刷新', callback_data=self._register_action(action, payload))
        ]])

    def _history_keyboard(self, page: int, total_pages: int):
        """翻页按钮。到头的那侧不放按钮，比放一个灰的更容易理解。"""
        row = []
        if page > 1:
            row.append(InlineKeyboardButton('‹ 上一页', callback_data=self._register_action('history', page - 1)))
        row.append(InlineKeyboardButton(f'{page}/{total_pages}', callback_data=self._register_action('noop')))
        if page < total_pages:
            row.append(InlineKeyboardButton('下一页 ›', callback_data=self._register_action('history', page + 1)))
        return InlineKeyboardMarkup([row])

    # ==================== /start /help ====================

    async def handle_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """处理 /start 命令"""
        user = update.message.from_user
        logger.info(f"📨 收到 /start 命令: 用户={user.id}({user.username})")

        if not self.check_allowed_user(user.id):
            logger.warning(f"⚠️ 用户 {user.id} 不在允许列表中")
            await ui.send(update.message, ui.Renderer.error_card(
                '没有使用权限',
                '当前账号不在白名单里',
                f'把你的 ID {user.id} 发给管理员，加到「允许的用户」里就能用了',
            ))
            return

        await ui.send(
            update.message,
            ui.Renderer.welcome('Music Bot'),
            keyboard=self._main_keyboard(),
        )

    async def handle_status(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """处理 /status 命令"""
        if not self.check_allowed_user(update.message.from_user.id):
            await ui.send(update.message, ui.Renderer.error_card('没有使用权限', '不在白名单里'))
            return
        await ui.send(
            update.message,
            ui.Renderer.status_card(self._build_status_data()),
            keyboard=self._refresh_keyboard('status'),
        )

    def _build_status_data(self) -> Dict[str, Any]:
        """组装状态卡片的数据。状态卡被 /status 和按钮刷新共用。"""
        platforms = []
        for name, downloader in self.downloaders.items():
            _, label = ui.platform_label(name)
            note = ''
            ready = True
            if name == 'netease':
                logged_in = bool(getattr(downloader, 'logged_in', False))
                ready = True  # 未登录也能下部分公开内容，只提示不报错
                note = '已登录' if logged_in else '未登录'
            platforms.append({'label': label, 'ready': ready, 'note': note})

        try:
            queue = self.download_queue.get_status()
        except Exception:  # noqa: BLE001
            queue = {}

        try:
            library = self.config_manager.get_library_stats()
        except Exception:  # noqa: BLE001
            library = {'bytes': 0, 'files': 0, 'missing': []}

        try:
            stats = self.config_manager.get_history_stats()
        except Exception:  # noqa: BLE001
            stats = {'today': 0, 'total': 0}

        note = ''
        meta = {}
        try:
            meta = get_keeper(self.config_manager).get_meta()
            if str(meta.get('state', '')).lower() in ('expired', 'invalid'):
                note = '网易云登录态已失效，发送 /cookie 或到后台「账号」页重新登录'
        except Exception:  # noqa: BLE001
            pass

        return {
            'platforms': platforms,
            'queue': queue,
            'library': library,
            'stats': stats,
            'netease': meta,
            'note': note,
        }

    async def handle_queue(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """处理 /queue 命令 - 查看下载队列状态"""
        user = update.message.from_user
        if not self.check_allowed_user(user.id):
            await ui.send(update.message, ui.Renderer.error_card('没有使用权限', '不在白名单里'))
            return

        await ui.send(
            update.message,
            ui.Renderer.queue_card(self.download_queue.get_status()),
            keyboard=self._refresh_keyboard('queue'),
        )

    async def handle_history(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """处理 /history 命令 - 分页显示下载历史"""
        user = update.message.from_user
        if not self.check_allowed_user(user.id):
            await ui.send(update.message, ui.Renderer.error_card('没有使用权限', '不在白名单里'))
            return

        page = 1
        if context.args:
            try:
                page = max(1, int(context.args[0]))
            except (TypeError, ValueError):
                page = 1

        text, keyboard = self._render_history_page(page)
        await ui.send(update.message, text, keyboard=keyboard)

    HISTORY_PAGE_SIZE = 8

    def _render_history_page(self, page: int):
        """渲染历史某一页。返回 (文本, 键盘)。"""
        per_page = self.HISTORY_PAGE_SIZE
        page = max(1, page)

        total = self.config_manager.get_history_stats().get('total', 0)
        if total <= 0:
            return ui.Renderer.history_card([], 1, 1, 0), None

        total_pages = max(1, (total + per_page - 1) // per_page)
        page = min(page, total_pages)

        # 没有分页版的查询接口，只能按页大小取。页数多了之后这里会变成
        # 取 limit 再切片——数据量到那个级别再说，现在没必要加复杂度。
        rows = self.config_manager.get_download_history(limit=total_pages * per_page)
        start = (page - 1) * per_page
        window = rows[start:start + per_page]

        return (
            ui.Renderer.history_card(window, page, total_pages, total),
            self._history_keyboard(page, total_pages),
        )

    async def handle_id(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """处理 /id 命令 - 显示自己的 Telegram ID（配置白名单时用）"""
        user = update.message.from_user
        chat = update.message.chat
        lines = [
            f'🆔 {ui.b("你的信息")}',
            '',
            f'用户 ID：{ui.code(user.id)}',
            f'会话 ID：{ui.code(chat.id)}',
        ]
        if user.username:
            lines.append(f'用户名：@{ui.esc(user.username)}')
        lines.extend([
            '',
            ui.it('把用户 ID 填进后台「Telegram → 允许的用户」就能限制只有你能用。'),
        ])
        await ui.send(update.message, '\n'.join(lines))

    async def handle_cancel(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """处理 /cancel 命令 - 取消自己在跑/排队的任务"""
        user = update.message.from_user
        if not self.check_allowed_user(user.id):
            await ui.send(update.message, ui.Renderer.error_card('没有使用权限', '不在白名单里'))
            return

        cancelled = 0
        for task in self.download_queue.get_all_tasks():
            if task.status.name in ('PENDING', 'RUNNING', 'RETRYING'):
                if self.download_queue.cancel(task.task_id):
                    cancelled += 1

        if cancelled:
            text = f'⏹ {ui.b("已取消")} {ui.code(cancelled)} 个任务\n\n{ui.it("已下载的部分保留断点，重新发链接会接着下。")}'
        else:
            text = f'{ui.it("当前没有进行中的任务。")}'
        await ui.send(update.message, text)

    async def handle_cookie(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """处理 /cookie 命令 - 查看/更新网易云登录态"""
        user = update.message.from_user
        logger.info(f"📨 收到 /cookie 命令: 用户={user.id}({user.username})")

        if not self.check_allowed_user(user.id):
            await ui.send(update.message, ui.Renderer.error_card('没有使用权限', '不在白名单里'))
            return

        args = context.args or []

        if not args:
            await ui.send(
                update.message,
                self._cookie_help_text(),
                keyboard=self._cookie_keyboard(),
            )
            return

        new_cookies = ' '.join(args)
        if '=' not in new_cookies and not new_cookies.startswith('{'):
            await ui.send(update.message, ui.Renderer.error_card(
                'Cookie 格式不对',
                '既没有 key=value，也不是 JSON',
                '正确示例：MUSIC_U=xxxx; __csrf=yyyy',
            ))
            return

        await ui.send(update.message, f'⏳ 正在校验 Cookie…')
        try:
            self.config_manager.set_config('netease_cookies', new_cookies)
            self.config['netease_cookies'] = new_cookies

            downloader = self.downloaders.get('netease')
            if not downloader:
                await ui.send(update.message, ui.Renderer.error_card(
                    'Cookie 已保存',
                    '网易云下载器没启用',
                    '到后台「设置」里启用网易云音乐下载器',
                ))
                return

            if hasattr(downloader, 'reload_config'):
                downloader.reload_config()

            if getattr(downloader, 'logged_in', False):
                info = getattr(downloader, 'user_info', {}) or {}
                nickname = info.get('nickname') or '未知'
                vip_type = info.get('vipType') or 0
                vip_str = '黑胶 VIP' if vip_type == 11 else ('VIP' if vip_type > 0 else '普通用户')

                lines = [
                    f'✅ {ui.b("登录成功")}',
                    '',
                    f'👤 {ui.esc(nickname)}',
                    f'💎 {ui.esc(vip_str)}',
                    '',
                    ui.it('已开启自动续期，之后过期会自动用 token 续，不用再手动贴。'),
                ]
                await ui.send(update.message, '\n'.join(lines))
                logger.info(f"✅ 用户 {user.id} 更新了网易云 cookies，登录用户: {nickname}")
            else:
                await ui.send(update.message, ui.Renderer.error_card(
                    'Cookie 已保存，但校验没过',
                    '登录态无效（多为已过期或复制不全）',
                    '重新复制完整的 MUSIC_U；或者到后台「账号」页扫码登录，更省事',
                ))
                logger.warning(f"⚠️ 用户 {user.id} 更新了 cookies，但校验失败")

        except Exception as e:
            logger.exception(f"❌ 保存 cookies 失败: {e}")
            await ui.send(update.message, ui.Renderer.error_card('保存 Cookie 失败', e))

    def _cookie_help_text(self) -> str:
        """Cookie 页。优先引导去后台扫码——让用户手抠 cookie 是很差的体验。"""
        status = '❔ 未知'
        nickname = ''
        try:
            downloader = self.downloaders.get('netease')
            meta = get_keeper(self.config_manager).get_meta()
            status = ui.Renderer.netease_block(meta)
            nickname = meta.get('nickname') or ''
        except Exception:  # noqa: BLE001
            if self.downloaders.get('netease') and getattr(self.downloaders['netease'], 'logged_in', False):
                status = '✅ 已登录'

        web_url = str(self._cfg('web_ui_url', '') or '').strip()
        lines = [
            f'🍪 {ui.b("网易云账号")}',
            '',
            f'{ui.b("当前状态")}',
            f'  {status}' + (f' · {ui.esc(nickname)}' if nickname else ''),
            '',
            f'{ui.b("推荐做法")}',
            f'  去后台「账号」页点扫码登录，30 秒搞定，登录态到期会自动续。',
        ]
        if web_url.startswith('http'):
            lines.append(f'  {ui.code(web_url)}')
        lines.extend([
            '',
            f'{ui.b("或者手动贴 Cookie")}',
            f'  {ui.code("/cookie MUSIC_U=xxxx; __csrf=yyyy")}',
            '',
            ui.it('注意：Cookie 会存进本地数据库，别在群里发。'),
        ])
        return '\n'.join(lines)

    def _cookie_keyboard(self):
        rows = []
        web_url = str(self._cfg('web_ui_url', '') or '').strip()
        if web_url.startswith('http'):
            rows.append([InlineKeyboardButton('🌐 去后台扫码登录', url=f'{web_url.rstrip("/")}/account')])
        rows.append([InlineKeyboardButton('🔄 重新检查', callback_data=self._register_action('cookie'))])
        return InlineKeyboardMarkup(rows)


    def get_download_path_for_platform(self, platform: str) -> str:
        """获取平台专属的下载路径"""
        platform_paths = {
            'netease': self._cfg('netease_download_path', '/downloads/netease'),
            'apple_music': self._cfg('apple_music_download_path', '/downloads/apple_music'),
        }
        return platform_paths.get(platform, self.download_path)
    
    async def _safe_handle_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """带超时保护的消息处理包装器

        这里的超时只保护"解析链接 + 入队"这段，不覆盖下载过程。
        下载已改成后台任务（见 _spawn_download），所以用 handler_timeout
        （默认 60s）就够；旧实现用 download_timeout 是同一件事的两个默认值
        打架，最终永远走 600s。
        """
        try:
            await asyncio.wait_for(
                self.handle_message(update, context),
                timeout=self._cfg('handler_timeout', 60)
            )
        except asyncio.TimeoutError:
            logger.error("❌ 消息处理超时")
            if update.message:
                await ui.send(update.message, ui.Renderer.error_card(
                    '处理超时', '解析链接超时', '稍等一下再发一次；如果一直这样，看看后台日志'))
        except Exception as e:
            logger.exception(f"❌ 消息处理异常: {e}")
            if update.message:
                await ui.send(update.message, ui.Renderer.error_card('处理出错', e))

    async def handle_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """处理消息"""
        if not update.message or not update.message.text:
            return

        user = update.message.from_user
        text = update.message.text.strip()

        logger.info(f"📨 收到消息: 用户={user.id}({user.username}), 内容={text[:50]}...")

        if not self.check_allowed_user(user.id):
            logger.warning(f"⚠️ 用户 {user.id} 不在允许列表中")
            await ui.send(update.message, ui.Renderer.error_card(
                '没有使用权限',
                '当前账号不在白名单里',
                f'把你的 ID {user.id} 发给管理员加白名单',
            ))
            return

        url = self._extract_url(text)
        if not url:
            # 私聊里发了一段不像链接的话，别装死——旧版直接 return，
            # 用户以为机器人挂了。群聊里则保持安静，避免刷屏。
            if update.message.chat.type == 'private' and len(text) >= 2:
                await self._maybe_hint_no_link(update.message, user.id)
            logger.debug("📝 消息里没有链接，已忽略")
            return

        result = self.get_downloader_for_url(url)
        if not result:
            await ui.send(update.message, ui.Renderer.error_card(
                '这个链接暂时不支持',
                '没匹配到可用的下载器',
                '目前支持网易云音乐（歌曲/专辑/歌单）和 Apple Music（歌曲/专辑）',
            ))
            return

        downloader_name, downloader = result

        parsed = downloader.parse_url(url)
        if not parsed:
            await ui.send(update.message, ui.Renderer.error_card(
                '链接解析失败',
                '看懂了平台，但认不出具体是哪首歌',
                '用 App 里的「分享 → 复制链接」重新拿一次，别手动改参数',
            ))
            return

        content_type = parsed.get('type')
        content_id = parsed.get('id')

        existing = self.config_manager.check_download_exists(downloader_name, content_type, content_id)
        if existing:
            title = existing.get('title') or '未知'
            keyboard = InlineKeyboardMarkup([[
                InlineKeyboardButton(
                    '✅ 重新下载',
                    callback_data=self._register_action(
                        'redownload', (downloader_name, content_type, content_id)),
                ),
                InlineKeyboardButton(
                    '❌ 算了',
                    callback_data=self._register_action('noop'),
                ),
            ]])
            await ui.send(
                update.message,
                ui.Renderer.duplicate_found(title, downloader_name, existing.get('created_at')),
                keyboard=keyboard,
            )
            return

        self._spawn_download(update.message, downloader_name, downloader, content_type, content_id)

    async def _maybe_hint_no_link(self, message, user_id: int):
        """私聊里没找到链接时给一次提示，30 秒内不重复。"""
        now = time.time()
        if now - self._hint_at.get(user_id, 0) < 30:
            return
        self._hint_at[user_id] = now
        await ui.send(message, '\n'.join([
            f'🤔 {ui.b("没看到链接")}',
            '',
            '直接把歌曲 / 专辑 / 歌单的分享链接发过来就行，例如：',
            ui.code('https://music.163.com/song?id=1234567'),
            '',
            f'{ui.it("不确定有什么命令的话，发 /start 看看。")}',
        ]))

    async def handle_callback_query(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """处理回调查询（按钮点击）"""
        query = update.callback_query
        data = query.data or ''

        # ---- 兼容升级前留在聊天记录里的旧按钮 ----
        if data == 'cancel_download':
            await query.answer('已取消')
            await ui.edit(query.message, f'{ui.it("已取消。")}')
            return
        if data.startswith('redownload:'):
            parts = data.split(':')
            await query.answer()
            if len(parts) == 4:
                _, downloader_name, content_type, content_id = parts
                downloader = self.downloaders.get(downloader_name)
                if downloader:
                    await ui.edit(query.message, f'🔄 {ui.b("重新下载")}\n\n{ui.it("已加入队列，下面这条是进度。")}')
                    self._spawn_download(query.message, downloader_name, downloader,
                                         content_type, content_id, is_redownload=True)
                else:
                    await ui.edit(query.message, ui.Renderer.error_card('下载器不可用', f'找不到 {downloader_name}'))
            return

        action, payload = self._resolve_action(data)
        if action is None:
            # 按钮存在但短码已经过期（重启后内存表清空）
            await query.answer('这个按钮已过期，重新发一次链接吧', show_alert=True)
            return

        try:
            if action == 'noop':
                await query.answer('好的')
                return

            await query.answer()

            if action == 'status':
                await ui.edit(query.message, ui.Renderer.status_card(self._build_status_data()),
                              keyboard=self._refresh_keyboard('status'))

            elif action == 'queue':
                await ui.edit(query.message, ui.Renderer.queue_card(self.download_queue.get_status()),
                              keyboard=self._refresh_keyboard('queue'))

            elif action == 'history':
                text, keyboard = self._render_history_page(int(payload or 1))
                await ui.edit(query.message, text, keyboard=keyboard)

            elif action == 'cookie':
                await ui.edit(query.message, self._cookie_help_text(), keyboard=self._cookie_keyboard())

            elif action == 'cancel':
                ok = self.download_queue.cancel(str(payload))
                await query.answer('已请求取消' if ok else '这个任务已经结束了', show_alert=not ok)
                if ok:
                    await ui.edit(query.message, '\n'.join([
                        f'⏹ {ui.b("已取消")}',
                        '',
                        ui.it('已下载的部分留在 .part 文件里，重新发链接可以续传。'),
                    ]))

            elif action == 'redownload':
                downloader_name, content_type, content_id = payload
                downloader = self.downloaders.get(downloader_name)
                if not downloader:
                    await ui.edit(query.message, ui.Renderer.error_card('下载器不可用', f'找不到 {downloader_name}'))
                    return
                await ui.edit(query.message, f'🔄 {ui.b("重新下载")}\n\n{ui.it("已加入队列。")}')
                self._spawn_download(query.message, downloader_name, downloader,
                                     content_type, content_id, is_redownload=True)

        except Exception as e:
            logger.exception(f"❌ 处理按钮回调失败: {e}")
            try:
                await ui.edit(query.message, ui.Renderer.error_card('操作失败', e))
            except Exception:  # noqa: BLE001
                pass

    def _spawn_download(self, message, downloader_name: str, downloader,
                        content_type: str, content_id: str, is_redownload: bool = False):
        """把下载丢到后台执行，handler 立即返回。

        这是 concurrent_updates(8) 之外的另一半：即便允许并发处理更新，
        只要 handler 里 await 到下载结束，这个 handler 就一直占着。
        改成提交即返回后，同一个人连发多个链接、或者别人中途发 /status，
        都不会被前一个下载堵住；真正的并发上限由 DownloadQueue 的信号量控制。
        """
        task = asyncio.create_task(
            self._do_download(message, downloader_name, downloader,
                              content_type, content_id, is_redownload),
            name=f"download-{downloader_name}-{content_id}",
        )
        self._bg_tasks.add(task)
        task.add_done_callback(self._bg_tasks.discard)

    async def _do_download(self, message, downloader_name: str, downloader,
                           content_type: str, content_id: str, is_redownload: bool = False):
        """执行下载任务（通过下载队列，支持并发控制和重试）"""
        download_dir = self.get_download_path_for_platform(downloader_name)
        main_loop = asyncio.get_running_loop()
        task_id = f"{downloader_name}_{content_id}_{uuid.uuid4().hex[:8]}"

        # 进度卡片：一条消息滚动更新，自带「取消」按钮
        card = ui.ProgressCard(
            min_interval=self._cfg('telegram_progress_interval', 1.0),
            cancel_data=self._register_action('cancel', task_id),
        )
        card.platform = downloader_name
        card.content_type = content_type
        card.content_id = content_id
        card.set_queued(0)

        progress_msg = await ui.send(message, card.render(), keyboard=card.keyboard())
        if progress_msg is None:
            logger.error("❌ 进度消息发送失败，放弃本次下载")
            return
        card.attach(progress_msg)

        # 取消信号：下载线程和队列之间唯一的通信渠道
        task_cancel_event = threading.Event()

        # 进度回调是下载线程里同步调的，这里只做两件事：
        # 1) 把数据喂给卡片（纯内存操作）
        # 2) 过了节流窗口才往主循环排一次渲染
        last_scheduled = [0.0]
        min_interval = card.min_interval
        # 设置里关掉「推送下载进度」后，只保留起始卡和最终结果，
        # 中间不再刷同一条消息——省 API 调用，也方便只想看结果的人。
        progress_enabled = bool(self._cfg('telegram_notify_progress', True))

        def progress_callback(progress_info):
            try:
                card.feed(progress_info)
            except Exception:  # noqa: BLE001
                return
            if not progress_enabled:
                return
            now = time.time()
            if now - last_scheduled[0] < min_interval:
                return
            last_scheduled[0] = now
            try:
                asyncio.run_coroutine_threadsafe(card.push(), main_loop)
            except RuntimeError:
                pass

        async def run_download_async():
            """在线程池中执行下载（供队列调用）"""
            def run_download():
                try:
                    if hasattr(downloader, 'reload_config'):
                        downloader.reload_config()

                    kwargs = {
                        'progress_callback': progress_callback,
                        'cancel_check': task_cancel_event.is_set,
                    }
                    if content_type == 'song':
                        return downloader.download_song(content_id, download_dir, **kwargs)
                    if content_type == 'album':
                        return downloader.download_album(content_id, download_dir, **kwargs)
                    if content_type == 'playlist':
                        return downloader.download_playlist(content_id, download_dir, **kwargs)
                    return {'success': False, 'error': f'不支持的类型: {content_type}', 'retryable': False}
                except DownloadCancelled:
                    raise
                except Exception as e:
                    logger.error(f"下载线程异常: {e}")
                    import traceback
                    logger.error(traceback.format_exc())
                    return {'success': False, 'error': str(e)}

            return await asyncio.to_thread(run_download)

        task = DownloadTask(
            task_id=task_id,
            coroutine_func=run_download_async,
            platform=downloader_name,
            content_type=content_type,
            content_id=content_id,
            max_retries=self.download_max_retries,
        )
        task.cancel_event = task_cancel_event

        watcher = None
        try:
            if not self.download_queue.is_running():
                await self.download_queue.start()

            self.download_queue.enqueue(task)
            logger.info(f"🚀 任务已入队: {task_id}")

            # 排队阶段单独刷新，让用户知道"前面还有几个"，别以为卡死了
            watcher = asyncio.create_task(self._watch_queue_position(card, task))

            try:
                download_timeout = self._cfg('download_timeout', 1800)
                result = await asyncio.wait_for(task._future, timeout=download_timeout)
            except asyncio.TimeoutError:
                logger.error(f"⏱️ 下载超时: {task_id}")
                self.download_queue.cancel(task_id)
                result = {'success': False, 'error': f'下载超时（{download_timeout}s）'}
            except asyncio.CancelledError:
                self.download_queue.cancel(task_id)
                raise
            except DownloadCancelled:
                result = {'success': False, 'cancelled': True, 'error': '已取消'}
            except Exception as e:
                logger.error(f"❌ 下载任务异常: {e}")
                result = {'success': False, 'error': str(e)}

            if watcher and not watcher.done():
                watcher.cancel()

            # 等最后一帧进度落盘，否则完成卡片会被迟到的进度帧盖掉
            await asyncio.sleep(0.2)

            if result.get('cancelled'):
                card.mark('cancelled')
                await ui.edit(progress_msg, card.render(), keyboard=None)
                return

            text = ui.Renderer.result_card(result, content_type, downloader_name)
            if result.get('success'):
                self._save_download_history(downloader_name, content_type, content_id, result, download_dir)
                logger.info(f"✅ 下载完成: {task_id}")
            else:
                card.mark('failed')
                logger.warning(f"❌ 下载失败: {task_id} - {result.get('error')}")

            if not await ui.edit(progress_msg, text, keyboard=None):
                # 编辑失败（例如消息太老）就补发一条，别让用户完全看不到结果
                await ui.send(message, text)

        except Exception as e:
            logger.exception(f"下载流程异常: {e}")
            if watcher and not watcher.done():
                watcher.cancel()
            try:
                await ui.edit(progress_msg, ui.Renderer.error_card('下载出错', e))
            except Exception:  # noqa: BLE001
                pass

    async def _watch_queue_position(self, card, task):
        """排队期间定期刷新「前面还有 N 个任务」。"""
        try:
            while task.status.name == 'PENDING':
                position = self.download_queue.get_position(task.task_id) or 0
                card.set_queued(position)
                await card.push()
                await asyncio.sleep(2.5)
        except asyncio.CancelledError:
            pass
        except Exception as e:  # noqa: BLE001
            logger.debug(f"排队位次刷新结束: {e}")

    def _save_download_history(self, platform: str, content_type: str, content_id: str, result: Dict[str, Any], download_dir: str):
        """保存下载历史记录

        具体字段处理收敛到 ConfigManager.record_download_result()，
        和 Web 后台走同一份逻辑——以前只有 Bot 写历史，Web 触发的下载
        跑完什么都不记，仪表盘对纯 Web 用户永远是 0。
        """
        self.config_manager.record_download_result(
            platform, content_type, content_id, result, download_dir)
    def _extract_url(self, text: str) -> Optional[str]:
        """从文本中提取 URL"""
        import re
        
        # 匹配 URL
        url_pattern = r'https?://[^\s<>"\']+|music\.163\.com[^\s<>"\']*'
        match = re.search(url_pattern, text)
        
        if match:
            url = match.group(0)
            if not url.startswith('http'):
                url = 'https://' + url
            return url
        
        return None
    
    def check_allowed_user(self, user_id: int) -> bool:
        """检查用户是否被允许"""
        allowed_users = self._cfg('telegram_allowed_users', '')
        
        if not allowed_users:
            return True  # 未配置则允许所有人
        
        allowed_ids = [int(uid.strip()) for uid in allowed_users.split(',') if uid.strip()]
        return user_id in allowed_ids
    
    async def _playlist_sync_loop(self):
        """歌单定时同步循环"""
        logger.info("📋 歌单定时同步服务已启动")
        
        # 首次启动等待 60 秒再开始检查
        await asyncio.sleep(60)
        
        while True:
            try:
                # 获取所有启用的订阅歌单
                playlists = self.config_manager.get_subscribed_playlists()
                
                for playlist in playlists:
                    if not playlist.get('enabled', True):
                        continue
                    
                    if not playlist.get('auto_download', False):
                        continue
                    
                    # 检查是否到了同步时间
                    last_check = playlist.get('last_check_time')
                    check_interval = playlist.get('check_interval', 3600)  # 默认1小时
                    
                    should_sync = False
                    if last_check is None:
                        should_sync = True
                    else:
                        from datetime import datetime
                        try:
                            last_check_time = datetime.fromisoformat(last_check)
                            elapsed = (datetime.now() - last_check_time).total_seconds()
                            if elapsed >= check_interval:
                                should_sync = True
                        except Exception:
                            should_sync = True
                    
                    if should_sync:
                        await self._sync_single_playlist(playlist)
                
            except Exception as e:
                logger.error(f"❌ 歌单同步循环出错: {e}")
            
            # 每分钟检查一次
            await asyncio.sleep(60)
    
    async def _sync_single_playlist(self, playlist: dict):
        """同步单个歌单"""
        platform = playlist.get('platform', 'netease')
        playlist_id = playlist.get('playlist_id')
        playlist_name = playlist.get('playlist_name', playlist_id)
        
        logger.info(f"🔄 开始同步歌单: {playlist_name} ({playlist_id})")
        
        try:
            if platform == 'netease':
                downloader = self.downloaders.get('netease')
                if not downloader:
                    logger.warning(f"⚠️ 网易云下载器未启用，跳过歌单 {playlist_name}")
                    return
                
                # 获取下载目录
                download_dir = self._cfg('netease_download_path', '/downloads/netease')

                # 重新加载配置与 cookies，使 Web 修改即时生效
                if hasattr(downloader, 'reload_config'):
                    downloader.reload_config()

                # 在线程池中执行同步（因为下载是同步操作）
                result = await asyncio.to_thread(
                    downloader.sync_playlist, playlist_id, download_dir
                )
                
                if result:
                    new_count = result.get('new_songs', 0)
                    downloaded = result.get('downloaded_songs', 0)
                    skipped = result.get('skipped_songs', 0)
                    failed = result.get('failed_songs', 0)
                    total = result.get('total_songs', 0)
                    
                    if new_count > 0:
                        logger.info(f"✅ 歌单 {playlist_name} 同步完成: 新增 {new_count} 首，成功 {downloaded}，失败 {failed}")
                        
                        # 发送 Telegram 通知（使用新模板）
                        await self._send_playlist_sync_notification(
                            result=result,
                            playlist_name=playlist_name,
                            is_auto=True
                        )
                    else:
                        logger.info(f"✅ 歌单 {playlist_name} 已是最新，无新歌曲")
                else:
                    logger.warning(f"⚠️ 歌单 {playlist_name} 同步失败")
            else:
                logger.warning(f"⚠️ 暂不支持 {platform} 平台的歌单同步")
                
        except Exception as e:
            logger.error(f"❌ 同步歌单 {playlist_name} 出错: {e}")
    
    async def _send_playlist_sync_notification(self, result: dict, playlist_name: str, is_auto: bool = False):
        """发送歌单同步通知（使用新模板）"""
        try:
            if not self.app or not self.app.bot:
                return
            
            # 检查是否启用通知
            if not self._cfg('telegram_notify_enabled', True):
                return
            if not self._cfg('telegram_notify_complete', True):
                return

            # 获取允许的用户列表
            allowed_users = self._cfg('telegram_allowed_users', '')
            if not allowed_users:
                return
            
            # 使用新的消息模板
            from web.tg_notifier import MessageTemplates
            
            total = result.get('total_songs', 0)
            new_songs = result.get('new_songs', 0)
            downloaded = result.get('downloaded_songs', 0)
            skipped = result.get('skipped_songs', 0)
            failed = result.get('failed_songs', 0)
            
            # 获取失败歌曲列表
            songs = result.get('songs', [])
            failed_songs_list = [s for s in songs if not s.get('success')]
            
            message = MessageTemplates.playlist_sync_completed(
                playlist_name=playlist_name,
                total_songs=total,
                new_songs=new_songs,
                downloaded=downloaded,
                failed=failed,
                skipped=skipped,
                failed_songs_list=failed_songs_list
            )
            
            # 如果是自动同步，添加标识
            if is_auto:
                message = "🤖 [自动同步]\n\n" + message
            
            # 向所有配置的用户发送通知
            for user_id in allowed_users.split(','):
                user_id = user_id.strip()
                if user_id:
                    try:
                        await self.app.bot.send_message(
                            chat_id=int(user_id),
                            text=message
                        )
                    except Exception as e:
                        logger.debug(f"发送通知给用户 {user_id} 失败: {e}")
                        
        except Exception as e:
            logger.debug(f"发送歌单同步通知失败: {e}")
    
    async def run_bot(self):
        """运行 Telegram Bot"""
        if not TELEGRAM_AVAILABLE:
            logger.error("❌ Telegram 模块不可用")
            return
        
        bot_token = self.config.get('telegram_bot_token')
        if not bot_token or bot_token == '******':
            logger.error("❌ 未配置 Telegram Bot Token")
            logger.info("💡 请访问 Web 配置界面 (http://localhost:5000) 配置 Bot Token")
            # 不退出，保持 Web 服务运行，定期检查配置
            while True:
                await asyncio.sleep(60)
                # 重新加载配置检查是否已配置
                self.config = self.config_manager.get_all_config()
                bot_token = self.config.get('telegram_bot_token')
                if bot_token and bot_token != '******':
                    logger.info("✅ 检测到 Bot Token 已配置，正在启动...")
                    break
        
        # 配置代理
        proxy_url = None
        if self.config.get('proxy_enabled', False):
            proxy_url = self.config.get('proxy_host', '')
            if proxy_url:
                logger.info(f"🌐 使用代理: {proxy_url}")
        
        # 创建应用
        try:
            builder = Application.builder().token(bot_token)
            # 关键：PTB v20+ 默认串行处理更新（concurrent_updates=False）。
            # 而下载是在 handler 里等待的，串行模式下一个人下专辑会把
            # /status、/queue 和所有人的消息全部堵住。
            builder = builder.concurrent_updates(8)
            if proxy_url:
                builder = builder.proxy_url(proxy_url).get_updates_proxy_url(proxy_url)
            self.app = builder.build()
        except Exception as e:
            logger.error(f"❌ 创建 Telegram 应用失败: {e}")
            return
        
        # 添加处理器
        self._register_handlers(self.app)
        
        logger.info("🤖 Telegram Bot 启动中...")
        
        # 运行，添加重试机制
        max_retries = 3
        retry_count = 0
        
        while retry_count < max_retries:
            try:
                await self.app.initialize()
                await self.app.start()
                await self.app.updater.start_polling(drop_pending_updates=True)
                logger.info("✅ Telegram Bot 已启动")
                await self._sync_bot_commands()
                break
            except Exception as e:
                retry_count += 1
                logger.error(f"❌ Telegram Bot 启动失败 (尝试 {retry_count}/{max_retries}): {e}")
                if retry_count < max_retries:
                    logger.info(f"⏳ 等待 30 秒后重试...")
                    await asyncio.sleep(30)
                else:
                    logger.error("❌ Telegram Bot 启动失败，请检查网络连接或代理设置")
                    logger.info("💡 如果在中国大陆，请在 Web 界面配置代理")
                    return
        
        logger.info("✅ Telegram Bot 已启动")
        
        # 启动定时同步歌单任务
        sync_task = asyncio.create_task(self._playlist_sync_loop())
        logger.info("✅ 歌单定时同步任务已启动")
        
        # 启动健康检查任务
        health_task = asyncio.create_task(self._health_check_loop())
        logger.info("✅ Bot 健康检查任务已启动")

        # 启动网易云 Cookie 守护
        # 用 get_keeper() 而不是 new 一个：Web 线程可能已经把实例建好了，
        # 直接替换会导致两个守护线程同时跑、重复续期。
        try:
            self.cookie_keeper = get_keeper(self.config_manager)
            self.cookie_keeper.on_cookies_updated = self._reload_netease_downloader
            self.cookie_keeper.start()
        except Exception as e:
            logger.warning(f"⚠️ Cookie 守护启动失败: {e}")
        
        # 保持运行
        try:
            while True:
                await asyncio.sleep(1)
        except asyncio.CancelledError:
            pass
        finally:
            sync_task.cancel()
            health_task.cancel()
            if self.cookie_keeper:
                self.cookie_keeper.stop()
            await self.download_queue.stop()
            try:
                await sync_task
            except asyncio.CancelledError:
                pass
            try:
                await health_task
            except asyncio.CancelledError:
                pass
            await self.app.updater.stop()
            await self.app.stop()
            await self.app.shutdown()

    def _reload_netease_downloader(self):
        """cookie 变更后热重载网易云下载器（Web 与 Bot 共用同一个实例）"""
        downloader = self.downloaders.get('netease')
        if downloader and hasattr(downloader, 'reload_config'):
            try:
                downloader.reload_config()
            except Exception as e:
                logger.warning(f"重载网易云下载器失败: {e}")
    
    # Bot 命令菜单。注册到 Telegram 之后，聊天框左下角会出一个菜单按钮，
    # 不用再靠记忆敲命令——这是交互层面最省事的一处改进。
    BOT_COMMANDS = [
        ('start', '🏠 开始 / 使用说明'),
        ('status', '📊 状态总览'),
        ('queue', '📥 下载队列'),
        ('history', '📜 下载历史'),
        ('cookie', '🍪 网易云账号'),
        ('cancel', '⏹ 取消我的任务'),
        ('id', '🆔 我的 Telegram ID'),
        ('help', '❓ 帮助'),
    ]

    async def _sync_bot_commands(self):
        """把命令菜单同步给 Telegram（重复调用无副作用）。"""
        if not self.app or not self.app.bot:
            return
        try:
            commands = [BotCommand(name, desc) for name, desc in self.BOT_COMMANDS]
            await self.app.bot.set_my_commands(commands)
            logger.info(f"✅ 已同步 {len(commands)} 个命令到 Telegram 菜单")
        except Exception as e:  # noqa: BLE001
            logger.warning(f"⚠️ 同步命令菜单失败（不影响使用）: {e}")

    def _register_handlers(self, app):
        """注册 Telegram 处理器（初始化与重建时复用）"""
        app.add_handler(CommandHandler('start', self.handle_start))
        app.add_handler(CommandHandler('help', self.handle_start))
        app.add_handler(CommandHandler('status', self.handle_status))
        app.add_handler(CommandHandler('queue', self.handle_queue))
        app.add_handler(CommandHandler('history', self.handle_history))
        app.add_handler(CommandHandler('cookie', self.handle_cookie))
        app.add_handler(CommandHandler('cancel', self.handle_cancel))
        app.add_handler(CommandHandler('id', self.handle_id))
        app.add_handler(CallbackQueryHandler(self.handle_callback_query))
        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self._safe_handle_message))

    async def _health_check_loop(self):
        """Bot 健康检查 + 看门狗

        设计要点（修复“长时间运行后崩溃、再也没反馈”）：
        - 不再调用 get_updates：它会与长轮询抢占 offset，导致丢消息 / 409 Conflict
        - 通过 _polling_task.done() 检测 polling 是否已静默死亡（核心看门狗）
        - 通过 get_me() 验证网络连通
        - 连续异常先轻量重连；仍失败则完全重建 Application
        """
        logger.info("💓 Bot 健康检查与看门狗已启动")
        check_interval = 120
        consecutive_failures = 0
        max_failures = 3

        while True:
            try:
                await asyncio.sleep(check_interval)

                need_reconnect = False
                reason = ""

                if not self.app or not self.app.bot:
                    need_reconnect, reason = True, "Application 未初始化"
                else:
                    updater = getattr(self.app, 'updater', None)
                    # 1) 看 polling task 是否还活着——这是“再也没反馈”的根因
                    polling_task = getattr(updater, '_polling_task', None) if updater else None
                    if polling_task is not None and polling_task.done():
                        exc = polling_task.exception()
                        need_reconnect = True
                        reason = f"polling task 已结束({exc})" if exc else "polling task 已结束"
                    elif updater is None or not getattr(updater, 'running', False):
                        need_reconnect, reason = True, "updater 未运行"
                    else:
                        # 2) 验证网络连通
                        try:
                            await asyncio.wait_for(self.app.bot.get_me(), timeout=15)
                            consecutive_failures = 0
                        except asyncio.TimeoutError:
                            need_reconnect, reason = True, "get_me 超时"
                        except Exception as e:
                            need_reconnect, reason = True, f"get_me 失败: {e}"

                if need_reconnect:
                    consecutive_failures += 1
                    logger.warning(f"⚠️ 健康检查异常: {reason}（连续 {consecutive_failures}/{max_failures}）")
                    if consecutive_failures >= max_failures:
                        logger.warning("⚠️ 连续失败达上限，执行完全重建")
                        await self._rebuild_application()
                        consecutive_failures = 0
                    else:
                        await self._reconnect_bot()
                else:
                    if consecutive_failures:
                        consecutive_failures = 0

            except asyncio.CancelledError:
                logger.info("💓 健康检查任务已取消")
                break
            except Exception as e:
                logger.error(f"❌ 健康检查循环异常: {e}")
                await asyncio.sleep(60)
    
    async def _reconnect_bot(self):
        """轻量重连：仅重启 polling，不动 Application。

        单次尝试，失败则升级为完全重建（_rebuild_application）。
        不在这里做长时间重试循环，避免阻塞健康检查。
        """
        if not self.app or not self.app.updater:
            await self._rebuild_application()
            return
        try:
            if getattr(self.app.updater, 'running', False):
                try:
                    await asyncio.wait_for(self.app.updater.stop(), timeout=10)
                except asyncio.TimeoutError:
                    logger.warning("⚠️ 停止 updater 超时，继续重启")
            await asyncio.sleep(2)
            # drop_pending_updates=False：重连不丢弃未处理消息，避免漏消息
            await asyncio.wait_for(
                self.app.updater.start_polling(drop_pending_updates=False),
                timeout=30
            )
            logger.info("✅ 轻量重连成功（polling 已重启）")
        except Exception as e:
            logger.error(f"❌ 轻量重连失败: {e}，升级为完全重建")
            await self._rebuild_application()

    async def _rebuild_application(self):
        """完全重建 Application：轻量重连无效时的兜底。

        长时间运行后 Application 内部状态可能损坏，
        仅重启 polling 无法恢复，需要整体销毁重建。
        """
        logger.warning("🔄 完全重建 Telegram Application...")
        old_app = self.app
        bot_token = self._cfg('telegram_bot_token')
        if not bot_token or bot_token == '******':
            logger.error("❌ 重建失败：未配置 Bot Token")
            return
        proxy_url = None
        if self._cfg('proxy_enabled', False):
            proxy_url = self._cfg('proxy_host', '')

        # 1) 关闭旧 Application（每步都加超时，防止卡死）
        if old_app:
            try:
                if old_app.updater and getattr(old_app.updater, 'running', False):
                    await asyncio.wait_for(old_app.updater.stop(), timeout=10)
            except Exception as e:
                logger.warning(f"⚠️ 停止旧 updater: {e}")
            try:
                if getattr(old_app, 'running', False):
                    await asyncio.wait_for(old_app.stop(), timeout=10)
            except Exception as e:
                logger.warning(f"⚠️ 停止旧 app: {e}")
            try:
                await asyncio.wait_for(old_app.shutdown(), timeout=10)
            except Exception as e:
                logger.warning(f"⚠️ shutdown 旧 app: {e}")

        await asyncio.sleep(2)

        # 2) 重建
        try:
            builder = Application.builder().token(bot_token)
            if proxy_url:
                builder = builder.proxy_url(proxy_url).get_updates_proxy_url(proxy_url)
            self.app = builder.build()
            self._register_handlers(self.app)
            await self.app.initialize()
            await self.app.start()
            await self.app.updater.start_polling(drop_pending_updates=False)
            await self._sync_bot_commands()
            logger.info("✅ Application 完全重建成功")
        except Exception as e:
            logger.error(f"❌ Application 重建失败: {e}，将在下次健康检查时重试")


def _reload_netease_after_cookie_change():
    """Cookie 变更后热重载下载器（Web 侧扫码/手动填写时用）"""
    try:
        from downloaders import get_downloader
        downloader = get_downloader('netease')
        if hasattr(downloader, 'reload_config'):
            downloader.reload_config()
    except Exception as e:  # noqa: BLE001
        logger.warning(f"热重载网易云下载器失败: {e}")


def run_web_server(host: str = '0.0.0.0', port: int = 5000, db_path: str = None):
    """运行 Web 服务器"""
    init_web_app(db_path)

    # --web-only 模式下没人启动 Cookie 守护线程，之前只有 run_bot() 会启动，
    # 结果队列/仪表盘写着「Cookie 自动守护中」而实际什么都没在守护。
    # start() 是幂等的，和 Bot 同时跑也不会起两个线程。
    try:
        from cookie_keeper import get_keeper
        keeper = get_keeper()
        if keeper.on_cookies_updated is None:
            keeper.on_cookies_updated = _reload_netease_after_cookie_change
        keeper.start()
    except Exception:
        logger.exception("启动 Cookie 守护线程失败（不影响 Web 服务）")

    web_app.run(host=host, port=port, debug=False, threaded=True)


def main():
    """主函数"""
    import argparse
    
    parser = argparse.ArgumentParser(description='Music Bot - 音乐下载机器人')
    parser.add_argument('--web-only', action='store_true', help='仅启动 Web 配置服务')
    parser.add_argument('--bot-only', action='store_true', help='仅启动 Telegram Bot')
    parser.add_argument('--web-port', type=int, default=5000, help='Web 服务端口')
    parser.add_argument('--db-path', type=str, default=None, help='数据库路径')
    
    args = parser.parse_args()
    
    logger.info(f"🎵 Music Bot v{BOT_VERSION}")
    logger.info("=" * 50)
    
    if args.web_only:
        # 仅运行 Web 服务
        logger.info(f"🌐 启动 Web 配置服务 (端口: {args.web_port})")
        run_web_server(port=args.web_port, db_path=args.db_path)
    elif args.bot_only:
        # 仅运行 Bot
        bot = MusicBot(args.db_path)
        asyncio.run(bot.run_bot())
    else:
        # 同时运行 Web 和 Bot
        bot = MusicBot(args.db_path)
        
        # 在后台线程运行 Web 服务（db_path 一并传下去，否则 Web 走默认库）
        web_thread = threading.Thread(
            target=run_web_server,
            kwargs={'port': args.web_port, 'db_path': args.db_path},
            daemon=True
        )
        web_thread.start()
        logger.info(f"🌐 Web 配置服务已启动 (端口: {args.web_port})")
        
        # 在主线程运行 Bot
        try:
            asyncio.run(bot.run_bot())
        except KeyboardInterrupt:
            logger.info("👋 程序已停止")


if __name__ == '__main__':
    main()
