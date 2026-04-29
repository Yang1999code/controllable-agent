"""tests/agent/test_hook.py — HookChain 测试。"""

import pytest
from ai.types import AgentEvent, AgentEventType
from agent.hook import HookHandler, HookChain


class TestHookChain:
    def test_register_handler(self):
        chain = HookChain()
        results = []

        def callback(event):
            results.append(event.type)

        handler = HookHandler(
            name="test_handler",
            event_type=AgentEventType.TURN_START,
            callback=callback,
        )
        chain.register(handler)
        assert len(chain._handlers[AgentEventType.TURN_START]) == 1

    def test_register_invalid_priority(self):
        with pytest.raises(ValueError):
            HookHandler(
                name="bad",
                event_type=AgentEventType.TURN_START,
                callback=lambda e: None,
                priority=150,
            )

    @pytest.mark.asyncio
    async def test_fire_triggers_handler(self):
        chain = HookChain()
        results = []

        async def callback(event):
            results.append(event.data.get("key"))

        chain.register(HookHandler(
            name="h1", event_type=AgentEventType.TURN_START,
            callback=callback, priority=10,
        ))

        await chain.fire(AgentEvent(type=AgentEventType.TURN_START, data={"key": "value"}))
        assert results == ["value"]

    @pytest.mark.asyncio
    async def test_fire_priority_order(self):
        chain = HookChain()
        order = []

        def make_cb(n):
            def cb(event):
                order.append(n)
            return cb

        chain.register(HookHandler(name="h2", event_type=AgentEventType.TURN_END,
                                    callback=make_cb(2), priority=50))
        chain.register(HookHandler(name="h1", event_type=AgentEventType.TURN_END,
                                    callback=make_cb(1), priority=10))

        await chain.fire(AgentEvent(type=AgentEventType.TURN_END))
        assert order == [1, 2]

    @pytest.mark.asyncio
    async def test_fire_exception_isolation(self):
        chain = HookChain()
        results = []

        def failing_cb(event):
            raise RuntimeError("boom")

        def normal_cb(event):
            results.append("ok")

        chain.register(HookHandler(name="failing", event_type=AgentEventType.ERROR,
                                    callback=failing_cb, priority=10))
        chain.register(HookHandler(name="normal", event_type=AgentEventType.ERROR,
                                    callback=normal_cb, priority=20))

        await chain.fire(AgentEvent(type=AgentEventType.ERROR))
        assert results == ["ok"]

    def test_unregister(self):
        chain = HookChain()
        chain.register(HookHandler(name="to_remove", event_type=AgentEventType.LOOP_START,
                                    callback=lambda e: None))
        chain.unregister("to_remove")
        assert len(chain._handlers[AgentEventType.LOOP_START]) == 0

    def test_disabled_handler_not_called(self):
        chain = HookChain()
        results = []

        chain.register(HookHandler(
            name="disabled", event_type=AgentEventType.TURN_START,
            callback=lambda e: results.append(1),
            enabled=False,
        ))

        import asyncio
        asyncio.run(chain.fire(AgentEvent(type=AgentEventType.TURN_START)))
        assert results == []
