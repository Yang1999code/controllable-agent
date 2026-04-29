"""app/providers/ — 模型提供商适配器。"""

from ai.provider import (
    AnthropicProvider,
    IModelProvider,
    OpenAICompatibleProvider,
)


def create_provider(provider_type: str = "openai_compat", **kwargs) -> IModelProvider:
    """工厂函数：根据类型创建模型提供商。"""
    if provider_type == "openai_compat":
        return OpenAICompatibleProvider(**kwargs)
    elif provider_type == "anthropic":
        return AnthropicProvider(**kwargs)
    else:
        raise ValueError(f"Unknown provider type: {provider_type}")
