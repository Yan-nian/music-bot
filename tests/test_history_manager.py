# -*- coding: utf-8 -*-
"""下载历史写入与统计

踩过的坑：
- 「累计下载」拿 limit=200 的列表长度算，超过 200 就永远停在 200；
- 「今日新增」拿本地日期字符串去比 UTC 时间，东八区 0-8 点全算成昨天；
- 专辑/歌单往 file_size 里写的是曲目数，不是字节数。
"""

import datetime as dt



def _insert_raw(cm, payload: dict):
    """直接写库，用来构造特定时间戳的记录"""
    with cm._connect() as conn:
        conn.execute(
            """INSERT INTO download_history
               (platform, content_type, content_id, title, artist,
                file_path, file_size, quality, created_at)
               VALUES (:platform, :content_type, :content_id, :title, :artist,
                       :file_path, :file_size, :quality, :created_at)""",
            payload,
        )
        conn.commit()


def test_stats_are_not_capped_at_limit(cfg):
    for i in range(260):
        cfg.add_download_history(
            platform='netease', content_type='song', content_id=str(i),
            title=f'歌 {i}', artist='歌手', file_path='',
            file_size=1024, quality='无损')

    stats = cfg.get_history_stats()
    assert stats['total'] == 260
    assert stats['song_count'] == 260
    assert stats['total_bytes'] == 260 * 1024


def test_today_uses_local_midnight(cfg):
    """本地零点是 UTC 的 16:00（东八区），跨点前后各放一条记录验证边界"""
    now = dt.datetime.now()
    local_midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    fmt = '%Y-%m-%d %H:%M:%S'

    def _utc(ts: dt.datetime) -> str:
        return ts.astimezone(dt.timezone.utc).strftime(fmt)

    base = dict(platform='netease', content_type='song', artist='歌手',
                file_path='', file_size=1024, quality='无损')

    _insert_raw(cfg, dict(base, content_id='before', title='昨天最后一秒',
                          created_at=_utc(local_midnight - dt.timedelta(seconds=30))))
    _insert_raw(cfg, dict(base, content_id='after', title='今天第一分钟',
                          created_at=_utc(local_midnight + dt.timedelta(seconds=10))))

    stats = cfg.get_history_stats()
    assert stats['total'] == 2
    assert stats['today'] == 1, '本地零点换算错了，跨点的记录被算到别的日子'


def test_album_history_stores_bytes_not_song_count(cfg):
    cfg.record_download_result(
        'netease', 'album', '28558',
        {
            'success': True, 'album_name': '叶惠美', 'artist': '周杰伦',
            'total_songs': 2,
            'songs': [
                {'name': '以父之名', 'success': True, 'size_mb': 10.0},
                {'name': '晴天', 'success': True, 'size_mb': 8.0},
            ],
        },
        '/downloads/netease',
    )

    row = cfg.get_download_history(limit=1)[0]
    assert row['content_type'] == 'album'
    assert row['file_size'] == int(18 * 1024 * 1024)
    assert row['quality'] == '2/2'


def test_failed_download_is_not_recorded(cfg):
    wrote = cfg.record_download_result(
        'netease', 'song', '1', {'success': False, 'error': '版权限制'})
    assert wrote is False
    assert cfg.get_history_stats()['total'] == 0


def test_playlist_without_success_songs_is_not_recorded(cfg):
    wrote = cfg.record_download_result(
        'netease', 'playlist', '9',
        {'success': True, 'playlist_title': '空歌单', 'total_songs': 3,
         'songs': [{'name': 'a', 'success': False}]},
        '/downloads')
    assert wrote is False


def test_song_size_falls_back_to_file(tmp_path, cfg):
    audio = tmp_path / 'song.flac'
    audio.write_bytes(b'0' * 2048)

    cfg.record_download_result(
        'netease', 'song', '1',
        {'success': True, 'song_title': '稻香', 'size_mb': 0,
         'filepath': str(audio)},
        str(tmp_path),
    )

    row = cfg.get_download_history(limit=1)[0]
    assert row['file_size'] == 2048
