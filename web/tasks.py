#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Web 长任务的执行体

为什么单独一层：
    旧版把「拉歌单 + 下载 + 写历史 + 发 TG 通知」整段写在 Flask 视图里，
    浏览器/反向代理 60 秒断连就报错，而后端其实还在跑。现在这类操作统一
    交给 web/jobs.py 的线程池，但执行体本身不能再写两份——之前就是这样：
    `/api/playlists/<id>/sync` 会发 TG 通知，而 `/api/jobs/playlist-sync`
    一条都不发，行为随入口不同而不同。

    所以这里只做「构建任务」：把参数收敛成一个 TaskSpec（标题 + 总数 + 执行
    函数），`/api/jobs/*`（异步）和 `/api/playlists/*`（兼容旧脚本）都从这里
    拿 spec 去投递，执行逻辑只有一份。
"""

import logging
from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional

logger = logging.getLogger('music_bot.tasks')


@dataclass
class TaskSpec:
    """一次可提交到任务池的工作"""

    kind: str
    title: str
    total: int
    run: Callable[[Any], Any]     # run(job) -> 结果字典
    # 旧接口需要的「结果整形」：把 run 的返回值转成老字段，保持脚本兼容
    legacy_shape: Optional[Callable[[Any], Dict[str, Any]]] = None


def _download_dir(cm, platform: str) -> str:
    """取某个平台的下载目录。不同平台配置键不同，统一在这里兜底。"""
    key = 'netease_download_path' if platform == 'netease' else f'{platform}_download_path'
    default = '/downloads/netease' if platform == 'netease' else f'/downloads/{platform}'
    return cm.get_config(key, default) or default


def _notify_single(cm, result: Dict[str, Any], playlist_name: str):
    """同步单个歌单后的 TG 通知。通知失败不能影响任务本身。"""
    try:
        from .tg_notifier import notify_playlist_sync_result
        notify_playlist_sync_result(cm, result, playlist_name)
    except Exception as e:  # noqa: BLE001
        logger.debug(f'发送歌单同步通知失败: {e}')


def _notify_all(cm, total: int, results):
    try:
        from .tg_notifier import notify_all_playlists_sync_result
        notify_all_playlists_sync_result(cm, total, results)
    except Exception as e:  # noqa: BLE001
        logger.debug(f'发送全量同步通知失败: {e}')


def build_playlist_sync(cm, playlist_id: str, platform: str = 'netease') -> TaskSpec:
    """同步单个歌单（增量下载 + 写历史 + 通知）"""
    from downloaders import get_downloader

    playlist = cm.get_subscribed_playlist(playlist_id, platform) or {}
    name = playlist.get('playlist_name') or f'歌单 {playlist_id}'

    def run(job):
        downloader = get_downloader(platform, cm)
        download_dir = _download_dir(cm, platform)
        job.update(progress=1, total=4, message='拉取歌单信息')
        result = downloader.download_playlist_incremental(playlist_id, download_dir)
        job.update(progress=3, message='写入记录')
        # Web 触发的同步必须自己写历史，否则仪表盘的累计/今日/容量永远是 0
        cm.record_download_result(platform, 'playlist', playlist_id, result, download_dir)
        job.update(progress=4, message='发送通知')
        _notify_single(cm, result, name)
        return result

    return TaskSpec(
        kind='playlist_sync',
        title=f'同步歌单 · {name}',
        total=4,
        run=run,
        legacy_shape=lambda r: {'success': bool(r.get('success')), 'data': r},
    )


def build_playlist_sync_all(cm) -> TaskSpec:
    """同步所有启用的歌单"""
    from downloaders import get_downloader

    playlists = cm.get_subscribed_playlists(enabled_only=True)

    def run(job):
        downloader = get_downloader('netease', cm)
        download_dir = _download_dir(cm, 'netease')
        job.update(total=len(playlists), progress=0)

        results = []
        totals = {'new': 0, 'downloaded': 0, 'failed': 0}
        for index, item in enumerate(playlists, 1):
            if job.cancelled:
                break
            pid = item['playlist_id']
            pname = item.get('playlist_name') or pid
            job.update(progress=index - 1, message=f'({index}/{len(playlists)}) {pname}')
            try:
                result = downloader.download_playlist_incremental(pid, download_dir)
                cm.record_download_result('netease', 'playlist', pid, result, download_dir)
                failed = result.get('failed_songs', 0)
                results.append({
                    'playlist_id': pid,
                    'playlist_name': pname,
                    'success': result.get('success', False),
                    'new_songs': result.get('new_songs', 0),
                    'downloaded': result.get('downloaded_songs', 0),
                    'failed': failed,
                })
                totals['new'] += result.get('new_songs', 0) or 0
                totals['downloaded'] += result.get('downloaded_songs', 0) or 0
                totals['failed'] += failed or 0
            except Exception as e:  # noqa: BLE001
                logger.warning(f'同步歌单 {pname} 失败: {e}')
                results.append({
                    'playlist_id': pid, 'playlist_name': pname,
                    'success': False, 'error': str(e),
                })
            job.update(progress=index)

        _notify_all(cm, len(playlists), results)
        return {
            'total': len(playlists),
            'synced': sum(1 for r in results if r.get('success')),
            'results': results,
            'total_new_songs': totals['new'],
            'total_downloaded': totals['downloaded'],
            'total_failed': totals['failed'],
        }

    return TaskSpec(
        kind='playlist_sync_all',
        title=f'同步全部歌单（{len(playlists)} 个）',
        total=max(len(playlists), 1),
        run=run,
        legacy_shape=lambda r: {'success': True, 'data': r},
    )


def build_retry_failed(cm, playlist_id: str) -> TaskSpec:
    """重试某个歌单里下载失败的曲目"""
    from downloaders import get_downloader

    failed = cm.get_failed_songs(playlist_id) or []

    def run(job):
        downloader = get_downloader('netease', cm)
        download_dir = _download_dir(cm, 'netease')
        job.update(total=len(failed), progress=0)

        ok = 0
        for index, song in enumerate(failed, 1):
            if job.cancelled:
                break
            job.update(progress=index - 1, message=f"({index}/{len(failed)}) {song.get('song_name')}")
            # 先清失败态，失败再重新标记，避免重试成功却还挂着旧错误
            cm.clear_song_fail_status(playlist_id, song['song_id'])
            try:
                result = downloader.download_song(song['song_id'], download_dir)
                cm.record_download_result(
                    'netease', 'song', str(song['song_id']), result, download_dir)
                if result.get('success'):
                    cm.mark_song_downloaded(playlist_id, song['song_id'])
                    ok += 1
                else:
                    cm.mark_song_failed(
                        playlist_id, song['song_id'], result.get('error', '重试失败'))
            except Exception as e:  # noqa: BLE001
                logger.warning(f'重试失败曲目出错: {e}')
                cm.mark_song_failed(playlist_id, song['song_id'], str(e)[:200])
            job.update(progress=index)

        return {
            'retried': len(failed),
            'success': ok,
            'still_failed': len(failed) - ok,
        }

    return TaskSpec(
        kind='retry_failed',
        title=f'重试失败曲目（{len(failed)} 首）',
        total=max(len(failed), 1),
        run=run,
        legacy_shape=lambda r: {'success': True, 'data': r},
    )


def build_refresh_metadata(cm, playlist_id: str = '') -> TaskSpec:
    """刷新已下载文件的元数据（全库或单个歌单）"""
    from downloaders import get_downloader

    def run(job):
        downloader = get_downloader('netease', cm)
        download_dir = _download_dir(cm, 'netease')
        job.update(total=1, progress=0, message='扫描并修复元数据')
        if playlist_id:
            result = downloader.refresh_playlist_metadata(playlist_id, download_dir)
        else:
            result = downloader.refresh_metadata(download_dir)
        return result

    return TaskSpec(
        kind='refresh_metadata',
        title='刷新元数据',
        total=1,
        run=run,
        legacy_shape=lambda r: {'success': bool(r.get('success')), 'data': r},
    )
