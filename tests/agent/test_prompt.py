"""tests/agent/test_prompt.py — IPromptBuilder / PromptBuilder 测试。"""

import pytest
from ai.types import Context
from agent.prompt import PromptFragment, PromptBuilder


class TestPromptFragment:
    def test_default_values(self):
        frag = PromptFragment(name="TEST", content="test content")
        assert frag.priority == 50
        assert frag.condition is None
        assert frag.source == ""

    def test_with_condition(self):
        called = []

        def check(ctx):
            called.append(True)
            return ctx.system_prompt != ""

        frag = PromptFragment(
            name="COND", content="conditional",
            priority=30, condition=check,
        )
        ctx = Context(system_prompt="exists")
        assert frag.condition(ctx) is True
        assert len(called) == 1


class TestPromptBuilder:
    @pytest.fixture
    def builder(self):
        return PromptBuilder()

    def test_register_fragment(self, builder):
        frag = PromptFragment(name="CORE", content="core prompt", priority=10)
        builder.register_fragment(frag)
        result = builder.build(Context())
        assert "core prompt" in result

    def test_unregister_fragment(self, builder):
        frag = PromptFragment(name="TEMP", content="temp content")
        builder.register_fragment(frag)
        builder.unregister_fragment("TEMP")
        result = builder.build(Context())
        assert "temp content" not in result

    def test_build_priority_order(self, builder):
        builder.register_fragment(PromptFragment(
            name="LOW", content="low priority", priority=90,
        ))
        builder.register_fragment(PromptFragment(
            name="HIGH", content="high priority", priority=10,
        ))
        result = builder.build(Context())
        high_idx = result.index("high priority")
        low_idx = result.index("low priority")
        assert high_idx < low_idx

    def test_core_fragment_always_retained(self, builder):
        builder.register_fragment(PromptFragment(
            name="CORE", content="must keep", priority=10,
        ))
        builder.register_fragment(PromptFragment(
            name="OPT", content="optional", priority=60,
        ))
        result = builder.build(Context(), max_tokens=1)
        assert "must keep" in result

    def test_conditional_fragment_skipped(self, builder):
        def never(ctx):
            return False

        builder.register_fragment(PromptFragment(
            name="SKIP", content="should not appear",
            priority=30, condition=never,
        ))
        result = builder.build(Context())
        assert "should not appear" not in result

    def test_conditional_fragment_included(self, builder):
        def always(ctx):
            return True

        builder.register_fragment(PromptFragment(
            name="INC", content="should appear",
            priority=30, condition=always,
        ))
        result = builder.build(Context())
        assert "should appear" in result

    def test_refresh_clears_cache(self, builder):
        builder.register_fragment(PromptFragment(name="A", content="version 1"))
        v1 = builder.build(Context())
        builder.refresh_fragments("turn_end")
        # Should not crash
        v2 = builder.build(Context())
        assert v1 == v2

    def test_get_token_usage(self, builder):
        builder.register_fragment(PromptFragment(name="A", content="hello"))
        usage = builder.get_token_usage()
        assert "A" in usage
        assert usage["A"]["chars"] == 5
