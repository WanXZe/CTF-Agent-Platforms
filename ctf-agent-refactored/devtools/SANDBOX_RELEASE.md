# CTF Sandbox：镜像与构建源码

发布页：https://github.com/WanXZe/CTF-Agent-Platforms/releases/tag/sandbox-re-tools-20261003

本发布打包的是 Docker 镜像，不是运行中的题目容器；不包含题库、模型密钥、Agent 运行数据或工作区。平台及沙箱构建代码在同一 GitHub 仓库中。

## 文件

- `ctf-sandbox-image-linux-amd64.tar.gz`：可直接 `docker load` 的完整镜像，包含 UPX、Unipacker、Ghidra、radare2、JADX、apktool 和现有逆向、密码学、取证工具。
- `ctf-sandbox-build-kit.tar.gz`：Dockerfile、构建脚本、Python 依赖列表、实际安装版本清单、Ghidra 辅助源码、冒烟测试、四个固定版本的官方安装包，以及 `wanxze/nc:latest` 基础镜像的导出包。
- `release-manifest.json`：镜像 ID、平台架构、代码提交、安装包来源、文件大小和校验和。
- `SHA256SUMS`：下载文件的 SHA256 校验清单。

Linux / amd64。Windows、macOS 上需要能运行 Linux 容器的 Docker 环境；ARM 主机需要 amd64 模拟环境。第三方工具保留各自许可，来源和版本见安装资源 manifest 及镜像内工具清单。

## 导入现成镜像

把发布文件下载到同一个目录，然后：

```bash
sha256sum -c SHA256SUMS
# 若镜像被分卷，先合并；未分卷时无需执行：
# cat ctf-sandbox-image-linux-amd64.tar.gz.part* > ctf-sandbox-image-linux-amd64.tar.gz
docker load -i ctf-sandbox-image-linux-amd64.tar.gz
docker image inspect ctf-sandbox:latest --format '{{.Id}}'
docker run --rm --network=none --memory=8g --cpus=2 ctf-sandbox:latest python3 /opt/ctf-tools/smoke.py
```

预期镜像 ID：`sha256:1f18183141d2f79e91e317597ecebbbcf1952688b14447a51075bfe22b9fd574`。

应用的 `config.yaml` 设置 `sandbox.image: ctf-sandbox:latest` 即可使用；无需重新运行应用构建。

## 从构建源码重新构建

```bash
tar -xzf ctf-sandbox-build-kit.tar.gz
cd ctf-sandbox-build-kit
docker load -i base-image-linux-amd64.tar.gz
bash devtools/build_sandbox.sh
```

构建套件带有基础镜像和四个固定安装包；APT 和 pip 安装步骤仍需网络连接，并非完全离线构建。Python 与 APT 的实际版本清单随包提供。基础镜像按 manifest 中的 ID 固定，重新安装网络依赖得到的新镜像 ID 不保证与发布镜像相同。

如果只克隆仓库，可以先运行下面的脚本下载并核验官方安装包，再构建；基础镜像可以从 build kit 导入。

```bash
python3 devtools/download_sandbox_assets.py
# 需要代理时：
# python3 devtools/download_sandbox_assets.py --proxy socks5h://127.0.0.1:10808
# SANDBOX_BUILD_PROXY=http://your-proxy:port bash devtools/build_sandbox.sh
```

## 自己打包

在应用项目目录运行：

```bash
python3 devtools/package_sandbox.py --output "$HOME/ctf-sandbox-release" --verify-import
```

脚本验证安装包、导出镜像、生成构建套件与 SHA256 清单。输出建议放在 Git 仓库外，体积较大的镜像与安装包通过 GitHub Release 分发，不写入 Git 历史。
