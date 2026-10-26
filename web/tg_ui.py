#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Telegram 消息渲染层。

为什么需要这一层：原先所有消息都是 `reply_text(拼字符串)`，有两个硬伤。

1. **旧版 Markdown 一碰就炸**。`/history`、`/status` 用 ParseMode.MARKDOWN
   拼 `*${title}*`，网易云的歌单名里经常有 `[`、`_`、`*`，Telegram 会回
   "can't parse entities"，整条消息发不出去，用户看到的是「没反应」。
   这里统一走 HTML：动态内容全部 escape，只有我们自己加的标签是标记。

2. **进度消息没有节流/去重**。每个 chunk 都想 edit 一次，撞上
   "Message is not modified" 之后整个编辑流程就断了。这里用
   「时间间隔 + 文本指纹」双重去重，并且把常见异常分类处理。

另外集中处理：超长截断（4096 上限）、RetryAfter 退避、消息不可编辑时降级发新消息。
"""

import asyncio
import hashlib
import html
import logging
import time
from typing import Any, Dict, Optional

# python-telegram-bot 只在 Bot 模式下需要。纯 Web 模式（--web-only）也会 import
# 这个模块（main.py 顶层导入），所以这里做降级，别让缺依赖把 Web 服务带崩。
try:
    from telegram.constants import ParseMode
    from telegram.error import BadRequest, RetryAfter
    TELEGRAM_AVAILABLE = True
except ImportError:  # pragma: no cover
    TELEGRAM_AVAILABLE = False

    class ParseMode:  # type: ignore[no-redef]
        HTML = 'HTML'

    class BadRequest(Exception):  # type: ignore[no-redef]
        pass

    class RetryAfter(Exception):  # type: ignore[no-redef]
        retry_after = 3

logger = logging.getLogger('music_bot.tg_ui')

# Telegram 单条消息 4096 字符。留 200 余量给"被截断"提示和标签闭合
MAX_LEN = 3900
TRUNCATED_HINT = '\n\n<i>…（内容过长，已截断）</i>'

# 进度条：12 格在手机上不会折行，20 格就开始换行难看了
BAR_CELLS = 12
BAR_FULL = '█'
BAR_EMPTY = '░'

STATE_TITLES = {
    'queued': ('⏳', '排队中'),
    'downloading': ('⬇️', '下载中'),
    'preparing': ('🔎', '准备下载'),
    'retrying': ('🔄', '重试中'),
    'done': ('✅', '下载完成'),
    'failed': ('❌', '下载失败'),
    'cancelled': ('⏹', '已取消'),
}

TYPE_LABELS = {
    'song': ('🎵', '单曲'),
    'album': ('💿', '专辑'),
    'playlist': ('📋', '歌单'),
}

PLATFORM_LABELS = {
    'netease': ('🎵', '网易云音乐'),
    'apple_music': ('🍎', 'Apple Music'),
}


# ==================== 基础转义 / 格式化 ====================

def esc(value: Any) -> str:
    """转义为 HTML 安全文本。Telegram 的 HTML 模式只认少数标签，
    文本里的 & < > 必须转义，否则同样报 parse error。"""
    return html.escape(str(value if value is not None else ''), quote=False)


def b(value: Any) -> str:
    return f'<b>{esc(value)}</b>'


def code(value: Any) -> str:
    return f'<code>{esc(value)}</code>'


def it(value: Any) -> str:
    return f'<i>{esc(value)}</i>'


def dehtml(text: str) -> str:
    """把 HTML 还原成纯文本，用于 parse 失败后的降级发送。"""
    import re
    plain = re.sub(r'<[^>]+>', '', text or '')
    return html.unescape(plain)


def clip(text: str, limit: int = MAX_LEN) -> str:
    """超长截断。先按纯文本长度粗判，避免把标签切一半。"""
    if len(text) <= limit:
        return text
    plain = dehtml(text)
    if len(plain) <= limit:
        return text
    return esc(plain[:limit - len(TRUNCATED_HINT)]) + TRUNCATED_HINT


def bar(percent: float, cells: int = BAR_CELLS) -> str:
    """文字进度条。必须包在 <code> 里才等宽——Telegram 正文字体不是等宽字体，
    不包的话 █ 和 ░ 宽度不一致，进度条看起来是歪的。"""
    try:
        percent = max(0.0, min(100.0, float(percent or 0)))
    except (TypeError, ValueError):
        percent = 0.0
    filled = int(round(cells * percent / 100))
    filled = max(0, min(cells, filled))
    return code(BAR_FULL * filled + BAR_EMPTY * (cells - filled))


def fmt_size(num_bytes: float) -> str:
    try:
        size = float(num_bytes or 0)
    except (TypeError, ValueError):
        return '—'
    if size <= 0:
        return '—'
    for unit, scale in (('GB', 1024 ** 3), ('MB', 1024 ** 2), ('KB', 1024)):
        if size >= scale:
            value = size / scale
            return f'{value:.1f} {unit}' if value < 10 else f'{value:.0f} {unit}'
    return f'{int(size)} B'


def fmt_speed(bytes_per_sec: float) -> str:
    try:
        speed = float(bytes_per_sec or 0)
    except (TypeError, ValueError):
        return '—'
    if speed <= 0:
        return '—'
    return f'{fmt_size(speed)}/s'


def fmt_eta(seconds: float) -> str:
    try:
        seconds = int(float(seconds or 0))
    except (TypeError, ValueError):
        return '—'
    if seconds <= 0:
        return '—'
    if seconds >= 3600:
        h, rest = divmod(seconds, 3600)
        return f'{h}h{rest // 60:02d}m'
    if seconds >= 60:
        m, s = divmod(seconds, 60)
        return f'{m}m{s:02d}s'
    return f'{seconds}s'


def fmt_duration(seconds: float) -> str:
    try:
        seconds = int(float(seconds or 0))
    except (TypeError, ValueError):
        return ''
    if seconds <= 0:
        return ''
    m, s = divmod(seconds, 60)
    return f'{m}:{s:02d}'


def truncate(text: str, limit: int = 40) -> str:
    text = str(text or '').strip()
    if len(text) <= limit:
        return text
    return text[:limit - 1] + '…'


def platform_label(platform: str):
    return PLATFORM_LABELS.get(str(platform or '').lower(), ('🎵', platform or '未知平台'))


def type_label(content_type: str):
    return TYPE_LABELS.get(str(content_type or '').lower(), ('📀', content_type or '内容'))


# ==================== 错误翻译 ====================

# 用户看到 "HTTP 403" 是没有任何信息的。把常见的失败翻译成「原因 + 该怎么办」。
_ERROR_RULES = (
    (('cookie', '登录', '未登录', 'cookie 失效', '未找到用户',
      '401', 'unauthorized', 'login required', 'invalid token'),
     'Cookie 失效或未登录',
     '在 Web 后台「账号」页扫码重新登录，或发送 /cookie 更新'),
    (('vip', '会员', '付费', '无版权', '版权'),
     '需要会员或无版权',
     '换一首歌，或登录已开通会员的账号'),
    (('404', '不存在', '找不到'),
     '内容不存在或被删除',
     '检查链接是否完整、歌单是否公开'),
    (('timeout', '超时', 'timed out', 'read timed out'),
     '网络超时',
     '稍后重试；如果需要代理，检查设置里的代理地址'),
    (('connection', '连接', 'ssl', 'proxy', '代理'),
     '网络/代理异常',
     '检查代理是否可用，或稍后重试'),
    (('403', 'forbidden'),
     '服务器拒绝访问',
     '多半是 Cookie 失效或触发风控，换 Cookie 后重试'),
    (('429', 'too many', '频繁'),
     '请求过于频繁',
     '等几分钟再试，不要连续刷同一个链接'),
    (('space', 'no space', '磁盘', '权限', 'permission', 'read-only'),
     '写入磁盘失败',
     '检查下载目录是否可写、磁盘是否还有空间'),
    (('已取消', 'cancelled', 'canceled'),
     '已手动取消',
     ''),
)


def explain_error(error: Any):
    """把一句异常翻译成 (原因, 建议)。认不出来就原样返回。"""
    raw = str(error or '').strip()
    lowered = raw.lower()
    for keywords, reason, advice in _ERROR_RULES:
        if any(k in lowered for k in keywords):
            return reason, advice
    return truncate(raw, 120) or '未知错误', ''


# ==================== 安全发送 / 编辑 ====================

def _is_parse_error(exc: Exception) -> bool:
    text = str(exc).lower()
    return 'parse' in text or 'entities' in text or 'tag' in text


async def send(target, text: str, keyboard=None, disable_preview: bool = True,
               parse_mode: str = ParseMode.HTML):
    """安全发送。target 可以是 Message / Bot+chat_id 包装，见 send_to。

    - HTML 解析失败 → 自动降级为纯文本重发（宁可丢样式，也不能丢消息）
    - RetryAfter → 按 Telegram 给的时间等一次
    """
    payload = dict(
        text=clip(text),
        parse_mode=parse_mode,
        disable_web_page_preview=disable_preview,
    )
    if keyboard is not None:
        payload['reply_markup'] = keyboard

    for attempt in range(2):
        try:
            if hasattr(target, 'reply_text'):
                return await target.reply_text(**payload)
            return await target.send_message(**payload)
        except RetryAfter as e:
            wait = getattr(e, 'retry_after', 3)
            logger.warning(f'⏳ Telegram 限流，等待 {wait}s 后重发')
            await asyncio.sleep(min(float(wait) + 0.5, 30))
        except BadRequest as e:
            if _is_parse_error(e) and payload.get('parse_mode') and attempt == 0:
                logger.warning(f'⚠️ HTML 解析失败，降级为纯文本重发: {e}')
                payload['text'] = dehtml(payload['text'])
                payload['parse_mode'] = None
                continue
            logger.error(f'❌ 发送消息失败: {e}')
            return None
        except Exception as e:  # noqa: BLE001
            logger.error(f'❌ 发送消息异常: {e}')
            return None
    return None


async def edit(message, text: str, keyboard=None, disable_preview: bool = True):
    """安全编辑。返回 True 表示确实改动了内容。

    三类正常情况必须静默处理，否则日志会被刷爆：
    - 内容没变 → Telegram 报 "Message is not modified"
    - 消息太老/被删 → "message can't be edited"
    """
    if message is None:
        return False

    body = clip(text)
    current = getattr(message, 'text_html', None) or getattr(message, 'text', None) or ''
    if current == body and keyboard is None:
        return False

    try:
        await message.edit_text(
            text=body,
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=disable_preview,
            reply_markup=keyboard,
        )
        return True
    except RetryAfter as e:
        logger.debug(f'进度编辑被限流 {getattr(e, "retry_after", 0)}s')
        return False
    except BadRequest as e:
        lowered = str(e).lower()
        if 'not modified' in lowered:
            return False
        if _is_parse_error(e):
            logger.warning(f'⚠️ 进度消息 HTML 解析失败，降级为纯文本: {e}')
            try:
                await message.edit_text(text=dehtml(body), reply_markup=keyboard)
                return True
            except Exception:  # noqa: BLE001
                return False
        if "can't be edited" in lowered or 'message to edit not found' in lowered:
            logger.debug('消息已不可编辑（过期或被删除），后续进度改为发新消息')
            return False
        logger.warning(f'⚠️ 编辑消息失败: {e}')
        return False
    except Exception as e:  # noqa: BLE001
        logger.debug(f'编辑消息异常: {e}')
        return False


# ==================== 进度卡片 ====================

class ProgressCard:
    """一条消息内滚动的进度卡片。

    与旧实现的区别：
    - 文本指纹去重：内容没变就不发 API（旧实现靠时间间隔，仍然会撞 not modified）
    - 队列阶段单独渲染：排队时显示"前面还有 N 个任务"，别让用户以为卡住了
    - 自带取消按钮的 callback_data
    """

    def __init__(self, min_interval: float = 1.0, cancel_data: Optional[str] = None):
        self.min_interval = max(0.3, float(min_interval or 1.0))
        self.cancel_data = cancel_data
        self.message = None
        self._last_push = 0.0
        self._last_hash = ''
        self._last_text = ''

        # 渲染状态
        self.state = 'preparing'
        self.platform = ''
        self.content_type = 'song'
        self.content_id = ''
        self.collection = ''
        self.filename = ''
        self.current_index = 0
        self.total_count = 0
        self.percent = 0.0
        self.downloaded = 0
        self.total_bytes = 0
        self.speed = 0.0
        self.eta = 0.0
        self.queue_position = 0
        self.retry_count = 0
        self.note = ''

    # ---------- 状态更新 ----------

    def attach(self, message):
        self.message = message
        return self

    def set_queued(self, position: int = 0):
        self.state = 'queued'
        self.queue_position = position

    def feed(self, info: Dict[str, Any]):
        """吃一个 progress_callback 的 payload。"""
        if not isinstance(info, dict):
            return
        status = info.get('status', '')

        if status in ('album_progress', 'playlist_progress'):
            self.state = 'preparing'
            self.content_type = 'album' if status == 'album_progress' else 'playlist'
            self.current_index = int(info.get('current') or 0)
            self.total_count = int(info.get('total') or 0)
            self.filename = info.get('song') or ''
            self.collection = info.get('album') or info.get('playlist') or self.collection
            # 切歌时进度归零，否则新歌会先显示上一首的 100%
            self.percent = 0.0
            self.downloaded = 0
            self.total_bytes = 0
            self.speed = 0.0
            self.eta = 0.0
            return

        for key in ('album_context', 'playlist_context'):
            ctx = info.get(key)
            if ctx:
                self.content_type = 'album' if key == 'album_context' else 'playlist'
                self.collection = ctx.get('album') or ctx.get('playlist') or self.collection
                self.current_index = int(ctx.get('current') or self.current_index)
                self.total_count = int(ctx.get('total') or self.total_count)
                if ctx.get('song'):
                    self.filename = ctx['song']

        name = info.get('filename')
        if name:
            import os
            self.filename = os.path.basename(str(name))

        if info.get('percent') is not None:
            try:
                self.percent = float(info.get('percent') or 0)
            except (TypeError, ValueError):
                self.percent = 0.0
        if info.get('downloaded') is not None:
            self.downloaded = int(info.get('downloaded') or 0)
        if info.get('total') is not None:
            self.total_bytes = int(info.get('total') or 0)
        if info.get('speed'):
            self.speed = float(info.get('speed') or 0)
        if info.get('eta'):
            self.eta = float(info.get('eta') or 0)
        if self.total_bytes and self.downloaded:
            self.percent = min(100.0, self.downloaded / self.total_bytes * 100)

        if status == 'finished':
            self.percent = 100.0
        if self.state in ('preparing', 'queued'):
            self.state = 'downloading'

    def mark(self, state: str, note: str = ''):
        self.state = state
        self.note = note

    # ---------- 渲染 ----------

    def _overall_percent(self) -> float:
        """专辑/歌单：把「第几首」和「这首的字节进度」合成一个总进度，
        否则进度条会一首歌从 0 刷到 100 再跳回 0，看着像在反复重来。"""
        if self.content_type in ('album', 'playlist') and self.total_count > 0:
            done = max(0, self.current_index - 1)
            fraction = self.percent / 100.0
            return min(100.0, (done + fraction) / self.total_count * 100)
        return self.percent

    def render(self) -> str:
        icon, title = STATE_TITLES.get(self.state, ('⬇️', '处理中'))
        p_icon, p_name = platform_label(self.platform)
        t_icon, t_name = type_label(self.content_type)

        lines = [f'{icon} {b(title)} · {t_name}']

        if self.content_type in ('album', 'playlist') and self.collection:
            lines.append(f'{t_icon} {b(truncate(self.collection, 44))}')
            if self.current_index and self.total_count:
                song = f' · {esc(truncate(self.filename, 30))}' if self.filename else ''
                lines.append(f'第 {self.current_index}/{self.total_count} 首{song}')
        else:
            name = truncate(self.filename or self.content_id or '获取中…', 52)
            lines.append(f'{t_icon} {b(name)}')

        if self.state == 'queued':
            if self.queue_position and self.queue_position > 1:
                lines.append('')
                lines.append(f'前面还有 {self.queue_position - 1} 个任务，轮到会自动开始')
            else:
                lines.append('')
                lines.append('等待空闲下载位…')
            return '\n'.join(lines)

        if self.state == 'preparing':
            lines.append('')
            lines.append(f'{bar(0)} 正在解析…')
            return '\n'.join(lines)

        if self.state in ('downloading', 'retrying'):
            percent = self._overall_percent()
            lines.append('')
            lines.append(f'{bar(percent)} {b(f"{percent:.0f}%")}')

            detail = []
            if self.total_bytes > 0:
                detail.append(f'{fmt_size(self.downloaded)} / {fmt_size(self.total_bytes)}')
            elif self.downloaded:
                detail.append(fmt_size(self.downloaded))
            if self.speed:
                detail.append(fmt_speed(self.speed))
            if self.eta:
                detail.append(f'剩 {fmt_eta(self.eta)}')
            if detail:
                lines.append(' · '.join(detail))
            if self.state == 'retrying':
                lines.append(f'⚠️ 第 {self.retry_count} 次重试')
            return '\n'.join(lines)

        if self.state == 'done':
            lines.append('')
            lines.append(f'{bar(100)} {b("100%")}')
            return '\n'.join(lines)

        if self.state == 'cancelled':
            lines.append('')
            lines.append('已停止下载，未完成的文件保留断点，重新发送链接可继续')
            return '\n'.join(lines)

        lines.append('')
        lines.append(esc(self.note or '未知错误'))
        return '\n'.join(lines)

    def keyboard(self):
        """下载中给一个取消按钮；结束后不挂按钮（点了也没意义）。"""
        if not self.cancel_data or self.state in ('done', 'failed', 'cancelled'):
            return None
        try:
            from telegram import InlineKeyboardButton, InlineKeyboardMarkup
        except ImportError:  # pragma: no cover
            return None
        return InlineKeyboardMarkup([[
            InlineKeyboardButton('⏹ 取消下载', callback_data=self.cancel_data)
        ]])

    # ---------- 推送 ----------

    async def push(self, force: bool = False) -> bool:
        if self.message is None:
            return False

        now = time.time()
        if not force and now - self._last_push < self.min_interval:
            return False

        text = self.render()
        digest = hashlib.md5(text.encode('utf-8')).hexdigest()
        if not force and digest == self._last_hash:
            self._last_push = now
            return False

        changed = await edit(self.message, text, keyboard=self.keyboard())
        self._last_push = now
        if changed:
            self._last_hash = digest
            self._last_text = text
        return changed


class Renderer:
    """各类固定卡片。都不带 parse_mode 之外的副作用，方便单测。"""

    @staticmethod
    def welcome(bot_name: str = 'Music Bot') -> str:
        return (
            f'🎵 {b(bot_name)}\n'
            f'{it("发个链接就能下载，网易云和 Apple Music 都行。")}\n\n'
            f'{b("怎么用")}\n'
            f'直接把歌曲 / 专辑 / 歌单链接发给我，也可以直接复制分享文案：\n'
            f'{code("分享周杰伦的歌曲《稻香》: https://163cn.tv/xxxx")}\n\n'
            f'{b("支持的链接")}\n'
            f'🎵 网易云音乐 · 单曲 / 专辑 / 歌单\n'
            f'🍎 Apple Music · 单曲 / 专辑\n\n'
            f'{b("常用命令")}\n'
            f'/status 状态总览 · /queue 下载队列\n'
            f'/history 下载历史 · /cookie 网易云账号\n'
            f'/cancel 取消任务 · /id 查看我的 ID\n\n'
            f'{it("点下面的按钮可以少打字。")}'
        )

    @staticmethod
    def status_card(data: Dict[str, Any]) -> str:
        """data 由 main.py 组装，详见 _build_status_data()。"""
        lines = [f'📊 {b("Bot 状态")}', '']

        # 平台
        lines.append(b('下载器'))
        for item in data.get('platforms', []):
            mark = '✅' if item.get('ready') else '⚠️'
            extra = item.get('note') or ''
            lines.append(f'  {mark} {esc(item.get("label"))}' + (f' · {esc(extra)}' if extra else ''))
        if not data.get('platforms'):
            lines.append('  ⚠️ 暂无可用下载器')
        if data.get('netease'):
            lines.append(f'  {Renderer.netease_block(data["netease"])}')
        lines.append('')

        # 队列
        q = data.get('queue', {})
        lines.append(b('下载队列'))
        lines.append(
            f'  并发 {code(q.get("max_concurrent", 0))} · '
            f'执行 {code(q.get("active", 0))} · '
            f'排队 {code(q.get("pending", 0))}'
        )
        lines.append(
            f'  完成 {code(q.get("completed", 0))} · '
            f'失败 {code(q.get("failed", 0))}'
        )
        lines.append('')

        # 曲库
        lib = data.get('library', {})
        lines.append(b('曲库'))
        lines.append(
            f'  {fmt_size(lib.get("bytes"))} · '
            f'{code(lib.get("files", 0))} 个文件'
        )
        if lib.get('missing'):
            lines.append(f'  ⚠️ 目录不存在：{esc("、".join(lib["missing"][:3]))}')
        lines.append('')

        # 今日
        stats = data.get('stats', {})
        lines.append(b('今天'))
        lines.append(
            f'  新增 {code(stats.get("today", 0))} · '
            f'累计 {code(stats.get("total", 0))}'
        )

        if data.get('note'):
            lines.append('')
            lines.append(it(data['note']))
        return '\n'.join(lines)

    @staticmethod
    def netease_block(meta: Dict[str, Any]) -> str:
        state = (meta or {}).get('state', 'unknown')
        icon = {
            'ok': '✅', 'valid': '✅', 'expiring': '⚠️',
            'expired': '❌', 'invalid': '❌', 'unknown': '❔',
        }.get(str(state).lower(), '❔')
        nickname = (meta or {}).get('nickname') or ''
        vip = (meta or {}).get('vip_label') or ''
        label = {'ok': '登录正常', 'valid': '登录正常', 'expiring': '即将过期',
                 'expired': '已过期', 'invalid': '登录失效'}.get(str(state).lower(), '未知')
        tail = ' · '.join(x for x in (nickname, vip) if x)
        return f'{icon} {esc(label)}' + (f' · {esc(tail)}' if tail else '')

    @staticmethod
    def queue_card(status: Dict[str, Any]) -> str:
        tasks = [t for t in status.get('tasks', [])
                 if t.get('status') in ('pending', 'running', 'retrying')]
        lines = [f'📥 {b("下载队列")}',
                 f'并发 {code(status.get("max_concurrent", 0))} · '
                 f'执行 {code(status.get("active", 0))} · '
                 f'排队 {code(status.get("pending", 0))}',
                 '']

        if not tasks:
            lines.append(it('当前没有进行中的任务'))
            return '\n'.join(lines)

        state_icon = {'pending': '⏳', 'running': '▶️', 'retrying': '🔄'}
        for index, task in enumerate(tasks[:10], 1):
            icon = state_icon.get(task.get('status'), '❔')
            _, t_name = type_label(task.get('content_type'))
            _, p_name = platform_label(task.get('platform'))
            lines.append(
                f'{index}. {icon} {esc(t_name)} · {esc(p_name)}\n'
                f'    {esc(truncate(task.get("message") or task.get("content_id"), 34))}'
            )
        if len(tasks) > 10:
            lines.append(f'\n{it(f"还有 {len(tasks) - 10} 个任务")}')
        return '\n'.join(lines)

    @staticmethod
    def history_card(rows, page: int, total_pages: int, total: int) -> str:
        if not rows:
            return f'📜 {b("下载历史")}\n\n{it("还没有下载记录")}'

        lines = [f'📜 {b("下载历史")} {it(f"第 {page}/{total_pages} 页 · 共 {total} 条")}', '']
        for index, row in enumerate(rows, 1):
            _, p_name = platform_label(row.get('platform'))
            t_icon, t_name = type_label(row.get('content_type'))
            title = truncate(row.get('title') or '未知', 34)
            artist = truncate(row.get('artist') or '', 24)
            when = str(row.get('created_at') or '')[:16]

            size_text = ''
            raw_size = row.get('file_size') or 0
            if row.get('content_type') == 'song':
                if raw_size:
                    size_text = fmt_size(raw_size)
            else:
                # 老数据这里存的是曲目数，别当字节显示成 "12 B"
                size_text = str(row.get('quality') or '')

            tail = ' · '.join(x for x in (size_text, when) if x)
            head = f'<b>{index + (page - 1) * 8}.</b> {t_icon} {esc(title)}'
            if artist:
                head += f' {it(esc(artist))}'
            lines.append(head)
            if tail:
                lines.append(f'    {esc(tail)}')
        return '\n'.join(lines)

    @staticmethod
    def duplicate_found(title: str, platform: str, created_at: str) -> str:
        _, p_name = platform_label(platform)
        return (
            f'⚠️ {b("这个之前下过了")}\n\n'
            f'📀 {esc(truncate(title, 60))}\n'
            f'🕐 {esc(str(created_at)[:16])} · {esc(p_name)}\n\n'
            f'{it("要重新下一遍吗？已存在的文件会被覆盖。")}'
        )

    @staticmethod
    def result_card(result: Dict[str, Any], content_type: str, platform: str) -> str:
        """下载结果。单曲一张卡；专辑/歌单折叠成一行行统计，不刷屏。"""
        if not result.get('success'):
            reason, advice = explain_error(result.get('error'))
            lines = [f'❌ {b("下载失败")}',
                     '',
                     f'{b("原因")}：{esc(reason)}']
            if advice:
                lines.append(f'{b("建议")}：{esc(advice)}')
            return '\n'.join(lines)

        p_icon, p_name = platform_label(platform)
        t_icon, t_name = type_label(content_type)

        if content_type == 'song':
            title = result.get('song_title') or '未知'
            artist = result.get('song_artist') or ''
            quality = result.get('quality') or ''
            bitrate = result.get('bitrate') or ''
            duration = result.get('duration') or ''
            size_text = fmt_size(float(result.get('size_mb') or 0) * 1024 * 1024)

            lines = [f'✅ {b("下载完成")}', '',
                     f'{t_icon} {b(truncate(title, 52))}']
            if artist:
                lines.append(f'{it(esc(truncate(artist, 40)))}')
            lines.append('')
            meta = ' · '.join(x for x in (size_text, quality, bitrate, duration) if x and x != '—')
            lines.append(f'💾 {esc(meta)}')

            path = result.get('filepath') or ''
            if path:
                import os
                folder = os.path.dirname(path)
                if folder:
                    lines.append(f'📁 {code(truncate(folder, 60))}')
            return '\n'.join(lines)

        songs = result.get('songs') or []
        ok = [s for s in songs if isinstance(s, dict) and s.get('success')]
        failed = [s for s in songs if isinstance(s, dict) and not s.get('success')]
        total = result.get('total_songs') or len(songs)
        new_count = result.get('new_songs')
        total_bytes = sum(float(s.get('size_mb') or 0) for s in ok) * 1024 * 1024
        name = (result.get('album_name') or result.get('playlist_title')
                or result.get('playlist_title') or '未知')

        lines = [f'✅ {b("下载完成")}', '',
                 f'{t_icon} {b(truncate(name, 52))}', '']

        if not ok:
            lines.append(it('这一轮没有新增曲目（都已在本地）'))
            if new_count:
                lines.append(f'检查到 {code(new_count)} 首新歌，但都没能下载')
            return '\n'.join(lines)

        lines.append(f'✅ 成功 {code(len(ok))} 首' + (f' · ❌ 失败 {code(len(failed))} 首' if failed else ''))
        lines.append(f'💾 本次 {b(fmt_size(total_bytes))} · 专辑共 {code(total)} 首')
        if new_count is not None:
            lines.append(f'🆕 本轮新增 {code(new_count)} 首')

        # 只列前 5 首，剩下的折叠成一句——旧版列 15 首，手机上要翻好几屏
        lines.append('')
        for song in ok[:5]:
            lines.append(f'· {esc(truncate(song.get("song_title") or "未知", 40))}')
        if len(ok) > 5:
            lines.append(it(f'…以及另外 {len(ok) - 5} 首'))

        if failed:
            lines.append('')
            lines.append(b('失败曲目'))
            for song in failed[:3]:
                reason, _ = explain_error(song.get('error'))
                lines.append(
                    f'· {esc(truncate(song.get("song_title") or "未知", 30))} — {esc(reason)}'
                )
            if len(failed) > 3:
                lines.append(it(f'…以及另外 {len(failed) - 3} 首'))
        return '\n'.join(lines)

    @staticmethod
    def error_card(title: str, error: Any = '', advice: str = '') -> str:
        reason, auto_advice = explain_error(error)
        lines = [f'❌ {b(title)}', '', f'{b("原因")}：{esc(reason)}']
        final_advice = advice or auto_advice
        if final_advice:
            lines.append(f'{b("建议")}：{esc(final_advice)}')
        return '\n'.join(lines)
