# Agent Workflow — Material Science Research Agent

## Project Overview

This project extends Pi Agent with a persistent material-science literature workflow.
The main product path is target performance → structure → laboratory-feasible process
system → composition/ratio. Scientific computing and the `agent_learning` bridge are
supporting capabilities.

## Workflow invariants

- Abstract screening must stop at `WAITING_PAPER_APPROVAL` for user confirmation.
- Full-text extractions must stop at `WAITING_DATA_REVIEW` for owner/admin review.
- Abstract-only claims are low-evidence and cannot be promoted as full-text facts.
- Store original and normalized units/ratio bases; never normalize before comparability checks.
- A paper failure degrades independently and must not block the task.
- Keep user sessions isolated; only reviewed objective knowledge is shared.
- Preserve extraction versions and audit events.

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
- **Workflow core**: `./python-tools/workflow_engine.py` (SQLite state machine + FIFO)
- **Workflow routes**: `./python-tools/workflow_api.py` (`/api/research/*`)
- **Workflow database**: `./data/workflow.db` (override with `WORKFLOW_DB_PATH`)

## Dependencies

- Pi Agent installed in WSL (`pi` command available)
- Python 3.13+ with: sympy, pint, mendeleev, mcp, chromadb, fastapi, uvicorn
- Ollama or another Pi-supported local inference server
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

### Workflow tests
```bash
python3 -m unittest discover -s tests -v
```
