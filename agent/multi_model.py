"""agent/multi_model.py — IMultiModelOrchestrator（V3 预留）。

V1 只定义 ABC 签名，不实现业务逻辑。
"""

from abc import ABC, abstractmethod
from typing import AsyncIterator


class IMultiModelOrchestrator(ABC):
    """多模型协同编排接口（V3 实现）。"""

    @abstractmethod
    async def select_model(self, task_type: str, context: dict) -> str:
        """根据任务类型选择模型。"""
        ...

    @abstractmethod
    async def route(self, messages: list, tools: list) -> AsyncIterator:
        """路由到对应的模型提供商。"""
        ...
