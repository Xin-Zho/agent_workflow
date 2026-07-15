# science-pi — 科学计算 Pi Agent

> 基于 Pi Agent（TypeScript CLI/TUI）底座，
> 集成 agent_learning 的科学计算、三层记忆、RAG、评估能力。
> 记忆/RAG 直接桥接 agent_learning 成熟实现，共享 ChromaDB 实例。

## 项目结构

```
.pi/
├── extensions/
│   ├── shared/
│   │   └── mcp-bridge.ts              # 共享 MCP/JSON-RPC 桥接（标准 MCP 2024-11-05）
│   ├── science-extension/
│   │   └── index.ts                   # 科学计算：13 个化学/物理工具
│   ├── mcp-extension/                 ★ Phase 3 新增
│   │   ├── index.ts                   # 通用 MCP 管理器（自动连接/发现/注册）
│   │   └── servers.json               # MCP server 配置
│   ├── memory-extension/
│   │   └── index.ts                   # 三层记忆：桥接 agent_learning MemoryManager
│   ├── rag-extension/
│   │   └── index.ts                   # RAG：桥接 agent_learning science_kb_ingest
│   └── evaluation-extension/
│       └── index.ts                   # 评估：metrics/evaluate/stats
├── skills/
│   └── scientific-method.md           # 科学解题方法论
└── templates/
    └── compute-verify-output.md       # 提示模板

python-tools/
├── web_api_server.py                  ★ Phase 3 新增（FastAPI + Pi RPC Bridge）
├── agent_learning_bridge.py           # 统一桥接（复用 agent_learning 记忆/RAG）
├── chemistry_server.py                # 化学 MCP 服务器（sympy+mendeleev）
├── physics_server.py                  # 物理 MCP 服务器（sympy+pint）
├── embedding_server.py                # BGE 嵌入服务（独立，可选）
├── verify_tools.py                    # 验证链工具（从 agent_learning 复用）
├── science_kb_ingest.py               # 知识库摄入管道（从 agent_learning 复用）
└── requirements.txt                   # Python 依赖
```

## 架构

```
┌──────────────────────────────────────────────────────────────────────┐
│  Pi Agent（CLI / TUI）                                               │
│  ┌────────────────────────────────────────────────────────────────┐ │
│  │  双层 Agent 循环（steering + followUp）                          │ │
│  └────────────────────────────────────────────────────────────────┘ │
│  ┌────────────────────────────────────────────────────────────────┐ │
│  │  Extension 层                                                   │ │
│  │  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐          │ │
│  │  │ Science  │ │ MCP Mgr  │ │ Memory   │ │ Eval     │          │ │
│  │  │ 13 tools │ │通用管理器│ │ 5 tools  │ │ 2 tools  │          │ │
│  │  │          │ │ 2 tools  │ │          │ │          │          │ │
│  │  └────┬─────┘ └────┬─────┘ └────┬─────┘ └────┬─────┘          │ │
│  └───────┼────────────┼────────────┼────────────┼────────────────┘ │
│          │            │            │            │                   │
│  ┌───────┼────────────┼────────────┼────────────┼────────────────┐ │
│  │ MCP Bridge (标准 JSON-RPC 2.0 2024-11-05)                     │ │
│  └───────┼────────────┼────────────┼────────────┼────────────────┘ │
│          ▼            ▼            ▼            ▼                   │
│  ┌──────────┐ ┌──────────┐ ┌─────────────────┐ ┌──────────────┐   │
│  │Chemistry │ │ Physics  │ │ agent_learning   │ │ Evaluation   │   │
│  │Server.py │ │Server.py │ │ Bridge.py        │ │ (纯 TS)      │   │
│  │sympy     │ │sympy     │ │                  │ │              │   │
│  │mendeleev │ │pint      │ │ → MemoryManager  │ │ hooks +      │   │
│  └──────────┘ └──────────┘ │ → science_kb     │ │ LLM Judge    │   │
│                            │                  │ └──────────────┘   │
│  ┌──────────────────────┐  │ D:\agent_learning\                   │   │
│  │ Web API Server  ★    │  │ data\chroma\     ← 共享 ChromaDB    │   │
│  │ FastAPI :8000        │  │ ├── episodic_all                     │   │
│  │ Pi RPC Bridge        │  │ ├── semantic_all                     │   │
│  │ POST /api/chat (SSE) │  │ └── science_kb                       │   │
│  │ WebSocket /ws        │  └──────────────────────────────────────┘   │
│  └──────────────────────┘                                            │
└──────────────────────────────────────────────────────────────────────┘
```

