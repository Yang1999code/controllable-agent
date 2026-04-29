"""tests/agent/test_inspector.py — IFlowInspector / FlowInspector 测试。"""

import asyncio
import pytest
from agent.inspector import FlowInspector, InspectionSnapshot


class TestInspectionSnapshot:
    def test_default_values(self):
        snap = InspectionSnapshot()
        assert snap.turn_count == 0
        assert snap.tool_success_rate == 1.0
        assert snap.active_tools == []


class TestFlowInspector:
    @pytest.fixture
    def inspector(self):
        return FlowInspector(queue_size=100)

    @pytest.mark.asyncio
    async def test_push_and_stats(self, inspector):
        await inspector.start(interval_sec=1)
        await inspector.push({"success": True, "tool_name": "read", "latency_ms": 100})
        await inspector.push({"success": False, "tool_name": "write", "latency_ms": 200})
        await asyncio.sleep(0.2)

        stats = await inspector.get_recent_stats()
        assert stats.turn_count >= 1
        await inspector.stop()

    @pytest.mark.asyncio
    async def test_empty_stats(self, inspector):
        stats = await inspector.get_recent_stats()
        assert stats.turn_count == 0
        assert stats.tool_success_rate == 1.0

    @pytest.mark.asyncio
    async def test_success_rate(self, inspector):
        await inspector.start(interval_sec=1)
        await inspector.push({"success": True, "tool_name": "a", "latency_ms": 50})
        await inspector.push({"success": True, "tool_name": "b", "latency_ms": 50})
        await asyncio.sleep(0.2)

        stats = await inspector.get_recent_stats()
        assert stats.tool_success_rate == 1.0
        await inspector.stop()

    @pytest.mark.asyncio
    async def test_stop(self, inspector):
        await inspector.start(interval_sec=1)
        await inspector.stop()
        assert not inspector._running

    @pytest.mark.asyncio
    async def test_stop_idempotent(self, inspector):
        await inspector.stop()
        assert not inspector._running
