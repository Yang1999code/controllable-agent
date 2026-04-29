"""tests/agent/test_self_modify.py — quality_score 质量评分测试。"""

import pytest
from agent.self_modify import calculate_quality_score, QualityScore, ISelfModification


class DummySkillCard:
    """模拟 SkillCard 用于评分测试。"""
    def __init__(self, description="", trigger_condition="", steps=None):
        self.description = description
        self.trigger_condition = trigger_condition
        self.steps = steps or []


class TestCalculateQualityScore:
    def test_good_skill_passes(self):
        """好技能：长描述 + 触发条件 + 多个步骤 → quality >= 60"""
        skill = DummySkillCard(
            description="部署 Django 应用到 AWS EC2 并配置负载均衡",
            trigger_condition="当需要部署 Django 应用时",
            steps=[
                {"tool_name": "bash", "args": {}, "description": "安装依赖"},
                {"tool_name": "write", "args": {}, "description": "创建配置文件"},
                {"tool_name": "bash", "args": {}, "description": "部署命令"},
            ],
        )
        result = calculate_quality_score(skill)
        assert result.quality_score >= 60
        assert result.passed is True

    def test_bad_skill_fails(self):
        """差技能：无描述 + 无步骤 → quality < 60"""
        skill = DummySkillCard(description="", steps=[])
        result = calculate_quality_score(skill)
        assert result.quality_score < 60
        assert result.passed is False

    def test_minimal_skill(self):
        """边界: 短描述 + 1 步无描述 → 应低于 60"""
        skill = DummySkillCard(
            description="a",
            steps=[{"tool_name": "bash"}],
        )
        result = calculate_quality_score(skill)
        assert 0 <= result.quality_score <= 100

    def test_weighted_formula(self):
        """验证加权公式: quality = clarity*0.3 + completeness*0.3 + actionability*0.4"""
        skill = DummySkillCard(
            description="A well-described task with enough words",
            trigger_condition="when needed",
            steps=[
                {"tool_name": "read", "description": "read file"},
                {"tool_name": "write", "description": "write file"},
                {"tool_name": "bash", "description": "run command"},
            ],
        )
        result = calculate_quality_score(skill)
        expected = round(result.clarity * 0.3 + result.completeness * 0.3 + result.actionability * 0.4, 1)
        assert result.quality_score == expected


class TestISelfModification:
    def test_quality_score_method(self):
        skill = DummySkillCard(
            description="A test skill description",
            trigger_condition="when testing",
            steps=[
                {"tool_name": "read", "description": "step 1"},
                {"tool_name": "write", "description": "step 2"},
            ],
        )
        sm = ISelfModification()
        result = sm.quality_score(skill)
        assert "clarity" in result
        assert "completeness" in result
        assert "actionability" in result
        assert "quality_score" in result
        assert "pass" in result

    def test_crystallize_threshold_good(self):
        """文档约定: quality >= 60 才持久化"""
        good = DummySkillCard(
            description="Deploy Django to AWS with full CI/CD pipeline setup",
            trigger_condition="当需要部署 Django 应用时",
            steps=[
                {"tool_name": "bash", "description": "install deps"},
                {"tool_name": "write", "description": "config"},
                {"tool_name": "bash", "description": "deploy"},
            ],
        )
        result = ISelfModification.quality_score(good)
        assert result["pass"] is True

    def test_crystallize_threshold_bad(self):
        """文档约定: < 60 丢弃"""
        bad = DummySkillCard(description="", steps=[])
        result = ISelfModification.quality_score(bad)
        assert result["pass"] is False
