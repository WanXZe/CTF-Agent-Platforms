# CTF 靶场平台 Skill (ctf-platform-skill)

> 独立 Skill 插件，封装 CTF 比赛靶场平台（西湖论剑 slab-match 等）的全部 REST 接口，供 CTF-Agent 主项目直接 import 调用。

## 功能能力

| 能力 | 函数 | 平台接口 |
|------|------|----------|
| 获取题目列表 | `list_challenges()` | `GET /ctf/exercise-list` |
| 查询单个题目 | `get_challenge_detail(id)` | `GET /ctf/exercise` |
| 下载题目附件 | `download_attachment(id, filename)` | 附件 URL 直链 |
| 启动容器 | `start_container(id)` | `POST /ctf/build-exercise-env` |
| 停止容器 | `stop_container(id)` | `POST /ctf/recover-exercise-env` |
| 查询容器状态 | `get_container_status(id)` | `GET /ctf/exercise`（推导） |
| 提交 Flag | `submit_flag(id, flag)` | `POST /answer-panel/answer` |
| 竞赛信息 | `get_match_info()` | `GET /match/notice/match-info` |
| 排名概览 | `get_overview()` | `GET /answer-panel/overview` |
| 公告列表 | `list_notices()` | `GET /match/notice/now-list` |
| 公告详情 | `get_notice_detail(id)` | `GET /match/notice/detail` |

## 安装

```bash
# 方式一：作为本地包安装
pip install -e .

# 方式二：直接放入主项目 skills/ 目录
cp -r ctf_platform_skill/ /path/to/ctf-agent/skills/
```

依赖：`httpx>=0.27`

## 快速使用

```python
import asyncio
from ctf_platform_skill import CTFPlatformSkill

async def main():
    skill = CTFPlatformSkill(
        api_base_url="https://pro.dasctf.com",
        access_key="your_access_key",
    )

    # 1. 获取题目列表
    result = await skill.list_challenges(light=True)
    if result["success"]:
        for ch in result["data"]:
            print(f"[{ch['id']}] {ch['name']} ({ch['category']})")

    # 2. 查询题目详情（含容器状态）
    detail = await skill.get_challenge_detail("123")
    if detail["success"]:
        d = detail["data"]
        print(f"需要容器: {d['need_container']}")
        print(f"容器状态: {d['container_status']}")

    # 3. 启动容器
    if detail["data"]["need_container"]:
        env = await skill.start_container("123")
        if env["success"]:
            print(f"访问地址: {env['data']['connection_info']}")

    # 4. 提交 Flag
    submit = await skill.submit_flag("123", "DASCTF{test_flag}")
    if submit["success"]:
        print(f"正确: {submit['data']['is_correct']}")

    await skill.close()

asyncio.run(main())
```

## 返回值格式（统一结构化）

所有函数返回统一 dict，适配 Agent 工具调用：

```python
# 成功
{
    "success": True,
    "code": "00000",
    "message": "ok",
    "data": { ... },       # 业务数据
    "error": None
}

# 失败
{
    "success": False,
    "code": "network_error",
    "message": "网络请求失败: ...",
    "data": None,
    "error": "network_error"   # Agent 可感知的错误类型
}
```

错误类型：`network_error` / `platform_error` / `container_error` / `invalid_param` / `rate_limited` / `server_error` / `timeout` / `invalid_response`

## 容器状态枚举

| 状态 | 说明 |
|------|------|
| `not_started` | 未启动 |
| `starting` | 启动中（loading） |
| `running` | 运行中 |
| `stopped` | 已停止 |
| `failed` | 操作失败 |

## 平台协议

- **Base URL**: `{host}/slab-match/api/v1/agent`
- **认证**: Header `X-Agent-AccessKey: <access_key>`
- **响应**: 统一 JSON `{code, message, data}`，`code == "00000"` 表示成功
- **题目模型**: 分类(category) → 子题(corpus) 两级，所有操作使用 `corpus.id` 作为 `exerciseId`

## 配置

见 `.env.example`。支持环境变量或构造参数传入。

## 注意事项

- 本模块是**纯能力 Skill**，不包含 Web 路由，供 Agent、脚本、后端业务直接调用
- 容器启动是异步操作，`start_container()` 内部轮询直到就绪或超时
- 平台 429 限流后自动进入 5 分钟冷却期，冷却期内请求快速失败
- Flag 自动归一化：`DASCTF{xxx}` / `flag{xxx}` 自动剥壳后提交
