# Agent Workflow — Scientific Computing Agent

## Project Overview

This project extends Pi Agent (by earendil-works) with scientific computing capabilities,
bridging the mature Python-based computation stack from `agent_learning` into Pi's
TypeScript extension system.

## Architecture

```
Pi Agent (TypeScript, CLI/TUI)
  └── Extensions (.pi/extensions/)
        ├── science-extension  → MCP subprocess (chemistry_server.py, physics_server.py)
        ├── memory-extension    → agent_learning_bridge.py → MemoryManager + ChromaDB
        ├── rag-extension       → agent_learning_bridge.py → science_kb_ingest + ChromaDB
        ├── evaluation-extension → pure TypeScript (LLM Judge)
        ├── mcp-extension       → generic MCP server manager
        └── shared/mcp-bridge.ts → MCP 2024-11-05 JSON-RPC utilities
```

## Key Paths

- **agent_learning root**: `D:\agent_learning` (Python source, ChromaDB data)
- **ChromaDB data**: `D:\agent_learning\data\chroma` (shared instance)
- **Python tools**: `./python-tools/` (MCP servers, bridge scripts)
- **Web API**: `./python-tools/web_api_server.py` (FastAPI + Pi RPC bridge)

## Dependencies

- Pi Agent installed in WSL (`pi` command available)
- Python 3.13+ with: sympy, pint, mendeleev, mcp, chromadb, fastapi, uvicorn
- Ollama running locally with qwen2.5:7b model
- agent_learning project at `D:\agent_learning` (for MemoryManager and RAG)

## Usage

### CLI Mode (Pi TUI)
```bash
cd /d/agent_workflow
pi
```

### Web API Mode
```bash
cd python-tools
python web_api_server.py --port 8000
# Then open http://localhost:8000 in browser
```
