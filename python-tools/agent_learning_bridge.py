"""
agent_learning_bridge.py — agent_learning 记忆/RAG 薄桥接
通过 stdin/stdout JSON-RPC 协议，直接调用 agent_learning 成熟的 MemoryManager 和 RAG 系统。
复用 D:\agent_learning\data\chroma 共享 ChromaDB 实例，不重复造轮子。

请求格式: {"id": "...", "method": "memory_add", "params": {...}}
响应格式: {"id": "...", "result": {...}} 或 {"id": "...", "error": "..."}
"""

import json
import sys
import os
import asyncio

# 注入 agent_learning 到 Python path，直接复用其成熟实现
AGENT_LEARNING_ROOT = r"D:\agent_learning"
sys.path.insert(0, AGENT_LEARNING_ROOT)
os.chdir(AGENT_LEARNING_ROOT)  # MemoryManager 内部用相对路径 data/chroma

from backend.memory import MemoryManager, MemoryConfig
from backend.memory.science_kb_ingest import (
    ingest_science_document,
    search_knowledge,
    get_collection_count,
    delete_collection,
)

USER_ID = int(os.environ.get("MEMORY_USER_ID", "1"))
_mgr: MemoryManager | None = None


def get_manager() -> MemoryManager:
    global _mgr
    if _mgr is None:
        _mgr = MemoryManager(user_id=USER_ID)
    return _mgr


# ── JSON-RPC 路由表 ──

async def dispatch(method: str, params: dict) -> dict:
    mgr = get_manager()

    # ── 记忆写入 ──
    if method == "memory_add":
        memory_id = await mgr.add(
            content=params["content"],
            memory_type=params.get("memory_type", "episodic"),
            importance=params.get("importance", 0.5),
            metadata=params.get("metadata"),
        )
        return {"memory_id": memory_id}

    # ── 记忆检索 ──
    elif method == "memory_search":
        items = await mgr.search(
            query=params["query"],
            memory_types=params.get("memory_types"),
            limit=params.get("limit", 5),
        )
        return {"items": [it.to_dict() if hasattr(it, "to_dict") else it for it in items]}

    # ── 获取上下文（用于 prompt 注入） ──
    elif method == "memory_get_context":
        ctx = await mgr.get_context_for_prompt(params.get("query", ""))
        return {"context": ctx}

    # ── 获取 Working 上下文 ──
    elif method == "memory_get_working":
        items = await mgr.get_working_context()
        return {"items": [it.to_dict() if hasattr(it, "to_dict") else it for it in items]}

    # ── 记忆固化 (Episodic → Semantic) ──
    elif method == "memory_consolidate":
        count = await mgr.consolidate(
            from_type=params.get("from_type", "episodic"),
            to_type=params.get("to_type", "semantic"),
            threshold=params.get("threshold"),
        )
        return {"consolidated_count": count}

    # ── 遗忘 ──
    elif method == "memory_forget":
        count = await mgr.forget(strategy=params.get("strategy", "importance_based"))
        return {"forgotten_count": count}

    # ── memory_stats ──
    elif method == "memory_stats":
        # 获取各类型记忆的大致数量
        episodic = await mgr.search("", memory_types=["episodic"], limit=1000)
        semantic = await mgr.search("", memory_types=["semantic"], limit=1000)
        working = await mgr.get_working_context()
        return {
            "episodic_count": len(episodic),
            "semantic_count": len(semantic),
            "working_count": len(working),
        }

    # ── RAG 检索 ──
    elif method == "rag_search":
        result = search_knowledge(
            query=params["query"],
            collection=params.get("collection", "all"),
            top_k=params.get("top_k", 5),
        )
        return {"results": result}

    # ── RAG 摄入 ──
    elif method == "rag_ingest":
        result = ingest_science_document(
            content=params["content"],
            source_url=params.get("source", "user-provided"),
            title=params.get("title", ""),
            collection=params.get("collection", "science_kb"),
        )
        return {"result": result}

    # ── RAG 统计 ──
    elif method == "rag_stats":
        count = get_collection_count(params.get("collection", "all"))
        return {"count": count}

    # ── RAG 删除集合 ──
    elif method == "rag_remove":
        result = delete_collection(params.get("collection", ""))
        return {"result": result}

    else:
        return None  # 未知方法


# ── 主循环 ──

async def main():
    """stdin/stdout JSON-RPC 主循环"""
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
            req_id = req.get("id")
            method = req.get("method", "")
            params = req.get("params", {})

            result = await dispatch(method, params)
            if result is None:
                resp = {"id": req_id, "error": f"Unknown method: {method}"}
            else:
                resp = {"id": req_id, "result": result}

            sys.stdout.write(json.dumps(resp, ensure_ascii=False, default=str) + "\n")
            sys.stdout.flush()

        except json.JSONDecodeError as e:
            sys.stderr.write(f"JSON parse error: {e}\n")
            sys.stderr.flush()
        except Exception as e:
            resp = {"id": req.get("id") if "req" in dir() else None, "error": str(e)}
            sys.stdout.write(json.dumps(resp, ensure_ascii=False) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    asyncio.run(main())
