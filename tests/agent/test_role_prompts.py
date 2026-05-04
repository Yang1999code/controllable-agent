"""tests/agent/test_role_prompts.py — role_prompts 加载器测试。"""

import pytest
from pathlib import Path

from agent.role_prompts import (
    load_role_config,
    load_all_roles,
    MULTI_AGENT_ROLES,
    ROLE_MAX_TURNS,
)


class TestLoadRoleConfig:

    def test_load_coordinator(self):
        config = load_role_config("coordinator")
        assert config.name == "coordinator"
        assert config.category == "coordination"
        assert "delegate_task" in config.tools_whitelist
        assert "bash" in config.tools_blacklist
        assert len(config.system_prompt) > 100

    def test_load_planner(self):
        config = load_role_config("planner")
        assert config.name == "planner"
        assert config.category == "planning"
        assert "plan.md" in config.system_prompt
        assert "delegate_task" in config.tools_blacklist

    def test_load_coder(self):
        config = load_role_config("coder")
        assert config.name == "coder"
        assert config.category == "execution"
        assert "bash" in config.tools_whitelist
        assert "delegate_task" in config.tools_blacklist

    def test_load_reviewer(self):
        config = load_role_config("reviewer")
        assert config.name == "reviewer"
        assert config.category == "review"
        assert "write" in config.tools_blacklist
        assert "edit" in config.tools_blacklist

    def test_load_memorizer(self):
        config = load_role_config("memorizer")
        assert config.name == "memorizer"
        assert config.category == "memory"
        assert "cross_agent_read" in config.tools_whitelist
        assert "bash" in config.tools_blacklist

    def test_load_nonexistent_role_raises(self):
        with pytest.raises(ValueError, match="Unknown role"):
            load_role_config("ghost_role")

    def test_load_with_custom_dir_raises_on_missing(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            load_role_config("coordinator", config_dir=tmp_path)

    def test_system_prompts_are_nonempty(self):
        for role in MULTI_AGENT_ROLES:
            config = load_role_config(role)
            assert config.system_prompt, f"{role} has empty system_prompt"


class TestLoadAllRoles:

    def test_loads_all_five(self):
        configs = load_all_roles()
        assert set(configs.keys()) == set(MULTI_AGENT_ROLES)

    def test_missing_role_skipped(self, tmp_path):
        configs = load_all_roles(config_dir=tmp_path)
        assert configs == {}

    def test_partial_load(self, tmp_path):
        from agent.runtime import AgentTypeConfig
        config = AgentTypeConfig(
            name="coder",
            description="test coder",
            category="execution",
        )
        config.to_yaml(str(tmp_path / "coder.yaml"))
        configs = load_all_roles(config_dir=tmp_path)
        assert "coder" in configs
        assert len(configs) == 1


class TestRoleMaxTurns:

    def test_all_roles_have_max_turns(self):
        for role in MULTI_AGENT_ROLES:
            assert role in ROLE_MAX_TURNS, f"{role} missing from ROLE_MAX_TURNS"

    def test_max_turns_reasonable_range(self):
        for role, turns in ROLE_MAX_TURNS.items():
            assert 5 <= turns <= 50, f"{role} has unreasonable max_turns: {turns}"
