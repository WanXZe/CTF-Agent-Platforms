import asyncio
import json
import tempfile
import unittest
from pathlib import Path

from config import Settings
from core.agent.coordinator import SolveSession
from core.agent.local_challenges import load_local_challenges
from core.models import Challenge, ToolResult
from core.tools import ToolRegistry
from core.tools.local_tools import build_local_tools


class FakeModel:
    def __init__(self, replies):
        self.replies = iter(replies)

    async def complete(self, messages, tools):
        return next(self.replies)


def call(name, args, id="call_1"):
    return {"tool_calls": [{"id": id, "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}]}


class SolverTests(unittest.TestCase):
    def settings(self):
        return Settings(_env_file=None, solver_max_rounds=3, container_auto_recover=False)

    def test_platform_verification_controls_solved(self):
        tools = ToolRegistry()
        attempts = []

        async def submit_flag(challenge_id, flag):
            attempts.append((challenge_id, flag))
            return ToolResult.ok({"is_correct": flag == "flag{right}"})

        tools.register("submit_flag", submit_flag)
        model = FakeModel([call("submit_flag", {"challenge_id": "other", "flag": "flag{wrong}"}),
                           call("submit_flag", {"challenge_id": "other", "flag": "flag{right}"})])
        settings = self.settings()
        settings.solver_allow_flag_submit = True
        session = SolveSession(Challenge(id="1", name="test"), tools, settings, model)
        result = asyncio.run(session.run())
        self.assertEqual(result["status"], "solved")
        self.assertEqual(attempts, [("1", "flag{wrong}"), ("1", "flag{right}")])
        self.assertNotIn("flag{right}", str(session.tool_history))

    def test_local_metadata_flag_not_exposed(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "Web" / "demo"
            path.mkdir(parents=True)
            (path / "QuestionInfo.json").write_text(json.dumps({"title": "demo", "flag": "flag{secret}", "hint": ["hint"]}), encoding="utf-8")
            (path / "WriteUp.md").write_text("flag{secret}", encoding="utf-8")
            (path / "source.py").write_text("print('hello')", encoding="utf-8")
            challenge = load_local_challenges(tmp)[0]
            self.assertNotIn("flag{secret}", str(challenge))
            tools = build_local_tools(challenge, self.settings())
            listing = asyncio.run(tools.call("list_files"))
            self.assertEqual([f["path"] for f in listing.data], ["source.py"])
            self.assertFalse(asyncio.run(tools.call("read_text", path="../demo/WriteUp.md")).success)

    def test_submit_disabled_by_default(self):
        tools = ToolRegistry()
        attempts = []

        async def submit_flag(challenge_id, flag):
            attempts.append(flag)
            return ToolResult.ok({"is_correct": True})

        tools.register("submit_flag", submit_flag)
        model = FakeModel([call("submit_flag", {"flag": "flag{guess}"}), {"content": "No proof"}])
        session = SolveSession(Challenge(id="1", name="test"), tools, self.settings(), model)
        result = asyncio.run(session.run())
        self.assertEqual(result["status"], "needs_human")
        self.assertEqual(attempts, [])
        self.assertNotIn("submit_flag", [tool["function"]["name"] for tool in session._schemas()])

    def test_unobserved_guess_not_a_candidate(self):
        session = SolveSession(Challenge(id="1", name="test"), ToolRegistry(), self.settings(), FakeModel([]))
        session._collect_candidates("CANDIDATE: flag{guessed} flag{...}")
        self.assertEqual(session.candidates, [])
        session._collect_candidates("flag{from_output}", from_tool=True)
        self.assertEqual(session.candidates, ["flag{from_output}"])


if __name__ == "__main__":
    unittest.main()
