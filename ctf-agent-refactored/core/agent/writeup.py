"""
自动生成 Writeup（解题报告）。

当 Flag 验证成功后，自动调用 LLM 生成该题目的解题报告。
基于题目信息 + 解题过程中的黑板/工具调用历史生成。
"""

from __future__ import annotations

import logging
from typing import Any

from config import Settings
from core.skills import PlatformSkillAdapter

logger = logging.getLogger(__name__)


async def auto_generate_writeup(
    challenge_id: str,
    adapter: PlatformSkillAdapter,
    *,
    solve_context: str = "",
) -> dict[str, Any]:
    """
    Flag 验证成功后自动生成 WP。

    Args:
        challenge_id: 题目 ID
        adapter: 平台 Skill 适配器
        solve_context: 解题过程上下文（黑板/工具历史摘要）

    Returns:
        {
            "success": bool,
            "challenge_id": str,
            "title": str,
            "content": str,       # WP 正文（Markdown）
            "category": str,
            "value": int,
        }
    """
    try:
        challenge = await adapter.get_challenge(challenge_id)
    except Exception as e:
        logger.warning("获取题目信息失败，无法生成 WP: %s", e)
        return {"success": False, "challenge_id": challenge_id, "error": str(e)}

    settings = Settings()
    api_key = settings.bailian_api_key
    base_url = settings.bailian_base_url
    model = settings.llm_default_model

    # 构建 WP 生成 prompt
    system_prompt = (
        "你是一名 CTF 解题专家。请根据题目信息和解题过程，生成一份结构清晰的"
        "Writeup（解题报告），用 Markdown 格式输出。包含：题目概述、解题思路、"
        "关键步骤、Flag、总结。语言简洁专业。"
    )

    user_content = (
        f"题目: {challenge.name}\n"
        f"分类: {challenge.category}\n"
        f"分值: {challenge.value}\n"
        f"描述: {challenge.description}\n"
    )
    if challenge.need_container:
        user_content += f"容器地址: {challenge.connection_info}\n"
    if solve_context:
        user_content += f"\n解题过程:\n{solve_context}\n"

    # 调用 LLM 生成 WP
    wp_content = ""
    if api_key:
        try:
            import httpx
            endpoint = base_url if "/llm-gateway/proxy/e/" in base_url else f"{base_url}/chat/completions"
            async with httpx.AsyncClient(timeout=60) as client:
                resp = await client.post(
                    endpoint,
                    headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                    json={
                        "model": model,
                        "messages": [
                            {"role": "system", "content": system_prompt},
                            {"role": "user", "content": user_content},
                        ],
                        "temperature": 0.3,
                    },
                )
                resp.raise_for_status()
                data = resp.json()
                wp_content = data["choices"][0]["message"]["content"]
        except Exception as e:
            logger.warning("LLM 生成 WP 失败，回退模板: %s", e)

    # 回退：模板拼接
    if not wp_content:
        wp_content = _template_writeup(challenge.name, challenge.category, challenge.description)

    return {
        "success": True,
        "challenge_id": challenge_id,
        "title": f"[{challenge.category}] {challenge.name}",
        "content": wp_content,
        "category": challenge.category,
        "value": challenge.value,
    }


def _template_writeup(name: str, category: str, description: str) -> str:
    """模板化 WP（LLM 不可用时的兜底）。"""
    return f"""# [{category}] {name}

## 题目概述
{description or '（无描述）'}

## 解题思路
（自动生成：本题已成功解出，Flag 验证通过。详细解题步骤待补充。）

## 关键步骤
1. 分析题目类型与入口
2. 利用相关漏洞/技术点
3. 获取 Flag

## Flag
`FLAG{...}`（已验证通过）

## 总结
本题考察 {category} 相关知识点。
"""
