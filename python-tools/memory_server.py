"""
memory_server.py — ChromaDB 记忆服务器
通过 stdin/stdout JSON-RPC 提供 Episodic / Semantic 记忆的存储与检索。
嵌入使用 BGE-small-zh-v1.5。
"""
import json
import sys
import os
import uuid
from datetime import datetime, timezone
from typing import Any

os.environ["TOKENIZERS_PARALLELISM"] = "false"

MODEL_NAME = "BAAI/bge-small-zh-v1.5"
MAX_SEQ_LENGTH = 512
CHROMA_PATH = os.path.join(os.getcwd(), "data", "chroma")

_model = None
_chroma_client = None
_collections: dict[str, Any] = {}


def load_model():
    global _model
    if _model is None:
        from sentence_transformers import SentenceTransformer
        _model = SentenceTransformer(MODEL_NAME)
        _model.max_seq_length = MAX_SEQ_LENGTH
    return _model


def get_chroma():
    global _chroma_client
    if _chroma_client is None:
        import chromadb
        os.makedirs(CHROMA_PATH, exist_ok=True)
        _chroma_client = chromadb.PersistentClient(path=CHROMA_PATH)
    return _chroma_client


def get_collection(name: str):
    if name not in _collections:
        client = get_chroma()
        try:
            _collections[name] = client.get_collection(name)
        except Exception:
            _collections[name] = client.create_collection(
                name=name,
                metadata={"hnsw:space": "cosine"},
            )
    return _collections[name]


def embed_texts(texts: list[str]) -> list[list[float]]:
    model = load_model()
    embeddings = model.encode(texts, normalize_embeddings=True)
    return embeddings.tolist()


# ── 工具实现 ──────────────────────────────────────────────────────────

def episodic_store(arguments: dict) -> dict:
    """存储一条情景记忆"""
    content = arguments.get("content", "")
    if not content:
        return {"error": "content is required"}

    metadata = arguments.get("metadata", {})
    memory_id = f"ep-{uuid.uuid4().hex[:12]}"
    now = datetime.now(timezone.utc).isoformat()

    coll = get_collection("episodic_memory")
    embedding = embed_texts([content])[0]

    coll.add(
        ids=[memory_id],
        embeddings=[embedding],
        documents=[content],
        metadatas=[{
            **metadata,
            "memory_id": memory_id,
            "created_at": now,
            "type": "episodic",
        }],
    )
    return {"memory_id": memory_id, "status": "stored"}


def episodic_search(arguments: dict) -> dict:
    """搜索情景记忆"""
    query = arguments.get("query", "")
    n_results = arguments.get("n_results", 5)

    if not query:
        return {"error": "query is required"}

    coll = get_collection("episodic_memory")
    query_embedding = embed_texts([query])[0]

    results = coll.query(
        query_embeddings=[query_embedding],
        n_results=n_results,
    )

    memories = []
    if results["ids"] and results["ids"][0]:
        for i, mem_id in enumerate(results["ids"][0]):
            memories.append({
                "id": mem_id,
                "content": results["documents"][0][i] if results["documents"] else "",
                "distance": results["distances"][0][i] if results["distances"] else 0,
                "metadata": results["metadatas"][0][i] if results["metadatas"] else {},
            })

    return {"memories": memories, "count": len(memories)}


def semantic_store(arguments: dict) -> dict:
    """存储一条语义记忆（长期知识）"""
    content = arguments.get("content", "")
    if not content:
        return {"error": "content is required"}

    topic = arguments.get("topic", "general")
    metadata = arguments.get("metadata", {})
    memory_id = f"sm-{uuid.uuid4().hex[:12]}"
    now = datetime.now(timezone.utc).isoformat()

    coll = get_collection("semantic_memory")
    embedding = embed_texts([content])[0]

    coll.add(
        ids=[memory_id],
        embeddings=[embedding],
        documents=[content],
        metadatas=[{
            **metadata,
            "memory_id": memory_id,
            "topic": topic,
            "created_at": now,
            "type": "semantic",
        }],
    )
    return {"memory_id": memory_id, "status": "stored"}


def semantic_search(arguments: dict) -> dict:
    """搜索语义记忆"""
    query = arguments.get("query", "")
    n_results = arguments.get("n_results", 5)

    if not query:
        return {"error": "query is required"}

    coll = get_collection("semantic_memory")
    query_embedding = embed_texts([query])[0]

    results = coll.query(
        query_embeddings=[query_embedding],
        n_results=n_results,
    )

    memories = []
    if results["ids"] and results["ids"][0]:
        for i, mem_id in enumerate(results["ids"][0]):
            memories.append({
                "id": mem_id,
                "content": results["documents"][0][i] if results["documents"] else "",
                "distance": results["distances"][0][i] if results["distances"] else 0,
                "metadata": results["metadatas"][0][i] if results["metadatas"] else {},
            })

    return {"memories": memories, "count": len(memories)}


