"""agent/meta_agent.py — IMetaAgent（V4 预留）。

V1 只定义 ABC 签名，不实现业务逻辑。
"""

from abc import ABC, abstractmethod


class IMetaAgent(ABC):
    """自我优化/元 Agent 接口（V4 实现）。"""

    @abstractmethod
    async def analyze_performance(self, history: list) -> dict:
        """分析性能数据。"""
        ...

    @abstractmethod
    async def propose_improvements(self) -> list[str]:
        """提议改进方案。"""
        ...

    @abstractmethod
    async def apply_improvement(self, proposal_id: str) -> bool:
        """应用改进提案。"""
        ...
