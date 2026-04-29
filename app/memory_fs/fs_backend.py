"""app/memory_fs/fs_backend.py — FileSystemMemoryBackend 具体实例化。

便捷的工厂函数，创建绑定到特定项目目录的记忆后端。
"""

from agent.memory.store import MemoryStore
from agent.memory.backend import FileSystemMemoryBackend


def create_memory_backend(
    base_path: str = ".agent-memory",
    project: str = "default",
) -> FileSystemMemoryBackend:
    """创建文件系统记忆后端。

    Args:
        base_path: 记忆根目录
        project: 项目名（子目录）

    Returns:
        配置好的 FileSystemMemoryBackend 实例
    """
    store = MemoryStore(base_path)
    return FileSystemMemoryBackend(store, project)
