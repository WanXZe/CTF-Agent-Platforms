"""
README → Skill 自动生成器（通用）。

将任意项目的 README.md 解析为 Agent Skill 能力文件。
支持两种模式：
  1. 规则解析模式（默认）：从 README 中提取 API 端点、代码示例、功能描述，
     自动生成带类型注解、docstring、异常处理的 Skill 文件。
  2. LLM 增强模式（--llm）：调用大模型对 README 做语义理解，生成更精准的 Skill。

用法：
  python -m readme_to_skill --input ./project/README.md --output ./skills/project_skill.py
  python -m readme_to_skill --input ./project/README.md --llm --model qwen3.7-plus
  python -m readme_to_skill --input ./project/README.md --name my_skill --base-url https://api.example.com
"""

from __future__ import annotations

import argparse
import logging
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# 数据结构
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class APIEndpoint:
    """从 README 中提取的 API 端点。"""
    method: str           # GET / POST / PUT / DELETE
    path: str             # /api/v1/resource
    description: str = "" # 功能描述
    params: list[dict[str, str]] = field(default_factory=list)  # [{name, type, desc}]
    returns: str = ""     # 返回值描述
    example: str = ""     # 示例代码/请求


@dataclass
class SkillSpec:
    """生成的 Skill 规格。"""
    name: str = "generated_skill"
    description: str = ""
    base_url: str = ""
    auth_type: str = ""       # Bearer / ApiKey / Header / None
    auth_header: str = ""     # 认证 header 名
    endpoints: list[APIEndpoint] = field(default_factory=list)
    functions: list[dict[str, str]] = field(default_factory=list)  # 非 API 的能力函数
    raw_readme: str = ""


# ─────────────────────────────────────────────────────────────────────────────
# 规则解析器
# ─────────────────────────────────────────────────────────────────────────────

