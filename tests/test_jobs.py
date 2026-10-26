# -*- coding: utf-8 -*-
"""后台任务池

重点是 wait()/finish()：旧的同步接口靠它做有界等待，如果异常路径下忘记
唤醒，接口会白等满超时再返回「已排队」。
"""

import threading
import time

from web.jobs import Job, JobManager


def test_job_runs_and_reports_result():
    manager = JobManager(max_workers=1)
    job = manager.submit('demo', '示例', lambda j: {'answer': 42}, total=3)

    assert job.wait(5) is True
    assert job.status == 'done'
    assert job.result == {'answer': 42}
    assert job.progress == job.total == 3
    assert job.to_dict()['percent'] == 100.0


def test_job_failure_is_captured_not_raised():
    def boom(_job):
        raise RuntimeError('下载器炸了')

    manager = JobManager(max_workers=1)
    job = manager.submit('demo', '会失败的任务', boom)

    assert job.wait(5) is True
    assert job.status == 'failed'
    assert '下载器炸了' in job.error


def test_wait_times_out_without_finishing():
    gate = threading.Event()
    manager = JobManager(max_workers=1)
    job = manager.submit('demo', '慢任务', lambda j: gate.wait(10))

    assert job.wait(0.2) is False
    assert job.status == 'running'

    gate.set()
    assert job.wait(5) is True
    assert job.status == 'done'


def test_cancel_marks_cancelled_and_wakes_waiter():
    gate = threading.Event()
    manager = JobManager(max_workers=1)
    job = manager.submit('demo', '可取消', lambda j: gate.wait(10))

    # 等任务真正开跑再取消
    for _ in range(50):
        if job.status == 'running':
            break
        time.sleep(0.01)

    assert manager.cancel(job.id) is True
    gate.set()
    assert job.wait(5) is True
    assert job.status == 'cancelled'


def test_cancel_unknown_job_returns_false():
    manager = JobManager(max_workers=1)
    assert manager.cancel('no-such-job') is False


def test_list_and_trim_keep_memory_bounded():
    manager = JobManager(max_workers=2)
    for i in range(JobManager._MAX_JOBS + 8):
        manager.submit('demo', f'任务 {i}', lambda j: i).wait(5)

    listed = manager.list(limit=JobManager._MAX_JOBS + 50)
    assert len(listed) <= JobManager._MAX_JOBS


def test_job_wait_returns_true_when_already_done():
    job = Job(id='x', kind='demo', title='已完成')
    job.update(status='done')
    job.finish()
    assert job.wait(0) is True
    assert job.finished is True
