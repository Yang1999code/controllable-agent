"""agent/logger.py — 结构化日志模块。

支持：
- JSON 格式日志（生产环境）
- 彩色终端日志（开发环境）
- 日志级别动态调整
- 请求追踪 ID
"""

import json
import logging
import sys
import time
from logging import Handler, LogRecord
from typing import Any


class ColoredFormatter(logging.Formatter):
    """终端彩色日志格式化器。"""

    COLORS = {
        logging.DEBUG: "\033[36m",     # 青色
        logging.INFO: "\033[32m",      # 绿色
        logging.WARNING: "\033[33m",   # 黄色
        logging.ERROR: "\033[31m",     # 红色
        logging.CRITICAL: "\033[35m",  # 紫色
    }
    RESET = "\033[0m"
    BOLD = "\033[1m"

    def format(self, record: logging.LogRecord) -> str:
        color = self.COLORS.get(record.levelno, self.RESET)
        record.levelname = f"{self.BOLD}{color}{record.levelname}{self.RESET}"
        record.module = f"{color}{record.module}{self.RESET}"
        return super().format(record)


class JsonFormatter(logging.Formatter):
    """JSON 格式日志（适合生产环境）。"""

    def format(self, record: LogRecord) -> str:
        log_data: dict[str, Any] = {
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(record.created)),
            "level": record.levelname,
            "module": record.module,
            "function": record.funcName,
            "line": record.lineno,
            "message": record.getMessage(),
        }

        # 添加额外字段
        if hasattr(record, "trace_id"):
            log_data["trace_id"] = record.trace_id
        if hasattr(record, "agent_id"):
            log_data["agent_id"] = record.agent_id
        if hasattr(record, "duration_ms"):
            log_data["duration_ms"] = record.duration_ms

        # 异常信息
        if record.exc_info:
            log_data["exception"] = self.formatException(record.exc_info)

        return json.dumps(log_data, ensure_ascii=False)


class StructuredLogger:
    """结构化日志记录器。

    用法：
        from agent.logger import get_logger
        logger = get_logger(__name__)
        logger.info("Agent started", agent_id="main", duration_ms=100)
    """

    _loggers: dict[str, logging.Logger] = {}

    @classmethod
    def get_logger(
        cls,
        name: str,
        level: int = logging.INFO,
        json_format: bool = False,
    ) -> logging.Logger:
        """获取或创建日志记录器。"""
        if name in cls._loggers:
            return cls._loggers[name]

        logger = logging.getLogger(name)
        logger.setLevel(level)

        # 避免重复添加 handler
        if not logger.handlers:
            handler = logging.StreamHandler(sys.stdout)
            handler.setLevel(level)

            if json_format:
                handler.setFormatter(JsonFormatter())
            else:
                formatter = ColoredFormatter(
                    "%(asctime)s %(levelname)s [%(module)s:%(lineno)d] %(message)s",
                    datefmt="%H:%M:%S",
                )
                handler.setFormatter(formatter)

            logger.addHandler(handler)

        cls._loggers[name] = logger
        return logger


def get_logger(name: str, **kwargs) -> logging.Logger:
    """快捷函数：获取日志记录器。"""
    return StructuredLogger.get_logger(name, **kwargs)


def setup_logging(
    level: str = "INFO",
    json_format: bool = False,
) -> None:
    """配置全局日志。

    Args:
        level: 日志级别 (DEBUG/INFO/WARNING/ERROR)
        json_format: 是否使用 JSON 格式
    """
    log_level = getattr(logging, level.upper(), logging.INFO)

    root_logger = logging.getLogger()
    root_logger.setLevel(log_level)

    # 清除现有 handlers
    root_logger.handlers.clear()

    handler = logging.StreamHandler(sys.stdout)
    handler.setLevel(log_level)

    if json_format:
        handler.setFormatter(JsonFormatter())
    else:
        formatter = ColoredFormatter(
            "%(asctime)s %(levelname)s [%(name)s] %(message)s",
            datefmt="%H:%M:%S",
        )
        handler.setFormatter(formatter)

    root_logger.addHandler(handler)
