import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx

from config import Settings
from core.agent import solve_runner as runner
from core.agent.coordinator import SolveSession
from core.agent.local_llm import LocalModel
from core.agent.solve_control import SolveControl
from core.models import Challenge, ToolResult
from core.stats import solve_log
from core.tools import ToolRegistry


async def until(predicate):
    async with asyncio.timeout(3):
        while not predicate():
            await asyncio.sleep(.005)


class ControlTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.logpatch = patch.object(solve_log, "_LOG_FILE", Path(self.tmp.name) / "logs.json")
        self.logpatch.start()
        self.settings = Settings(_env_file=None, solver_max_rounds=3, container_auto_recover=False)
        self.settings.max_concurrent_challenges = 2
        self.configpatch = patch.object(runner, "Settings", return_value=self.settings)
        self.configpatch.start()

    async def asyncTearDown(self):
        await runner.shutdown_tasks()
        await asyncio.sleep(0)
        runner._tasks.clear()
        runner._controls.clear()
        self.configpatch.stop()
        self.logpatch.stop()
        self.tmp.cleanup()

    def status(self, id="test"):
        return solve_log.get_solve_log("local", id)["status"]

    async def test_pause_preserves_reply_and_defers_tools(self):
        entered, release = asyncio.Event(), asyncio.Event()
        tools = ToolRegistry()
        calls = []

        async def list_files():
            calls.append("files")
            return ToolResult.ok([])

        tools.register("list_files", list_files)

        class Model:
            count = 0

            async def complete(self, messages, schemas):
                self.count += 1
                if self.count == 1:
                    entered.set()
                    await release.wait()
                    return {"tool_calls": [{"id": "call", "type": "function", "function": {"name": "list_files", "arguments": "{}"}}]}
                return {"content": "Done"}

        control = SolveControl("local", "test")
        model = Model()
        session = SolveSession(Challenge(id="test", name="test"), tools, self.settings, model, control=control)
        task = asyncio.create_task(session.run())
        await entered.wait()
        control.pause()
        self.assertEqual(control.status, "pausing")
        release.set()
        await until(lambda: control.status == "paused")
        self.assertEqual(calls, [])
        self.assertEqual(model.count, 1)
        control.resume()
        result = await task
        self.assertEqual(calls, ["files"])
        self.assertEqual(result["status"], "needs_human")

    async def test_cancel_inflight_model_cleans_tools(self):
        entered = asyncio.Event()

        async def complete(*args):
            entered.set()
            await asyncio.Event().wait()

        tools = ToolRegistry()
        tools.cleanup = AsyncMock()
        adapter = AsyncMock()
        adapter.get_challenge.return_value = Challenge(id="test", name="test")
        with patch.object(runner, "build_local_tools", return_value=tools), patch.object(LocalModel, "complete", complete):
            self.assertTrue(runner.start_solve("local", "test", adapter))
            await entered.wait()
            self.assertEqual(runner.control_solve("local", "test", "cancel"), "cancelling")
            runner.control_solve("local", "test", "cancel")
            await until(lambda: self.status() == "cancelled")
        tools.cleanup.assert_awaited_once()
        self.assertFalse(runner.is_running("local", "test"))

    async def test_immediate_cancel_before_task_started(self):
        adapter = AsyncMock()
        runner.start_solve("local", "test", adapter)
        runner.control_solve("local", "test", "cancel")
        await until(lambda: self.status() == "cancelled")
        adapter.get_challenge.assert_not_awaited()

    async def test_global_capacity_and_paused_cancel(self):
        adapter = AsyncMock()
        adapter.get_challenge.side_effect = lambda _: asyncio.sleep(100)
        runner.start_solve("local", "one", adapter)
        runner.control_solve("local", "one", "pause")
        runner.start_solve("local", "two", adapter)
        runner.control_solve("local", "two", "pause")
        with self.assertRaisesRegex(ValueError, "上限"):
            runner.start_solve("local", "three", adapter)
        await until(lambda: self.status("one") == "paused")
        runner.control_solve("local", "one", "cancel")
        await until(lambda: self.status("one") == "cancelled")

    async def test_api_controls_and_log_delete_guard(self):
        from fastapi import FastAPI
        from web.routes.platform import router
        app = FastAPI()
        app.include_router(router)
        adapter = AsyncMock()
        runner.start_solve("local", "test", adapter)
        runner.control_solve("local", "test", "pause")
        await until(lambda: self.status() == "paused")
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            response = await client.delete("/api/solve-log/local/test")
            self.assertEqual(response.status_code, 409)
            response = await client.post("/api/solve/local/test/cancel")
            self.assertEqual(response.json()["data"]["status"], "cancelling")
            await until(lambda: self.status() == "cancelled")
            self.assertEqual((await client.post("/api/solve/local/test/resume")).status_code, 409)

    async def test_restart_reconciles_stale_session(self):
        solve_log.set_solve_status("local", "test", "paused")
        runner.reconcile_sessions()
        self.assertEqual(self.status(), "interrupted")


class ModelTests(unittest.IsolatedAsyncioTestCase):
    def model(self):
        settings = Settings(_env_file=None)
        settings.llm_base_url = "http://127.0.0.1:11434/v1"
        settings.llm_default_model = "test"
        settings.llm_api_key = "test-key"
        return LocalModel(settings)

    async def test_probe_only_gets_models_and_completion_reuses_client(self):
        seen = []

        def handler(request):
            seen.append((request.method, request.url.path))
            if request.method == "GET":
                return httpx.Response(200, json={"data": [{"id": "test"}]})
            return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

        model = self.model()
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        model._client = client
        self.assertTrue((await model.probe())["available"])
        await model.complete([], [])
        await model.complete([], [])
        self.assertIs(model._http(), client)
        self.assertEqual(seen, [("GET", "/v1/models"), ("POST", "/v1/chat/completions"), ("POST", "/v1/chat/completions")])
        await model.aclose()
        self.assertTrue(client.is_closed)

    async def test_connection_error_includes_endpoint_without_key(self):
        def handler(request):
            raise httpx.ConnectError("failed", request=request)

        model = self.model()
        model._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        try:
            with self.assertRaises(ValueError) as caught:
                await model.complete([], [])
            self.assertIn("127.0.0.1:11434/v1/chat/completions", str(caught.exception))
            self.assertNotIn("test-key", str(caught.exception))
        finally:
            await model.aclose()

    async def test_balance_error_is_actionable(self):
        model = self.model()
        model._client = httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(402)))
        try:
            with self.assertRaisesRegex(ValueError, "余额不足"):
                await model.complete([], [])
        finally:
            await model.aclose()
