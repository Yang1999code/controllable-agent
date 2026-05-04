"""agent/memory/task_detector.py — 任务单元完成判断。

启发式规则判断当前对话是否完成了一个"任务单元"。
Phase 2 不调 LLM，纯规则判断；Phase 3 可替换为 LLM 判断。
"""

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class TaskDetection:
    """任务检测结果。"""

    is_complete: bool
    confidence: float
    reason: str


# 任务完成的正面信号（用户说的）
_DONE_PATTERNS = [
    re.compile(r"(好的|谢谢|感谢|完美|没问题|可以了|就这样|行了|搞定|完成|done|ok|thanks)", re.IGNORECASE),
]

# 意图切换信号（开始新话题 = 旧任务结束）
_TOPIC_SWITCH_PATTERNS = [
    re.compile(r"(接下来|换个话题|另外|还有一件事|对了|顺便|现在帮我|下一个|接下来请)", re.IGNORECASE),
]

# 任务未完成的信号（用户还在追问）
_CONTINUE_PATTERNS = [
    re.compile(r"(继续|还有|不对|错了|再试|不是这个|改一下|重写|换一种|详细点|展开)", re.IGNORECASE),
]


class TaskDetector:
    """任务单元完成判断器。

    启发式规则（Phase 2），不调 LLM。
    规则优先级：未完成信号 > 完成信号 > 切换信号 > 默认。
    """

    def detect(self, user_input: str, turn_count: int, had_tool_calls: bool) -> TaskDetection:
        """判断当前轮次是否标志着任务单元完成。

        参数:
            user_input: 用户最新输入
            turn_count: 当前轮次
            had_tool_calls: 本轮是否有工具调用
        返回:
            TaskDetection（is_complete, confidence, reason）
        """
        text = user_input.strip()

        if not text:
            return TaskDetection(is_complete=False, confidence=0.3, reason="empty_input")

        # 1. 未完成信号（最高优先级）
        for pat in _CONTINUE_PATTERNS:
            if pat.search(text):
                return TaskDetection(is_complete=False, confidence=0.85, reason="continue_signal")

        # 2. 意图切换 = 旧任务完成
        for pat in _TOPIC_SWITCH_PATTERNS:
            if pat.search(text):
                return TaskDetection(is_complete=True, confidence=0.8, reason="topic_switch")

        # 3. 完成确认信号
        for pat in _DONE_PATTERNS:
            if pat.search(text):
                return TaskDetection(is_complete=True, confidence=0.9, reason="done_signal")

        # 4. 短回复（<=10字）+ 无工具调用 = 可能在确认/结束
        if len(text) <= 10 and not had_tool_calls:
            return TaskDetection(is_complete=True, confidence=0.6, reason="short_reply_no_tools")

        # 5. 多轮工具调用后任务完成：3+ 轮工具调用后模型给出纯文本回答
        if had_tool_calls and turn_count >= 3:
            return TaskDetection(is_complete=True, confidence=0.7, reason="complex_task_done")

        # 6. 多轮对话但有工具调用 = 可能还在做复杂任务，不判断完成
        if turn_count >= 2 and had_tool_calls:
            return TaskDetection(is_complete=False, confidence=0.5, reason="active_tool_usage")

        # 7. 默认：任务未完成
        return TaskDetection(is_complete=False, confidence=0.4, reason="default")
