"""agent/config_validator.py — 配置校验模块。

确保配置文件格式正确，提供清晰的错误信息。
"""

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)

# 允许的 provider 类型
ALLOWED_PROVIDERS = {"openai_compat", "anthropic"}

# 必需的 provider 字段
REQUIRED_PROVIDER_FIELDS = {"model"}

# 可选但推荐的 provider 字段
RECOMMENDED_PROVIDER_FIELDS = {"base_url", "api_key_env", "api_key"}

# 允许的 Agent 角色
ALLOWED_ROLES = {"coordinator", "planner", "coder", "reviewer", "memorizer"}


@dataclass
class ValidationResult:
    """配置校验结果。"""
    valid: bool
    errors: list[str]
    warnings: list[str]

    def add_error(self, msg: str) -> None:
        self.errors.append(msg)
        self.valid = False

    def add_warning(self, msg: str) -> None:
        self.warnings.append(msg)


def validate_config(config: dict[str, Any]) -> ValidationResult:
    """校验配置字典。

    Args:
        config: 从 YAML 加载的配置字典

    Returns:
        ValidationResult 包含错误和警告列表
    """
    result = ValidationResult(valid=True, errors=[], warnings=[])

    # 检查 providers
    providers = config.get("providers")
    if not providers:
        result.add_error("缺少 'providers' 配置")
        return result

    # 检查默认 provider
    default = providers.get("default")
    if not default:
        result.add_error("缺少 'providers.default' 配置")
    elif default not in ALLOWED_PROVIDERS:
        result.add_error(f"不支持的 provider '{default}', 可选: {ALLOWED_PROVIDERS}")

    # 校验每个 provider
    provider_configs = {
        k: v for k, v in providers.items()
        if k != "default" and isinstance(v, dict)
    }

    if not provider_configs:
        result.add_error("没有配置任何 provider")

    for name, cfg in provider_configs.items():
        _validate_provider(result, name, cfg)

    # 检查 agent 配置
    agent = config.get("agent", {})
    _validate_agent(result, agent)

    # 检查 runtime 配置
    runtime = config.get("runtime", {})
    _validate_runtime(result, runtime)

    return result


def _validate_provider(result: ValidationResult, name: str, cfg: dict) -> None:
    """校验单个 provider 配置。"""
    # 必需字段
    if "model" not in cfg:
        result.add_error(f"Provider '{name}': 缺少必需字段 'model'")

    # 推荐字段
    has_key = "api_key" in cfg or "api_key_env" in cfg
    if not has_key:
        result.add_warning(
            f"Provider '{name}': 缺少 API Key 配置 "
            f"(建议使用 'api_key_env' 从环境变量读取)"
        )

    # OpenAI 兼容需要 base_url
    if name == "openai_compat" and "base_url" not in cfg:
        result.add_warning(
            f"Provider '{name}': 缺少 'base_url'，将使用默认值"
        )


def _validate_agent(result: ValidationResult, agent: dict) -> None:
    """校验 agent 配置。"""
    max_turns = agent.get("max_turns", 0)
    if max_turns <= 0:
        result.add_warning(f"max_turns={max_turns} 不合理，建议 >= 1")

    max_tool = agent.get("max_tool_calls_per_turn", 0)
    if max_tool <= 0:
        result.add_warning(f"max_tool_calls_per_turn={max_tool} 不合理，建议 >= 1")

    max_ctx = agent.get("max_context_tokens", 0)
    if max_ctx > 0 and max_ctx < 4096:
        result.add_warning(f"max_context_tokens={max_ctx} 过小，可能影响功能")


def _validate_runtime(result: ValidationResult, runtime: dict) -> None:
    """校验 runtime 配置。"""
    max_concurrent = runtime.get("max_concurrent_children", 0)
    if max_concurrent <= 0:
        result.add_warning(f"max_concurrent_children={max_concurrent} 不合理，建议 >= 1")

    max_depth = runtime.get("max_depth", 0)
    if max_depth <= 0:
        result.add_warning(f"max_depth={max_depth} 不合理，建议 >= 1")

    timeout = runtime.get("default_timeout_sec", 0)
    if timeout <= 0:
        result.add_warning(f"default_timeout_sec={timeout} 不合理，建议 >= 30")


def validate_config_file(path: str | Path | None = None) -> ValidationResult:
    """从文件加载并校验配置。

    Args:
        path: 配置文件路径，None 时使用默认路径

    Returns:
        ValidationResult
    """
    if path:
        path = Path(path)
    else:
        path = Path("app/config/agent.yaml")

    if not path.exists():
        return ValidationResult(
            valid=False,
            errors=[f"配置文件不存在: {path}"],
            warnings=[],
        )

    try:
        content = path.read_text(encoding="utf-8")
        config = yaml.safe_load(content)
    except yaml.YAMLError as e:
        return ValidationResult(
            valid=False,
            errors=[f"YAML 解析错误: {e}"],
            warnings=[],
        )

    if not isinstance(config, dict):
        return ValidationResult(
            valid=False,
            errors=["配置文件必须是 YAML 字典"],
            warnings=[],
        )

    return validate_config(config)


def load_validated_config(path: str | Path | None = None) -> tuple[dict, ValidationResult]:
    """加载并校验配置。

    Returns:
        (config, validation_result) 元组
    """
    if path:
        path = Path(path)
    else:
        path = Path("app/config/agent.yaml")

    if not path.exists():
        config = {}
    else:
        try:
            content = path.read_text(encoding="utf-8")
            config = yaml.safe_load(content) or {}
        except yaml.YAMLError as e:
            return {}, ValidationResult(
                valid=False,
                errors=[f"YAML 解析错误: {e}"],
                warnings=[],
            )

    result = validate_config(config)
    return config, result
