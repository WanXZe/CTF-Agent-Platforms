# CTF Agent

一个用于 CTF 题目研究的内网工作台：管理比赛平台与模型连接，让 Agent 在沙箱中分析题目，保存完整分析过程和模型调用记录。

## 快速启动

本项目与相邻的 `ctf-platform-skill` 一起使用。需要 Python 3.11 或更高版本；本地沙箱需要 Docker 和已构建的 `ctf-sandbox:latest` 镜像。

```bash
cd ctf-agent-refactored
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e . -e ../ctf-platform-skill
cp config.example.yaml config.yaml
cp .env.example .env
# 编辑 config.yaml 中的题库目录、模型连接、沙箱配置；在 .env 中填写密钥。
./run.sh start
```

访问 `http://<服务器内网地址>:12346/`。`./run.sh status` 查看状态，`./run.sh restart` 重启，`./run.sh log 100` 查看服务日志。重启服务会中断正在运行的任务；上下文不跨服务重启保存。

## 网页操作

- **赛题库**：切换平台，搜索、按方向及状态筛选题目。题目工作台提供描述、附件、题目环境、模型连接检查、开始分析、暂停、继续、取消、手动提交和日志下载。
- **方向与模型**：分别给 Web、Pwn、Reverse、Crypto、Misc、Osint、AI 等方向选择默认模型。配置适用于所有平台；同方向的题目共享默认模型。选择“跟随全局”恢复全局默认。
- **模型连接**：添加模型或点击已有连接的“编辑连接”。可反复修改模型名称、提供商、接口地址和密钥环境变量名；重命名会同步更新方向及全局默认引用。正在运行的任务继续使用启动时的配置。
- **调用用量**：查看全局累计调用和当前平台的逐题用量。

模型选择优先级：**本轮明确选择 > 方向默认 > 全局默认**。旧的逐题默认配置已经停用。密钥值从服务端 `.env` 读取，不通过模型配置 API 返回。

前端从零编写，入口为 `frontend/index.html`，仅加载 `workspace.js`、`workspace.css` 和 `mark.svg`，无旧前端依赖，无需 Node 或前端构建步骤。

## 完整日志

每道题的日志按平台、题目 ID 分目录追加保存：

```text
data/challenge_logs/p_<平台>/c_<题目>/solve.jsonl
data/challenge_logs/p_<平台>/c_<题目>/agent.jsonl
```

`solve.jsonl` 保存各轮分析、命令及完整命令输出；`agent.jsonl` 保存模型请求、响应、推理内容、用量、工具调用、错误、取消及耗时，使用 `run_id` / `call_id` 关联。认证请求头不归档。

页面默认只显示最后 200 条，可分别下载两种完整 JSONL。“清空显示”保留日志文件。重新开始同题会追加新的轮次，不删除历史。旧版本已经丢弃的日志无法恢复，尚存的旧日志会自动迁移。

## API

服务自带 `/docs`。题目及方向列表通过 `X-Platform-Id` 请求头选择平台。

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| GET | `/api/challenges` | 题目列表，含方向默认模型 |
| GET | `/api/category-models` | 方向及其有效默认模型 |
| PUT | `/api/category-models/{方向}` | `{"model":"模型名称"}`，null 恢复全局 |
| GET / POST | `/api/models` | 列出 / 添加模型连接 |
| PUT / DELETE | `/api/models/{模型名称}` | 修改 / 删除已有模型连接 |
| POST | `/api/models/check` | 只读连接检查，传 challenge_id 和可选 model |
| POST | `/api/solve/{平台}/{题目}` | 开始分析，传 challenge_id 和可选 model |
| POST | `/api/solve/{平台}/{题目}/{pause,resume,cancel}` | 控制解题 |
| GET | `/api/solve-log/{平台}/{题目}?kind=solve&limit=200` | 日志预览，kind 可选 agent |
| GET | `/api/solve-log/{平台}/{题目}/download?kind=agent` | 完整日志下载 |
| DELETE | `/api/solve-log/{平台}/{题目}` | 清空显示窗口 |