class ReadmeParser:
    """从 README.md 中提取 API 端点和能力描述。"""

    # 常见 API 路径正则
    _API_PATH_RE = re.compile(
        r'(?:GET|POST|PUT|DELETE|PATCH)\s+([`"\']?)(/[a-zA-Z0-9_/{}:.\-]+)\1',
        re.IGNORECASE,
    )
    # curl 命令中的端点
    _CURL_RE = re.compile(
        r'curl\s+(?:-[Xx]\s+)?(\w+)?\s*[`"\']?(https?://[^\s`"\']+|[`"\']?/[^\s`"\']+)',
        re.IGNORECASE,
    )
    # 代码块
    _CODE_BLOCK_RE = re.compile(r'```(\w*)\n(.*?)```', re.DOTALL)
    # 标题
    _HEADING_RE = re.compile(r'^(#{1,4})\s+(.+)$', re.MULTILINE)

    def parse(self, readme_content: str, *, name: str = "", base_url: str = "") -> SkillSpec:
        """
        解析 README 内容，生成 SkillSpec。

        Args:
            readme_content: README.md 原始文本
            name: Skill 名称（为空时从 README 标题推导）
            base_url: API 基础 URL（为空时从 README 中提取）

        Returns:
            SkillSpec 规格
        """
        spec = SkillSpec(raw_readme=readme_content)

        # 1. 提取名称和描述
        spec.name = name or self._extract_name(readme_content)
        spec.description = self._extract_description(readme_content)

        # 2. 提取 base_url
        spec.base_url = base_url or self._extract_base_url(readme_content)

        # 3. 提取认证方式
        spec.auth_type, spec.auth_header = self._extract_auth(readme_content)

        # 4. 提取 API 端点
        spec.endpoints = self._extract_endpoints(readme_content)

        # 5. 提取功能函数（非 API 的能力，如 CLI 命令、SDK 方法）
        spec.functions = self._extract_functions(readme_content)

        return spec

    def _extract_name(self, content: str) -> str:
        """从第一个 H1 标题推导名称。"""
        for line in content.splitlines():
            m = re.match(r'^#\s+(.+)$', line)
            if m:
                name = m.group(1).strip()
                # 清理：去掉 emoji、特殊符号，转 snake_case
                name = re.sub(r'[^\w\s-]', '', name).strip()
                name = re.sub(r'[\s-]+', '_', name).lower()
                return name or "generated_skill"
        return "generated_skill"

    def _extract_description(self, content: str) -> str:
        """提取 H1 之后的第一段作为描述。"""
        lines = content.splitlines()
        in_desc = False
        desc_lines: list[str] = []
        for line in lines:
            if line.startswith('# '):
                in_desc = True
                continue
            if in_desc:
                if line.startswith('#'):
                    break
                if line.strip():
                    desc_lines.append(line.strip())
                elif desc_lines:
                    break
        return ' '.join(desc_lines)[:500]

    def _extract_base_url(self, content: str) -> str:
        """从 README 中提取 API 基础 URL。"""
        # 匹配常见的 base URL 声明
        patterns = [
            r'(?:base[_-]?url|api[_-]?url|endpoint|host)\s*[:=]\s*[`"\']?(https?://[^\s`"\']+)',
            r'(?:https?://[^\s`"\']+)/api/',
        ]
        for pat in patterns:
            m = re.search(pat, content, re.IGNORECASE)
            if m:
                url = m.group(1) if m.lastindex and m.lastindex >= 1 else m.group(0)
                # 去掉路径部分，只保留 scheme://host:port
                url_match = re.match(r'(https?://[^/]+)', url)
                if url_match:
                    return url_match.group(1)
                return url.rstrip('/')
        return ""

    def _extract_auth(self, content: str) -> tuple[str, str]:
        """提取认证方式和 header 名。"""
        content_lower = content.lower()
        if 'x-agent-accesskey' in content_lower or 'x-api-key' in content_lower:
            if 'x-agent-accesskey' in content_lower:
                return "ApiKey", "X-Agent-AccessKey"
            return "ApiKey", "X-API-Key"
        if 'bearer' in content_lower or 'authorization' in content_lower:
            return "Bearer", "Authorization"
        if 'token' in content_lower:
            return "Bearer", "Authorization"
        return "", ""

    def _extract_endpoints(self, content: str) -> list[APIEndpoint]:
        """提取所有 API 端点。"""
        endpoints: list[APIEndpoint] = []
        seen: set[tuple[str, str]] = set()

        # 方法1：直接匹配 "METHOD /path"
        for m in self._API_PATH_RE.finditer(content):
            method = m.group(1).upper() if m.group(1) else "GET"
            path = m.group(2)
            key = (method, path)
            if key not in seen:
                seen.add(key)
                # 尝试提取附近的描述
                desc = self._extract_nearby_description(content, m.start())
                endpoints.append(APIEndpoint(method=method, path=path, description=desc))

        # 方法2：从 curl 命令提取
        for m in self._CURL_RE.finditer(content):
            method = (m.group(1) or "GET").upper()
            url = m.group(2).strip('`"\'')
            # 从 URL 中提取路径
            path_match = re.match(r'https?://[^/]+(.+)', url)
            path = path_match.group(1) if path_match else url
            if path and not path.startswith('/'):
                path = '/' + path
            key = (method, path)
            if key not in seen and path.count('/') >= 1:
                seen.add(key)
                desc = self._extract_nearby_description(content, m.start())
                endpoints.append(APIEndpoint(method=method, path=path, description=desc))

        # 方法3：从 Markdown 表格提取（| 方法 | 路径 | 描述 |）
        for line in content.splitlines():
            if '|' in line and re.search(r'(GET|POST|PUT|DELETE|PATCH)', line, re.IGNORECASE):
                cells = [c.strip() for c in line.split('|') if c.strip()]
                if len(cells) >= 2:
                    method_cell = cells[0].upper()
                    if method_cell in ('GET', 'POST', 'PUT', 'DELETE', 'PATCH'):
                        path = cells[1].strip('`')
                        desc = cells[2] if len(cells) > 2 else ""
                        key = (method_cell, path)
                        if key not in seen and path.startswith('/'):
                            seen.add(key)
                            endpoints.append(APIEndpoint(method=method_cell, path=path, description=desc))

        return endpoints

    def _extract_nearby_description(self, content: str, pos: int) -> str:
        """提取某个位置附近的描述文本（前后 200 字符）。"""
        start = max(0, pos - 200)
        end = min(len(content), pos + 200)
        snippet = content[start:end]
        # 取最近的非空行
        lines = [l.strip() for l in snippet.splitlines() if l.strip() and not l.strip().startswith('```')]
        for line in reversed(lines):
            if len(line) > 10 and not line.startswith('#') and 'curl' not in line.lower():
                return line[:120]
        return ""

    def _extract_functions(self, content: str) -> list[dict[str, str]]:
        """提取非 API 的能力函数（CLI 命令、SDK 方法等）。"""
        functions: list[dict[str, str]] = []
        # 匹配代码块中的函数定义
        for m in self._CODE_BLOCK_RE.finditer(content):
            code = m.group(2)
            lang = m.group(1).lower()
            if lang in ('python', 'py', ''):
                # 匹配 def 函数定义
                for fm in re.finditer(r'def\s+(\w+)\s*\(([^)]*)\)', code):
                    fname = fm.group(1)
                    if not fname.startswith('_'):
                        functions.append({
                            "name": fname,
                            "params": fm.group(2).strip(),
                            "description": "",
                        })
        return functions


