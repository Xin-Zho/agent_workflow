"""
embedding_server.py — BGE-small-zh-v1.5 嵌入服务
通过 stdin/stdout JSON-RPC 协议提供文本向量化。
供 memory-extension 和 rag-extension 共用。
"""
import json
import sys
import os
from typing import Any

# 抑制 sentence-transformers 的冗余日志
os.environ["TOKENIZERS_PARALLELISM"] = "false"

MODEL_NAME = "BAAI/bge-small-zh-v1.5"
MAX_SEQ_LENGTH = 512
_model = None


def load_model():
    global _model
    if _model is None:
        from sentence_transformers import SentenceTransformer
        _model = SentenceTransformer(MODEL_NAME)
        _model.max_seq_length = MAX_SEQ_LENGTH
    return _model


def embed(texts: list[str]) -> list[list[float]]:
    model = load_model()
    # instruction prefix for BGE
    if isinstance(texts, str):
        texts = [texts]
    embeddings = model.encode(texts, normalize_embeddings=True)
    return embeddings.tolist()


def handle_request(req: dict[str, Any]) -> dict[str, Any]:
    method = req.get("method", "")
    req_id = req.get("id")
    params = req.get("params", {})

    if method == "initialize":
        load_model()  # 热加载模型
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "capabilities": {"embedding": True},
                "serverInfo": {"name": "bge-embedding-server", "model": MODEL_NAME},
            },
        }

    if method == "tools/list":
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "tools": [
                    {
                        "name": "embed",
                        "description": "Generate embeddings for one or more texts using BGE-small-zh-v1.5",
                        "inputSchema": {
                            "type": "object",
                            "properties": {
                                "texts": {
                                    "type": "array",
                                    "items": {"type": "string"},
                                    "description": "Array of text strings to embed",
                                }
                            },
                            "required": ["texts"],
                        },
                    },
                    {
                        "name": "embed_query",
                        "description": "Generate a single embedding for a query (optimized with instruction prefix)",
                        "inputSchema": {
                            "type": "object",
                            "properties": {
                                "text": {"type": "string", "description": "Query text"},
                            },
                            "required": ["text"],
                        },
                    },
                ]
            },
        }

    if method == "tools/call":
        tool_name = params.get("name", "")
        arguments = params.get("arguments", {})

        if tool_name == "embed":
            texts = arguments.get("texts", [])
            if not texts:
                return {"jsonrpc": "2.0", "id": req_id, "error": {"code": -1, "message": "texts is required"}}
            result = embed(texts)
            return {"jsonrpc": "2.0", "id": req_id, "result": {"embeddings": result, "dim": len(result[0]) if result else 0}}

        if tool_name == "embed_query":
            text = arguments.get("text", "")
            if not text:
                return {"jsonrpc": "2.0", "id": req_id, "error": {"code": -1, "message": "text is required"}}
            result = embed([text])
            return {"jsonrpc": "2.0", "id": req_id, "result": {"embedding": result[0], "dim": len(result[0])}}

        return {"jsonrpc": "2.0", "id": req_id, "error": {"code": -1, "message": f"Unknown tool: {tool_name}"}}

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
                "id": req.get("id") if "req" in dir() else None,
                "error": {"code": -1, "message": str(e)},
            }
            sys.stdout.write(json.dumps(err_resp, ensure_ascii=False) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
