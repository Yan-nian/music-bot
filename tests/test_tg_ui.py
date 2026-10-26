# -*- coding: utf-8 -*-
"""TG 消息渲染

旧版最大的坑：用 ParseMode.MARKDOWN 拼 *{title}*，歌单名里带 `[` `_` `*`
就会 400，整条消息发不出去，用户只看到「机器人没反应」。
所以这里重点盯「转义」和「长文本截断」。
"""

from web import tg_ui as ui


def test_escape_blocks_html_injection():
    text = ui.esc('<b>稻香</b> & "晴天"')
    assert '<b>' not in text
    assert '&lt;b&gt;' in text
    assert '&amp;' in text


def test_markdown_special_chars_survive_as_html():
    """歌单名里有下划线/星号/方括号时不能再被当成 Markdown 语法"""
    tricky = 'G.E.M._邓紫棋 *精选* [2024]'

    card_obj = ui.ProgressCard(min_interval=0.3)
    card_obj.content_type = 'playlist'
    card_obj.collection = tricky
    card_obj.current_index = 2
    card_obj.total_count = 10
    card_obj.filename = '光年之外.flac'
    card_obj.feed({'percent': 40, 'downloaded': 4_000_000, 'total': 10_000_000})

    text = card_obj.render()
    assert 'G.E.M._邓紫棋' in text
    assert '<b>' in text or '<i>' in text  # 是 HTML 渲染，不是裸 Markdown


def test_progress_card_shows_bytes_speed_eta():
    card = ui.ProgressCard(min_interval=0.3)
    card.feed({
        'percent': 50, 'downloaded': int(2.5 * 1024 ** 2),
        'total': int(5 * 1024 ** 2), 'speed': 1024 ** 2, 'eta': 3,
        'filename': '稻香.flac',
    })
    text = card.render()

    assert '50' in text
    assert 'MB' in text
    assert 'MB/s' in text
    assert '剩' in text


def test_progress_card_queued_explains_position():
    card = ui.ProgressCard(min_interval=0.3)
    card.set_queued(3)
    text = card.render()
    assert '2 个任务' in text


def test_collection_progress_is_synthesized():
    """专辑/歌单总进度要把「第几首」和「这首的字节进度」合起来，
    否则每首歌都从 0 刷到 100，看着像反复重来。"""
    card = ui.ProgressCard(min_interval=0.3)
    card.content_type = 'album'
    card.collection = '叶惠美'
    card.current_index = 5
    card.total_count = 10
    card.feed({'percent': 50, 'downloaded': 50, 'total': 100})

    percent = card._overall_percent()
    assert 40 <= percent <= 45  # (4 + 0.5) / 10 = 45%


def test_result_card_song_has_quality_and_path():
    text = ui.Renderer.result_card(
        {
            'success': True, 'song_title': '稻香', 'song_artist': '周杰伦',
            'size_mb': 9.7, 'quality': '无损', 'bitrate': '1071kbps',
            'duration': '3:42', 'filepath': r'D:\music\稻香.flac',
        },
        'song', 'netease',
    )
    assert '稻香' in text
    assert '无损' in text
    assert '3:42' in text


def test_result_card_failure_translated_to_human():
    text = ui.Renderer.error_card('下载失败', 'HTTP 403 Forbidden')
    assert '403' in text or '拒绝' in text or 'Cookie' in text


def test_clip_limits_length():
    long_text = '歌' * 5000
    assert len(ui.clip(long_text, 100)) <= 110


def test_truncate_adds_ellipsis():
    out = ui.truncate('一二三四五六七八九十', 4)
    assert out.endswith('…')
    assert len(out) <= 5


def test_fmt_helpers():
    assert ui.fmt_size(0) == '—'          # 0 视为「未知」，别显示成 0 B 误导用户
    assert ui.fmt_size(512) == '512 B'
    assert 'KB' in ui.fmt_size(2048)
    assert 'MB' in ui.fmt_size(5 * 1024 ** 2)
    assert ui.fmt_eta(0) == '—'           # 0 表示算不出来，不能显示「剩 0s」
    assert ui.fmt_eta(30) == '30s'
    assert ui.fmt_eta(90) == '1m30s'


def test_explain_error_covers_common_cases():
    reason, advice = ui.explain_error('HTTP 401 Unauthorized')
    assert 'Cookie' in reason or '登录' in reason
    assert advice

    reason, _ = ui.explain_error('HTTP 403 Forbidden')
    assert '拒绝' in reason

    reason, _ = ui.explain_error('Request timed out')
    assert '超时' in reason

    assert ui.explain_error(None)[0]


def test_queue_card_renders_task_rows():
    text = ui.Renderer.queue_card({
        'max_concurrent': 3, 'queue_size': 1, 'active': 1, 'pending': 1,
        'completed': 4, 'failed': 0, 'cancelled': 0, 'total': 6,
        'tasks': [
            {'platform': 'netease', 'content_type': 'song', 'content_id': '1',
             'status': 'running', 'retry_count': 0, 'max_retries': 3},
            {'platform': 'netease', 'content_type': 'playlist', 'content_id': '2',
             'status': 'pending', 'retry_count': 1, 'max_retries': 3},
        ],
    })
    assert '并发' in text
    # 平台名要本地化，别把 netease 这种内部标识丢给用户看
    assert '网易云音乐' in text
    assert 'netease' not in text
