"""Exercise the project's actual Docker workspace backend with the new image."""
import asyncio
import json
import shutil
import tempfile
from pathlib import Path
from types import SimpleNamespace

from core.tools.local_tools import Workspace


async def main():
    parent = Path(tempfile.mkdtemp(prefix="ctf-sandbox-integration-"))
    workspace = Workspace(parent, SimpleNamespace(sandbox_mode="docker", sandbox_image="ctf-sandbox",
        sandbox_dns="192.168.174.1", sandbox_memory_limit="8g", sandbox_cpu_limit=2))
    try:
        result = await workspace.run("upx --version && r2 -v && jadx --version && python3 -c 'import angr, pwn, lief, frida, fpylll' && test -f /opt/ctf-tools/README.md", timeout=60)
        print(result)
        assert result.success, str(result)
        print(json.dumps({"project_workspace_backend": await workspace.backend(), "container": workspace.container, "passed": True}))
    finally:
        await workspace.close()
        shutil.rmtree(parent)


asyncio.run(main())
