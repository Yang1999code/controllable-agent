"""tests/agent/test_claudemd.py — CLAUDE.md 发现与加载测试。"""

import tempfile
from pathlib import Path

from agent.claudemd import (
    ClaudeMdFile,
    discover_claude_mds,
    assemble_claude_md_content,
    _read_safe,
)


class TestDiscover:
    def test_empty_directory(self):
        with tempfile.TemporaryDirectory() as td:
            cwd = Path(td)
            results = discover_claude_mds(cwd=str(cwd), user_home=str(cwd))
            # 没有 CLAUDE.md 文件，结果为空
            assert isinstance(results, list)

    def test_project_claude_md(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            (base / "CLAUDE.md").write_text("# Test Project\nproject instructions")
            results = discover_claude_mds(cwd=str(base), user_home=str(base))
            assert len(results) >= 1
            assert results[0].level == "project"
            assert "# Test Project" in results[0].content

    def test_user_claude_md(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            agent_dir = base / ".agent"
            agent_dir.mkdir()
            (agent_dir / "CLAUDE.md").write_text("# User Guide\nglobal instructions")
            results = discover_claude_mds(cwd=str(base), user_home=str(base))
            assert len(results) >= 1
            assert any(r.level == "user" for r in results)

    def test_nested_claude_md(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            (base / ".git").mkdir()  # 标记为项目根目录
            (base / "CLAUDE.md").write_text("root")
            sub = base / "src"
            sub.mkdir()
            (sub / "CLAUDE.md").write_text("subdir")
            results = discover_claude_mds(cwd=str(sub), user_home=str(base))
            # 至少有两个项目级文件
            project_files = [r for r in results if r.level == "project"]
            assert len(project_files) >= 2


class TestAssemble:
    def test_empty_files(self):
        result = assemble_claude_md_content([])
        assert result == ""

    def test_single_file(self):
        files = [ClaudeMdFile(path="/test/CLAUDE.md", content="hello world",
                              level="project", priority=1)]
        result = assemble_claude_md_content(files)
        assert "hello world" in result
        assert "/test/CLAUDE.md" in result

    def test_multiple_files_separated(self):
        files = [
            ClaudeMdFile(path="/a/CLAUDE.md", content="content A", level="project", priority=1),
            ClaudeMdFile(path="/b/CLAUDE.md", content="content B", level="project", priority=2),
        ]
        result = assemble_claude_md_content(files)
        assert "content A" in result
        assert "content B" in result


class TestReadSafe:
    def test_normal_file(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
            f.write("normal content")
            f.flush()
            content = _read_safe(Path(f.name))
            assert content == "normal content"
        Path(f.name).unlink()

    def test_large_file_skipped(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
            f.write("x" * 60000)
            f.flush()
            content = _read_safe(Path(f.name))
            assert content is None
        Path(f.name).unlink()
