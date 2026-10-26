# -*- coding: utf-8 -*-
"""测试公共夹具

原则：不依赖网络、不依赖 Telegram、不写仓库目录（全部落 tmp_path）。
"""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@pytest.fixture()
def cfg(tmp_path):
    """独立的 ConfigManager（直接实例化，绕开进程内单例）"""
    from config_manager import ConfigManager

    return ConfigManager(str(tmp_path / 'unit.db'))


@pytest.fixture()
def registry_clean():
    """清掉下载器注册表，避免用例之间共享单例"""

    import downloaders

    before = dict(downloaders._registry)
    downloaders._registry.clear()
    yield
    downloaders._registry.clear()
    downloaders._registry.update(before)


@pytest.fixture()
def webapp(tmp_path, registry_clean):
    """初始化好的 Flask 应用（每个用例一个独立数据库）"""
    import importlib

    module = importlib.import_module('web.app')
    module.app.config.update(TESTING=True)
    module.init_app(str(tmp_path / 'web.db'))
    return module


@pytest.fixture()
def client(webapp):
    """已登录的测试客户端"""
    with webapp.app.test_client() as test_client:
        with test_client.session_transaction() as sess:
            sess['logged_in'] = True
            sess['username'] = 'admin'
        yield test_client


@pytest.fixture()
def anon_client(webapp):
    """未登录的测试客户端"""
    with webapp.app.test_client() as test_client:
        yield test_client
