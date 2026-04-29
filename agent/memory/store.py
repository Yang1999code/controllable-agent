"""agent/memory/store.py — MemoryStore 文件系统存储。

文件读写 + asyncio.Lock 并发保护。

核心公理（来自 GenericAgent）：
1. No Execution, No Memory — 只有工具验证过的才记
2. 神圣不可删改 — 验证过的信息不能丢
3. 禁止存储易变状态 — 不记临时数据
4. 最小充分指针 — 上层只留能定位下层的最短标识
"""

import asyncio
from pathlib import Path


class MemoryStore:
    """文件系统记忆存储。"""

    def __init__(self, base_path: str = ".agent-memory"):
        self.base_path = Path(base_path)
        self._lock = asyncio.Lock()

    def _resolve(self, path: str) -> Path:
        """解析为 base_path 下的绝对路径，防止路径穿越。"""
        resolved = (self.base_path / path).resolve()
        if not str(resolved).startswith(str(self.base_path.resolve())):
            raise ValueError(f"Path traversal detected: {path}")
        return resolved

    async def write(self, path: str, content: str) -> None:
        """写入文件。自动创建父目录。"""
        async with self._lock:
            full_path = self._resolve(path)
            full_path.parent.mkdir(parents=True, exist_ok=True)
            full_path.write_text(content, encoding="utf-8")

    async def read(self, path: str) -> str | None:
        """读取文件。不存在返回 None。"""
        full_path = self._resolve(path)
        if full_path.exists():
            return full_path.read_text(encoding="utf-8")
        return None

    async def delete(self, path: str) -> bool:
        """删除文件。"""
        async with self._lock:
            full_path = self._resolve(path)
            if full_path.exists():
                full_path.unlink()
                return True
            return False

    async def glob(self, pattern: str) -> list[Path]:
        """搜索文件。"""
        return list(self.base_path.glob(pattern))

    async def exists(self, path: str) -> bool:
        """检查文件是否存在。"""
        return self._resolve(path).exists()

    async def list_dir(self, path: str = "") -> list[str]:
        """列出目录中的文件和子目录名。"""
        full_path = self._resolve(path) if path else self.base_path
        if not full_path.exists():
            return []
        return [p.name for p in full_path.iterdir()]
