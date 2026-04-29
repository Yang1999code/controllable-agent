"""agent/discovery.py — IDiscovery（V2 预留）。

V1 只定义 ABC 签名，不实现业务逻辑。
"""

from abc import ABC, abstractmethod
from typing import Any


class IDiscovery(ABC):
    """自动发现加载接口（V2 实现）。"""

    @abstractmethod
    async def scan(self, base_path: str) -> list[str]:
        """扫描发现模块路径。"""
        ...

    @abstractmethod
    async def load_module(self, module_path: str) -> Any:
        """加载模块并返回。"""
        ...
