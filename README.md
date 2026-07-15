# science-pi — 科学计算 Pi Agent

> 基于 Pi Agent（TypeScript CLI/TUI）底座，
> 集成 agent_learning 的科学计算、三层记忆、RAG、评估能力。
> 记忆/RAG 直接桥接 agent_learning 成熟实现，共享 ChromaDB 实例。

## 项目结构

```
.pi/
├── extensions/
│   ├── shared/
│   │   └── mcp-bridge.ts           # 共享子进程桥接（MCP + flat JSON-RPC）
│   ├── science-extension/
│   │   └── index.ts                # 科学计算：13 个化学/物理工具
│   ├── memory-extension/
│   │   └── index.ts                # 三层记忆：桥接 agent_learning MemoryManager
│   ├── rag-extension/
│   │   └── index.ts                # RAG：桥接 agent_learning science_kb_ingest
│   └── evaluation-extension/
│       └── index.ts                # 评估：metrics/evaluate/stats
├── skills/
│   └── scientific-method.md        # 科学解题方法论
└── templates/
    └── compute-verify-output.md    # 提示模板

python-tools/
├── agent_learning_bridge.py        # ★ 统一桥接（复用 agent_learning 记忆/RAG）
├── chemistry_server.py             # 化学 MCP 服务器（sympy+mendeleev）
├── physics_server.py               # 物理 MCP 服务器（sympy+pint）
├── embedding_server.py             # BGE 嵌入服务（独立，可选）
├── verify_tools.py                 # 验证链工具（从 agent_learning 复用）
├── science_kb_ingest.py            # 知识库摄入管道（从 agent_learning 复用）
└── requirements.txt                # Python 依赖
```

## 架构

```
┌──────────────────────────────────────────────────────────────────┐
│  Pi Agent（CLI / TUI）                                           │
│  ┌────────────────────────────────────────────────────────────┐  │
│  │  双层 Agent 循环（steering + followUp）                      │  │
│  └────────────────────────────────────────────────────────────┘  │
│  ┌────────────────────────────────────────────────────────────┐  │
│  │  Extension 层                                               │  │
│  │  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────────┐  │  │
│  │  │ Science  │ │ Memory   │ │ RAG      │ │ Evaluation   │  │  │
│  │  │ 13 tools │ │ 5 tools  │ │ 4 tools  │ │ 2 tools      │  │  │
│  │  └────┬─────┘ └────┬─────┘ └────┬─────┘ └──────┬───────┘  │  │
│  └───────┼────────────┼────────────┼───────────────┼──────────┘  │
│          │            │            │               │              │
│  ┌───────┼────────────┼────────────┼───────────────┼──────────┐  │
│  │ MCP Bridge (stdio JSON-RPC)     │               │          │  │
│  └───────┼────────────┼────────────┼───────────────┼──────────┘  │
│          ▼            ▼            ▼               ▼              │
│  ┌──────────┐ ┌─────────────────────┐  ┌──────────────────────┐ │
│  │Chemistry │ │ agent_learning      │  │ Evaluation           │ │
│  │Server.py │ │ Bridge.py ★         │  │ (纯 TS, hooks)       │ │
│  │sympy     │ │                     │  │                      │ │
│  │mendeleev │ │ → MemoryManager     │  │ turn_end/metrics     │ │
│  └──────────┘ │   .add/.search      │  │ agent_end/judge      │ │
│  ┌──────────┐ │   .consolidate      │  └──────────────────────┘ │
│  │ Physics  │ │   .forget           │                           │
│  │Server.py │ │                     │                           │
│  │sympy     │ │ → science_kb_ingest │                           │
│  │pint      │ │   .search_knowledge │                           │
│  └──────────┘ │   .ingest_document  │                           │
│               └─────────┬───────────┘                           │
│                         │                                       │
│               ┌─────────▼───────────┐                           │
│               │ agent_learning      │                           │
│               │ D:\agent_learning\   │                           │
│               │ data\chroma\        │ ← 共享 ChromaDB 实例      │
│               │ ├── episodic_all    │                           │
│               │ ├── semantic_all    │                           │
│               │ └── science_kb      │                           │
│               └─────────────────────┘                           │
└──────────────────────────────────────────────────────────────────┘
```

## 扩展详情

| 扩展 | 工具数 | Python 后端 | 说明 |
|------|:---:|------|------|
| science-extension | 13 | chemistry_server + physics_server | 化学：配平/元素/溶液/pH/热力学/平衡/动力学/电化学；物理：力学/电磁/量子/热力学/光学/误差 |
| memory-extension | 5 | agent_learning_bridge → MemoryManager | remember/recall/forget/consolidate/stats；复用 agent_learning 三层记忆 + 上下文注入 |
| rag-extension | 4 | agent_learning_bridge → science_kb_ingest | search/ingest/stats/remove；公式感知分块、权威来源、混合检索 |
| evaluation-extension | 2 | (纯TS) | evaluate/eval_stats；turn_end/agent_end/tool_call 钩子 |

## 关键设计决策

- **记忆/RAG 不重写**：直接桥接 agent_learning 成熟实现，通过 `agent_learning_bridge.py`（stdin/stdout JSON-RPC）
- **共享 ChromaDB**：复用 `D:\agent_learning\data\chroma`，所有 collection（episodic_all / semantic_all / science_kb）在原位
- **MemoryManager 是统一入口**：`.add()` / `.search()` / `.get_context_for_prompt()` / `.consolidate()` / `.forget()`
- **上下文注入**：memory-extension 的 `context` hook 调用 `memory_get_context`，自动注入到 system prompt

## Phase 进度

- [x] **Phase 1** — 项目骨架 + 科学计算
- [x] **Phase 2** — 记忆 + RAG + 评估（修复：桥接 agent_learning 代替自定义实现）
- [ ] **Phase 3** — MCP 完整协议 + Web API + 前端（可选）

## Git 回退

```bash
cd D:\agent_workflow
git log --oneline                    # 查看历史
git checkout <commit-hash>           # 回退到任意版本
git checkout master                  # 回到最新
```
