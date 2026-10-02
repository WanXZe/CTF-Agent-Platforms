"""Run in VM project root to verify the local Ollama tool-call path."""
import asyncio
from config import Settings
from core.agent.local_llm import LocalModel


async def main():
    settings = Settings()
    model = LocalModel(settings)
    tools = [{"type": "function", "function": {
        "name": "list_files", "description": "List challenge files",
        "parameters": {"type": "object", "properties": {}, "required": []},
    }}]
    reply = await model.complete([{"role": "user", "content": "Call list_files now."}], tools)
    names = [(call.get("function") or {}).get("name") for call in reply.get("tool_calls") or []]
    print({"model": settings.llm_default_model, "tool_calls": names,
           "prompt_tokens": (model.last_usage or {}).get("prompt_tokens")})
    if "list_files" not in names:
        raise SystemExit("Expected list_files tool call")


if __name__ == "__main__":
    asyncio.run(main())
