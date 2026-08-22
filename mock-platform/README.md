# Mock CTF 靶场平台

仿西湖论剑 slab-match AI Agent API 的本地 Mock 服务器，用于 CTF-Agent 开发调试。

## 快速启动

```bash
pip install fastapi uvicorn pydantic
python mock_server.py --port 8080 --access-key test-key-123
```

启动后：
- API 地址：`http://localhost:8080/slab-match/api/v1/agent`
- AccessKey：`test-key-123`
- 健康检查：`http://localhost:8080/health`

## 在 CTF-Agent 中使用

编辑 `ctf-agent-refactored/.env`：
```
SLAB_ACCESS_KEY=test-key-123
```

编辑 `ctf-agent-refactored/config.yaml`：
```yaml
platform:
  type: "slab"
  api_base_url: "http://localhost:8080"
```

然后启动 CTF-Agent，前端即可看到 Mock 题目列表。

## 支持的接口

| 接口 | 方法 | 说明 |
|------|------|------|
| `/ctf/exercise-list` | GET | 题目列表（分类→子题两级） |
| `/ctf/exercise` | GET | 题目详情（?exerciseId=xxx） |
| `/ctf/build-exercise-env` | POST | 启动容器（异步，2秒后就绪） |
| `/ctf/recover-exercise-env` | POST | 回收容器 |
| `/answer-panel/answer` | POST | 提交 Flag |
| `/match/notice/match-info` | GET | 竞赛信息 |
| `/answer-panel/overview` | GET | 得分排名 |
| `/match/notice/now-list` | GET | 公告列表 |
| `/match/notice/detail` | GET | 公告详情（?id=xxx） |

## Mock 数据

6 道题目，覆盖 Web / Pwn / Crypto / Reverse / Misc：
- 3 道需要容器环境（Web×2, Pwn×1）
- 1 道已解出（Crypto）
- Flag 格式：`DASCTF{mock_xxx}`

## 认证

所有 `/slab-match/api/v1/agent/*` 接口需要请求头：
```
X-Agent-AccessKey: test-key-123
```

无效或缺失返回 401/403。
