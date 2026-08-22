"""
README → Skill 自动生成器。

将任意项目的 README.md 解析为 Agent Skill 能力文件。
支持规则解析和 LLM 增强解析两种模式。
"""

from .generator import (
    APIEndpoint,
    LLMReadmeParser,
    ReadmeParser,
    SkillCodeGenerator,
    SkillSpec,
    main,
)

__all__ = [
    "APIEndpoint",
    "LLMReadmeParser",
    "ReadmeParser",
    "SkillCodeGenerator",
    "SkillSpec",
    "main",
]
