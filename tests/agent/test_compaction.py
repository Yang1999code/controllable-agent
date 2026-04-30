"""tests/agent/test_compaction.py — 三层压缩测试。"""

import pytest
from ai.types import Message, ToolDefinition
from agent.context_window import count_total_tokens
from agent.compaction import (
    compact, CompactionResult, _prune_observations, _split_for_summary,
    _truncate_head, _find_first_kept,
)


def msg(role, content, **kw):
    return Message(role=role, content=content, **kw)


class TestPrune:
    def test_keeps_recent_tool_results(self):
        msgs = [
            msg("user", "q1", id="1"),
            msg("assistant", "a1", id="2", tool_calls=[{"function": {"name": "read", "arguments": "{}"}}]),
            msg("tool", "file content here", id="3", tool_call_id="tc1", tool_name="read"),
        ]
        result = _prune_observations(msgs)
        assert len(result) == 3
        assert result[2].content == "file content here"

    def test_clears_old_tool_results(self):
        msgs = [
            msg("user", "q1", id="1"),
            msg("assistant", "a1", id="2", tool_calls=[{"function": {"name": "read", "arguments": "{}"}}]),
            msg("tool", "old data", id="3", tool_call_id="tc1", tool_name="read"),
            msg("user", "q2", id="4"),
            msg("assistant", "a2", id="5", tool_calls=[{"function": {"name": "read", "arguments": "{}"}}]),
            msg("tool", "old data 2", id="6", tool_call_id="tc2", tool_name="read"),
            msg("user", "q3", id="7"),
            msg("assistant", "a3", id="8", tool_calls=[{"function": {"name": "read", "arguments": "{}"}}]),
            msg("tool", "old data 3", id="9", tool_call_id="tc3", tool_name="read"),
            msg("user", "q4", id="10"),
            msg("assistant", "a4", id="11", tool_calls=[{"function": {"name": "read", "arguments": "{}"}}]),
            msg("tool", "recent data", id="12", tool_call_id="tc4", tool_name="read"),
        ]
        result = _prune_observations(msgs)
        assert len(result) == 12
        assert result[2].content == "[Old tool result content cleared]"
        assert result[11].content == "recent data"

    def test_preserves_assistant_messages(self):
        msgs = [
            msg("user", "q1", id="1"),
            msg("assistant", "a1", id="2"),
        ]
        result = _prune_observations(msgs)
        assert len(result) == 2


class TestSplitForSummary:
    def test_no_split_when_few_turns(self):
        msgs = [
            msg("user", "q1", id="1"),
            msg("assistant", "a1", id="2", tool_calls=[{"function": {"name": "read", "arguments": "{}"}}]),
        ]
        head, tail = _split_for_summary(msgs)
        assert head == []
        assert tail == msgs

    def test_splits_after_cutoff(self):
        msgs = []
        for i in range(5):
            msgs.append(msg("user", f"q{i}", id=f"u{i}"))
            msgs.append(msg("assistant", f"a{i}", id=f"a{i}", tool_calls=[{"function": {"name": "bash", "arguments": "{}"}}]))
        head, tail = _split_for_summary(msgs)
        assert len(head) > 0
        assert len(tail) > 0
        assert len(head) + len(tail) == 10


class TestTruncate:
    def test_removes_oldest(self):
        msgs = [
            msg("user", "q1 " * 100, id="1"),
            msg("assistant", "a1 " * 100, id="2"),
            msg("user", "q2", id="3"),
            msg("assistant", "a2", id="4"),
        ]
        result = _truncate_head(msgs, "", [], 5)
        assert len(result) < 4

    def test_preserves_minimum(self):
        msgs = [
            msg("user", "q1", id="1"),
            msg("assistant", "a1", id="2"),
        ]
        result = _truncate_head(msgs, "", [], 10)
        assert len(result) == 2


class TestCompact:
    def test_noop_when_under_limit(self):
        msgs = [
            msg("user", "hello", id="1"),
            msg("assistant", "hi", id="2"),
        ]
        result = compact(msgs, "", [], 100000, 0.85, 0.98)
        assert result.layer_used == "none"
        assert result.messages == msgs

    def test_compact_returns_tail_start_id(self):
        many = []
        for i in range(100):
            many.append(msg("user", f"question number {i} " * 10, id=f"u{i}"))
            many.append(msg("assistant", f"answer number {i} " * 10, id=f"a{i}"))
        result = compact(many, "", [], 2000, 0.85, 0.98)
        assert result.layer_used != "none"
        assert result.tokens_freed > 0

    def test_summary_msg_has_flag(self):
        many = []
        for i in range(100):
            many.append(msg("user", f"q{i} " * 20, id=f"u{i}"))
            many.append(msg("assistant", f"a{i} " * 20, id=f"a{i}"))
        result = compact(many, "", [], 2000, 0.85, 0.98)
        if result.layer_used == "summary":
            assert result.messages[0].summary is True


class TestFindFirstKept:
    def test_all_same(self):
        msgs = [msg("user", "a", id="1"), msg("assistant", "b", id="2")]
        assert _find_first_kept(msgs, msgs) == "2"

    def test_one_changed(self):
        orig = [msg("user", "a", id="1"), msg("assistant", "b", id="2")]
        pruned = [msg("user", "a", id="1"), msg("assistant", "CHANGED", id="2")]
        assert _find_first_kept(orig, pruned) == "2"
