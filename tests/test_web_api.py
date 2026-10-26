# -*- coding: utf-8 -*-
"""页面与接口冒烟

不追求覆盖度，只保证「打开不 500、返回的 JSON 里 success 不被写成 False」
这类低级错误能被 CI 拦住——之前仪表盘统计错、导出 500 都是这么漏出去的。
"""

import pytest

PAGES = ['/', '/playlists', '/history', '/logs', '/settings', '/account', '/tg/setup']

APIS = [
    '/api/dashboard',
    '/api/status',
    '/api/queue/status',
    '/api/playlists',
    '/api/history?limit=5',
    '/api/logs?limit=5',
    '/api/logs/stats',
    '/api/logs/categories',
    '/api/config',
    '/api/config/export',
    '/api/netease/status',
    '/api/jobs',
]


@pytest.mark.parametrize('path', PAGES)
def test_pages_render(client, path):
    resp = client.get(path)
    assert resp.status_code == 200, f'{path} 返回 {resp.status_code}'
    assert len(resp.data) > 200


@pytest.mark.parametrize('path', APIS)
def test_apis_answer_success(client, path):
    resp = client.get(path)
    assert resp.status_code == 200, f'{path} 返回 {resp.status_code}'
    body = resp.get_json()
    assert body is not None, f'{path} 不是 JSON'
    assert body.get('success') is not False, f'{path} 返回 success=False: {body}'


def test_health_needs_no_login(anon_client):
    resp = anon_client.get('/api/health')
    assert resp.status_code == 200


def test_protected_api_returns_401(anon_client):
    resp = anon_client.get('/api/dashboard')
    assert resp.status_code == 401
    assert resp.get_json()['code'] == 401


def test_protected_page_redirects_to_login(anon_client):
    resp = anon_client.get('/history')
    assert resp.status_code == 302
    assert '/login' in resp.headers['Location']


def test_dashboard_numbers_come_from_database(client, webapp):
    cm = webapp.config_manager
    for i in range(7):
        cm.add_download_history(
            platform='netease', content_type='song', content_id=str(i),
            title=f'歌 {i}', artist='歌手', file_path='',
            file_size=3 * 1024 * 1024, quality='无损')

    data = client.get('/api/dashboard').get_json()['data']

    assert data['stats']['total_downloads'] == 7
    assert data['stats']['song_count'] == 7
    assert data['stats']['today_downloads'] == 7


def test_config_export_masks_secrets(client, webapp):
    webapp.config_manager.set_config('telegram_bot_token', '123456:SECRET-TOKEN')
    webapp.config_manager.set_config('netease_cookies', 'MUSIC_U=abcdef')

    body = client.get('/api/config/export').get_json()['data']

    assert 'SECRET-TOKEN' not in body
    assert 'abcdef' not in body


def test_logout_clears_session(client):
    resp = client.post('/api/auth/logout')
    assert resp.status_code == 200
    assert client.get('/api/dashboard').status_code == 401


# ==================== 安全加固 ====================

def test_security_headers_present(client):
    resp = client.get('/')
    assert resp.headers['X-Content-Type-Options'] == 'nosniff'
    assert resp.headers['X-Frame-Options'] == 'DENY'
    assert 'default-src' in resp.headers['Content-Security-Policy']


def test_session_cookie_is_hardened(webapp):
    assert webapp.app.config['SESSION_COOKIE_HTTPONLY'] is True
    assert webapp.app.config['SESSION_COOKIE_SAMESITE'] == 'Lax'


def test_cross_site_write_is_rejected(client):
    resp = client.post(
        '/api/config', json={'telegram_bot_token': 'x'},
        headers={'Sec-Fetch-Site': 'cross-site'})

    assert resp.status_code == 403
    assert '跨站' in resp.get_json()['error']


def test_same_origin_write_is_allowed(client):
    resp = client.post(
        '/api/config', json={'download_max_concurrent': 4},
        headers={'Sec-Fetch-Site': 'same-origin'})

    assert resp.status_code == 200
    assert resp.get_json()['success'] is True


def test_script_without_fetch_headers_is_allowed(client):
    """curl / 脚本不会带 Sec-Fetch-Site，不能被误伤"""
    resp = client.post('/api/config', json={'download_max_concurrent': 5})
    assert resp.status_code == 200
