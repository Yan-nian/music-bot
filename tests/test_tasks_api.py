# -*- coding: utf-8 -*-
"""旧同步接口的任务化改造

改造前后的契约：
- 任务在 wait 时间内跑完 → 200 + 和旧版一致的 {success, data}
- 没跑完 → 202 + job_id（调用方轮询 /api/jobs/<id>）
- 异常 → 500
- 无论哪条路径，历史都要写进去（以前只有 Bot 写）
"""

import time

import pytest


class FakeDownloader:
    def __init__(self, result=None, delay=0.0, raise_exc=None):
        self.result = result or {
            'success': True,
            'new_songs': 2,
            'downloaded_songs': 2,
            'failed_songs': 0,
            'total_songs': 2,
            'songs': [
                {'id': '1', 'name': '稻香', 'success': True, 'size_mb': 9.5, 'filepath': ''},
                {'id': '2', 'name': '晴天', 'success': True, 'size_mb': 8.5, 'filepath': ''},
            ],
        }
        self.delay = delay
        self.raise_exc = raise_exc
        self.song_calls = []

    def _maybe_fail(self):
        if self.raise_exc:
            raise self.raise_exc
        if self.delay:
            time.sleep(self.delay)

    def download_playlist_incremental(self, playlist_id, download_dir):
        self._maybe_fail()
        return dict(self.result)

    def download_song(self, song_id, download_dir, **kwargs):
        self._maybe_fail()
        self.song_calls.append(song_id)
        return {'success': True, 'song_title': f'歌{song_id}', 'song_artist': '歌手',
                'size_mb': 5.0, 'quality': '无损', 'filepath': ''}

    def refresh_metadata(self, download_dir):
        self._maybe_fail()
        return {'success': True, 'fixed': 3}

    def refresh_playlist_metadata(self, playlist_id, download_dir):
        self._maybe_fail()
        return {'success': True, 'fixed': 1}


@pytest.fixture()
def playlist(webapp):
    cm = webapp.config_manager
    cm.add_subscribed_playlist('123456', '华语经典')
    return cm


@pytest.fixture()
def fake(monkeypatch):
    holder = {}

    def _install(**kwargs):
        fake_dl = FakeDownloader(**kwargs)
        monkeypatch.setattr('downloaders.get_downloader', lambda name, cm=None: fake_dl)
        holder['dl'] = fake_dl
        return fake_dl

    return _install


def test_legacy_sync_returns_old_shape_when_fast(client, playlist, fake):
    fake()

    resp = client.post('/api/playlists/123456/sync')

    assert resp.status_code == 200
    body = resp.get_json()
    assert body['success'] is True
    assert body['data']['new_songs'] == 2
    assert 'job_id' in body

    # 历史必须写进去，否则仪表盘统计又是 0
    rows = playlist.get_download_history(limit=5)
    assert rows, '同步完成后没有写下载历史'
    assert rows[0]['content_type'] == 'playlist'


def test_legacy_sync_returns_202_when_slow(client, playlist, fake):
    """超过等待时间不能继续占着请求线程，返回 202 让调用方轮询"""
    fake(delay=1.0)

    resp = client.post('/api/playlists/123456/sync?wait=0')

    assert resp.status_code == 202
    body = resp.get_json()
    assert body['pending'] is True
    job_id = body['job_id']

    # 轮询到完成为止
    for _ in range(100):
        status = client.get(f'/api/jobs/{job_id}').get_json()['data']
        if status['status'] in ('done', 'failed'):
            break
        time.sleep(0.05)

    assert status['status'] == 'done'


def test_legacy_sync_failure_returns_500(client, playlist, fake):
    fake(raise_exc=RuntimeError('cookie 失效'))

    resp = client.post('/api/playlists/123456/sync')

    assert resp.status_code == 500
    body = resp.get_json()
    assert body['success'] is False
    assert 'cookie' in body['error']


def test_legacy_sync_unknown_playlist_404(client, fake):
    fake()
    resp = client.post('/api/playlists/not-exist/sync')
    assert resp.status_code == 404


def test_legacy_retry_failed(client, playlist, fake):
    dl = fake()
    # 失败标记是 UPDATE，先把曲目记录建出来
    playlist.add_playlist_song('123456', '999', '失败歌')
    playlist.mark_song_failed('123456', '999', '网络错误')
    assert playlist.get_failed_songs('123456')

    resp = client.post('/api/playlists/123456/retry-failed')

    assert resp.status_code == 200
    body = resp.get_json()
    assert body['data']['retried'] == 1
    assert body['data']['success'] == 1
    assert dl.song_calls == ['999']
    # 成功后失败标记要清掉，否则列表里还挂着
    assert not playlist.get_failed_songs('123456')


def test_legacy_retry_failed_without_failures_is_noop(client, playlist, fake):
    fake()
    resp = client.post('/api/playlists/123456/retry-failed')
    assert resp.status_code == 200
    assert resp.get_json()['data']['retried'] == 0


def test_legacy_refresh_metadata(client, playlist, fake):
    fake()
    resp = client.post('/api/refresh-metadata')
    assert resp.status_code == 200
    assert resp.get_json()['data']['fixed'] == 3


def test_sync_all_reports_per_playlist(client, playlist, fake):
    fake()
    playlist.add_subscribed_playlist('888888', '粤语金曲')

    resp = client.post('/api/playlists/sync-all')

    assert resp.status_code == 200
    body = resp.get_json()['data']
    assert body['total'] == 2
    assert body['total_new_songs'] == 4


def test_job_endpoints_share_the_same_executor(client, playlist, fake):
    """新老入口必须走同一份执行体，否则又会出现「一边发通知一边不发」"""
    fake()

    resp = client.post('/api/jobs/playlist-sync', json={'playlist_id': '123456'})
    assert resp.status_code == 200
    job_id = resp.get_json()['data']['id']

    for _ in range(100):
        data = client.get(f'/api/jobs/{job_id}').get_json()['data']
        if data['status'] in ('done', 'failed'):
            break
        time.sleep(0.05)

    assert data['status'] == 'done'
    assert playlist.get_download_history(limit=5)
