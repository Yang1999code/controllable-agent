"""tests/agent/test_cache_break.py — KV Cache 断点检测测试。"""

import pytest
from ai.types import Message
from agent.cache_break import CacheBreakDetector


def msg(role, content, id="", **kw):
    return Message(role=role, content=content, id=id, **kw)


class TestCacheBreakDetector:
    def test_first_call_is_break(self):
        """首次调用无基线 → is_break 返回 True 并建立基线。"""
        d = CacheBreakDetector()
        msgs = [msg("user", "hello", id="1")]
        assert d.is_break("sys", [], msgs) is True

    def test_same_prefix_no_break(self):
        """相同前缀第二次调用 → 不报 break。"""
        d = CacheBreakDetector()
        msgs = [msg("user", "hello", id="1")]
        d.is_break("sys", [], msgs)
        assert d.is_break("sys", [], msgs) is False

    def test_different_system_is_break(self):
        """system_prompt 变了 → 报 break。"""
        d = CacheBreakDetector()
        msgs = [msg("user", "hello", id="1")]
        d.is_break("sysA", [], msgs)
        assert d.is_break("sysB", [], msgs) is True

    def test_different_message_is_break(self):
        """消息内容变了 → 报 break。"""
        d = CacheBreakDetector()
        d.is_break("sys", [], [msg("user", "A", id="1")])
        assert d.is_break("sys", [], [msg("user", "B", id="2")]) is True

    def test_notify_compaction_resets(self):
        """压缩后重置基线 → 下次报 break。"""
        d = CacheBreakDetector()
        msgs = [msg("user", "hello", id="1")]
        d.is_break("sys", [], msgs)
        d.notify_compaction("new_tail_id")
        assert d.tail_start_id == "new_tail_id"
        assert d.is_break("sys", [], msgs) is True

    def test_tail_only_messages_same(self):
        """tail_start_id 之后的消息不变 → prefix hash 不变 → 不报 break。"""
        d = CacheBreakDetector()
        d.notify_compaction("t1")
        msgs = [
            msg("assistant", "summary", id="s1", summary=True),
            msg("user", "recent1", id="t1"),
            msg("assistant", "resp1", id="a1"),
        ]
        d.is_break("sys", [], msgs)
        # 追加一条新消息到尾部
        msgs.append(msg("user", "recent2", id="t2"))
        msgs.append(msg("assistant", "resp2", id="a2"))
        assert d.is_break("sys", [], msgs) is False
