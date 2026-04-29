"""tests/agent/test_skill.py — ISkill / SkillRegistry 测试。"""

import pytest
from agent.skill import Skill, SkillConfig, SkillRegistry


class TestSkillConfig:
    def test_default_values(self):
        cfg = SkillConfig()
        assert cfg.category == "general"
        assert cfg.priority == 50
        assert cfg.auto_load is True

    def test_to_dict(self):
        cfg = SkillConfig(category="coding", priority=80, tags=["python", "refactor"])
        d = cfg.to_dict()
        assert d["category"] == "coding"
        assert d["priority"] == 80
        assert "python" in d["tags"]

    def test_to_yaml(self, tmp_workspace):
        cfg = SkillConfig(category="test", priority=10)
        yaml_path = str(tmp_workspace / "test_skill_config.yaml")
        cfg.to_yaml(yaml_path)
        import yaml
        with open(yaml_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        assert data["category"] == "test"


class TestSkill:
    def test_match_keyword(self):
        skill = Skill(
            name="deploy_django",
            description="Deploy Django to AWS",
            trigger_condition="部署 Django AWS EC2",
        )
        assert skill.match("请帮我部署 Django 应用到 AWS") is True

    def test_match_no_keyword(self):
        skill = Skill(
            name="deploy_django",
            description="Deploy Django",
            trigger_condition="部署 Django AWS",
        )
        assert skill.match("写一个 React 组件") is False

    def test_match_empty_trigger(self):
        skill = Skill(name="empty", description="no trigger")
        assert skill.match("anything") is False

    def test_get_steps(self):
        steps = [{"tool_name": "bash", "description": "run"}]
        skill = Skill(name="test", steps=steps)
        assert skill.get_steps() == steps

    def test_default_values(self):
        skill = Skill(name="test")
        assert skill.description == ""
        assert skill.quality_score == 100.0
        assert skill.use_count == 0


class TestSkillRegistry:
    @pytest.fixture
    def registry(self):
        return SkillRegistry()

    @pytest.fixture
    def sample_skill(self):
        return Skill(
            name="deploy",
            description="Deploy application",
            trigger_condition="deploy 部署",
            config=SkillConfig(category="devops", priority=80),
        )

    def test_register_and_get(self, registry, sample_skill):
        registry.register(sample_skill)
        assert registry.get("deploy") is sample_skill

    def test_unregister(self, registry, sample_skill):
        registry.register(sample_skill)
        registry.unregister("deploy")
        assert registry.get("deploy") is None

    def test_find_by_tags(self, registry):
        s1 = Skill(name="a", config=SkillConfig(tags=["python"]))
        s2 = Skill(name="b", config=SkillConfig(tags=["javascript"]))
        registry.register(s1)
        registry.register(s2)
        result = registry.find_by_tags(["python"])
        assert len(result) == 1
        assert result[0].name == "a"

    def test_match_task(self, registry):
        registry.register(Skill(
            name="deploy", trigger_condition="deploy 部署",
            config=SkillConfig(priority=90),
        ))
        registry.register(Skill(
            name="build", trigger_condition="build 构建",
            config=SkillConfig(priority=50),
        ))
        matched = registry.match_task("请帮我部署应用")
        assert len(matched) >= 1
        assert matched[0].name == "deploy"

    def test_match_task_priority_order(self, registry):
        registry.register(Skill(
            name="low", trigger_condition="test",
            config=SkillConfig(priority=10),
        ))
        registry.register(Skill(
            name="high", trigger_condition="test",
            config=SkillConfig(priority=90),
        ))
        matched = registry.match_task("test task")
        assert matched[0].name == "high"
        assert matched[1].name == "low"

    def test_list_all(self, registry):
        registry.register(Skill(name="a"))
        registry.register(Skill(name="b"))
        assert len(registry.list_all()) == 2

    def test_load_from_dir(self, registry, tmp_workspace):
        skill_yaml = tmp_workspace / "deploy.yaml"
        skill_yaml.write_text("""
name: deploy_django
description: Deploy Django to AWS
trigger_condition: deploy Django
steps:
  - tool_name: bash
    description: install dependencies
config:
  category: devops
  priority: 80
""", encoding="utf-8")
        count = registry.load_from_dir(str(tmp_workspace))
        assert count == 1
        assert registry.get("deploy_django") is not None

    def test_load_from_nonexistent_dir(self, registry):
        count = registry.load_from_dir("/nonexistent/path")
        assert count == 0
