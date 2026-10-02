"""Integration check: cancel a harmless sleeping command in a real sandbox."""
import asyncio
import json
import sys
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from config import Settings
from core.agent import solve_runner as runner
from core.agent.local_llm import LocalModel
from core.models import Challenge
from core.stats import solve_log
from core.tools import ToolRegistry
from core.tools.local_tools import Workspace


async def main():
    with tempfile.TemporaryDirectory(prefix="ctf-cancel-check-") as tmp:
        root = Path(tmp)
        settings = Settings(_env_file=None, sandbox_mode="docker")
        workspace = Workspace(root, settings)
        tools = ToolRegistry()
        tools.register("run_command", workspace.run)
        tools.cleanup = workspace.close
        adapter = AsyncMock()
        adapter.get_challenge.return_value = Challenge(id="cancel-smoke", name="Cancel integration check")

        async def complete(*args):
            return {"tool_calls": [{"id": "sleep", "type": "function", "function": {"name": "run_command", "arguments": json.dumps({"command": "touch /workspace/started; sleep 120", "timeout": 180})}}]}

        with patch.object(solve_log, "_LOG_FILE", root / "logs.json"), patch.object(runner, "Settings", return_value=settings), patch.object(runner, "build_local_tools", return_value=tools), patch.object(LocalModel, "complete", complete):
            try:
                runner.start_solve("local", "cancel-smoke", adapter)
                async with asyncio.timeout(20):
                    while not (root / "started").exists():
                        await asyncio.sleep(.1)
                name = workspace.container
                assert name
                runner.control_solve("local", "cancel-smoke", "cancel")
                async with asyncio.timeout(20):
                    while runner.is_running("local", "cancel-smoke"):
                        await asyncio.sleep(.1)
                await asyncio.sleep(0)
                assert solve_log.get_solve_log("local", "cancel-smoke")["status"] == "cancelled"
                code, out, err = await workspace._run(["docker", "ps", "-a", "-q", "--filter", "name=^" + name + "$"], timeout=5)
                assert code == 0 and not out.strip(), (code, out, err)
                assert (root / "started").exists()
                print("PASS: cancellation interrupted real Docker command, removed sandbox, preserved workspace")
            finally:
                await runner.shutdown_tasks()
                await workspace.close()


asyncio.run(main())
