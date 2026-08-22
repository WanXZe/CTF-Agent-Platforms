"""
CTF 靶场平台 Skill 插件包。

封装 CTF 比赛靶场平台（西湖论剑 slab-match 等）的全部 REST 接口，
作为独立 Skill 插件供 CTF-Agent 主项目 import 调用。

快速开始：
    from ctf_platform_skill import PlatformRegistry, list_all_platform_ids

    reg = PlatformRegistry()
    ids = reg.list_all_platform_ids()
    platform = reg.load_platform("mock-local")
    result = platform.call("list_challenges")
"""
from .ctf_platform_skill import (
    CTFPlatformSkill,
    CONTAINER_STATUS,
    configure,
    get_skill,
    list_challenges,
    get_challenge_detail,
    download_attachment,
    start_container,
    stop_container,
    get_container_status,
    submit_flag,
    get_match_info,
    get_overview,
    list_notices,
    get_notice_detail,
)
from .registry import (
    PlatformRegistry,
    PlatformSkill,
    get_registry,
    get_platform,
    list_platforms,
    list_all_platform_ids,
)

__all__ = [
    "CTFPlatformSkill",
    "CONTAINER_STATUS",
    "configure",
    "get_skill",
    "list_challenges",
    "get_challenge_detail",
    "download_attachment",
    "start_container",
    "stop_container",
    "get_container_status",
    "submit_flag",
    "get_match_info",
    "get_overview",
    "list_notices",
    "get_notice_detail",
    "PlatformRegistry",
    "PlatformSkill",
    "get_registry",
    "get_platform",
    "list_platforms",
    "list_all_platform_ids",
]

__version__ = "1.0.0"
