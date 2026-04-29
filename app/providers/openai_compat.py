"""app/providers/openai_compat.py — OpenAI 兼容提供商工厂。

从 ai/provider.py 重新导出，提供便捷的工厂函数。
"""

from ai.provider import OpenAICompatibleProvider

# 直接使用 ai/provider.py 中的实现
__all__ = ["OpenAICompatibleProvider"]