# ─────────────────────────────────────────────────────────────────────────────
# LLM 增强解析器（可选）
# ─────────────────────────────────────────────────────────────────────────────

class LLMReadmeParser:
    """
    使用 LLM 对 README 做语义理解，生成更精准的 SkillSpec。
    需要配置 LLM API Key（从环境变量读取）。
    """

    def __init__(self, *, model: str = "qwen3.7-plus", base_url: str = "", api_key: str = "") -> None:
        self.model = model
        self.base_url = base_url or "https://dashscope.aliyuncs.com/compatible-mode/v1"
        self.api_key = api_key

    async def parse(self, readme_content: str, *, name: str = "", base_url: str = "") -> SkillSpec:
        """使用 LLM 解析 README。"""
        if not self.api_key:
            logger.warning("未配置 LLM API Key，回退到规则解析")
            return ReadmeParser().parse(readme_content, name=name, base_url=base_url)

        import httpx
        import json

        system_prompt = (
            "你是一个 Skill 生成专家。请分析给定的 README.md，提取所有 API 接口和能力点，"
            "输出 JSON 格式的 Skill 规格。JSON 结构："
            '{"name": "skill名称", "description": "描述", "base_url": "API基础URL", '
            '"auth_type": "Bearer/ApiKey/None", "auth_header": "认证header名", '
            '"endpoints": [{"method": "GET/POST/...", "path": "/api/...", '
            '"description": "功能说明", "params": [{"name":"参数名","type":"类型","desc":"说明"}], '
            '"returns": "返回值说明"}], "functions": [{"name":"函数名","description":"说明"}]}'
        )

        endpoint = f"{self.base_url}/chat/completions"
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": f"请解析以下 README：\n\n{readme_content[:8000]}"},
            ],
            "temperature": 0.1,
            "response_format": {"type": "json_object"},
        }
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}

        try:
            async with httpx.AsyncClient(timeout=60) as client:
                resp = await client.post(endpoint, json=payload, headers=headers)
                resp.raise_for_status()
                data = resp.json()
                result_text = data["choices"][0]["message"]["content"]
                parsed = json.loads(result_text)
                spec = SkillSpec(
                    name=parsed.get("name", name or "generated_skill"),
                    description=parsed.get("description", ""),
                    base_url=parsed.get("base_url", base_url or ""),
                    auth_type=parsed.get("auth_type", ""),
                    auth_header=parsed.get("auth_header", ""),
                    raw_readme=readme_content,
                )
                for ep in parsed.get("endpoints", []):
                    spec.endpoints.append(APIEndpoint(
                        method=ep.get("method", "GET"),
                        path=ep.get("path", ""),
                        description=ep.get("description", ""),
                        params=ep.get("params", []),
                        returns=ep.get("returns", ""),
                    ))
                for fn in parsed.get("functions", []):
                    spec.functions.append(fn)
                return spec
        except Exception as e:
            logger.warning("LLM 解析失败，回退规则解析: %s", e)
            return ReadmeParser().parse(readme_content, name=name, base_url=base_url)


# ─────────────────────────────────────────────────────────────────────────────
# Skill 代码生成器
# ─────────────────────────────────────────────────────────────────────────────

