"""agent/marketplace.py — IPluginMarketplace（V3 预留）。

V1 只定义 ABC 签名，不实现业务逻辑。
"""

from abc import ABC, abstractmethod


class IPluginMarketplace(ABC):
    """插件市场接口（V3 实现）。"""

    @abstractmethod
    async def search(self, query: str, top_k: int = 10) -> list[dict]:
        """搜索插件。"""
        ...

    @abstractmethod
    async def install(self, plugin_id: str) -> bool:
        """安装插件。"""
        ...

    @abstractmethod
    async def uninstall(self, plugin_id: str) -> bool:
        """卸载插件。"""
        ...

    @abstractmethod
    async def list_installed(self) -> list[dict]:
        """列出已安装插件。"""
        ...
