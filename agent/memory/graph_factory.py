"""agent/memory/graph_factory.py — 图记忆后端工厂。

根据配置或命令行参数创建合适的 IGraphBackend 实例。
支持运行时切换，包含优雅降级逻辑。
"""

import logging
import os
from pathlib import Path

from agent.memory.store import MemoryStore
from agent.memory.graph_backend import (
    IGraphBackend,
    FileGraphBackend,
    Neo4jGraphBackend,
    FalkorDBGraphBackend,
    KuzuGraphBackend,
)

logger = logging.getLogger(__name__)

BACKEND_TYPES = frozenset({"file", "neo4j", "falkordb", "kuzu"})


def create_graph_backend(
    backend_type: str = "file",
    store: MemoryStore | None = None,
    relation_store=None,
    **kwargs,
) -> IGraphBackend:
    """创建图记忆后端实例。

    向后兼容：relation_store 参数为 Phase 1 的 RelationStore 实例。

    参数：
    - backend_type: "file" | "neo4j" | "falkordb" | "kuzu"
    - store: MemoryStore 实例（file/kuzu 后端需要）
    - relation_store: RelationStore 实例（file 后端复用）
    - kwargs: 后端特定参数
        * file: 无额外参数
        * neo4j: uri, user, password, database
        * falkordb: host, port, graph_name
        * kuzu: db_path

    返回：
    - IGraphBackend 实例

    异常：
    - ValueError: 不支持的 backend_type
    """
    backend_type = backend_type.lower().strip()

    if backend_type not in BACKEND_TYPES:
        raise ValueError(
            f"Unsupported backend type: '{backend_type}'. "
            f"Must be one of {BACKEND_TYPES}"
        )

    logger.info("Creating graph backend: %s", backend_type)

    if backend_type == "file":
        if store is None:
            memory_dir = os.path.expanduser("~/.agent-memory")
            store = MemoryStore(memory_dir)
        return FileGraphBackend(store, relation_store)

    elif backend_type == "kuzu":
        db_path = kwargs.get("db_path", ".agent-memory/kuzu_graph")
        return KuzuGraphBackend(db_path)

    elif backend_type == "neo4j":
        uri = kwargs.get("uri", os.environ.get("NEO4J_URI", "bolt://localhost:7687"))
        user = kwargs.get("user", os.environ.get("NEO4J_USER", "neo4j"))
        password = kwargs.get("password", os.environ.get("NEO4J_PASSWORD", "neo4j"))
        database = kwargs.get("database", "neo4j")
        return Neo4jGraphBackend(uri=uri, user=user, password=password, database=database)

    elif backend_type == "falkordb":
        host = kwargs.get("host", os.environ.get("FALKORDB_HOST", "localhost"))
        port = kwargs.get("port", int(os.environ.get("FALKORDB_PORT", "6379")))
        graph_name = kwargs.get("graph_name", "agent_memory")
        return FalkorDBGraphBackend(host=host, port=port, graph_name=graph_name)

    # 不应到达这里
    return FileGraphBackend(store or MemoryStore(".agent-memory"))


def create_graph_backend_from_config(
    config: dict,
    store: MemoryStore | None = None,
    relation_store=None,
) -> IGraphBackend:
    """从配置字典创建图记忆后端。

    配置格式（agent.yaml）：
    ```yaml
    memory:
      graph_backend: kuzu
      graph:
        kuzu:
          db_path: .agent-memory/kuzu_graph
        neo4j:
          uri: bolt://localhost:7687
          user: neo4j
          password: neo4j
        falkordb:
          host: localhost
          port: 6379
    ```
    """
    memory_config = config.get("memory", {})
    backend_type = memory_config.get("graph_backend", "file")
    graph_config = memory_config.get("graph", {}).get(backend_type, {})

    try:
        return create_graph_backend(
            backend_type=backend_type,
            store=store,
            relation_store=relation_store,
            **graph_config,
        )
    except Exception as e:
        logger.warning(
            "Failed to create %s graph backend: %s. Falling back to file backend.",
            backend_type, e,
        )
        return FileGraphBackend(
            store or MemoryStore(".agent-memory"),
            relation_store,
        )


def detect_available_backends() -> list[str]:
    """检测当前环境可用的图后端。

    检查可选依赖是否已安装。
    """
    available = ["file"]  # file 始终可用

    # 检查 Kuzu
    try:
        import kuzu  # noqa: F401
        available.append("kuzu")
    except ImportError:
        pass

    # 检查 Neo4j
    try:
        import neo4j  # noqa: F401
        available.append("neo4j")
    except ImportError:
        pass

    # 检查 FalkorDB
    try:
        import falkordb  # noqa: F401
        available.append("falkordb")
    except ImportError:
        pass

    return available
