# README → Skill 自动生成器

将任意项目的 README.md 自动解析为 Agent Skill 能力文件。

## 功能

- **规则解析**：从 README 中自动提取 API 端点（METHOD /path）、curl 示例、Markdown 表格、代码块中的函数定义
- **LLM 增强**（可选）：调用大模型对 README 做语义理解，生成更精准的 Skill
- **标准化输出**：生成的 Skill 文件带完整类型注解、docstring、异常处理、统一返回格式

## 快速使用

```bash
# 1. 将任意项目的 README.md 放入当前目录
cp /path/to/any_project/README.md ./

# 2. 规则解析模式（默认，无需 API Key）
python -m readme_to_skill --input ./README.md --output ./skills/my_project_skill.py

# 3. LLM 增强模式（更精准，需要 BAILIAN_API_KEY）
python -m readme_to_skill --input ./README.md --llm --model qwen3.7-plus

# 4. 自定义名称和基础 URL
python -m readme_to_skill -i README.md -n my_skill --base-url https://api.example.com

# 5. 仅查看解析规格（不生成文件）
python -m readme_to_skill -i README.md --print-spec
```

## 解析能力

| 提取来源 | 说明 |
|----------|------|
| `GET /api/v1/users` 格式 | 直接匹配 METHOD + PATH |
| `curl -X POST https://...` | 从 curl 命令提取 |
| Markdown 表格 `\| GET \| /api \| 描述 \|` | 从表格行提取 |
| Python 代码块中的 `def func()` | 提取函数定义作为能力方法 |
| H1 标题 | 推导 Skill 名称 |
| 第一段文本 | 推导 Skill 描述 |
| `base_url: https://...` | 提取 API 基础 URL |
| `Bearer` / `X-API-Key` / `token` | 识别认证方式 |

## 生成的 Skill 结构

```python
class ProjectNameSkill:
    def __init__(self, api_base_url, api_key, timeout, verify_ssl):
        ...

    async def _ensure_client(self) -> httpx.AsyncClient:
        ...

    async def _request(self, method, path, **kwargs) -> dict:
        # 统一返回 {success, code, message, data, error}
        ...

    async def get_users(self, **kwargs) -> dict:
        """GET /api/v1/users — 获取用户列表"""
        ...

    async def create_user(self, **kwargs) -> dict:
        """POST /api/v1/users — 创建用户"""
        ...

    async def close(self) -> None:
        ...
```

## 作为库使用

```python
from readme_to_skill import ReadmeParser, SkillCodeGenerator

parser = ReadmeParser()
spec = parser.parse(open("README.md").read(), name="my_skill")
generator = SkillCodeGenerator()
code = generator.generate(spec)
open("my_skill.py", "w").write(code)
```

## 注意事项

- 生成的 Skill 是**骨架代码**，复杂业务逻辑需要人工补充
- LLM 模式需要配置 `BAILIAN_API_KEY` 环境变量
- 生成后建议检查端点路径和参数是否准确
