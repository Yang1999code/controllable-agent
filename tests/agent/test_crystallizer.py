"""tests/agent/test_crystallizer.py — SkillCrystallizer 测试。"""

import pytest
from pathlib import Path

from agent.crystallizer import SkillCrystallizer
from agent.skill import SkillRegistry


@pytest.fixture
def registry():
    return SkillRegistry()


@pytest.fixture
def tmp_skills_dir(tmp_path):
    return tmp_path / "skills"


@pytest.fixture
def crystallizer(registry, tmp_skills_dir):
    return SkillCrystallizer(registry, tmp_skills_dir)


# ── 基本提取 ──


class TestExtractProposals:

    def test_extract_from_skill_block(self, crystallizer):
        text = """一些文字

```skill
name: create_flask_api
description: 创建 Flask REST API
trigger_condition: flask api rest
steps:
  - tool: write
    args: {path: "app.py", content: "..."}
    description: 创建主文件
  - tool: bash
    args: {command: "pytest tests/"}
    description: 运行测试
```

更多文字"""
        proposals = crystallizer._extract_skill_proposals(text)
        assert len(proposals) == 1
        assert proposals[0]["name"] == "create_flask_api"
        assert len(proposals[0]["steps"]) == 2

    def test_extract_from_yaml_block(self, crystallizer):
        text = """```yaml
name: search_code
description: 代码搜索
trigger_condition: search grep find
steps:
  - tool: grep
    args: {pattern: "TODO"}
    description: 搜索 TODO
```"""
        proposals = crystallizer._extract_skill_proposals(text)
        assert len(proposals) == 1
        assert proposals[0]["name"] == "search_code"

    def test_extract_multiple_skills(self, crystallizer):
        text = """
```skill
name: skill_a
trigger_condition: a
steps:
  - tool: bash
    description: step 1
```

```skill
name: skill_b
trigger_condition: b
steps:
  - tool: write
    description: step 2
```
"""
        proposals = crystallizer._extract_skill_proposals(text)
        assert len(proposals) == 2

    def test_extract_no_skills(self, crystallizer):
        text = "普通文本没有技能"
        proposals = crystallizer._extract_skill_proposals(text)
        assert len(proposals) == 0

    def test_extract_invalid_yaml_skipped(self, crystallizer):
        text = """
```skill
invalid: yaml: content: [:
```
"""
        proposals = crystallizer._extract_skill_proposals(text)
        assert len(proposals) == 0

    def test_extract_from_skill_section(self, crystallizer):
        text = """## 总结

一些总结文字

## 技能结晶

以下为可复用技能：

```yaml
name: test_skill
trigger_condition: test
steps:
  - tool: bash
    description: run test
```
"""
        proposals = crystallizer._extract_skill_proposals(text)
        assert len(proposals) >= 1
        names = [p["name"] for p in proposals]
        assert "test_skill" in names


# ── 验证 ──


class TestValidate:

    def test_valid_proposal(self, crystallizer):
        data = {
            "name": "valid_skill",
            "trigger_condition": "create file",
            "steps": [
                {"tool": "write", "description": "write file"},
            ],
        }
        skill = crystallizer._validate_and_build(data)
        assert skill is not None
        assert skill.name == "valid_skill"
        assert len(skill.steps) == 1

    def test_missing_name(self, crystallizer):
        data = {"trigger_condition": "x", "steps": [{"tool": "bash"}]}
        assert crystallizer._validate_and_build(data) is None

    def test_missing_trigger(self, crystallizer):
        data = {"name": "x", "steps": [{"tool": "bash"}]}
        assert crystallizer._validate_and_build(data) is None

    def test_missing_steps(self, crystallizer):
        data = {"name": "x", "trigger_condition": "x"}
        assert crystallizer._validate_and_build(data) is None

    def test_empty_steps(self, crystallizer):
        data = {"name": "x", "trigger_condition": "x", "steps": []}
        assert crystallizer._validate_and_build(data) is None

    def test_non_dict_input(self, crystallizer):
        assert crystallizer._validate_and_build("string") is None
        assert crystallizer._validate_and_build([1, 2]) is None

    def test_config_preserved(self, crystallizer):
        data = {
            "name": "with_config",
            "trigger_condition": "x",
            "steps": [{"tool": "bash"}],
            "config": {"category": "testing", "priority": 90, "tags": ["test"]},
        }
        skill = crystallizer._validate_and_build(data)
        assert skill is not None
        assert skill.config.category == "testing"
        assert skill.config.priority == 90


# ── 持久化 ──


