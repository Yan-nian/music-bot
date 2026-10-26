# 音乐下载器模块
import logging
import threading
from typing import Dict

from .base import BaseDownloader
from .netease import NeteaseDownloader
from .apple_music import AppleMusicDownloader
from .metadata import MusicMetadataManager

logger = logging.getLogger(__name__)

__all__ = [
    'BaseDownloader',
    'NeteaseDownloader',
    'AppleMusicDownloader',
    'MusicMetadataManager',
    'get_downloader',
    'get_all_downloaders',
]

# 进程内下载器注册表。
# 旧实现里 Bot 和 Web 各自 new 一个下载器：Web 每个请求都会新建 Session、
# 重新走一次登录态请求，而且 Web 改了 cookie 后 Bot 那份还是旧的。
# 现在统一从这里取，取的时候顺带 reload_config，做到"改完即生效"。
_registry: Dict[str, BaseDownloader] = {}
_registry_lock = threading.RLock()

_BUILDERS = {
    'netease': NeteaseDownloader,
    'apple_music': AppleMusicDownloader,
}


def get_downloader(name: str, config_manager=None) -> BaseDownloader:
    """按名称取下载器（进程内单例，取用时刷新配置）"""
    with _registry_lock:
        downloader = _registry.get(name)
        if downloader is None:
            builder = _BUILDERS.get(name)
            if builder is None:
                raise ValueError(f'未知下载器: {name}')
            downloader = builder(config_manager)
            _registry[name] = downloader
            logger.info(f'🏗️ 已创建下载器实例: {name}')
            return downloader

        if config_manager is not None:
            downloader.config_manager = config_manager
        try:
            downloader.reload_config()
        except Exception as e:  # noqa: BLE001
            logger.debug(f'刷新下载器配置失败 {name}: {e}')
        return downloader


def get_all_downloaders(config_manager=None) -> Dict[str, BaseDownloader]:
    """按配置返回已启用的下载器"""
    result: Dict[str, BaseDownloader] = {}
    for name in _BUILDERS:
        enabled_key = f'{name}_enabled'
        enabled = True
        if config_manager is not None:
            enabled = config_manager.get_config(enabled_key, True)
        if not enabled:
            continue
        try:
            result[name] = get_downloader(name, config_manager)
        except Exception as e:  # noqa: BLE001
            logger.exception(f'❌ 下载器 {name} 初始化失败: {e}')
    return result
