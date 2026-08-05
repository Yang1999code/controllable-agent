"""tests/agent/test_logger.py — 测试结构化日志模块。"""

import json
import logging
from agent.logger import (
    ColoredFormatter,
    JsonFormatter,
    StructuredLogger,
    get_logger,
    setup_logging,
)


class TestColoredFormatter:
    def test_format_basic(self):
        formatter = ColoredFormatter("%(levelname)s %(message)s")
        record = logging.LogRecord(
            name="test", level=logging.INFO, pathname="", lineno=0,
            msg="Hello", args=(), exc_info=None,
        )
        result = formatter.format(record)
        assert "INFO" in result
        assert "Hello" in result

    def test_format_error_level(self):
        formatter = ColoredFormatter("%(levelname)s %(message)s")
        record = logging.LogRecord(
            name="test", level=logging.ERROR, pathname="", lineno=0,
            msg="Error occurred", args=(), exc_info=None,
        )
        result = formatter.format(record)
        assert "ERROR" in result
        assert "Error occurred" in result

    def test_format_debug_level(self):
        formatter = ColoredFormatter("%(levelname)s %(message)s")
        record = logging.LogRecord(
            name="test", level=logging.DEBUG, pathname="", lineno=0,
            msg="Debug info", args=(), exc_info=None,
        )
        result = formatter.format(record)
        assert "DEBUG" in result

    def test_format_with_module(self):
        formatter = ColoredFormatter("%(module)s:%(lineno)d %(message)s")
        record = logging.LogRecord(
            name="test", level=logging.INFO, pathname="test.py", lineno=42,
            msg="Test message", args=(), exc_info=None,
        )
        record.module = "test_module"
        result = formatter.format(record)
        assert "test_module" in result
        assert "42" in result


class TestJsonFormatter:
    def test_format_json(self):
        formatter = JsonFormatter()
        record = logging.LogRecord(
            name="test", level=logging.INFO, pathname="", lineno=10,
            msg="Test message", args=(), exc_info=None,
        )
        record.module = "test_mod"
        record.funcName = "test_func"
        result = formatter.format(record)
        data = json.loads(result)
        assert data["level"] == "INFO"
        assert data["message"] == "Test message"
        assert data["module"] == "test_mod"
        assert data["function"] == "test_func"
        assert data["line"] == 10
        assert "timestamp" in data

    def test_format_with_trace_id(self):
        formatter = JsonFormatter()
        record = logging.LogRecord(
            name="test", level=logging.INFO, pathname="", lineno=0,
            msg="Trace test", args=(), exc_info=None,
        )
        record.trace_id = "abc123"
        result = formatter.format(record)
        data = json.loads(result)
        assert data["trace_id"] == "abc123"

    def test_format_with_agent_id(self):
        formatter = JsonFormatter()
        record = logging.LogRecord(
            name="test", level=logging.INFO, pathname="", lineno=0,
            msg="Agent test", args=(), exc_info=None,
        )
        record.agent_id = "main"
        result = formatter.format(record)
        data = json.loads(result)
        assert data["agent_id"] == "main"

    def test_format_with_duration(self):
        formatter = JsonFormatter()
        record = logging.LogRecord(
            name="test", level=logging.INFO, pathname="", lineno=0,
            msg="Timed test", args=(), exc_info=None,
        )
        record.duration_ms = 150
        result = formatter.format(record)
        data = json.loads(result)
        assert data["duration_ms"] == 150

    def test_format_with_exception(self):
        formatter = JsonFormatter()
        try:
            raise ValueError("Test error")
        except ValueError:
            import sys
            record = logging.LogRecord(
                name="test", level=logging.ERROR, pathname="", lineno=0,
                msg="Exception test", args=(), exc_info=sys.exc_info(),
            )
        result = formatter.format(record)
        data = json.loads(result)
        assert "exception" in data
        assert "ValueError" in data["exception"]


class TestStructuredLogger:
    def test_get_logger(self):
        logger = StructuredLogger.get_logger("test_module")
        assert isinstance(logger, logging.Logger)
        assert logger.name == "test_module"

    def test_get_logger_same_instance(self):
        logger1 = StructuredLogger.get_logger("test_module2")
        logger2 = StructuredLogger.get_logger("test_module2")
        assert logger1 is logger2

    def test_get_logger_with_level(self):
        logger = StructuredLogger.get_logger("test_debug", level=logging.DEBUG)
        assert logger.level == logging.DEBUG

    def test_get_logger_json_format(self):
        logger = StructuredLogger.get_logger("test_json", json_format=True)
        assert len(logger.handlers) > 0
        assert isinstance(logger.handlers[0].formatter, JsonFormatter)


class TestGetLogger:
    def test_get_logger_function(self):
        logger = get_logger("test_func")
        assert isinstance(logger, logging.Logger)

    def test_get_logger_with_kwargs(self):
        logger = get_logger("test_kwargs", level=logging.WARNING)
        assert logger.level == logging.WARNING


class TestSetupLogging:
    def test_setup_logging_info(self):
        setup_logging(level="INFO")
        root = logging.getLogger()
        assert root.level == logging.INFO

    def test_setup_logging_debug(self):
        setup_logging(level="DEBUG")
        root = logging.getLogger()
        assert root.level == logging.DEBUG

    def test_setup_logging_json(self):
        setup_logging(level="INFO", json_format=True)
        root = logging.getLogger()
        assert len(root.handlers) > 0
        assert isinstance(root.handlers[0].formatter, JsonFormatter)

    def test_setup_logging_invalid_level(self):
        setup_logging(level="INVALID")
        root = logging.getLogger()
        # 应该回退到默认 INFO
        assert root.level == logging.INFO
