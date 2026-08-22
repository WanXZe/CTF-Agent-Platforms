"""
平台 Skill 注册中心。

自动扫描包内 platforms/ 目录下的所有平台文件夹，加载每个平台的 skill.py 和 config.yaml。
每个平台文件夹 = 一个独立的"对话"，可被前端切换选择。

目录约定（包内）：
  ctf_platform_skill/
    platforms/
      slab-match/
        skill.py       # 平台能力（curl 形式）
        config.yaml    # 平台级配置（优先级最高，必须存在）
        README.md      # 平台说明
      mock-local/
        ...
      ctfd/
        ...

配置优先级（LLM 调用时）：
  平台同级 config.yaml > Agent 全局 config.yaml > .env 环境变量

加载规则：
  平台文件夹必须同时存在 skill.py + config.yaml 才会被扫描加载。
"""
from __future__ import annotations

import importlib.util
import logging
import sys
from pathlib import Path
from typing import Any, Optional

import yaml

logger = logging.getLogger(__name__)

# 平台目录：registry.py 所在目录的 platforms/ 子目录（包内）
PLATFORMS_DIR = Path(__file__).resolve().parent / "platforms"


class PlatformSkill:
    """
    已加载的平台 Skill 包装。
    持有 skill 模块、配置、元信息。
    """

    def __init__(self, name: str, skill_module: Any, config: dict[str, Any], folder: Path):
        self.name = name
        self.module = skill_module
        self.config = config
        self.folder = folder
        self.platform_root = str(folder)
        self.meta = config.get("meta", {})
        self.display_name = self.meta.get("name", name)
        self.icon = self.meta.get("icon", "📦")
        self.description = self.meta.get("description", "")
        self.platform_type = self.meta.get("type", name)

    def has_method(self, method_name: str) -> bool:
        """检查平台是否支持某个方法。"""
        return hasattr(self.module, method_name) and callable(getattr(self.module, method_name))

    def list_functions(self) -> list[str]:
        """列出平台 skill 暴露的所有可调用函数名。"""
        return [
            name for name in dir(self.module)
            if not name.startswith("_")
            and callable(getattr(self.module, name))
        ]

    def call(self, method_name: str, *args, **kwargs) -> dict[str, Any]:
        """调用平台方法。"""
        if not self.has_method(method_name):
            return {
                "success": False,
                "error": "not_supported",
                "message": f"平台 {self.display_name} 不支持 {method_name}",
                "data": None,
            }
        return getattr(self.module, method_name)(*args, **kwargs)

    def __repr__(self) -> str:
        return f"<PlatformSkill {self.name} ({self.display_name})>"


class PlatformRegistry:
    """
    平台 Skill 注册中心。
    单例模式，自动扫描并加载所有平台。
    """

    _instance: Optional["PlatformRegistry"] = None

    def __init__(self):
        self._platforms: dict[str, PlatformSkill] = {}
        self._scan()

    @classmethod
    def get_instance(cls) -> "PlatformRegistry":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def _scan(self) -> None:
        """扫描 platforms/ 目录，加载所有平台。必须同时存在 skill.py + config.yaml。"""
        if not PLATFORMS_DIR.exists():
            logger.warning("平台目录不存在: %s", PLATFORMS_DIR)
            return
        for folder in sorted(PLATFORMS_DIR.iterdir()):
            if not folder.is_dir():
                continue
            skill_file = folder / "skill.py"
            config_file = folder / "config.yaml"
            if not skill_file.exists():
                logger.debug("跳过平台 %s: 缺少 skill.py", folder.name)
                continue
            if not config_file.exists():
                logger.warning("跳过平台 %s: 缺少 config.yaml", folder.name)
                continue
            try:
                self._load_platform(folder.name, folder, skill_file, config_file)
            except Exception as e:
                logger.error("加载平台 %s 失败: %s", folder.name, e)

    def _load_platform(
        self, name: str, folder: Path, skill_file: Path, config_file: Path
    ) -> None:
        """加载单个平台。"""
        config: dict[str, Any] = {}
        with open(config_file, encoding="utf-8") as f:
            config = yaml.safe_load(f) or {}

        module_name = f"platform_skill_{name.replace('-', '_')}"
        spec = importlib.util.spec_from_file_location(module_name, str(skill_file))
        if spec is None or spec.loader is None:
            raise ImportError(f"无法加载 {skill_file}")
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)

        skill = PlatformSkill(name=name, skill_module=module, config=config, folder=folder)
        self._platforms[name] = skill
        logger.info("已加载平台: %s (%s)", name, skill.display_name)

    # ── 查询方法 ──

    def list_all_platform_ids(self) -> list[str]:
        """列出所有已加载平台的 ID 列表。"""
        return list(self._platforms.keys())

    def list_platforms(self) -> list[dict[str, Any]]:
        """列出所有可用平台（供前端展示，返回完整信息）。"""
        return [
            {
                "id": name,
                "name": skill.display_name,
                "icon": skill.icon,
                "description": skill.description,
                "type": skill.platform_type,
            }
            for name, skill in self._platforms.items()
        ]

    def load_platform(self, platform_id: str) -> Optional[PlatformSkill]:
        """按 ID 加载/获取平台 Skill（get 的别名，语义更清晰）。"""
        return self._platforms.get(platform_id)

    def get(self, name: str) -> Optional[PlatformSkill]:
        """按名称获取平台 Skill。"""
        return self._platforms.get(name)

    def get_or_default(self, name: Optional[str] = None) -> PlatformSkill:
        """获取指定平台，不存在则返回第一个。"""
        if name and name in self._platforms:
            return self._platforms[name]
        if self._platforms:
            return next(iter(self._platforms.values()))
        raise RuntimeError("没有可用的平台 Skill")

    def reload(self) -> None:
        """重新扫描加载所有平台。"""
        self._platforms.clear()
        self._scan()

    def get_config_path(self, platform_id: str) -> Optional[Path]:
        """获取平台 config.yaml 的路径。"""
        skill = self._platforms.get(platform_id)
        if skill is None:
            return None
        return skill.folder / "config.yaml"

    def get_platform_config(self, platform_id: str) -> Optional[dict[str, Any]]:
        """读取平台 config.yaml 完整内容（前端展示和编辑用）。"""
        path = self.get_config_path(platform_id)
        if path is None or not path.exists():
            return None
        with open(path, encoding="utf-8") as f:
            return yaml.safe_load(f) or {}

    def save_platform_config(self, platform_id: str, config: dict[str, Any]) -> bool:
        """保存平台 config.yaml（前端修改 auto_solve 等设置后调用）。"""
        path = self.get_config_path(platform_id)
        if path is None:
            return False
        with open(path, "w", encoding="utf-8") as f:
            yaml.dump(config, f, allow_unicode=True, default_flow_style=False, sort_keys=False)
        # 重新加载该平台
        if platform_id in self._platforms:
            del self._platforms[platform_id]
        skill_file = path.parent / "skill.py"
        if skill_file.exists():
            try:
                self._load_platform(platform_id, path.parent, skill_file, path)
            except Exception as e:
                logger.error("重新加载平台 %s 失败: %s", platform_id, e)
        return True


# ── 便捷函数 ──

def get_registry() -> PlatformRegistry:
    return PlatformRegistry.get_instance()


def get_platform(name: str) -> Optional[PlatformSkill]:
    return get_registry().get(name)


def list_platforms() -> list[dict[str, Any]]:
    return get_registry().list_platforms()


def list_all_platform_ids() -> list[str]:
    return get_registry().list_all_platform_ids()
