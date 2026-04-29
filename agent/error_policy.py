"""agent/error_policy.py — IToolErrorPolicy（V2 预留）。

V1 只定义 ABC 签名，不实现业务逻辑。
"""

from abc import ABC, abstractmethod


class IToolErrorPolicy(ABC):
    """工具异常处理策略接口（V2 实现）。"""

    @abstractmethod
    def should_retry(self, tool_name: str, error: Exception, attempt: int) -> bool:
        """判断是否应该重试。"""
        ...

    @abstractmethod
    def get_retry_delay(self, tool_name: str, attempt: int) -> float:
        """获取重试延迟秒数。"""
        ...

    @abstractmethod
    def on_permanent_failure(self, tool_name: str, error: Exception) -> None:
        """永久失败回调。"""
        ...
