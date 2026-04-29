"""app/config/loader.py — YAML 配置加载器。"""

from pathlib import Path

import yaml


def load_config(config_path: str | None = None) -> dict:
    """加载 YAML 配置文件。

    查找顺序：
    1. 指定的 config_path
    2. 当前目录的 app/config/agent.yaml
    3. 模块目录的 agent.yaml
    """
    if config_path:
        path = Path(config_path)
    else:
        # 尝试当前目录
        path = Path("app/config/agent.yaml")
        if not path.exists():
            # 尝试相对于模块目录
            path = Path(__file__).parent / "agent.yaml"

    if path.exists():
        with open(path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    return {}


def get_provider_config(config: dict, provider_name: str = "") -> dict:
    """提取指定 provider 的配置。"""
    providers = config.get("providers", {})
    name = provider_name or providers.get("default", "openai_compat")
    return providers.get(name, {})
