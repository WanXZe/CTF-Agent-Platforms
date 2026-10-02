"""Verify the staged sandbox workspace no longer carries the stored answer."""
from pathlib import Path

from config import Settings
from core.agent.local_challenges import load_local_challenges
from core.tools.local_tools import _SAFE_CONTAINER_CHARS, stage_workspace

NEEDLE = "0xGame{"


def main() -> None:
    settings = Settings()
    for challenge in load_local_challenges(settings.local_root):
        if challenge.id not in {"35", "8"}:
            continue
        source = Path(challenge.raw["local_dir"])
        slug = _SAFE_CONTAINER_CHARS.sub("-", f"{challenge.id}-{source.name}")[:40].strip("-")
        dest = stage_workspace(source, slug)
        print(f"--- {challenge.id} {challenge.category}/{challenge.name}")
        print("    source files:", sorted(p.name for p in source.iterdir()))
        print("    staged files:", sorted(p.name for p in dest.iterdir()))
        bad_meta = [str(p.relative_to(dest)) for p in dest.rglob("*")
                    if p.is_file() and p.name.lower() in {"questioninfo.json", "writeup.md"}]
        print("    答案元数据是否泄漏:", bad_meta or "无（正确）")
        # 附件/解出来的二进制里带 flag 字符串是正常的（题目本身就靠 strings 能做），
        # 这里只做提示，不算泄漏。
        hits = [str(p.relative_to(dest)) for p in dest.rglob("*")
                if p.is_file() and NEEDLE.encode() in p.read_bytes()]
        print(f"    （提示）二进制/附件里出现 {NEEDLE!r} 的文件: {hits or '(none)'}")


main()
