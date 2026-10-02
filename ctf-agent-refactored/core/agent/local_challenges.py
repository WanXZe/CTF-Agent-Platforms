"""Read 0xgame style QuestionInfo.json without exposing stored flags."""
from __future__ import annotations

import json
from pathlib import Path

from core.models import Challenge, ChallengeFile


def load_local_challenges(root: str) -> list[Challenge]:
    base = Path(root).expanduser().resolve()
    if not base.is_dir():
        raise FileNotFoundError(f"Challenge root does not exist: {base}")
    challenges = []
    for path in sorted(base.glob("*/*/QuestionInfo.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            continue
        files = []
        for item in data.get("attachment", []):
            if isinstance(item, dict) and item.get("filename"):
                files.append(ChallengeFile(name=str(item["filename"]), url=""))
        challenge = Challenge(
            id=str(data.get("challengeId") or path.parent.relative_to(base)),
            name=str(data.get("title") or path.parent.name),
            category=str(data.get("category") or path.parent.parent.name),
            description=str(data.get("description") or ""),
            value=int(data.get("score") or 0),
            tags=[str(x) for x in data.get("tag", [])],
            need_container=bool(data.get("needContainer", False)),
            files=files,
            raw={"hints": data.get("hint", []), "local_dir": str(path.parent)},
        )
        challenges.append(challenge)
    return challenges
