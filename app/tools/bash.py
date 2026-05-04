"""app/tools/bash.py — BashTool。

异步执行 shell 命令，超时 120s，返回 stdout+stderr+exit_code。
is_concurrency_safe=False —— 多个 shell 并行会冲突。

跨平台：Windows 自动使用 cmd.exe，Linux/macOS 使用 bash。
Windows 编码修复：强制 PYTHONUTF8=1 + chcp 65001。
"""

import asyncio
import os
import sys
from pathlib import Path

from ai.types import Context, ToolDefinition, ToolParameter, ToolResult

_IS_WINDOWS = sys.platform == "win32"


class BashTool:
    definition = ToolDefinition(
        name="bash",
        description=(
            "执行 shell 命令。返回 stdout、stderr 和退出码。"
            "Windows 上使用 cmd.exe，Linux/macOS 上使用 bash。"
        ),
        parameters=[
            ToolParameter(name="command", type="string",
                          description="要执行的 shell 命令", required=True),
            ToolParameter(name="timeout", type="integer",
                          description="超时秒数（默认 120）", required=False),
            ToolParameter(name="workdir", type="string",
                          description="工作目录", required=False),
        ],
    )
    is_concurrency_safe = False

    async def execute(self, args: dict, context: Context) -> ToolResult:
        command = args["command"]
        timeout = int(args.get("timeout", 120))
        workdir = args.get("workdir", "")

        try:
            cwd = str(Path(workdir).resolve()) if workdir else None

            # 构建子进程环境：强制 UTF-8
            env = os.environ.copy()
            env["PYTHONUTF8"] = "1"
            env["PYTHONIOENCODING"] = "utf-8"

            # Windows 下先切换代码页
            if _IS_WINDOWS:
                command = f"chcp 65001 >nul 2>&1 & {command}"

            proc = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=cwd,
                env=env,
            )
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                proc.communicate(), timeout=timeout
            )

            stdout = stdout_bytes.decode("utf-8", errors="replace")
            stderr = stderr_bytes.decode("utf-8", errors="replace")

            output_parts = []
            if stdout:
                output_parts.append(stdout)
            if stderr:
                output_parts.append(f"[stderr]\n{stderr}")
            output_parts.append(f"[exit_code: {proc.returncode}]")

            return ToolResult(
                tool_name="bash", success=proc.returncode == 0,
                content="\n".join(output_parts) or "(空输出)",
                error=None if proc.returncode == 0 else f"命令退出码: {proc.returncode}",
            )
        except asyncio.TimeoutError:
            return ToolResult(
                tool_name="bash", success=False,
                error=f"命令超时 ({timeout}s): {command[:100]}",
            )
        except Exception as e:
            return ToolResult(
                tool_name="bash", success=False,
                error=str(e),
            )
