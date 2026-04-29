"""agent/step_outcome.py — 控制流原语。

循环中每一步的返回控制——继续/退出/注入新 prompt。避免复杂的枚举状态机。

参考：GenericAgent StepOutcome (agent_loop.py:5-7) / Pi Agent tool result → next_prompt
"""

from dataclasses import dataclass, field
from typing import Any


@dataclass
class StepOutcome:
    """工具执行后的控制原语。

    参考 GenericAgent 的三字段设计：
    - data: 工具返回数据
    - next_prompt: 下一轮的 user prompt（None = 无后续）
    - should_exit: 是否终止循环

    扩展版增加 steer 和 error 字段。
    """

    data: Any = None
    next_prompt: str | None = None
    should_exit: bool = False
    exit_reason: str = ""
    steer_message: str | None = None
    error: str | None = None

    @classmethod
    def done(cls, data: Any = None) -> "StepOutcome":
        """任务正常完成。"""
        return cls(data=data, should_exit=True, exit_reason="completed")

    @classmethod
    def continue_(cls, next_prompt: str) -> "StepOutcome":
        """继续执行，指定下一轮 prompt。"""
        return cls(next_prompt=next_prompt)

    @classmethod
    def steer(cls, message: str) -> "StepOutcome":
        """注入一条 steer 消息。"""
        return cls(steer_message=message)

    @classmethod
    def error(cls, message: str, data: Any = None) -> "StepOutcome":
        """工具执行出错，但循环继续。"""
        return cls(data=data, error=message)
