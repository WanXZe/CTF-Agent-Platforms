"""Read-only deployment diagnostics. Prints no credentials or challenge content."""
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core.agent.local_llm import LocalModel
from core.agent.solve_runner import selected_settings
from core.stats import solve_log


async def main():
    for name in ("deepseek-flash", "qwen3:4b"):
        model = LocalModel(selected_settings(name))
        try:
            print(name, json.dumps(await model.probe(), ensure_ascii=False))
            if name == "deepseek-flash":
                response = await model._http().get("https://api.deepseek.com/user/balance", headers={"Authorization": "Bearer " + model._api_key()}, timeout=12)
                print("balance endpoint:", response.status_code, "available:", response.json().get("is_available"))
        except Exception as exc:
            print(name, type(exc).__name__, str(exc))
        finally:
            await model.aclose()
    print("active stored sessions:", [(s["platform_id"], s["challenge_id"], s["status"]) for s in solve_log.get_all_sessions() if s["status"] in ("running", "paused", "pausing", "cancelling")])


asyncio.run(main())
