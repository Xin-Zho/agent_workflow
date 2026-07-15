"""
rag_server.py — RAG 知识库服务器
通过 stdin/stdout JSON-RPC 提供文档摄入和检索。
支持公式感知分块（数学/化学公式不被切割）。
嵌入使用 BGE-small-zh-v1.5，ChromaDB 持久化。
"""
import json
import sys
import os
import re
import uuid
from datetime import datetime, timezone
from typing import Any

os.environ["TOKENIZERS_PARALLELISM"] = "false"

MODEL_NAME = "BAAI/bge-small-zh-v1.5"
MAX_SEQ_LENGTH = 512
CHUNK_SIZE = 400
CHUNK_OVERLAP = 80
CHROMA_PATH = os.path.join(os.getcwd(), "data", "chroma")
COLLECTION_NAME = "science_kb"

_model = None
_chroma_client = None
_collection = None


def load_model():
    global _model
    if _model is None:
        from sentence_transformers import SentenceTransformer
        _model = SentenceTransformer(MODEL_NAME)
        _model.max_seq_length = MAX_SEQ_LENGTH
    return _model


def get_collection():
    global _chroma_client, _collection
    if _chroma_client is None:
        import chromadb
        os.makedirs(CHROMA_PATH, exist_ok=True)
        _chroma_client = chromadb.PersistentClient(path=CHROMA_PATH)
    if _collection is None:
        try:
            _collection = _chroma_client.get_collection(COLLECTION_NAME)
        except Exception:
            _collection = _chroma_client.create_collection(
                name=COLLECTION_NAME,
                metadata={"hnsw:space": "cosine"},
            )
    return _collection


def embed_texts(texts: list[str]) -> list[list[float]]:
    model = load_model()
    embeddings = model.encode(texts, normalize_embeddings=True)
    return embeddings.tolist()


# ── 公式感知分块 ──────────────────────────────────────────────────────

# 识别公式块标记（LaTeX 模式）
FORMULA_PATTERN = re.compile(
    r'(\$\$[\s\S]*?\$\$|\$[^$\n]+?\$|\\\[[\s\S]*?\\\]|\\\([^)]+?\\\))'
)

# 识别化学方程式
CHEM_EQ_PATTERN = re.compile(
    r'[A-Z][a-z]?\d*(?:[A-Z][a-z]?\d*)*(?:\s*[-=→⇌]\s*[A-Z][a-z]?\d*(?:[A-Z][a-z]?\d*)*)+'
)


def is_formula_line(line: str) -> bool:
    """判断一行是否主要包含公式"""
    stripped = line.strip()
    if not stripped:
        return False
    if FORMULA_PATTERN.search(stripped):
        return True
    if CHEM_EQ_PATTERN.search(stripped) and len(stripped) > 10:
        return True
    return False


