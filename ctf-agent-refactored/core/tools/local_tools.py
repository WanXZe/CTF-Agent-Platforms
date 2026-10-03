"""Per-challenge workspace tools: bounded file access + command execution.

Execution backend (settings.sandbox_mode):
  * "auto"   -> docker when the configured sandbox image is present locally;
                otherwise fail closed (never silently execute on the VM)
  * "docker" -> always docker (image must exist, e.g. a reused CTF image)
  * "host"   -> run directly on the VM in the challenge directory

Docker backend reuses ONE long-lived container within each solve run,
bind-mounting a fresh per-run challenge copy to /workspace,
so repeated commands are cheap and state (installed files, compiled binaries)
persists between calls.  It is torn down via registry.cleanup().
"""
from __future__ import annotations

import asyncio
from contextlib import ExitStack
import json
import logging
import os
import re
import shutil
import signal
import time
import uuid
from pathlib import Path
from typing import Any, Optional

from core.models import ToolResult
from core.tools.platform_tools import ToolRegistry

logger = logging.getLogger(__name__)

MAX_OUTPUT_CHARS = 12000
MAX_FILE_BYTES = 1_000_000
MAX_COMMAND_TIMEOUT = 300
MAX_JOB_TIMEOUT = 3600
_EXCLUDED_NAMES = {"questioninfo.json", "writeup.md"}
_SAFE_CONTAINER_CHARS = re.compile(r"[^a-zA-Z0-9_.-]+")
# 容器里的 DNS 是坏的（VM 无外网），这些域名统一指到宿主机中继，pip/apt/curl 才能用
RELAYED_HOSTS = (
    "pypi.org",
    "files.pythonhosted.org",
    "archive.ubuntu.com",
    "security.ubuntu.com",
    "github.com",
    "objects.githubusercontent.com",
    "codeload.github.com",
    "registry-1.docker.io",
    "auth.docker.io",
    "production.cloudflare.docker.com",
)


def _truncate(text: str, limit: int = MAX_OUTPUT_CHARS) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n...[truncated {len(text) - limit} chars]"



# ---------- 工作区净化 ----------
# 沙箱挂载的不是原始题目目录，而是"净化副本"：QuestionInfo.json（里面存着
# flag）和 WriteUp.md 一律不拷。否则模型一条 `cat QuestionInfo.json` 或
# `grep -r 0xGame .` 就能把答案抄出来，"解题"也就没意义了。
# 每轮创建独立净化副本；结束后留存用于追溯，但下一轮不会挂载旧副本。
_WORKSPACE_HOME = Path("/tmp/ctf-agent-workspaces")


def stage_workspace(source: Path, slug: str) -> Path:
    """镜像题目目录到沙箱工作区（剔除答案元数据与题解），返回工作区路径。"""
    safe_slug = _SAFE_CONTAINER_CHARS.sub('-', str(slug))[:40].strip('-') or 'workspace'
    dest = _WORKSPACE_HOME / f'{safe_slug}-{uuid.uuid4().hex}'
    ignored = {name.lower() for name in _EXCLUDED_NAMES}

    def _ignore(_dir: str, names: list[str]) -> set[str]:
        return {n for n in names if n.lower() in ignored or n in {".git", "__pycache__"}}

    _WORKSPACE_HOME.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source, dest, ignore=_ignore)
    return dest


