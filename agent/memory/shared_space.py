"""agent/memory/shared_space.py — 共享区 MD 文件管理。

管理 shared/ 目录下的所有共享文件：plan.md, status/, decisions.md,
issues.md, interrupts/, skills/。
提供结构化的读写接口，避免各 Agent 直接操作裸文件路径。
"""

import logging
import time

from agent.memory.store import MemoryStore

logger = logging.getLogger(__name__)


class SharedSpace:
    """共享交流区管理器。

    封装 shared/ 目录下所有文件的结构化读写。
    内部使用 MemoryStore 做文件 I/O，天然有 asyncio.Lock 并发保护。
    """

    def __init__(self, store: MemoryStore):
        self._store = store

    # ── plan.md ──

    async def read_plan(self) -> str:
        """读取当前计划。"""
        content = await self._store.read("plan.md")
        return content or ""

    async def write_plan(self, content: str) -> None:
        """写入/更新计划。"""
        await self._store.write("plan.md", content)

    # ── status/ ──

    async def read_status(self, agent_id: str) -> str:
        """读取指定 Agent 的状态文件。"""
        content = await self._store.read(f"status/{agent_id}.md")
        return content or ""

    async def write_status(self, agent_id: str, content: str) -> None:
        """写入/更新指定 Agent 的状态。"""
        await self._store.write(f"status/{agent_id}.md", content)

    async def list_statuses(self) -> list[str]:
        """列出所有有状态文件的 Agent ID。"""
        files = await self._store.glob("status/*.md")
        return [f.name.replace(".md", "") for f in files]

    # ── decisions.md ──

    async def read_decisions(self) -> str:
        """读取决策记录。"""
        content = await self._store.read("decisions.md")
        return content or ""

    async def append_decision(self, agent_id: str, decision: str) -> None:
        """追加一条决策记录。"""
        existing = await self.read_decisions()
        timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
        entry = f"\n## [{timestamp}] {agent_id}\n{decision}\n"
        await self._store.write("decisions.md", existing + entry)

    async def write_decisions(self, content: str) -> None:
        """覆盖写入决策记录。"""
        await self._store.write("decisions.md", content)

    # ── issues.md ──

    async def read_issues(self) -> str:
        """读取问题记录。"""
        content = await self._store.read("issues.md")
        return content or ""

    async def append_issue(self, agent_id: str, issue: str, tag: str = "") -> None:
        """追加一条问题记录。tag 可用于标注 [INTEGRATION] 等。"""
        existing = await self.read_issues()
        prefix = f"{tag} " if tag else ""
        timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
        entry = f"\n## {prefix}[{timestamp}] {agent_id}\n{issue}\n"
        await self._store.write("issues.md", existing + entry)

    # ── interrupts/ ──

    async def create_interrupt(
        self, content: str, priority: str = "medium",
    ) -> str:
        """创建用户中途补充信息。返回文件 ID。"""
        files = await self._store.glob("interrupts/intr_*.md")
        max_num = 0
        for f in files:
            name = f.name
            num_part = name.replace("intr_", "").replace(".md", "").replace(".done", "")
            try:
                max_num = max(max_num, int(num_part))
            except ValueError:
                pass
        interrupt_id = f"intr_{max_num + 1:03d}"
        timestamp = time.strftime("%Y-%m-%dT%H:%M:%S")
        md_content = (
            f"---\ntimestamp: \"{timestamp}\"\npriority: {priority}\nstatus: pending\n---\n\n"
            f"{content}\n"
        )
        await self._store.write(f"interrupts/{interrupt_id}.md", md_content)
        return interrupt_id

    async def read_interrupt(self, interrupt_id: str) -> str | None:
        """读取指定 interrupt 内容。"""
        return await self._store.read(f"interrupts/{interrupt_id}.md")

    async def list_pending_interrupts(self) -> list[str]:
        """列出所有未处理的 interrupt ID。"""
        files = await self._store.glob("interrupts/intr_*.md")
        pending: list[str] = []
        for f in files:
            if ".done." not in f.name:
                pending.append(f.name.replace(".md", ""))
        return sorted(pending)

    async def mark_interrupt_done(self, interrupt_id: str) -> bool:
        """标记 interrupt 为已处理（重命名）。"""
        old_path = f"interrupts/{interrupt_id}.md"
        content = await self._store.read(old_path)
        if content is None:
            return False
        done_path = f"interrupts/{interrupt_id}.done.md"
        done_content = content.replace("status: pending", "status: done")
        await self._store.write(done_path, done_content)
        await self._store.delete(old_path)
        return True

    # ── skills/ ──

    async def write_skill(self, skill_name: str, content: str) -> None:
        """写入共享技能文件。"""
        ext = ".md" if not skill_name.endswith((".md", ".yaml", ".yml")) else ""
        await self._store.write(f"skills/{skill_name}{ext}", content)

    async def read_skill(self, skill_name: str) -> str | None:
        """读取共享技能文件。"""
        ext = ".md" if not skill_name.endswith((".md", ".yaml", ".yml")) else ""
        return await self._store.read(f"skills/{skill_name}{ext}")

    async def list_skills(self) -> list[str]:
        """列出所有共享技能文件名。"""
        files = await self._store.glob("skills/*")
        return [f.name for f in files]

    async def delete_skill(self, skill_name: str) -> bool:
        """删除共享技能文件。"""
        ext = ".md" if not skill_name.endswith((".md", ".yaml", ".yml")) else ""
        return await self._store.delete(f"skills/{skill_name}{ext}")

    # ── skill proposals（Memorizer 提案，等用户确认）──

    async def write_skill_proposal(self, proposal_id: str, content: str) -> None:
        """写入技能提案到 interrupts/ 目录（等用户确认）。"""
        await self._store.write(f"interrupts/{proposal_id}.md", content)

    # ── 通用 ──

    async def initialize(self) -> None:
        """初始化共享区目录结构。"""
        await self.write_plan("# 当前计划\n\n（等待 Planner 写入）\n")
        await self.write_decisions("# 决策记录\n")
        await self._store.write("issues.md", "# 问题记录\n")
