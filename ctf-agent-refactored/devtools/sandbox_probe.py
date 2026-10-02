import asyncio

from config import Settings
from core.skills.local_adapter import LocalAdapter
from core.tools.local_tools import build_local_tools

CODE = """from Crypto.Util.number import long_to_bytes
import gmpy2, z3, sympy
print('SANDBOX OK', gmpy2.version(), z3.get_version_string())
"""


async def main() -> None:
    settings = Settings()
    adapter = LocalAdapter(settings)
    challenge = await adapter.get_challenge("8")
    tools = build_local_tools(challenge, settings)
    result = await tools.call("write_file", path="_probe.py", content=CODE)
    print("write:", result.success)
    result = await tools.call("run_command", command="python3 -V && python3 _probe.py && rm -f _probe.py", timeout=120)
    data = result.data if isinstance(result.data, dict) else {}
    print("backend:", data.get("backend"), "exit:", data.get("exit_code"))
    print((data.get("stdout") or "").strip()[:300])
    if data.get("stderr"):
        print("ERR:", (data["stderr"] or "").strip()[:200])
    await tools.cleanup()


asyncio.run(main())