本地题库只读取题目资料，使用净化副本作为工作区；本地平台没有判题及靶机管理接口。连接比赛平台后，相关操作由适配器提供。Agent 自动提交默认关闭。

## 沙箱与测试

工具清单见 [SANDBOX_TOOLS.md](SANDBOX_TOOLS.md)。现有镜像包含 UPX、Unipacker、Ghidra、radare2、JADX、apktool、Python 逆向与取证工具。构建入口为 `devtools/build_sandbox.sh` 和 `devtools/sandbox.Dockerfile`；需要基础镜像 `wanxze/nc:latest` 及 `devtools/sandbox-assets/manifest.json` 中的安装资源。大体积安装包作为本地构建缓存，不上传 Git。构建脚本先验证候选镜像，再更新 latest。

```bash
PYTHONPATH=.:../ctf-platform-skill python3 -m unittest discover -s tests -p 'test_*.py' -v
```

测试使用临时数据和模型替身，不调用付费模型。覆盖方向默认选择、已有模型修改、配置快照、日志迁移与完整输出，以及任务暂停、取消、后台任务和平台注册。

## 本地文件

`config.yaml`、`.env`、题库附件、运行日志、虚拟环境、构建缓存及备份均保留在部署环境中，不提交到仓库。仓库提供 `config.example.yaml` 和 `.env.example` 作为配置模板。


## 沙箱镜像与完整构建包

[下载容器镜像和构建包](https://github.com/WanXZe/CTF-Agent-Platforms/releases/tag/sandbox-re-tools-20261003)。

构建源码位于 `devtools/sandbox.Dockerfile`，导入、重建与校验步骤见 [SANDBOX_RELEASE.md](devtools/SANDBOX_RELEASE.md)。构建包包含工具安装资源及基础镜像；不包含应用密钥或题目数据。

## 每轮预算、重新开始与继续

题目详情的“Agent 解题”区域可设置本轮 Token 预算（累计输入 + 输出，1,000–10,000,000，默认来自服务端配置）。只改变本轮快照，不修改全局或方向默认配置；继续时也可重新选择模型和预算。

- **重新开始**：创建全新的净化工作区和 Docker 容器，不继承旧文件或旧模型上下文。当前做题日志与 Agent 日志重新记录，旧文件移入该题日志目录的 `archives/`，完整日志下载仍包含所有归档。
- **接着上次**：恢复上一轮工作区文件，把历史做题日志整理为有界上下文发送给新一轮 LLM，日志追加。最多使用近期 160 条、48,000 字符，每条长输出限制 3,000 字符并明确标记缩略；完整内容始终保留在下载日志中。
- 上一轮结束后会清理容器，因此继续模式会重建隔离容器并挂载原工作区；不会恢复上一轮容器根文件系统中的临时改动或临时安装包。正在运行时的“暂停/继续”保留的是同一轮上下文。
- 每次容器启动与清理会写明后端、唯一容器名称、镜像和工作区。`auto` 模式缺少 Docker/镜像时直接失败，不再静默回退到 VM shell。显式 `host` 模式会有明确告警。

## 模型连接与代理

模型 HTTP 客户端不继承系统代理。公网模型需要代理时，可在 `config.yaml` 的 `llm` 节配置 `proxy_url: socks5h://<代理主机>:<端口>`，或在不提交的 `.env` 中设置 `LLM_PROXY_URL`。环境变量优先。SOCKS 支持通过项目依赖 `httpx[socks]` 安装；也可运行 `python3 -m pip install 'httpx[socks]>=0.28.1'`。

`socks5h` 经代理解析公网模型域名，避免 VM 的旧 `/etc/hosts` 中继映射造成直连失败；TLS 校验保持开启。回环和私有 IP、`localhost`、`.local` 地址自动绕过代理。

`http://127.0.0.1:11434/v1` 指 **Agent 后端所在机器** 的 Ollama，不是浏览器所在机器。若模型运行在宿主机，请先确认 Ollama 已启动并监听 VM 可达的地址，再在模型页面修改接口地址。模型页面的“检查连接”只读取模型列表，不消耗生成额度。