class TestPersist:

    def test_persist_creates_yaml(self, crystallizer, tmp_skills_dir):
        from agent.skill import Skill, SkillConfig
        skill = Skill(
            name="test_persist",
            trigger_condition="test",
            steps=[{"tool": "bash", "description": "run"}],
        )
        result = crystallizer._persist(skill)
        assert result is True
        yaml_file = tmp_skills_dir / "test_persist.yaml"
        assert yaml_file.exists()

    def test_persist_content_valid(self, crystallizer, tmp_skills_dir):
        import yaml
        from agent.skill import Skill, SkillConfig
        skill = Skill(
            name="check_content",
            trigger_condition="check",
            steps=[{"tool": "read", "description": "read file"}],
        )
        crystallizer._persist(skill)
        yaml_file = tmp_skills_dir / "check_content.yaml"
        data = yaml.safe_load(yaml_file.read_text(encoding="utf-8"))
        assert data["name"] == "check_content"
        assert data["trigger_condition"] == "check"
        assert len(data["steps"]) == 1

    def test_persist_preserves_use_count(self, crystallizer, tmp_skills_dir):
        import yaml
        from agent.skill import Skill, SkillConfig
        skill = Skill(name="counter", trigger_condition="x", steps=[{"tool": "bash"}])
        crystallizer._persist(skill)
        yaml_file = tmp_skills_dir / "counter.yaml"
        data = yaml.safe_load(yaml_file.read_text(encoding="utf-8"))
        assert "use_count" not in data  # 新技能 use_count=0，不写入

        # 模拟有使用记录的技能
        skill2 = Skill(
            name="counter", trigger_condition="x",
            steps=[{"tool": "bash"}], use_count=5, last_used_at=1000.0,
        )
        crystallizer._persist(skill2)
        data2 = yaml.safe_load(yaml_file.read_text(encoding="utf-8"))
        assert data2["use_count"] == 5
        assert data2["last_used_at"] == 1000.0


# ── 完整 crystallize 流程 ──


class TestCrystallize:

    def test_full_flow(self, crystallizer, registry, tmp_skills_dir):
        text = """
```skill
name: create_api
description: 创建 API
trigger_condition: api create rest
steps:
  - tool: write
    args: {path: "app.py"}
    description: write file
  - tool: bash
    args: {command: "pytest"}
    description: run tests
```
"""
        skills = crystallizer.crystallize(text)
        assert len(skills) == 1
        assert skills[0].name == "create_api"
        assert registry.get("create_api") is not None
        assert (tmp_skills_dir / "create_api.yaml").exists()

    def test_no_proposals_returns_empty(self, crystallizer):
        skills = crystallizer.crystallize("普通文本")
        assert skills == []

    def test_mixed_valid_invalid(self, crystallizer):
        text = """
```skill
name: good_skill
trigger_condition: good
steps:
  - tool: bash
    description: step
```

```skill
bad_no_trigger: true
```
"""
        skills = crystallizer.crystallize(text)
        assert len(skills) == 1
        assert skills[0].name == "good_skill"


# ── 加载已有技能 ──


class TestLoadExisting:

    def test_load_from_dir(self, registry, tmp_skills_dir):
        import yaml
        tmp_skills_dir.mkdir(parents=True, exist_ok=True)
        (tmp_skills_dir / "loaded.yaml").write_text(yaml.dump({
            "name": "loaded_skill",
            "trigger_condition": "load test",
            "steps": [{"tool": "bash", "description": "run"}],
        }, allow_unicode=True), encoding="utf-8")

        cryst = SkillCrystallizer(registry, tmp_skills_dir)
        count = cryst.load_existing_skills()
        assert count == 1
        assert registry.get("loaded_skill") is not None

    def test_load_empty_dir(self, registry, tmp_skills_dir):
        cryst = SkillCrystallizer(registry, tmp_skills_dir)
        count = cryst.load_existing_skills()
        assert count == 0

    def test_load_nonexistent_dir(self, registry, tmp_path):
        cryst = SkillCrystallizer(registry, tmp_path / "nope")
        count = cryst.load_existing_skills()
        assert count == 0


# ── 动态复杂度估算 ──


class TestEstimateComplexity:
    def test_simple_task(self):
        from agent.runtime import AgentRuntime
        plan = "创建一个 main.py 文件"
        result = AgentRuntime._estimate_task_complexity(plan)
        assert result["coder"] == 30

    def test_medium_task(self):
        from agent.runtime import AgentRuntime
        plan = """
## 步骤
1. 创建 app.py
2. 创建 utils.py
3. 创建 models.py
4. 创建 tests/test_app.py
5. 创建 config.yaml
"""
        result = AgentRuntime._estimate_task_complexity(plan)
        assert result["coder"] == 50

    def test_complex_task(self):
        from agent.runtime import AgentRuntime
        plan = """
## 文件列表
- backend/app.py
- backend/models.py
- backend/routes.py
- backend/auth.py
- frontend/index.html
- frontend/app.js
- frontend/style.css
- tests/test_api.py
- tests/test_models.py
- config.yaml
- README.md

## 步骤
1. 创建后端
2. 创建前端
3. 创建测试
4. 集成测试
5. 文档
6. 配置
7. 安全审查
8. 性能测试
9. 部署脚本
10. CI 配置
"""
        result = AgentRuntime._estimate_task_complexity(plan)
        assert result["coder"] == 80
        assert result["reviewer"] == 40
