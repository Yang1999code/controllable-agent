"""tests/agent/test_runtime.py — AgentRuntime 测试（需求4 核心）。"""

import pytest
from agent.runtime import AgentTypeConfig, SubAgentResult, AgentRuntime
from agent.hook import HookChain
from tests.conftest import MockProvider, MockTool


@pytest.fixture
def runtime():
    """创建带 mock 工具的 AgentRuntime。"""
    tools = {
        "read": MockTool(return_value="file content"),
        "write": MockTool(return_value="written"),
        "bash": MockTool(return_value="command output"),
        "glob": MockTool(return_value="files"),
        "grep": MockTool(return_value="matches"),
        "delegate_task": MockTool(return_value="done"),
    }
    # 刷新 tool name
    for name, tool in tools.items():
        tool.definition.name = name

    provider = MockProvider()
    hooks = HookChain()
    return AgentRuntime(tools=tools, provider=provider, hooks=hooks)


class TestAgentTypeConfig:
    def test_create_config(self):
        config = AgentTypeConfig(
            name="coder",
            description="coding agent",
            tools_blacklist=["delegate_task", "crystallize"],
        )
        assert config.name == "coder"
        assert "delegate_task" in config.tools_blacklist

    def test_default_blacklist(self):
        config = AgentTypeConfig(name="test", description="desc")
        assert "delegate_task" in config.tools_blacklist
        assert "crystallize" in config.tools_blacklist

    def test_from_yaml(self, tmp_workspace):
        yaml_path = tmp_workspace / "test_agent.yaml"
        yaml_path.write_text("""
name: test_agent
description: A test agent
category: testing
when_to_use: for testing purposes
""")
        config = AgentTypeConfig.from_yaml(str(yaml_path))
        assert config.name == "test_agent"
        assert config.category == "testing"


class TestAgentRuntime:
    def test_register_agent_type(self, runtime):
        config = AgentTypeConfig(name="tester", description="test agent")
        runtime.register_agent_type(config)
        assert runtime.get_agent_type("tester") is not None

    def test_register_duplicate_raises(self, runtime):
        config = AgentTypeConfig(name="coder", description="coder agent")
        runtime.register_agent_type(config)
        with pytest.raises(ValueError, match="already registered"):
            runtime.register_agent_type(config)

    def test_list_agent_types(self, runtime):
        runtime.register_agent_type(AgentTypeConfig(name="a", description="a"))
        runtime.register_agent_type(AgentTypeConfig(name="b", description="b"))
        types = runtime.list_agent_types()
        assert len(types) == 2

    def test_select_agent_match(self, runtime):
        runtime.register_agent_type(AgentTypeConfig(
            name="coder", description="coding and refactoring agent",
            when_to_use="当需要编写代码或重构代码时", category="coding",
        ))
        runtime.register_agent_type(AgentTypeConfig(
            name="reviewer", description="code review agent",
            when_to_use="当需要审查代码时", category="review",
        ))

        # 中文匹配
        result = runtime.select_agent("重构代码")
        assert result == "coder"

    def test_select_agent_no_match(self, runtime):
        runtime.register_agent_type(AgentTypeConfig(
            name="coder", description="coding agent",
            when_to_use="coding tasks", category="coding",
        ))
        result = runtime.select_agent("xyzzy 无意义查询")
        assert result is None

    def test_select_agent_explorer(self, runtime):
        runtime.register_agent_type(AgentTypeConfig(
            name="explorer", description="file search and exploration agent",
            when_to_use="当需要搜索文件或探索代码库查找时", category="exploration",
        ))
        runtime.register_agent_type(AgentTypeConfig(
            name="coder", description="coding agent",
            when_to_use="coding tasks", category="coding",
        ))
        result = runtime.select_agent("搜索文件查找注释")
        assert result is not None

    def test_filter_tools_blacklist(self, runtime):
        config = AgentTypeConfig(
            name="limited", description="limited agent",
            tools_blacklist=["bash", "delegate_task"],
        )
        filtered = runtime._filter_tools(config)
        assert "bash" not in filtered
        assert "read" in filtered

    def test_filter_tools_whitelist(self, runtime):
        config = AgentTypeConfig(
            name="reader", description="read-only agent",
            tools_whitelist=["read", "glob", "grep"],
        )
        filtered = runtime._filter_tools(config)
        assert "read" in filtered
        assert "bash" not in filtered
        assert "write" not in filtered

    def test_agent_communication(self, runtime):
        """Agent间通信：send_message + check_inbox"""
        runtime.send_message("child_1", "main", "发现文件 X")
        msg = runtime.check_inbox("main")
        assert msg is not None
        assert "发现文件 X" in msg

        # 第二次检查应为空
        msg2 = runtime.check_inbox("main")
        assert msg2 is None

    def test_get_delegation_stats(self, runtime):
        stats = runtime.get_delegation_stats()
        assert "total_spawned" in stats
        assert "active_count" in stats

    def test_get_config(self, runtime):
        cfg = runtime.get_config()
        assert cfg["max_concurrent_children"] == 3
        assert cfg["max_depth"] == 2

    @pytest.mark.asyncio
    async def test_spawn_depth_limit(self, runtime):
        runtime.register_agent_type(AgentTypeConfig(
            name="coder", description="coding agent",
        ))
        result = await runtime.spawn(
            agent_type="coder", task="test task",
            current_depth=2,  # >= max_depth
        )
        assert result.status == "rejected"
        assert "Max depth" in (result.error or "")

    @pytest.mark.asyncio
    async def test_spawn_unknown_type(self, runtime):
        result = await runtime.spawn(
            agent_type="nonexistent", task="test",
        )
        assert result.status == "failed"

    @pytest.mark.asyncio
    async def test_spawn_auto_select_none(self, runtime):
        """没有注册类型时 auto-select 返回 None，spawn 失败。"""
        result = await runtime.spawn(task="test task")  # agent_type optional
        assert result.status == "rejected"

    def test_tokenize(self, runtime):
        tokens = runtime._tokenize("部署 Django 到 AWS")
        assert len(tokens) > 0
