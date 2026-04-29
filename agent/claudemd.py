"""agent/claudemd.py — CLAUDE.md 发现与加载。

零依赖模块（仅 pathlib），在 Phase 1 提前实现。
从项目层级目录中向上遍历，收集所有 CLAUDE.md 文件，按优先级组装为上下文片段。

参考：CCB claudemd.ts / andrej-karpathy-skills CLAUDE.md / opencode .opencode/rules/
"""

from dataclasses import dataclass
from pathlib import Path


@dataclass
class ClaudeMdFile:
    """一个 CLAUDE.md 文件及其元数据。"""

    path: str
    content: str
    level: str  # "user" | "project"
    priority: int  # 数字越小优先级越低


def discover_claude_mds(
    cwd: str | None = None,
    user_home: str | None = None,
) -> list[ClaudeMdFile]:
    """从项目层级中发现所有 CLAUDE.md 文件。

    发现顺序（优先级从低到高）：
    1. 用户级：~/.agent/CLAUDE.md（全局行为指南）
    2. 项目级：从项目根目录向 CWD 逐级向上遍历

    V1 简化：
    - 不做 @include 指令解析
    - 不做 frontmatter 解析
    - 不做条件规则（paths glob）
    - 文件大小限制：单文件最大 50KB
    """
    cwd = Path(cwd or Path.cwd()).resolve()
    user_home = Path(user_home or Path.home())

    results: list[ClaudeMdFile] = []

    # 1. 用户级 CLAUDE.md（最低优先级，优先加载）
    user_claude_md = user_home / ".agent" / "CLAUDE.md"
    if user_claude_md.exists():
        content = _read_safe(user_claude_md)
        if content:
            results.append(ClaudeMdFile(
                path=str(user_claude_md),
                content=content,
                level="user",
                priority=0,
            ))

    # 2. 项目级：从 CWD 向上遍历
    project_root = _find_project_root(cwd)
    directories = _collect_directories(cwd, project_root)

    for idx, directory in enumerate(directories):
        # 检查 {dir}/CLAUDE.md
        claude_md = directory / "CLAUDE.md"
        if claude_md.exists():
            content = _read_safe(claude_md)
            if content:
                results.append(ClaudeMdFile(
                    path=str(claude_md),
                    content=content,
                    level="project",
                    priority=idx + 1,
                ))

        # 检查 {dir}/.agent/CLAUDE.md
        agent_claude_md = directory / ".agent" / "CLAUDE.md"
        if agent_claude_md.exists():
            content = _read_safe(agent_claude_md)
            if content:
                results.append(ClaudeMdFile(
                    path=str(agent_claude_md),
                    content=content,
                    level="project",
                    priority=idx + 1,
                ))

    results.sort(key=lambda f: f.priority)
    return results


def assemble_claude_md_content(files: list[ClaudeMdFile]) -> str:
    """将多个 CLAUDE.md 文件组装为单个上下文字符串。"""
    if not files:
        return ""

    parts = ["# CLAUDE.md\n"]
    for f in files:
        if f.level == "user":
            desc = "user's global instructions for all projects"
        else:
            desc = "project instructions, checked into the codebase"

        parts.append(f"Contents of {f.path} ({desc}):\n")
        parts.append(f.content)
        parts.append("")

    return "\n".join(parts)


def _find_project_root(cwd: Path) -> Path:
    """查找项目根目录（.git 所在目录或文件系统根目录）。"""
    current = cwd
    while current != current.parent:
        if (current / ".git").exists():
            return current
        current = current.parent
    return cwd


def _collect_directories(cwd: Path, project_root: Path) -> list[Path]:
    """从 project_root 到 CWD 的目录列表（根目录在前，CWD 在最后）。"""
    directories = []
    current = cwd
    while current != project_root.parent:
        directories.append(current)
        if current == project_root:
            break
        current = current.parent
    directories.reverse()
    return directories


def _read_safe(path: Path, max_bytes: int = 50_000) -> str | None:
    """安全读取文件。超过大小限制返回 None，编码错误返回 None。"""
    try:
        if path.stat().st_size > max_bytes:
            return None
        return path.read_text(encoding="utf-8")
    except Exception:
        return None
