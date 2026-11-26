"""Chạy nền các job tester (thread). Tối đa `max_running` job cùng lúc, job cũ tự dọn sau `ttl` giây.

Job = hàm fn(progress) -> kết quả (dict). progress(done, total) để báo tiến độ (tuỳ chọn).
Trạng thái: running -> done | error. Lỗi của fn được bắt lại và lưu thành chuỗi, không làm sập server.
"""

import threading
import time
import uuid
from typing import Callable, Dict, Optional


class JobLimitError(RuntimeError):
    """Đã đủ số job chạy cùng lúc."""


class Job:
    def __init__(self, kind: str, now: float):
        self.id = uuid.uuid4().hex[:12]
        self.kind = kind
        self.status = "running"
        self.done = 0
        self.total = 0  # 0 = chưa biết tiến độ
        self.result = None
        self.error: Optional[str] = None
        self.created_at = now
        self.finished_at: Optional[float] = None

    def to_dict(self, now: float, with_result: bool = True) -> dict:
        end = self.finished_at or now
        out = {"id": self.id, "kind": self.kind, "status": self.status,
               "progress": {"done": self.done, "total": self.total},
               "error": self.error, "elapsed_s": round(end - self.created_at, 2)}
        if with_result and self.status == "done":
            out["result"] = self.result
        return out


class JobManager:
    def __init__(self, max_running: int = 2, ttl: float = 3600.0, clock: Callable[[], float] = time.time):
        self.max_running, self.ttl, self.clock = max_running, ttl, clock
        self.jobs: Dict[str, Job] = {}
        self.lock = threading.Lock()

    def _purge(self) -> None:
        """Xoá job đã xong quá ttl (gọi khi đã giữ lock)."""
        now = self.clock()
        for jid in [j.id for j in self.jobs.values()
                    if j.finished_at is not None and now - j.finished_at > self.ttl]:
            del self.jobs[jid]

    def running(self) -> int:
        return sum(1 for j in self.jobs.values() if j.status == "running")

    def submit(self, kind: str, fn: Callable) -> str:
        with self.lock:
            self._purge()
            if self.running() >= self.max_running:
                raise JobLimitError(f"đang chạy {self.max_running} job, chờ một job xong rồi thử lại")
            job = Job(kind, self.clock())
            self.jobs[job.id] = job
        threading.Thread(target=self._run, args=(job, fn), daemon=True).start()
        return job.id

    def _run(self, job: Job, fn: Callable) -> None:
        def progress(done, total):
            job.done, job.total = int(done), int(total)

        try:
            job.result = fn(progress)
            job.status = "done"
        except Exception as e:  # lỗi tester (holdout, thiếu dữ liệu...) -> báo lại cho người dùng
            job.error = f"{type(e).__name__}: {e}"
            job.status = "error"
        job.finished_at = self.clock()

    def get(self, job_id: str) -> Optional[Job]:
        with self.lock:
            self._purge()
            return self.jobs.get(job_id)