def chunk_text(text: str, chunk_size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> list[dict]:
    """
    公式感知分块：优先按段落切分，对长段落按句子切分，
    确保公式块不被切断。
    """
    # Step 1: 按段落分
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    chunks = []

    for para in paragraphs:
        # 整个段落全是公式 → 单独成块
        lines = para.split("\n")
        if all(is_formula_line(l) for l in lines if l.strip()):
            chunks.append({"text": para, "has_formula": True})
            continue

        # 段落较短 → 直接成块
        if len(para) <= chunk_size:
            chunks.append({"text": para, "has_formula": any(is_formula_line(l) for l in lines)})
            continue

        # 段落较长 → 按句子切分，保护公式
        sentences = re.split(r'(?<=[。！？.!?\n])\s*', para)
        current = ""
        has_formula = False

        for sent in sentences:
            sent = sent.strip()
            if not sent:
                continue

            if is_formula_line(sent):
                # 公式行：先输出当前块，公式单独成块（除非很短）
                if current.strip():
                    chunks.append({"text": current.strip(), "has_formula": has_formula})
                    current = ""
                    has_formula = False
                chunks.append({"text": sent, "has_formula": True})
                continue

            if len(current) + len(sent) > chunk_size and current:
                chunks.append({"text": current.strip(), "has_formula": has_formula})
                # overlap
                overlap_text = current[-overlap:] if len(current) > overlap else current
                current = overlap_text + " " + sent
                has_formula = is_formula_line(sent)
            else:
                current += (" " if current else "") + sent
                has_formula = has_formula or is_formula_line(sent)

        if current.strip():
            chunks.append({"text": current.strip(), "has_formula": has_formula})

    return chunks


# ── 工具实现 ──────────────────────────────────────────────────────────

def rag_search(arguments: dict) -> dict:
    """搜索知识库"""
    query = arguments.get("query", "")
    n_results = arguments.get("n_results", 5)
    topic_filter = arguments.get("topic")

    if not query:
        return {"error": "query is required"}

    coll = get_collection()
    query_embedding = embed_texts([query])[0]

    where_filter = None
    if topic_filter:
        where_filter = {"topic": topic_filter}

    results = coll.query(
        query_embeddings=[query_embedding],
        n_results=n_results,
        where=where_filter,
    )

    documents = []
    if results["ids"] and results["ids"][0]:
        for i, doc_id in enumerate(results["ids"][0]):
            doc = {
                "id": doc_id,
                "content": results["documents"][0][i] if results["documents"] else "",
                "distance": round(results["distances"][0][i], 4) if results["distances"] else 0,
                "metadata": results["metadatas"][0][i] if results["metadatas"] else {},
            }
            documents.append(doc)

    return {
        "documents": documents,
        "count": len(documents),
        "query": query,
    }


def rag_ingest(arguments: dict) -> dict:
    """摄入文档到知识库"""
    content = arguments.get("content", "")
    source = arguments.get("source", "unknown")
    topic = arguments.get("topic", "general")
    metadata = arguments.get("metadata", {})

    if not content:
        return {"error": "content is required"}

    chunks = chunk_text(content)

    if not chunks:
        return {"error": "no chunks generated"}

    ids = []
    texts = []
    metadatas_list = []
    now = datetime.now(timezone.utc).isoformat()
    doc_id = f"doc-{uuid.uuid4().hex[:12]}"

    for i, chunk in enumerate(chunks):
        chunk_id = f"{doc_id}-chunk-{i}"
        ids.append(chunk_id)
        texts.append(chunk["text"])
        metadatas_list.append({
            **metadata,
            "doc_id": doc_id,
            "chunk_index": i,
            "total_chunks": len(chunks),
            "source": source,
            "topic": topic,
            "has_formula": chunk["has_formula"],
            "ingested_at": now,
        })

    embeddings = embed_texts(texts)
    coll = get_collection()
    coll.add(ids=ids, embeddings=embeddings, documents=texts, metadatas=metadatas_list)

    return {
        "doc_id": doc_id,
        "chunks": len(chunks),
        "source": source,
        "topic": topic,
        "status": "ingested",
    }


def rag_stats(_arguments: dict) -> dict:
    """知识库统计"""
    coll = get_collection()
    count = coll.count()
    return {
        "collection": COLLECTION_NAME,
        "total_chunks": count,
        "path": CHROMA_PATH,
    }


def rag_remove(arguments: dict) -> dict:
    """删除文档及其所有分块"""
    doc_id = arguments.get("doc_id", "")
    if not doc_id:
        return {"error": "doc_id is required"}

    coll = get_collection()
    # 查询该文档的所有分块
    results = coll.get(where={"doc_id": doc_id})
    if results["ids"]:
        coll.delete(ids=results["ids"])
        return {"status": "deleted", "doc_id": doc_id, "chunks_removed": len(results["ids"])}
    return {"status": "not_found", "doc_id": doc_id}


# ── 请求路由 ──────────────────────────────────────────────────────────

TOOLS = {
    "rag_search": {"fn": rag_search, "desc": "Search the science knowledge base"},
    "rag_ingest": {"fn": rag_ingest, "desc": "Ingest a document with formula-aware chunking"},
    "rag_stats": {"fn": rag_stats, "desc": "Get knowledge base statistics"},
    "rag_remove": {"fn": rag_remove, "desc": "Remove a document and all its chunks"},
}


def handle_request(req: dict[str, Any]) -> dict[str, Any]:
    method = req.get("method", "")
    req_id = req.get("id")
    params = req.get("params", {})

    if method == "initialize":
        load_model()
        get_collection()
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "capabilities": {"rag": True},
                "serverInfo": {"name": "rag-server", "model": MODEL_NAME},
            },
        }

    if method == "tools/list":
        tools = [{"name": name, "description": info["desc"]} for name, info in TOOLS.items()]
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
