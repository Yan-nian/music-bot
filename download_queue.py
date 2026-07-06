#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
下载队列管理器
支持最大并发数控制、任务排队、重试机制（指数退避）
"""

import asyncio
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional, Callable, Dict, Any, Awaitable

logger = logging.getLogger('music_bot.queue')


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
    coroutine_func: Callable[[], Awaitable[Dict[str, Any]]]  # 返回 awaitable 的下载函数
    progress_callback: Optional[Callable] = None
    max_retries: int = 3
    retry_delay_base: float = 2.0  # 指数退避基数（秒）
    retry_delay_max: float = 60.0  # 最大重试延迟
    
    # 状态
    status: TaskStatus = TaskStatus.PENDING
    created_at: float = field(default_factory=time.time)
    started_at: Optional[float] = None
    finished_at: Optional[float] = None
    retry_count: int = 0
    last_error: Optional[str] = None
    result: Optional[Dict[str, Any]] = None
    
    # 元数据（用于状态展示）
    platform: str = ""
    content_type: str = ""
    content_id: str = ""
    user_id: Optional[int] = None
    
    # 内部：用于等待任务完成的 Future
    _future: Optional[asyncio.Future] = field(default=None, init=False)
    
    def next_retry_delay(self) -> float:
        """计算下次重试延迟（指数退避 + 抖动）"""
        delay = self.retry_delay_base * (2 ** (self.retry_count - 1))
        delay = min(delay, self.retry_delay_max)
        # 加 ±20% 抖动，避免多个任务同时重试
        import random
        jitter = delay * 0.2 * (2 * random.random() - 1)
        return max(1.0, delay + jitter)
    
    @property
    def duration(self) -> Optional[float]:
        if self.started_at and self.finished_at:
            return self.finished_at - self.started_at
        return None


class DownloadQueue:
    """下载队列管理器（单例）"""
    
    def __init__(self, max_concurrent: int = 3):
        """
        Args:
            max_concurrent: 最大并发下载数
        """
        self.max_concurrent = max_concurrent
        self._semaphore = asyncio.Semaphore(max_concurrent)
        self._queue: asyncio.Queue[DownloadTask] = asyncio.Queue()
        self._tasks: Dict[str, DownloadTask] = {}
        self._running = False
        self._worker_task: Optional[asyncio.Task] = None
        self._task_counter = 0
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        
    async def start(self):
        """启动队列处理器"""
        if self._running:
            return
        self._running = True
        self._loop = asyncio.get_running_loop()
        self._worker_task = asyncio.create_task(self._worker())
        logger.info(f"📥 下载队列已启动（最大并发: {self.max_concurrent}）")
    
    async def stop(self):
        """停止队列处理器"""
        self._running = False
        if self._worker_task:
            self._worker_task.cancel()
            try:
                await self._worker_task
            except asyncio.CancelledError:
                pass
        logger.info("📪 下载队列已停止")
    
    def set_max_concurrent(self, n: int):
        """动态修改最大并发数"""
        if n < 1:
            n = 1
        self.max_concurrent = n
        # 重新创建 semaphore（不能直接修改其内部计数器）
        self._semaphore = asyncio.Semaphore(n)
        logger.info(f"🔧 最大并发数已调整为: {n}")
    
    def enqueue(self, task: DownloadTask) -> str:
        """将任务加入队列。
        
        Returns:
            task_id
        """
        # 在主事件循环中创建 Future（供调用方 await）
        if task._future is None and self._loop is not None:
            task._future = self._loop.create_future()
        self._tasks[task.task_id] = task
        self._queue.put_nowait(task)
        logger.info(f"📥 任务入队: {task.task_id} ({task.platform}/{task.content_type})")
        return task.task_id
    
    async def _worker(self):
        """队列工作协程——不断从队列取任务执行"""
        while self._running:
            try:
                # 等待队列中有任务
                try:
                    task = await asyncio.wait_for(self._queue.get(), timeout=1.0)
                except asyncio.TimeoutError:
                    continue
                
                # 用信号量控制并发
                asyncio.create_task(self._execute_task(task))
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"队列 worker 异常: {e}")
    
    async def _execute_task(self, task: DownloadTask):
        """执行单个任务（带信号量和重试）"""
        # 创建 Future 供外部等待
        if task._future is None:
            task._future = asyncio.get_running_loop().create_future()
        
        async with self._semaphore:
            task.status = TaskStatus.RUNNING
            task.started_at = time.time()
            
            while True:
                try:
                    logger.info(f"▶️ 开始执行任务: {task.task_id} (尝试 {task.retry_count + 1}/{task.max_retries + 1})")
                    result = await task.coroutine_func()
                    
                    # 成功
                    task.result = result
                    task.status = TaskStatus.COMPLETED
                    task.finished_at = time.time()
                    logger.info(f"✅ 任务完成: {task.task_id} ({task.duration:.1f}s)")
                    
                    # 设置 Future 结果
                    if not task._future.done():
                        task._future.set_result(result)
                    break
                    
                except Exception as e:
                    task.retry_count += 1
                    task.last_error = str(e)
                    
                    # 判断是否可重试
                    if task.retry_count > task.max_retries:
                        task.status = TaskStatus.FAILED
                        task.finished_at = time.time()
                        logger.error(f"❌ 任务失败（已达最大重试次数）: {task.task_id}, 错误: {e}")
                        
                        # 设置 Future 异常
                        if not task._future.done():
                            task._future.set_exception(e)
                        break
                    
                    # 计算重试延迟
                    delay = task.next_retry_delay()
                    task.status = TaskStatus.RETRYING
                    logger.warning(f"⚠️ 任务失败，{delay:.1f}s 后重试: {task.task_id}, 错误: {e}")
                    
                    await asyncio.sleep(delay)
        
        # 如果 Future 还没设置（比如成功路径已经设置了），确保它不会挂起
        if task._future and not task._future.done():
            task._future.cancel()
    
    def get_task(self, task_id: str) -> Optional[DownloadTask]:
        return self._tasks.get(task_id)
    
    def get_all_tasks(self) -> list:
        return list(self._tasks.values())
    
    def get_status(self) -> Dict[str, Any]:
        """获取队列状态"""
        tasks = list(self._tasks.values())
        now = time.time()
        
        # 只保留最近 1 小时的任务在状态里
        recent_tasks = [t for t in tasks if now - t.created_at < 3600]
        # 清理过期任务
        for t in [t for t in tasks if now - t.created_at >= 3600 and t.status in (TaskStatus.COMPLETED, TaskStatus.FAILED)]:
            del self._tasks[t.task_id]
        
        status_counts = {s.value: 0 for s in TaskStatus}
        for t in recent_tasks:
            status_counts[t.status.value] += 1
        
        return {
            'max_concurrent': self.max_concurrent,
            'queue_size': self._queue.qsize(),
            'active': status_counts.get('running', 0) + status_counts.get('retrying', 0),
            'pending': status_counts.get('pending', 0),
            'completed': status_counts.get('completed', 0),
            'failed': status_counts.get('failed', 0),
            'tasks': [
                {
                    'task_id': t.task_id,
                    'platform': t.platform,
                    'content_type': t.content_type,
                    'content_id': t.content_id,
                    'status': t.status.value,
                    'retry_count': t.retry_count,
                    'max_retries': t.max_retries,
                    'created_at': datetime.fromtimestamp(t.created_at).isoformat(),
                    'duration': t.duration,
                    'last_error': t.last_error,
                }
                for t in sorted(recent_tasks, key=lambda x: x.created_at, reverse=True)[:20]
            ]
        }


# 全局队列实例
_queue_instance: Optional[DownloadQueue] = None


def get_download_queue(max_concurrent: int = 3) -> DownloadQueue:
    """获取全局下载队列实例"""
    global _queue_instance
    if _queue_instance is None:
        _queue_instance = DownloadQueue(max_concurrent=max_concurrent)
    return _queue_instance