def memory_forget(arguments: dict) -> dict:
    """删除指定记忆"""
    memory_id = arguments.get("memory_id", "")
    if not memory_id:
        return {"error": "memory_id is required"}

    collection_name = arguments.get("collection", "episodic_memory")
    coll = get_collection(collection_name)

    try:
        coll.delete(ids=[memory_id])
        return {"status": "deleted", "memory_id": memory_id}
    except Exception as e:
        return {"error": str(e)}


def memory_stats(_arguments: dict) -> dict:
    """获取记忆统计信息"""
    stats = {}
    for name in ["episodic_memory", "semantic_memory"]:
        try:
            coll = get_collection(name)
            stats[name] = {"count": coll.count()}
        except Exception:
            stats[name] = {"count": 0}

    return {"collections": stats, "chroma_path": CHROMA_PATH}


def batch_store(arguments: dict) -> dict:
    """批量存储记忆"""
    memories = arguments.get("memories", [])
    collection_name = arguments.get("collection", "episodic_memory")

    if not memories:
        return {"error": "memories is required"}

    ids = []
    contents = []
    embeddings_list = []
    metadatas_list = []
    now = datetime.now(timezone.utc).isoformat()

    for mem in memories:
        content = mem.get("content", "")
        if not content:
            continue
        mem_id = mem.get("id", f"{collection_name[:2]}-{uuid.uuid4().hex[:12]}")
        ids.append(mem_id)
        contents.append(content)
        metadatas_list.append({
            **(mem.get("metadata", {})),
            "memory_id": mem_id,
            "created_at": now,
            "type": collection_name.replace("_memory", ""),
        })

    if not ids:
        return {"error": "no valid memories"}

    embeddings_list = embed_texts(contents)
    coll = get_collection(collection_name)
    coll.add(ids=ids, embeddings=embeddings_list, documents=contents, metadatas=metadatas_list)

    return {"status": "stored", "count": len(ids)}


# ── 请求路由 ──────────────────────────────────────────────────────────

TOOLS = {
    "episodic_store": {"fn": episodic_store, "desc": "Store an episodic memory"},
    "episodic_search": {"fn": episodic_search, "desc": "Search episodic memories by similarity"},
    "semantic_store": {"fn": semantic_store, "desc": "Store a semantic memory (long-term knowledge)"},
    "semantic_search": {"fn": semantic_search, "desc": "Search semantic memories by similarity"},
    "memory_forget": {"fn": memory_forget, "desc": "Delete a memory by ID"},
    "memory_stats": {"fn": memory_stats, "desc": "Get memory collection statistics"},
    "batch_store": {"fn": batch_store, "desc": "Batch store multiple memories"},
}


def handle_request(req: dict[str, Any]) -> dict[str, Any]:
    method = req.get("method", "")
    req_id = req.get("id")
    params = req.get("params", {})

    if method == "initialize":
        load_model()
        get_chroma()
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "capabilities": {"memory": True},
                "serverInfo": {"name": "memory-server", "model": MODEL_NAME},
            },
        }

    if method == "tools/list":
        tools = [
            {"name": name, "description": info["desc"]}
            for name, info in TOOLS.items()
        ]
        return {"jsonrpc": "2.0", "id": req_id, "result": {"tools": tools}}

    if method == "tools/call":
        tool_name = params.get("name", "")
        arguments = params.get("arguments", {})

        tool = TOOLS.get(tool_name)
        if not tool:
            return {"jsonrpc": "2.0", "id": req_id, "error": {"code": -1, "message": f"Unknown tool: {tool_name}"}}

        try:
            result = tool["fn"](arguments)
            return {"jsonrpc": "2.0", "id": req_id, "result": result}
        except Exception as e:
            return {"jsonrpc": "2.0", "id": req_id, "error": {"code": -1, "message": str(e)}}

    return {"jsonrpc": "2.0", "id": req_id, "error": {"code": -1, "message": f"Unknown method: {method}"}}


def main():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
            resp = handle_request(req)
            sys.stdout.write(json.dumps(resp, ensure_ascii=False) + "\n")
            sys.stdout.flush()
        except json.JSONDecodeError:
            continue
        except Exception as e:
            err_resp = {
                "jsonrpc": "2.0",
                "id": req.get("id") if "req" in locals() else None,
                "error": {"code": -1, "message": str(e)},
            }
            sys.stdout.write(json.dumps(err_resp, ensure_ascii=False) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
