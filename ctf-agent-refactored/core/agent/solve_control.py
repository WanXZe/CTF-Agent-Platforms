"""Cooperative pause at model/tool boundaries; cancellation belongs to the task."""
import asyncio

from core.stats import solve_log


class SolveControl:
    def __init__(self, platform_id, challenge_id):
        self.platform_id = platform_id
        self.challenge_id = challenge_id
        self.gate = asyncio.Event()
        self.gate.set()
        self.status = "running"
        self.cleanup_failed = False

    def _status(self, status, message):
        self.status = status
        solve_log.set_solve_status(self.platform_id, self.challenge_id, status)
        solve_log.append_log(self.platform_id, self.challenge_id, "system", message)

    def pause(self):
        if self.status == "running":
            self.gate.clear()
            self._status("pausing", "已请求暂停；当前调用结束后暂停，不再发起下一次调用。后台长任务继续运行，需终止请取消解题。")

    def resume(self):
        if self.status in ("pausing", "paused"):
            self._status("running", "继续解题，保留原有上下文。")
            self.gate.set()

    async def checkpoint(self):
        if self.status == "cancelling":
            raise asyncio.CancelledError()
        if not self.gate.is_set():
            self._status("paused", "解题已暂停，等待继续或取消。")
            await self.gate.wait()
