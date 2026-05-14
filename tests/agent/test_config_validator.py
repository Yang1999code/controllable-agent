"""tests/agent/test_config_validator.py — 测试配置校验模块。"""

import tempfile
from pathlib import Path

import yaml

from agent.config_validator import (
    ValidationResult,
    validate_config,
    validate_config_file,
    load_validated_config,
)


def _create_temp_config(content: dict) -> Path:
    """创建临时配置文件。"""
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False)
    yaml.dump(content, f)
    f.flush()
    return Path(f.name)


class TestValidationResult:
    def test_valid_by_default(self):
        result = ValidationResult(valid=True, errors=[], warnings=[])
        assert result.valid is True
        assert result.errors == []
        assert result.warnings == []

    def test_add_error(self):
        result = ValidationResult(valid=True, errors=[], warnings=[])
        result.add_error("Test error")
        assert result.valid is False
        assert result.errors == ["Test error"]

    def test_add_multiple_errors(self):
        result = ValidationResult(valid=True, errors=[], warnings=[])
        result.add_error("Error 1")
        result.add_error("Error 2")
        assert len(result.errors) == 2
        assert result.valid is False

    def test_add_warning(self):
        result = ValidationResult(valid=True, errors=[], warnings=[])
        result.add_warning("Test warning")
        assert result.valid is True  # 警告不标记为无效
        assert result.warnings == ["Test warning"]


class TestValidateConfig:
    def test_valid_config(self):
        config = {
            "providers": {
                "default": "openai_compat",
                "openai_compat": {
                    "model": "gpt-4o",
                    "base_url": "https://api.openai.com/v1",
                    "api_key_env": "OPENAI_API_KEY",
                },
            },
            "agent": {
                "max_turns": 100,
                "max_tool_calls_per_turn": 10,
                "max_context_tokens": 128000,
            },
            "runtime": {
                "max_concurrent_children": 3,
                "max_depth": 2,
                "default_timeout_sec": 300,
            },
        }
        result = validate_config(config)
        assert result.valid is True
        assert len(result.errors) == 0

    def test_missing_providers(self):
        config = {}
        result = validate_config(config)
        assert result.valid is False
        assert any("providers" in e for e in result.errors)

    def test_missing_default_provider(self):
        config = {"providers": {"openai_compat": {"model": "gpt-4o"}}}
        result = validate_config(config)
        assert result.valid is False
        assert any("default" in e for e in result.errors)

    def test_invalid_provider_type(self):
        config = {
            "providers": {
                "default": "invalid_provider",
                "invalid_provider": {"model": "gpt-4o"},
            },
        }
        result = validate_config(config)
        assert result.valid is False
        assert any("不支持" in e for e in result.errors)

    def test_missing_model_field(self):
        config = {
            "providers": {
                "default": "openai_compat",
                "openai_compat": {"base_url": "https://api.openai.com/v1"},
            },
        }
        result = validate_config(config)
        assert result.valid is False
        assert any("model" in e for e in result.errors)

    def test_missing_api_key_warning(self):
        config = {
            "providers": {
                "default": "openai_compat",
                "openai_compat": {"model": "gpt-4o"},
            },
        }
        result = validate_config(config)
        assert result.valid is True  # 只有警告
        assert any("api_key" in w.lower() or "key" in w for w in result.warnings)

    def test_missing_base_url_warning(self):
        config = {
            "providers": {
                "default": "openai_compat",
                "openai_compat": {
                    "model": "gpt-4o",
                    "api_key_env": "OPENAI_API_KEY",
                },
            },
        }
        result = validate_config(config)
        assert any("base_url" in w for w in result.warnings)

    def test_invalid_max_turns_warning(self):
        config = {
            "providers": {
                "default": "openai_compat",
                "openai_compat": {"model": "gpt-4o"},
            },
            "agent": {"max_turns": 0},
        }
        result = validate_config(config)
        assert any("max_turns" in w for w in result.warnings)

    def test_small_context_warning(self):
        config = {
            "providers": {
                "default": "openai_compat",
                "openai_compat": {"model": "gpt-4o"},
            },
            "agent": {"max_context_tokens": 1024},
        }
        result = validate_config(config)
        assert any("max_context_tokens" in w for w in result.warnings)

    def test_anthropic_provider(self):
        config = {
            "providers": {
                "default": "anthropic",
                "anthropic": {
                    "model": "claude-sonnet-4-6",
                    "api_key_env": "ANTHROPIC_API_KEY",
                },
            },
        }
        result = validate_config(config)
        assert result.valid is True

    def test_empty_providers_dict(self):
        config = {"providers": {"default": "openai_compat"}}
        result = validate_config(config)
        assert result.valid is False
        assert any("没有配置任何 provider" in e for e in result.errors)

    def test_invalid_max_concurrent(self):
        config = {
            "providers": {
                "default": "openai_compat",
                "openai_compat": {"model": "gpt-4o"},
            },
            "runtime": {"max_concurrent_children": 0},
        }
        result = validate_config(config)
        assert any("max_concurrent" in w for w in result.warnings)

    def test_invalid_timeout(self):
        config = {
            "providers": {
                "default": "openai_compat",
                "openai_compat": {"model": "gpt-4o"},
            },
            "runtime": {"default_timeout_sec": -1},
        }
        result = validate_config(config)
        assert any("default_timeout" in w for w in result.warnings)


class TestValidateConfigFile:
    def test_nonexistent_file(self):
        result = validate_config_file("/nonexistent/path.yaml")
        assert result.valid is False
        assert any("不存在" in e for e in result.errors)

    def test_valid_file(self):
        config = {
            "providers": {
                "default": "openai_compat",
                "openai_compat": {
                    "model": "gpt-4o",
                    "api_key_env": "OPENAI_API_KEY",
                },
            },
        }
        path = _create_temp_config(config)
        try:
            result = validate_config_file(path)
            assert result.valid is True
        finally:
            path.unlink()

    def test_invalid_yaml(self):
        f = tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False)
        f.write("invalid: yaml: {{[")
        f.flush()
        path = Path(f.name)
        try:
            result = validate_config_file(path)
            assert result.valid is False
            assert any("YAML" in e for e in result.errors)
        finally:
            path.unlink()

    def test_non_dict_yaml(self):
        f = tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False)
        f.write("- item1\n- item2\n")
        f.flush()
        path = Path(f.name)
        try:
            result = validate_config_file(path)
            assert result.valid is False
            assert any("字典" in e for e in result.errors)
        finally:
            path.unlink()


class TestLoadValidatedConfig:
    def test_load_valid_config(self):
        config = {
            "providers": {
                "default": "openai_compat",
                "openai_compat": {
                    "model": "gpt-4o",
                    "api_key_env": "OPENAI_API_KEY",
                },
            },
        }
        path = _create_temp_config(config)
        try:
            loaded, result = load_validated_config(path)
            assert loaded["providers"]["default"] == "openai_compat"
            assert result.valid is True
        finally:
            path.unlink()

    def test_load_nonexistent_file(self):
        config, result = load_validated_config("/nonexistent.yaml")
        assert config == {}
        assert result.valid is False

    def test_load_invalid_yaml(self):
        f = tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False)
        f.write("invalid: yaml: {{[")
        f.flush()
        path = Path(f.name)
        try:
            config, result = load_validated_config(path)
            assert config == {}
            assert result.valid is False
        finally:
            path.unlink()
