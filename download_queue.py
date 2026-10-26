#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
下载队列管理器
支持最大并发数控制、任务排队、重试机制（指数退避 + 抖动）

设计要点（这三条在旧实现里都是错的）：
1. 协程返回 {'success': False} 也算失败——否则上游 try/except 把异常转成
   字典后，队列永远认为任务成功，重试机制形同虚设。
2. create_task 的返回值必须存引用，否则事件循环只持弱引用，长任务可能
   在执行中途被 GC 回收。
3. 并发上限不能靠"换一个新 Semaphore"来调整，旧信号量上的等待者会继续
   用旧额度，实际并发变成新旧上限之和。改为按差值增减令牌。
"""

import asyncio
import logging
import random
import time
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional, Callable, Dict, Any, Awaitable

logger = logging.getLogger('music_bot.queue')

# 这些失败重试也没用，别浪费 3 次退避等待
PERMANENT_ERROR_HINTS = (
    '需要 VIP', '需要VIP', '无法获取歌曲信息', '无法获取下载链接',
    '歌单不存在', '专辑不存在', '不支持', '无权限', '未登录',
)


class TaskStatus(Enum):
    PENDING = "pending"
    RUNNING = "running"
    RETRYING = "retrying"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class DownloadTask:
    """下载任务"""
    task_id: str
    coroutine_func: Callable[[], Awaitable[Dict[str, Any]]]
    progress_callback: Optional[Callable] = None
    max_retries: int = 3
    retry_delay_base: float = 2.0
    retry_delay_max: float = 60.0

    # 状态
    status: TaskStatus = TaskStatus.PENDING
    created_at: float = field(default_factory=time.time)
    started_at: Optional[float] = None
    finished_at: Optional[float] = None
    retry_count: int = 0
    last_error: Optional[str] = None
    result: Optional[Dict[str, Any]] = None
    cancelled: bool = False
    message: str = "排队中"

    # 元数据（用于状态展示）
    platform: str = ""
    content_type: str = ""
    content_id: str = ""
    user_id: Optional[int] = None

    # 内部：用于等待任务完成的 Future
    _future: Optional[asyncio.Future] = field(default=None, init=False)

    def next_retry_delay(self) -> float:
        """计算下次重试延迟（指数退避 + 抖动）"""
        delay = self.retry_delay_base * (2 ** max(0, self.retry_count - 1))
        delay = min(delay, self.retry_delay_max)
        jitter = delay * 0.2 * (2 * random.random() - 1)
        return max(1.0, delay + jitter)

    @property
    def duration(self) -> Optional[float]:
        if self.started_at and self.finished_at:
            return self.finished_at - self.started_at
        if self.started_at:
            return time.time() - self.started_at
        return None

    def to_dict(self) -> Dict[str, Any]:
        return {
            'task_id': self.task_id,
            'platform': self.platform,
            'content_type': self.content_type,
            'content_id': self.content_id,
            'status': self.status.value,
            'message': self.message,
            'retry_count': self.retry_count,
            'max_retries': self.max_retries,
            'created_at': datetime.fromtimestamp(self.created_at).isoformat(timespec='seconds'),
            'duration': round(self.duration, 1) if self.duration else None,
            'last_error': self.last_error,
        }


class DownloadQueue:
    """下载队列管理器（单例）"""

    _MAX_HISTORY = 200

    def __init__(self, max_concurrent: int = 3):
        self._max_concurrent = max(1, int(max_concurrent))
        self._semaphore = asyncio.Semaphore(self._max_concurrent)
        self._queue: asyncio.Queue[DownloadTask] = asyncio.Queue()
        self._tasks: Dict[str, DownloadTask] = {}
        self._running_tasks: "set[asyncio.Task]" = set()
        self._running = False
        self._worker_task: Optional[asyncio.Task] = None
        self._task_counter = 0
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    # ---------- 生命周期 ----------

    @property
    def max_concurrent(self) -> int:
        return self._max_concurrent

    def is_running(self) -> bool:
        return self._running

    async def start(self):
        """启动队列处理器"""
        if self._running:
            return
        self._running = True
        self._loop = asyncio.get_running_loop()
        self._worker_task = asyncio.create_task(self._worker(), name='download-queue-worker')
        logger.info(f"📥 下载队列已启动（最大并发: {self._max_concurrent}）")

    async def stop(self):
        """停止队列处理器"""
        self._running = False
        if self._worker_task:
            self._worker_task.cancel()
            try:
                await self._worker_task
            except BaseException:
                pass
            self._worker_task = None
        for t in list(self._running_tasks):
            t.cancel()
        self._running_tasks.clear()
        logger.info("📪 下载队列已停止")

    # ---------- 并发控制 ----------

    def set_max_concurrent(self, n: int):
        """动态修改最大并发数（必须在事件循环线程内调用）"""
        n = max(1, int(n))
        old = self._max_concurrent
        if n == old:
            return
        self._max_concurrent = n

        if n > old:
            # 放宽：补发令牌，让排队中的任务立刻能跑起来
            for _ in range(n - old):
                self._semaphore.release()
        else:
            # 收紧：吃掉多余令牌。已在跑的任务不受影响，只是后续不再放行
            async def _shrink(count: int):
                for _ in range(count):
                    await self._semaphore.acquire()
            if self._loop and not self._loop.is_closed():
                self._loop.create_task(_shrink(old - n))

        logger.info(f"🔧 最大并发数已调整为: {n}")

    def set_max_concurrent_threadsafe(self, n: int):
        """从其他线程（如 Flask 请求线程）调整并发数"""
        if self._loop and not self._loop.is_closed() and self._loop.is_running():
            self._loop.call_soon_threadsafe(self.set_max_concurrent, n)
        else:
            # --web-only 模式下队列从来没有 start()，也就没有事件循环。
            # 这里如果直接忽略，用户在 Web 上改并发数会「显示成功但根本不生效」，
            # 而且仪表盘永远停在启动时那个值。没人跑 worker，直接改是安全的。
            self.set_max_concurrent(n)
            logger.info("队列未启动（无事件循环），并发数直接写入内存")

    # ---------- 入队 ----------

    def enqueue(self, task: DownloadTask) -> str:
        """将任务加入队列（在事件循环线程内调用）"""
        if task._future is None:
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                loop = self._loop
            if loop is not None:
                task._future = loop.create_future()

        self._tasks[task.task_id] = task
        self._trim_history()
        self._queue.put_nowait(task)
        logger.info(f"📥 任务入队: {task.task_id} ({task.platform}/{task.content_type})")
        return task.task_id

    def enqueue_threadsafe(self, task: DownloadTask) -> Optional[str]:
        """从其他线程入队（Web 接口用）"""
        if not self._loop or self._loop.is_closed():
            logger.warning("队列尚未启动，无法入队")
            return None
        if task._future is None:
            task._future = self._loop.create_future()

        def _put():
            self._tasks[task.task_id] = task
            self._trim_history()
            self._queue.put_nowait(task)

        self._loop.call_soon_threadsafe(_put)
        return task.task_id

    def next_task_id(self, prefix: str = "task") -> str:
        self._task_counter += 1
        return f"{prefix}-{int(time.time())}-{self._task_counter}"

    def _trim_history(self):
        """控制内存里的任务数量，别让它无限增长"""
        if len(self._tasks) <= self._MAX_HISTORY:
            return
        finished = sorted(
            (t for t in self._tasks.values()
             if t.status in (TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED)),
            key=lambda t: t.created_at,
        )
        for t in finished[:len(self._tasks) - self._MAX_HISTORY]:
            self._tasks.pop(t.task_id, None)

    # ---------- 执行 ----------

    async def _worker(self):
        """队列工作协程——不断从队列取任务执行"""
        while self._running:
            try:
                # 不带 timeout：旧实现每秒创建并取消一个 task，
                # 存在"已取到 item 但被取消"导致任务凭空消失的竞态。
                task = await self._queue.get()
            except asyncio.CancelledError:
                break
            except Exception as e:  # pragma: no cover
                logger.error(f"队列 worker 取任务异常: {e}")
                continue

            if task.cancelled:
                task.status = TaskStatus.CANCELLED
                task.finished_at = time.time()
                self._resolve(task, exception=asyncio.CancelledError())
                continue

            # 必须保存引用，否则事件循环只持弱引用，任务可能被 GC
            worker = asyncio.create_task(self._execute_task(task))
            self._running_tasks.add(worker)
            worker.add_done_callback(self._running_tasks.discard)

    @staticmethod
    def _resolve(task: DownloadTask, result=None, exception=None):
        fut = task._future
        if fut is None or fut.done():
            return
        if exception is not None:
            fut.set_exception(exception)
            # 立刻标记已取走，避免 "exception was never retrieved" 噪声
            fut.exception()
        else:
            fut.set_result(result)

    async def _execute_task(self, task: DownloadTask):
        """执行单个任务（带信号量和重试）"""
        if task._future is None:
            task._future = asyncio.get_running_loop().create_future()

        async with self._semaphore:
            task.status = TaskStatus.RUNNING
            task.started_at = time.time()
            task.message = "执行中"

            while True:
                if task.cancelled:
                    task.status = TaskStatus.CANCELLED
                    task.finished_at = time.time()
                    self._resolve(task, exception=asyncio.CancelledError())
                    return
                try:
                    logger.info(
                        f"▶️ 开始执行任务: {task.task_id} "
                        f"(尝试 {task.retry_count + 1}/{task.max_retries + 1})"
                    )
                    result = await task.coroutine_func()

                    # 关键：下游把异常吞成了 {'success': False}，这里必须识别
                    if isinstance(result, dict) and result.get('success') is False:
                        error = str(result.get('error') or '未知错误')
                        if result.get('retryable', True) and self._is_retryable(error):
                            raise RuntimeError(error)
                        task.result = result
                        task.last_error = error
                        task.status = TaskStatus.FAILED
                        task.finished_at = time.time()
                        task.message = f"失败: {error}"
                        logger.warning(f"❌ 任务失败（不可重试）: {task.task_id}, {error}")
                        self._resolve(task, result)
                        return

                    task.result = result
                    task.status = TaskStatus.COMPLETED
                    task.finished_at = time.time()
                    task.message = "已完成"
                    logger.info(f"✅ 任务完成: {task.task_id} ({task.duration:.1f}s)")
                    self._resolve(task, result)
                    return

                except asyncio.CancelledError:
                    task.status = TaskStatus.CANCELLED
                    task.finished_at = time.time()
                    self._resolve(task, exception=asyncio.CancelledError())
                    raise

                except Exception as e:
                    task.retry_count += 1
                    task.last_error = str(e)

                    if task.retry_count > task.max_retries:
                        task.status = TaskStatus.FAILED
                        task.finished_at = time.time()
                        task.message = f"失败: {e}"
                        logger.error(f"❌ 任务失败（已达最大重试次数）: {task.task_id}, 错误: {e}")
                        self._resolve(task, exception=e)
                        return

                    delay = task.next_retry_delay()
                    task.status = TaskStatus.RETRYING
                    task.message = f"{delay:.0f}s 后重试（第 {task.retry_count} 次）"
                    logger.warning(f"⚠️ 任务失败，{delay:.1f}s 后重试: {task.task_id}, 错误: {e}")
                    await asyncio.sleep(delay)

    @staticmethod
    def _is_retryable(error: str) -> bool:
        return not any(hint in error for hint in PERMANENT_ERROR_HINTS)

    # ---------- 查询 / 操作 ----------

    def get_task(self, task_id: str) -> Optional[DownloadTask]:
        return self._tasks.get(task_id)

    def get_all_tasks(self) -> list:
        return list(self._tasks.values())

    def cancel(self, task_id: str) -> bool:
        task = self._tasks.get(task_id)
        if not task or task.status in (TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED):
            return False
        task.cancelled = True
        task.message = "已取消"
        if task.status is TaskStatus.PENDING:
            task.status = TaskStatus.CANCELLED
            task.finished_at = time.time()
            self._resolve(task, exception=asyncio.CancelledError())
        logger.info(f"🛑 已请求取消任务: {task_id}")
        return True

    def cancel_threadsafe(self, task_id: str) -> bool:
        if self._loop and not self._loop.is_closed():
            self._loop.call_soon_threadsafe(self.cancel, task_id)
            return True
        return False

    def get_status(self) -> Dict[str, Any]:
        """获取队列状态"""
        self._trim_history()
        tasks = list(self._tasks.values())
        status_counts = {s.value: 0 for s in TaskStatus}
        for t in tasks:
            status_counts[t.status.value] += 1

        recent = sorted(tasks, key=lambda x: x.created_at, reverse=True)[:30]
        return {
            'running': self._running,
            'max_concurrent': self._max_concurrent,
            'queue_size': self._queue.qsize(),
            'active': status_counts.get('running', 0) + status_counts.get('retrying', 0),
            'pending': status_counts.get('pending', 0),
            'completed': status_counts.get('completed', 0),
            'failed': status_counts.get('failed', 0),
            'cancelled': status_counts.get('cancelled', 0),
            'total': len(tasks),
            'tasks': [t.to_dict() for t in recent],
        }


# 全局队列实例
_queue_instance: Optional[DownloadQueue] = None


def get_download_queue(max_concurrent: int = 3) -> DownloadQueue:
    """获取全局下载队列实例"""
    global _queue_instance
    if _queue_instance is None:
        _queue_instance = DownloadQueue(max_concurrent=max_concurrent)
    return _queue_instance