class SkillCodeGenerator:
    """根据 SkillSpec 生成完整的 Skill Python 文件。"""

    def generate(self, spec: SkillSpec) -> str:
        """生成 Skill 源码。"""
        class_name = self._to_class_name(spec.name)
        lines: list[str] = []

        # 文件头
        lines.append('"""')
        lines.append(f"{spec.name} —— 自动生成的 Agent Skill。")
        lines.append("")
        if spec.description:
            lines.append(f"描述: {spec.description}")
        lines.append("")
        lines.append("本文件由 readme_to_skill 自动生成，基于项目 README.md 解析。")
        lines.append("返回值统一为结构化 dict，适配 Agent 工具调用格式。")
        lines.append('"""')
        lines.append("")
        lines.append("from __future__ import annotations")
        lines.append("")
        lines.append("import logging")
        lines.append("from typing import Any, Optional")
        lines.append("")
        lines.append("import httpx")
        lines.append("")
        lines.append(f'logger = logging.getLogger(__name__)')
        lines.append("")
        lines.append("")
        lines.append("def _ok(data: Any = None, message: str = 'ok') -> dict[str, Any]:")
        lines.append('    """构造成功返回。"""')
        lines.append("    return {'success': True, 'code': '0', 'message': message, 'data': data, 'error': None}")
        lines.append("")
        lines.append("")
        lines.append("def _fail(error_type: str, message: str, code: str = '') -> dict[str, Any]:")
        lines.append('    """构造失败返回。"""')
        lines.append("    return {'success': False, 'code': code or error_type, 'message': message, 'data': None, 'error': error_type}")
        lines.append("")
        lines.append("")

        # 类定义
        lines.append(f"class {class_name}:")
        lines.append(f'    """{spec.description or "自动生成的 Skill 类。"}"""')
        lines.append("")
        lines.append("    def __init__(")
        lines.append("        self,")
        lines.append(f"        api_base_url: str = {spec.base_url!r},")
        if spec.auth_type:
            lines.append(f"        api_key: str = '',")
        lines.append("        timeout: float = 30.0,")
        lines.append("        verify_ssl: bool = True,")
        lines.append("    ) -> None:")
        lines.append('        """初始化 Skill。"""')
        lines.append("        self.api_base_url = api_base_url.rstrip('/')")
        if spec.auth_type:
            lines.append("        self.api_key = api_key")
        lines.append("        self.timeout = timeout")
        lines.append("        self.verify_ssl = verify_ssl")
        lines.append("        self._client: Optional[httpx.AsyncClient] = None")
        lines.append("")

        # HTTP 客户端方法
        lines.append("    async def _ensure_client(self) -> httpx.AsyncClient:")
        lines.append('        """懒初始化 HTTP 客户端。"""')
        lines.append("        if self._client is None:")
        lines.append("            headers = {'User-Agent': 'Generated-Skill/1.0'}")
        if spec.auth_type == "Bearer":
            lines.append("            if self.api_key:")
            lines.append("                headers['Authorization'] = f'Bearer {self.api_key}'")
        elif spec.auth_type == "ApiKey" and spec.auth_header:
            lines.append("            if self.api_key:")
            lines.append(f"                headers[{spec.auth_header!r}] = self.api_key")
        lines.append("            self._client = httpx.AsyncClient(")
        lines.append("                base_url=self.api_base_url, headers=headers,")
        lines.append("                timeout=self.timeout, verify=self.verify_ssl,")
        lines.append("            )")
        lines.append("        return self._client")
        lines.append("")

        # 通用请求方法
        lines.append("    async def _request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:")
        lines.append('        """发起 HTTP 请求，返回结构化结果。"""')
        lines.append("        try:")
        lines.append("            client = await self._ensure_client()")
        lines.append("            resp = await client.request(method, path, **kwargs)")
        lines.append("            if resp.status_code >= 400:")
        lines.append("                return _fail('http_error', f'HTTP {resp.status_code}: {resp.text[:200]}')")
        lines.append("            try:")
        lines.append("                data = resp.json()")
        lines.append("            except Exception:")
        lines.append("                data = resp.text")
        lines.append("            return _ok(data=data)")
        lines.append("        except Exception as e:")
        lines.append("            logger.exception('请求失败: %s %s', method, path)")
        lines.append("            return _fail('network_error', str(e))")
        lines.append("")

        # 为每个端点生成方法
        for i, ep in enumerate(spec.endpoints):
            func_name = self._endpoint_to_func_name(ep)
            lines.append(f"    async def {func_name}(self, **kwargs: Any) -> dict[str, Any]:")
            lines.append(f'        """')
            lines.append(f"        {ep.description or f'调用 {ep.method} {ep.path}'}")
            lines.append("")
            lines.append(f"        API: {ep.method} {ep.path}")
            if ep.params:
                lines.append("")
                lines.append("        Args:")
                for p in ep.params:
                    lines.append(f"            {p.get('name', '?')}: {p.get('desc', '')}")
            if ep.returns:
                lines.append("")
                lines.append(f"        Returns: {ep.returns}")
            lines.append('        """')
            # 路径参数替换
            path_params = re.findall(r'\{(\w+)\}', ep.path)
            if path_params:
                lines.append(f"        path = f{ep.path!r}")
                lines.append("        return await self._request({!r}, path, **kwargs)".format(ep.method))
            else:
                lines.append(f"        return await self._request({ep.method!r}, {ep.path!r}, **kwargs)")
            lines.append("")

        # 为每个非 API 函数生成占位方法
        for fn in spec.functions:
            fname = fn.get("name", "")
            if fname and not any(f"{fname}(" in l for l in lines):
                lines.append(f"    async def {fname}(self, **kwargs: Any) -> dict[str, Any]:")
                lines.append(f'        """{fn.get("description", fname)}"""')
                lines.append("        # TODO: 基于 README 中的使用示例实现此方法")
                lines.append("        return _fail('not_implemented', f'{fname} 尚未实现')")
                lines.append("")

        # close 方法
        lines.append("    async def close(self) -> None:")
        lines.append('        """关闭 HTTP 客户端。"""')
        lines.append("        if self._client:")
        lines.append("            await self._client.aclose()")
        lines.append("            self._client = None")
        lines.append("")

        return '\n'.join(lines)

    def _to_class_name(self, name: str) -> str:
        """snake_case → PascalCase。"""
        parts = re.split(r'[_\-\s]+', name)
        return ''.join(p.capitalize() for p in parts if p) + "Skill"

    def _endpoint_to_func_name(self, ep: APIEndpoint) -> str:
        """将 API 端点转换为函数名。"""
        # 去掉路径中的参数占位
        path = re.sub(r'\{[^}]+\}', '', ep.path)
        # 提取路径中的有意义单词
        words = re.findall(r'[a-zA-Z0-9]+', path)
        # 去掉常见前缀
        prefixes = {'api', 'v1', 'v2', 'v3', 'v0'}
        words = [w for w in words if w.lower() not in prefixes]
        # 方法前缀
        method_prefix = {
            'GET': 'get_', 'POST': 'create_', 'PUT': 'update_',
            'DELETE': 'delete_', 'PATCH': 'patch_',
        }.get(ep.method, '')
        name = method_prefix + '_'.join(words).lower()
        # 清理
        name = re.sub(r'_+', '_', name).strip('_')
        return name or f"call_{ep.method.lower()}"


