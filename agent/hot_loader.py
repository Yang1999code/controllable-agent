"""agent/hot_loader.py — IHotLoader（V2 预留）。

V1 只定义 ABC 签名，不实现业务逻辑。
"""

from abc import ABC, abstractmethod
from typing import Callable


class IHotLoader(ABC):
    """运行时热加载接口（V2 实现）。"""

    @abstractmethod
    async def watch(self, path: str, callback: Callable) -> None:
        """监听文件变更。"""
        ...

    @abstractmethod
    async def unwatch(self, path: str) -> None:
        """停止监听。"""
        ...

    @abstractmethod
    async def reload(self, module_name: str) -> None:
        """重新加载模块。"""
        ...