class Workspace:
    """Executes commands for one challenge, in docker or on the VM shell."""

    def __init__(self, root: Path, settings: Any) -> None:
        self.root = root
        self.settings = settings
        self.mode_pref = str(getattr(settings, "sandbox_mode", "auto") or "auto").lower()
        self.image = str(getattr(settings, "sandbox_image", "") or "")
        self.dns = str(getattr(settings, "sandbox_dns", "") or "")
        self.memory_limit = str(getattr(settings, "sandbox_memory_limit", "16g") or "16g")
        self.cpu_limit = int(getattr(settings, "sandbox_cpu_limit", 2) or 2)
        self.container = ""
        self.instance_id = uuid.uuid4().hex
        self._backend = ""
        self._lock = asyncio.Lock()
        self._jobs_lock = asyncio.Lock()
        self._jobs: dict[str, dict[str, Any]] = {}
        self._closed = False
        self.audit_context = None

    # ---- backend selection ---------------------------------------------
    @staticmethod
    async def _run(args: list[str], timeout: int = 60, cwd: Optional[Path] = None,
                   output_paths: Optional[tuple[Path, Path]] = None) -> tuple[int, str, str]:
        with ExitStack() as stack:
            handles = [stack.enter_context(path.open("wb")) for path in output_paths] if output_paths else []
            proc = await asyncio.create_subprocess_exec(
                *args, stdout=handles[0] if handles else asyncio.subprocess.PIPE,
                stderr=handles[1] if handles else asyncio.subprocess.PIPE,
                cwd=str(cwd) if cwd else None, start_new_session=os.name == "posix",
            )
            try:
                out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout)
                code = proc.returncode or 0
            except (asyncio.TimeoutError, asyncio.CancelledError) as exc:
                # The shell may already have exited while its children still own
                # stdout. Kill the original process group, not only the parent.
                if os.name == "posix":
                    try:
                        os.killpg(proc.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                elif proc.returncode is None:
                    proc.kill()
                out, err = await proc.communicate()
                if isinstance(exc, asyncio.CancelledError):
                    raise
                code = 124
                err = (err or b"") + f"\ncommand timed out after {timeout}s".encode()
        if output_paths:
            previews = []
            for path in output_paths:
                with path.open("rb") as stream:
                    previews.append(_truncate(stream.read(MAX_OUTPUT_CHARS * 4 + 1).decode("utf-8", "replace")))
            if code == 124:
                previews[1] += f"\ncommand timed out after {timeout}s"
            return code, *previews
        return code, (out or b"").decode("utf-8", "replace"), (err or b"").decode("utf-8", "replace")

    async def _image_present(self) -> bool:
        if not self.image or shutil.which("docker") is None:
            return False
        try:
            code, out, _ = await self._run(["docker", "images", "-q", self.image], timeout=30)
        except Exception as exc:  # docker missing / permission denied
            logger.warning("docker probe failed: %s", exc)
            return False
        return code == 0 and bool(out.strip())

    async def backend(self) -> str:
        if self._backend:
            return self._backend
        if self.mode_pref == "host":
            self._backend = "host"
        elif self.mode_pref == "docker":
            self._backend = "docker"
        elif self.mode_pref == "auto":
            if not await self._image_present():
                raise RuntimeError(f'沙箱镜像 {self.image or "(未配置)"} 不可用：请检查 Docker 权限或构建镜像；不会回退到宿主机执行')
            self._backend = "docker"
        else:
            raise RuntimeError('sandbox.mode 须为 docker、auto 或显式 host')
        return self._backend

    def _audit_system(self, message, **metadata):
        if self.audit_context:
            from core.stats import solve_log
            solve_log.append_log(*self.audit_context, 'system', message, metadata)

    async def prepare(self):
        backend = await self.backend()
        message = (f'继续上轮工作区：{self.root}；保留文件，重建隔离容器' if getattr(self, 'restored_from_previous', False)
                   else f'本轮独立工作区：{self.root}；历史工作区不会挂载到本轮')
        self._audit_system(message,
                           workspace=str(self.root), backend=backend)
        if backend == 'docker':
            await self._ensure_container()
        else:
            self._audit_system('警告：显式 host 模式，命令在 VM 上运行，本轮未启用容器隔离', backend='host')

    # ---- docker backend -------------------------------------------------
    def _container_name(self) -> str:
        slug = _SAFE_CONTAINER_CHARS.sub("-", self.root.name)[:40].strip("-")
        return f"ctf-agent-{self.instance_id[:16]}-{slug[:28] or 'workspace'}"

    async def _ensure_container(self) -> str:
        async with self._lock:
            if self.container:
                return self.container
            name = self._container_name()
            code, out, _ = await self._run(
                ["docker", "ps", "-q", "-f", f"name=^{name}$", "-f", "status=running"], timeout=30
            )
            if code == 0 and out.strip():
                self.container = name
                return name
            await self._run(["docker", "rm", "-f", name], timeout=60)
            args = [
                "docker", "run", "-d", "--name", name,
                "--label", "ctf-agent.managed=true",
                "--label", f"ctf-agent.instance={self.instance_id}",
                "-v", f"{self.root}:/workspace", "-w", "/workspace",
                "--memory", self.memory_limit, "--cpus", str(self.cpu_limit),
            ]
            args += ["--add-host", "host.docker.internal:host-gateway"]
            if self.dns:
                args += ["--dns", self.dns]
                for host in RELAYED_HOSTS:
                    args += ["--add-host", f"{host}:{self.dns}"]
            args += [self.image, "sleep", "infinity"]
            try:
                code, out, err = await self._run(args, timeout=120)
            except asyncio.CancelledError:
                # docker may have created the sandbox before its client was killed.
                self.container = name
                await self._remove_container_unlocked()
                raise
            if code != 0 or not out.strip():
                self.container = name
                await self._remove_container_unlocked()
                raise RuntimeError(f"docker run failed: {_truncate(err or out, 500)}")
            self.container = name
            logger.info("sandbox container %s started (%s)", name, self.image)
            self._audit_system(f'沙箱已启动：Docker / {name} / 镜像 {self.image}',
                               backend='docker', container=name, container_id=out.strip(), image=self.image,
                               workspace=str(self.root))
            return name

    async def _docker_exec(self, command: str, timeout: int,
                           output_paths: Optional[tuple[Path, Path]] = None) -> ToolResult:
        name = await self._ensure_container()
        try:
            code, out, err = await self._run(
                ["docker", "exec", name, "timeout", "--signal=TERM", "--kill-after=2s", f"{timeout}s", "bash", "-lc", command],
                timeout=timeout + 5, output_paths=output_paths,
            )
            if code != 0 and "executable file not found" in err:
                code, out, err = await self._run(["docker", "exec", name, "timeout", "--kill-after=2s", f"{timeout}s", "sh", "-c", command],
                                                timeout=timeout + 5, output_paths=output_paths)
        except asyncio.CancelledError:
            await self._remove_container()
            raise
        data = {"exit_code": code, "stdout": _truncate(out), "stderr": _truncate(err),
                "backend": "docker", "container": name}
        if code in (124, 137):
            await self._remove_container()
            return ToolResult.fail("timeout", f"命令超时（{timeout}s），沙箱已重置", data=data)
        return ToolResult.ok(data=data) if code == 0 else ToolResult.fail("nonzero_exit", _truncate(err or out), data=data)

    # ---- host backend ---------------------------------------------------
    async def _host_run(self, command: str, timeout: int,
                        output_paths: Optional[tuple[Path, Path]] = None) -> ToolResult:
        args = ["/bin/sh", "-c", command] if os.name == "posix" else [os.environ.get("COMSPEC", "cmd.exe"), "/c", command]
        code, out, err = await self._run(args, timeout, self.root, output_paths)
        data = {"exit_code": code, "stdout": _truncate(out),
                "stderr": _truncate(err), "backend": "host",
                "cwd": str(self.root)}
        if code == 124:
            return ToolResult.fail("timeout", f"命令超时（{timeout}s）已终止", data=data)
        return ToolResult.ok(data=data) if code == 0 else ToolResult.fail("nonzero_exit", "", data=data)

    # ---- public API -----------------------------------------------------
    def _archive_output(self, paths):
        if not self.audit_context:
            return
        from core.stats import solve_log
        platform_id, challenge_id = self.audit_context
        for stream_name, path in zip(('stdout', 'stderr'), paths):
            if not path.is_file():
                continue
            with path.open(encoding='utf-8', errors='replace') as stream:
                chunk = 0
                while content := stream.read(16384):
                    chunk += 1
                    solve_log.append_log(platform_id, challenge_id, 'output', content,
                        {'stream': stream_name, 'source_path': str(path.relative_to(self.root)), 'chunk': chunk})

    async def run(self, command: str, timeout: int = 60) -> ToolResult:
        if self._closed:
            return ToolResult.fail("workspace_closed", "本次解题会话已结束")
        command = (command or "").strip()
        if not command:
            return ToolResult.fail("invalid_command", "command 不能为空")
        try:
            timeout = max(5, min(int(timeout or 60), MAX_COMMAND_TIMEOUT))
        except (TypeError, ValueError):
            timeout = 60
        output_dir = self.root / '.agent-command-logs'
        output_dir.mkdir(exist_ok=True)
        command_id = uuid.uuid4().hex
        output_paths = (output_dir / f'{command_id}.stdout.log', output_dir / f'{command_id}.stderr.log')
        try:
            if await self.backend() == "docker":
                result = await self._docker_exec(command, timeout, output_paths)
            else:
                result = await self._host_run(command, timeout, output_paths)
            if isinstance(result.data, dict):
                result.data.update(stdout_path=str(output_paths[0].relative_to(self.root)),
                    stderr_path=str(output_paths[1].relative_to(self.root)), output_saved=bool(self.audit_context))
            return result
        except Exception as exc:
            logger.exception("run_command failed")
            return ToolResult.fail("exec_error", f"{type(exc).__name__}: {exc}")
        finally:
            await asyncio.to_thread(self._archive_output, output_paths)

    async def close(self) -> None:
        self._closed = True
        for job in self._jobs.values():
            task = job["task"]
            if not task.done():
                task.cancel()
        if self._jobs:
            await asyncio.gather(*(job["task"] for job in self._jobs.values()), return_exceptions=True)
        try:
            for job in self._jobs.values():
                if job["task"].cancelled():
                    self._persist_job(job, "cancelled")
        finally:
            await self._remove_container()

    async def _remove_container(self) -> None:
        async with self._lock:
            await self._remove_container_unlocked()

    async def _remove_container_unlocked(self) -> None:
        if not self.container:
            return
        name = self.container
        code, out, err = await self._run(["docker", "rm", "-f", name], timeout=60)
        if code != 0 and "No such container" not in err:
            raise RuntimeError(f"Sandbox cleanup failed: {_truncate(err or out, 500)}")
        self.container = ""
        logger.info("sandbox container %s removed", name)
        self._audit_system(f'沙箱已清理：{name}；本轮工作区文件单独保留', backend='docker', container=name, workspace=str(self.root))

    def _persist_job(self, job: dict[str, Any], status: str, result: Optional[ToolResult] = None) -> None:
        payload = {key: job[key] for key in ("job_id", "timeout", "stdout_path", "stderr_path")}
        payload["result_path"] = job["path"]
        payload["status"] = status
        if result is not None:
            payload["result"] = result.to_dict()
        target = self.root / job["path"]
        temporary = target.with_suffix(".tmp")
        temporary.write_text(json.dumps(payload, ensure_ascii=False, default=str), encoding="utf-8")
        temporary.replace(target)

    async def start_job(self, command: str, timeout: int = 1800) -> ToolResult:
        """Start one long command without tying up the caller. Outputs persist on completion."""
        async with self._jobs_lock:
            return await self._start_job(command, timeout)

    async def _start_job(self, command: str, timeout: int) -> ToolResult:
        if self._closed:
            return ToolResult.fail("workspace_closed", "本次解题会话已结束")
        if not isinstance(command, str) or not command.strip():
            return ToolResult.fail("invalid_command", "command 不能为空")
        if any(not job["task"].done() for job in self._jobs.values()):
            return ToolResult.fail("job_limit", "已有后台任务运行；请先 poll_job 或 cancel_job")
        if len(self._jobs) >= 20:
            return ToolResult.fail("job_limit", "本次会话最多创建 20 个后台任务")
        try:
            seconds = max(1, min(int(timeout), MAX_JOB_TIMEOUT))
        except (TypeError, ValueError):
            return ToolResult.fail("invalid_timeout", "timeout 必须为整数秒")
        job_id = uuid.uuid4().hex[:12]
        folder = self.root / ".agent-jobs"
        folder.mkdir(exist_ok=True)
        result_path = folder / f"{job_id}.json"
        backend = await self.backend()
        if self._closed:
            return ToolResult.fail("workspace_closed", "本次解题会话已结束")
        job = {"job_id": job_id, "started": time.monotonic(), "timeout": seconds,
               "path": str(result_path.relative_to(self.root)),
               "stdout_path": str((folder / f"{job_id}.stdout.log").relative_to(self.root)),
               "stderr_path": str((folder / f"{job_id}.stderr.log").relative_to(self.root))}
        output_paths = (self.root / job["stdout_path"], self.root / job["stderr_path"])
        for path in output_paths:
            path.touch()
        self._persist_job(job, "running")

        async def execute() -> ToolResult:
            try:
                result = await (self._docker_exec(command, seconds, output_paths) if backend == "docker"
                                else self._host_run(command, seconds, output_paths))
                if isinstance(result.data, dict):
                    result.data['output_saved'] = bool(self.audit_context)
            except asyncio.CancelledError:
                self._persist_job(job, "cancelled")
                raise
            except Exception as exc:
                result = ToolResult.fail("exec_error", str(exc))
            finally:
                await asyncio.to_thread(self._archive_output, output_paths)
            self._persist_job(job, "completed" if result.success else "failed", result)
            return result

        job["task"] = asyncio.create_task(execute())
        self._jobs[job_id] = job
        return ToolResult.ok({"job_id": job_id, "status": "running", "timeout": seconds,
                              "result_path": str(result_path.relative_to(self.root)),
                              "stdout_path": job["stdout_path"], "stderr_path": job["stderr_path"],
                              "message": "Use poll_job to wait for output. Jobs are stopped when the solve session ends."})

    async def poll_job(self, job_id: str, wait_seconds: int = 30) -> ToolResult:
        job = self._jobs.get(job_id)
        if job is None:
            return ToolResult.fail("job_not_found", "当前会话没有这个 job_id")
        try:
            wait = max(0, min(int(wait_seconds), 180))
        except (TypeError, ValueError):
            return ToolResult.fail("invalid_wait", "wait_seconds 必须为整数秒")
        task = job["task"]
        if not task.done() and wait:
            await asyncio.wait({task}, timeout=wait)
        data = {"job_id": job_id, "elapsed_seconds": round(time.monotonic() - job["started"], 1),
                "result_path": job["path"], "timeout": job["timeout"],
                "stdout_path": job["stdout_path"], "stderr_path": job["stderr_path"]}
        if task.cancelled():
            return ToolResult.ok({**data, "status": "cancelled"})
        if not task.done():
            return ToolResult.ok({**data, "status": "running"})
        try:
            result = task.result()
        except Exception as exc:
            return ToolResult.fail("job_error", f"后台任务结果保存失败：{exc}", data={**data, "status": "failed"})
        return ToolResult.ok({**data, "status": "completed" if result.success else "failed",
                              "result": result.to_dict()})

    async def cancel_job(self, job_id: str) -> ToolResult:
        job = self._jobs.get(job_id)
        if job is None:
            return ToolResult.fail("job_not_found", "当前会话没有这个 job_id")
        task = job["task"]
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            if task.cancelled():
                # A task cancelled before its first turn cannot run its finally block.
                self._persist_job(job, "cancelled")
        return await self.poll_job(job_id, 0)


def previous_workspace(challenge):
    """Compatibility for pre-upgrade sessions, which did not record workspace paths."""
    local_dir = challenge.raw.get('local_dir')
    if not local_dir:
        return None
    source = Path(local_dir).resolve()
    slug = _SAFE_CONTAINER_CHARS.sub('-', f'{challenge.id}-{source.name}')[:40].strip('-') or 'workspace'
    root = _WORKSPACE_HOME / slug
    return str(root) if root.is_dir() else None


def build_local_tools(challenge, settings: Any, max_bytes: int = 65536, *, resume_root=None) -> ToolRegistry:
    """Read/write/execute tools bound to one challenge directory.

    目录先经 stage_workspace() 净化：沙箱里既没有 QuestionInfo.json 的 flag
    字段，也没有 WriteUp.md，模型只能靠真本事解题。
    """
    source = Path(challenge.raw["local_dir"]).resolve()
    slug = _SAFE_CONTAINER_CHARS.sub("-", f"{challenge.id}-{source.name}")[:40].strip("-")
    if resume_root is not None:
        root = Path(resume_root).resolve()
        home = _WORKSPACE_HOME.resolve()
        if root == home or not root.is_relative_to(home) or not root.is_dir():
            raise ValueError('上一次工作区不存在或路径不安全，请选择重新开始')
    else:
        root = stage_workspace(source, slug or "workspace")
    registry = ToolRegistry()
    workspace = Workspace(root, settings)
    workspace.restored_from_previous = resume_root is not None

    def safe_path(relative: str, *, for_write: bool = False) -> Path:
        path = (root / (relative or "")).resolve()
        if path != root and not path.is_relative_to(root):
            raise ValueError("Path escapes challenge directory")
        if path.name.lower() in _EXCLUDED_NAMES:
            raise ValueError("Answer metadata and writeups are excluded")
        return path

    async def list_files() -> ToolResult:
        files = []
        for path in sorted(root.rglob("*")):
            if len(files) >= 200:
                break
            if path.is_file() and path.name.lower() not in _EXCLUDED_NAMES:
                files.append({"path": str(path.relative_to(root)), "size": path.stat().st_size})
        return ToolResult.ok(files)

    async def read_text(path: str) -> ToolResult:
        try:
            target = safe_path(path)
            if not target.is_file():
                return ToolResult.fail("not_found", "File not found")
            size = target.stat().st_size
            content = target.read_bytes()
            if size > max_bytes:
                preview = content[:max_bytes]
                return ToolResult.ok({
                    "path": path,
                    "truncated": True,
                    "size": size,
                    "content": preview.decode("utf-8", errors="replace"),
                })
            if b"\x00" in content:
                return ToolResult.fail("binary", "Binary file; use run_command (file/xxd/strings/r2) to inspect it")
            return ToolResult.ok({"path": path, "content": content.decode("utf-8", errors="replace")})
        except (OSError, ValueError) as exc:
            return ToolResult.fail("invalid_path", str(exc))

    async def write_file(path: str, content: str) -> ToolResult:
        try:
            target = safe_path(path, for_write=True)
            raw = content.encode("utf-8")
            if len(raw) > MAX_FILE_BYTES:
                return ToolResult.fail("too_large", f"File exceeds {MAX_FILE_BYTES} bytes")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(raw)
            return ToolResult.ok({"path": path, "bytes": len(raw)})
        except (OSError, ValueError) as exc:
            return ToolResult.fail("write_error", str(exc))

    async def run_command(command: str, timeout: int = 60) -> ToolResult:
        return await workspace.run(command, timeout)

    registry.register("list_files", list_files, "List files in this challenge directory")
    registry.register("read_text", read_text, "Read one text file under this challenge (small files only)")
    registry.register("write_file", write_file, "Create/overwrite a text file under this challenge (e.g. solve.py)")
    registry.register("run_command", run_command,
                      "Run a shell command in the challenge workspace (docker sandbox or VM shell) and return stdout/stderr")
    registry.register("start_job", workspace.start_job, "Start a long calculation (up to 3600 seconds); one background job per challenge")
    registry.register("poll_job", workspace.poll_job, "Wait up to 180 seconds for a job; returns status and output when completed")
    registry.register("cancel_job", workspace.cancel_job, "Cancel a background job; Docker sandbox is reset but files are retained")
    registry.cleanup = workspace.close  # type: ignore[attr-defined]
    registry.workspace = workspace
    return registry