# ─────────────────────────────────────────────────────────────────────────────
# CLI 入口
# ─────────────────────────────────────────────────────────────────────────────

def main() -> None:
    """CLI 入口：python -m readme_to_skill --input README.md --output skill.py"""
    parser = argparse.ArgumentParser(description="README.md → Agent Skill 自动生成器")
    parser.add_argument("--input", "-i", required=True, help="输入 README.md 文件路径")
    parser.add_argument("--output", "-o", default=None, help="输出 Skill 文件路径（默认: {name}_skill.py）")
    parser.add_argument("--name", "-n", default="", help="Skill 名称（默认从 README 推导）")
    parser.add_argument("--base-url", default="", help="API 基础 URL（默认从 README 提取）")
    parser.add_argument("--llm", action="store_true", help="使用 LLM 增强解析")
    parser.add_argument("--model", default="qwen3.7-plus", help="LLM 模型（--llm 时生效）")
    parser.add_argument("--print-spec", action="store_true", help="仅打印解析规格，不生成文件")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    input_path = Path(args.input)
    if not input_path.exists():
        logger.error("输入文件不存在: %s", input_path)
        sys.exit(1)

    readme_content = input_path.read_text(encoding="utf-8")
    logger.info("读取 README: %s (%d 字符)", input_path, len(readme_content))

    # 解析
    if args.llm:
        import asyncio
        import os
        api_key = os.environ.get("BAILIAN_API_KEY", "")
        llm_parser = LLMReadmeParser(model=args.model, api_key=api_key)
        spec = asyncio.run(llm_parser.parse(readme_content, name=args.name, base_url=args.base_url))
    else:
        spec = ReadmeParser().parse(readme_content, name=args.name, base_url=args.base_url)

    logger.info("解析完成: name=%s, endpoints=%d, functions=%d",
                spec.name, len(spec.endpoints), len(spec.functions))

    if args.print_spec:
        import json
        print(json.dumps({
            "name": spec.name,
            "description": spec.description,
            "base_url": spec.base_url,
            "auth_type": spec.auth_type,
            "auth_header": spec.auth_header,
            "endpoints": [vars(e) for e in spec.endpoints],
            "functions": spec.functions,
        }, ensure_ascii=False, indent=2))
        return

    # 生成代码
    generator = SkillCodeGenerator()
    code = generator.generate(spec)

    # 输出
    output_path = Path(args.output) if args.output else Path(f"{spec.name}_skill.py")
    output_path.write_text(code, encoding="utf-8")
    logger.info("Skill 已生成: %s (%d 行)", output_path, code.count('\n'))
    print(f"\n✅ 生成成功: {output_path}")
    print(f"   类名: {generator._to_class_name(spec.name)}")
    print(f"   端点: {len(spec.endpoints)} 个 API 方法")
    print(f"   函数: {len(spec.functions)} 个能力方法")


if __name__ == "__main__":
    main()
