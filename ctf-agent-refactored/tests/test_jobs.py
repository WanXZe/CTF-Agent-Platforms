"""Long command lifecycle tests; Docker tests use a fake CLI, never a container."""
import asyncio
import json
import os
import shlex
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from core.agent.coordinator import SolveSession
from core.models import ToolResult
from core.tools.local_tools import MAX_OUTPUT_CHARS, Workspace


class FakeDockerWorkspace(Workspace):
    def __init__(self, root):
        super().__init__(root, SimpleNamespace(sandbox_mode="docker", sandbox_image="test-only"))
        self.commands = []
        self.executing = asyncio.Event()
        self.block_exec = True
        self.exec_code = 0

    async def _run(self, args, timeout=60, cwd=None, output_paths=None):
        self.commands.append(args)
        if args[1] == "ps":
            return 0, "", ""
        if args[1] == "run":
            return 0, "fake-container-id", ""
        if args[1] == "exec":
            self.executing.set()
            if self.block_exec:
                await asyncio.Future()
            return self.exec_code, "result", ""
        return 0, "", ""


class JobTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.workspace = Workspace(self.root, SimpleNamespace(sandbox_mode="host"))

    async def asyncTearDown(self):
        await self.workspace.close()
        self.temporary.cleanup()

    async def test_cancel_before_first_turn_persists_result(self):
        # No event-loop yield between start and cancel: execute() never begins.
        started = await self.workspace.start_job("unused")
        cancelled = await self.workspace.cancel_job(started.data["job_id"])
        self.assertEqual(cancelled.data["status"], "cancelled")
        saved = json.loads((self.root / started.data["result_path"]).read_text())
        self.assertEqual(saved["status"], "cancelled")
        self.assertEqual(saved["job_id"], started.data["job_id"])

    async def test_poll_timeout_does_not_cancel_job_and_start_is_serialized(self):
        entered = asyncio.Event()
        release = asyncio.Event()

        async def delayed_backend():
            await asyncio.sleep(0)
            return "host"

        async def execute(command, timeout, output_paths=None):
            entered.set()
            await release.wait()
            return ToolResult.ok({"stdout": "done"})

        self.workspace.backend = delayed_backend
        self.workspace._host_run = execute
        first, second = await asyncio.gather(self.workspace.start_job("one"), self.workspace.start_job("two"))
        self.assertTrue(first.success)
        self.assertFalse(second.success)
        await entered.wait()
        polled = await self.workspace.poll_job(first.data["job_id"], 1)
        self.assertEqual(polled.data["status"], "running")
        release.set()
        polled = await self.workspace.poll_job(first.data["job_id"], 1)
        self.assertEqual(polled.data["status"], "completed")

    async def test_close_cancels_jobs_and_rejects_new_work(self):
        started = await self.workspace.start_job("unused")
        await self.workspace.close()
        self.assertEqual((await self.workspace.poll_job(started.data["job_id"], 0)).data["status"], "cancelled")
        self.assertFalse((await self.workspace.start_job("unused")).success)

    async def test_docker_cancel_removes_sandbox_and_next_command_recreates_it(self):
        docker = FakeDockerWorkspace(self.root)
        try:
            (self.root / "keep.txt").write_text("workspace survives", encoding="utf-8")
            started = await docker.start_job("long calculation")
            await docker.executing.wait()
            self.assertTrue(docker.container)
            cancelled = await docker.cancel_job(started.data["job_id"])
            self.assertEqual(cancelled.data["status"], "cancelled")
            self.assertEqual(docker.commands[-1][:3], ["docker", "rm", "-f"])
            self.assertEqual(docker.container, "")
            docker.block_exec = False
            self.assertTrue((await docker.run("next command")).success)
            self.assertEqual(sum(args[1] == "run" for args in docker.commands), 2)
            self.assertEqual((self.root / "keep.txt").read_text(), "workspace survives")
        finally:
            await docker.close()

    async def test_docker_timeout_resets_sandbox(self):
        docker = FakeDockerWorkspace(self.root)
        docker.block_exec = False
        docker.exec_code = 124
        try:
            result = await docker.run("times out")
            self.assertFalse(result.success)
            self.assertEqual(docker.container, "")
            self.assertEqual(docker.commands[-1][:3], ["docker", "rm", "-f"])
        finally:
            await docker.close()

    async def test_cancel_during_docker_creation_removes_partial_sandbox(self):
        docker = FakeDockerWorkspace(self.root)
        original_run = docker._run
        creating = asyncio.Event()

        async def blocked_run(args, **kwargs):
            if args[1] == "run":
                docker.commands.append(args)
                creating.set()
                await asyncio.Future()
            return await original_run(args, **kwargs)

        docker._run = blocked_run
        try:
            started = await docker.start_job("long calculation")
            await creating.wait()
            cancelled = await docker.cancel_job(started.data["job_id"])
            self.assertEqual(cancelled.data["status"], "cancelled")
            self.assertEqual(docker.commands[-1][:3], ["docker", "rm", "-f"])
            self.assertEqual(docker.container, "")
        finally:
            await docker.close()

    @unittest.skipUnless(os.name == "posix", "VM uses POSIX shell/process groups")
    async def test_full_output_saved_and_poll_output_bounded(self):
        script = "import sys; print('x' * 20000); print('error-stream', file=sys.stderr)"
        started = await self.workspace.start_job(f"{shlex.quote(sys.executable)} -c {shlex.quote(script)}")
        polled = await self.workspace.poll_job(started.data["job_id"], 5)
        self.assertEqual(polled.data["status"], "completed")
        self.assertEqual((self.root / polled.data["stdout_path"]).read_text(), "x" * 20000 + "\n")
        self.assertEqual((self.root / polled.data["stderr_path"]).read_text(), "error-stream\n")
        self.assertLess(len(polled.data["result"]["data"]["stdout"]), MAX_OUTPUT_CHARS + 100)
        saved = json.loads((self.root / polled.data["result_path"]).read_text())
        self.assertEqual(saved["status"], "completed")
        self.assertEqual(saved["result"], polled.data["result"])

    @unittest.skipUnless(sys.platform == "linux", "Checks Linux descendant process state")
    async def test_cancel_and_timeout_stop_descendants_and_keep_partial_output(self):
        child_code = "import os,time; print(os.getpid(), flush=True); time.sleep(30)"
        script = f"import subprocess,sys,time; p=subprocess.Popen([sys.executable,'-c',{child_code!r}]); time.sleep(30)"
        command = f"{shlex.quote(sys.executable)} -c {shlex.quote(script)}"
        for cancel in (True, False):
            with self.subTest(cancel=cancel):
                started = await self.workspace.start_job(command, timeout=1)
                output = self.root / started.data["stdout_path"]
                for _ in range(100):
                    if output.read_text().strip():
                        break
                    await asyncio.sleep(0.01)
                self.assertTrue(output.read_text().strip(), "child did not start")
                pid = int(output.read_text().strip())
                result = (await self.workspace.cancel_job(started.data["job_id"]) if cancel
                          else await self.workspace.poll_job(started.data["job_id"], 5))
                self.assertEqual(result.data["status"], "cancelled" if cancel else "failed")
                # A killed child may briefly remain a zombie until PID 1 reaps it.
                proc_stat = Path(f"/proc/{pid}/stat")
                for _ in range(100):
                    if not proc_stat.exists() or proc_stat.read_text().split(") ", 1)[1][0] == "Z":
                        break
                    await asyncio.sleep(0.01)
                self.assertTrue(not proc_stat.exists() or proc_stat.read_text().split(") ", 1)[1][0] == "Z")
                self.assertIn(str(pid), output.read_text())


class ToolMessageTests(unittest.TestCase):
    def test_escaped_large_output_stays_valid_bounded_and_keeps_job_paths(self):
        payload = {"success": True, "data": {"job_id": "abc123", "status": "completed",
                   "result_path": ".agent-jobs/abc123.json", "stdout_path": ".agent-jobs/abc123.stdout.log",
                   "result": {"data": {"stdout": ('中文\\\"\n' * 3000)}}}}
        for limit in (2, 80, 1200, 4000):
            with self.subTest(limit=limit):
                message = SolveSession._tool_message(payload, limit)
                self.assertLessEqual(len(message), limit)
                parsed = json.loads(message)
                if limit >= 1200:
                    self.assertEqual(parsed["data"]["job_id"], "abc123")
                    self.assertEqual(parsed["data"]["stdout_path"], ".agent-jobs/abc123.stdout.log")
                    self.assertTrue(parsed["truncated"])


if __name__ == "__main__":
    unittest.main()