## 扩展详情

| 扩展 | 工具数 | Python 后端 | 说明 |
|------|:---:|------|------|
| science-extension | 13 | chemistry_server + physics_server | 化学：配平/元素/溶液/pH/热力学/平衡/动力学/电化学；物理：力学/电磁/量子/热力学/光学/误差 |
| **mcp-extension** ★ | **2 管理** | 通用 MCP 客户端 | `mcp_list` / `mcp_reconnect`；自动连接 servers.json 配置的 MCP servers，发现工具并注册 |
| memory-extension | 5 | agent_learning_bridge → MemoryManager | remember/recall/forget/consolidate/stats；复用 agent_learning 三层记忆 + 上下文注入 |
| rag-extension | 4 | agent_learning_bridge → science_kb_ingest | search/ingest/stats/remove；公式感知分块、权威来源、混合检索 |
| evaluation-extension | 2 | (纯TS) | evaluate/eval_stats；turn_end/agent_end/tool_call 钩子 |

## Web API ★ Phase 3

```bash
# 安装依赖
pip install -r python-tools/requirements.txt

# 启动
python python-tools/web_api_server.py --port 8000

# 端点
POST /api/auth/token       # 获取 JWT token
POST /api/chat             # SSE 流式对话
POST /api/chat/sync         # 同步对话
GET  /api/sessions          # 会话状态
POST /api/sessions/new      # 新建会话
WebSocket /ws               # 实时双向通信
GET  /api/health            # 健康检查
```

## 关键设计决策

- **记忆/RAG 不重写**：直接桥接 agent_learning 成熟实现，通过 `agent_learning_bridge.py`（stdin/stdout JSON-RPC）
- **共享 ChromaDB**：复用 `D:\agent_learning\data\chroma`，所有 collection（episodic_all / semantic_all / science_kb）在原位
- **MCP 标准协议**：完整实现 MCP 2024-11-05 握手（initialize → notifications/initialized → tools/list → tools/call），与 agent_learning 的 client_manager.py 完全对齐
- **science-extension 已重构**：消除重复代码，复用 shared/mcp-bridge.ts
- **mcp-extension 通用管理**：读取 servers.json 配置，自动连接任意 MCP server 并注册工具；与 science-extension 互补（通用 vs 专用）
- **Web API**：FastAPI 服务器通过 Pi RPC 模式（JSONL stdin/stdout）桥接，支持 SSE 流式 + WebSocket + JWT 认证

## Phase 进度

- [x] **Phase 1** — 项目骨架 + 科学计算
- [x] **Phase 2** — 记忆 + RAG + 评估（修复：桥接 agent_learning 代替自定义实现）
- [x] **Phase 3** — MCP 完整协议 + Web API
   - [x] mcp-bridge.ts 修复（完整 MCP 2024-11-05 协议）
   - [x] science-extension 重构（消除重复代码，复用共享 bridge）
   - [x] mcp-extension 通用 MCP 管理器
   - [x] Web API Server（FastAPI + Pi RPC Bridge + SSE/WS）
- [ ] **Phase 4** — 测试 + 前端（可选）

## Git 回退

```bash
cd D:\agent_workflow
git log --oneline                    # 查看历史
git checkout <commit-hash>           # 回退到任意版本
git checkout master                  # 回到最新
```
