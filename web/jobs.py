#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Web 侧长任务管理器

为什么需要它：
    歌单同步、批量重试失败曲目、刷新元数据这些都是分钟级甚至小时级操作。
    旧实现直接在 Flask 请求线程里同步跑完，浏览器/反向代理 60~300 秒就
    断连，用户看到"请求失败"，而后端其实还在跑。

用法：
    job = job_manager.submit('playlist_sync', '同步歌单 旅行', fn)
    # fn(job) 内可随时 job.update(progress=3, total=20, message='...')
    # 前端轮询 GET /api/jobs/<job_id>
"""

import logging
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger('music_bot.jobs')


@dataclass
class Job:
    id: str
    kind: str
    title: str
    status: str = 'pending'          # pending | running | done | failed | cancelled
    progress: int = 0
    total: int = 0
    message: str = '排队中'
    created_at: float = field(default_factory=time.time)
    started_at: Optional[float] = None
    finished_at: Optional[float] = None
    error: str = ''
    result: Any = None
    cancelled: bool = False
    _lock: threading.RLock = field(default_factory=threading.RLock, repr=False)
    # 完成信号：旧的同步接口靠它做有界等待，而不是 sleep 轮询
    _done: threading.Event = field(default_factory=threading.Event, repr=False)

    # ---------- 执行侧 API ----------

    def update(self, **kwargs):
        with self._lock:
            for key, value in kwargs.items():
                if hasattr(self, key):
                    setattr(self, key, value)

    def log(self, message: str):
        self.update(message=message[:200])

    def wait(self, timeout: Optional[float] = None) -> bool:
        """等任务结束，返回是否已结束（超时返回 False）"""
        return self._done.wait(timeout)

    def finish(self):
        """标记结束并唤醒所有等待者"""
        self._done.set()

    @property
    def finished(self) -> bool:
        return self.status in ('done', 'failed', 'cancelled')

    # ---------- 展示侧 API ----------

    @property
    def duration(self) -> Optional[float]:
        if not self.started_at:
            return None
        end = self.finished_at or time.time()
        return round(end - self.started_at, 1)

    def to_dict(self) -> Dict[str, Any]:
        with self._lock:
            return {
                'id': self.id,
                'kind': self.kind,
                'title': self.title,
                'status': self.status,
                'progress': self.progress,
                'total': self.total,
                'percent': round(self.progress / self.total * 100, 1) if self.total else None,
                'message': self.message,
                'created_at': self.created_at,
                'duration': self.duration,
                'error': self.error,
            }


class JobManager:
    """轻量任务池，任务状态放内存（重启即丢，够用且不引入依赖）"""

    _MAX_JOBS = 100

    def __init__(self, max_workers: int = 2):
        self._executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix='web-job')
        self._jobs: Dict[str, Job] = {}
        self._lock = threading.RLock()

    def submit(self, kind: str, title: str, fn: Callable[[Job], Any],
               total: int = 0) -> Job:
        job = Job(id=uuid.uuid4().hex[:12], kind=kind, title=title, total=total)
        with self._lock:
            self._jobs[job.id] = job
            self._trim()

        def _runner():
            try:
                if job.cancelled:
                    job.update(status='cancelled', finished_at=time.time(), message='已取消')
                    return
                job.update(status='running', started_at=time.time(), message='执行中')
                try:
                    result = fn(job)
                    if job.cancelled:
                        job.update(status='cancelled', finished_at=time.time(), message='已取消')
                    else:
                        job.update(status='done', finished_at=time.time(),
                                   result=result, message='已完成')
                        if not job.total:
                            job.update(progress=1, total=1)
                        else:
                            job.update(progress=job.total)
                except Exception as e:  # noqa: BLE001
                    logger.exception(f'任务 {job.id} 执行失败: {e}')
                    job.update(status='failed', finished_at=time.time(),
                               error=str(e), message=f'失败: {str(e)[:150]}')
            finally:
                # 无论正常结束还是异常逃逸，都必须唤醒等待者，否则旧同步接口会卡满超时
                job.finish()

        self._executor.submit(_runner)
        logger.info(f'🧵 已提交后台任务: {kind} / {title} ({job.id})')
        return job

    def get(self, job_id: str) -> Optional[Job]:
        with self._lock:
            return self._jobs.get(job_id)

    def list(self, kind: Optional[str] = None, limit: int = 20) -> List[Dict[str, Any]]:
        with self._lock:
            jobs = [j for j in self._jobs.values() if not kind or j.kind == kind]
        jobs.sort(key=lambda j: j.created_at, reverse=True)
        return [j.to_dict() for j in jobs[:limit]]

    def cancel(self, job_id: str) -> bool:
        job = self.get(job_id)
        if not job or job.status in ('done', 'failed', 'cancelled'):
            return False
        job.cancelled = True
        job.update(message='正在取消…')
        return True

    def _trim(self):
        if len(self._jobs) <= self._MAX_JOBS:
            return
        finished = sorted(
            (j for j in self._jobs.values() if j.status in ('done', 'failed', 'cancelled')),
            key=lambda j: j.created_at,
        )
        for j in finished[:len(self._jobs) - self._MAX_JOBS]:
            self._jobs.pop(j.id, None)


job_manager = JobManager(max_workers=2)
