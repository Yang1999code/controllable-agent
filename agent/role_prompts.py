"""agent/role_prompts.py — 角色 prompt 模板加载器。

从 .agent-base/agents/ 目录加载角色 YAML 配置，
解析为 AgentTypeConfig 对象供 AgentRuntime 使用。
"""

import logging
from pathlib import Path

from agent.runtime import AgentTypeConfig

logger = logging.getLogger(__name__)

AGENT_CONFIG_DIR = Path(".agent-base/agents")

# Phase 3 角色列表
MULTI_AGENT_ROLES = ("coordinator", "planner", "coder", "reviewer", "memorizer")

# 角色 max_turns 配置（覆盖 YAML 中的 max_tokens）
ROLE_MAX_TURNS: dict[str, int] = {
    "coordinator": 20,
    "planner": 15,
    "coder": 30,
    "reviewer": 20,
    "memorizer": 10,
}

# 角色 max_tool_calls_per_turn 配置
# coder 需要大量文件操作，给 30；reviewer 需要读+验证，给 20
ROLE_MAX_TOOL_CALLS: dict[str, int] = {
    "coordinator": 15,
    "planner": 20,
    "coder": 30,
    "reviewer": 20,
    "memorizer": 10,
}


def load_role_config(role_name: str, config_dir: Path | str | None = None) -> AgentTypeConfig:
    """加载单个角色的 YAML 配置。

    Args:
        role_name: 角色名称（coordinator/planner/coder/reviewer/memorizer）
        config_dir: 配置目录路径，默认 .agent-base/agents

    Returns:
        AgentTypeConfig 对象

    Raises:
        FileNotFoundError: 配置文件不存在
        ValueError: 角色名称无效
    """
    if role_name not in MULTI_AGENT_ROLES:
        raise ValueError(
            f"Unknown role '{role_name}', must be one of {MULTI_AGENT_ROLES}",
        )

    dir_path = Path(config_dir) if config_dir else AGENT_CONFIG_DIR
    yaml_path = dir_path / f"{role_name}.yaml"

    if not yaml_path.exists():
        raise FileNotFoundError(f"Role config not found: {yaml_path}")

    config = AgentTypeConfig.from_yaml(str(yaml_path))
    logger.info("Loaded role config: %s", role_name)
    return config


def load_all_roles(config_dir: Path | str | None = None) -> dict[str, AgentTypeConfig]:
    """加载所有 Phase 3 角色配置。

    Returns:
        角色 name -> AgentTypeConfig 映射
    """
    configs: dict[str, AgentTypeConfig] = {}
    for role in MULTI_AGENT_ROLES:
        try:
            config = load_role_config(role, config_dir)
            configs[role] = config
        except FileNotFoundError:
            logger.warning("Role config missing: %s", role)
    return configs


def register_roles(runtime, config_dir: Path | str | None = None) -> list[str]:
    """将所有 Phase 3 角色注册到 AgentRuntime。

    Args:
        runtime: AgentRuntime 实例
        config_dir: 配置目录路径

    Returns:
        成功注册的角色名列表
    """
    configs = load_all_roles(config_dir)
    registered: list[str] = []
    for name, config in configs.items():
        try:
            runtime.register_agent_type(config)
            registered.append(name)
            logger.info("Registered role: %s", name)
        except ValueError:
            logger.warning("Role already registered: %s", name)
    return registered
