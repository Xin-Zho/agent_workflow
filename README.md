# science-pi — 科学计算 Pi Agent

> 基于 Pi Agent（TypeScript CLI/TUI）底座，
> 集成 agent_learning 的科学计算、三层记忆、RAG、评估能力。

## 项目结构

```
.pi/
├── extensions/
│   ├── shared/
│   │   └── mcp-bridge.ts           # 共享 MCP 子进程桥接
│   ├── science-extension/
│   │   └── index.ts                # 科学计算：13 个化学/物理工具
│   ├── memory-extension/
│   │   └── index.ts                # 三层记忆：remember/recall/forget/summarize
│   ├── rag-extension/
│   │   └── index.ts                # RAG：search/ingest/stats/remove
│   └── evaluation-extension/
│       └── index.ts                # 评估：metrics/evaluate/stats
├── skills/
│   └── scientific-method.md        # 科学解题方法论
└── templates/
    └── compute-verify-output.md    # 提示模板

python-tools/
├── chemistry_server.py             # 化学 MCP 服务器（sympy+mendeleev）
├── physics_server.py               # 物理 MCP 服务器（sympy+pint）
├── embedding_server.py             # BGE 嵌入服务器
├── memory_server.py                # ChromaDB 记忆服务器
├── rag_server.py                   # RAG 知识库服务器（公式感知分块）
├── verify_tools.py                 # 验证链工具
├── science_kb_ingest.py            # 知识库摄入管道
└── requirements.txt                # Python 依赖

data/
└── chroma/                         # ChromaDB 持久化数据（自动创建）
```

## 架构

```
┌──────────────────────────────────────────────────────────┐
│  Pi Agent（CLI / TUI）                                   │
│  ┌────────────────────────────────────────────────────┐  │
│  │  双层 Agent 循环（steering + followUp）              │  │
│  └────────────────────────────────────────────────────┘  │
│  ┌────────────────────────────────────────────────────┐  │
│  │  Extension 层                                       │  │
│  │  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌───────┐ │  │
│  │  │ Science  │ │ Memory   │ │ RAG      │ │ Eval  │ │  │
│  │  │ 13 tools │ │ 5 tools  │ │ 4 tools  │ │ 2 tools│ │  │
│  │  └────┬─────┘ └────┬─────┘ └────┬─────┘ └───┬───┘ │  │
│  └───────┼────────────┼────────────┼────────────┼─────┘  │
│          │            │            │            │         │
│  ┌───────┼────────────┼────────────┼────────────┼─────┐  │
│  │ MCP Bridge (stdio JSON-RPC)                 │     │  │
│  └───────┼────────────┼────────────┼────────────┼─────┘  │
│          ▼            ▼            ▼            ▼         │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐                │
│  │Chemistry │ │ Physics  │ │ Memory   │                │
│  │Server.py │ │Server.py │ │Server.py │                │
│  │sympy     │ │sympy     │ │ChromaDB  │                │
│  │mendeleev │ │pint      │ │BGE embed │                │
│  └──────────┘ └──────────┘ └──────────┘                │
│  ┌──────────┐ ┌──────────┐                             │
│  │RAG       │ │Embedding │                             │
│  │Server.py │ │Server.py │                             │
│  │ChromaDB  │ │BGE-small │                             │
│  │公式分块  │ │-zh-v1.5  │                             │
│  └──────────┘ └──────────┘                             │
│  ┌──────────────────────────────────────────┐          │
│  │  ChromaDB Persistent (data/chroma/)      │          │
│  │  ├── episodic_memory                     │          │
│  │  ├── semantic_memory                     │          │
│  │  └── science_kb                          │          │
│  └──────────────────────────────────────────┘          │
└──────────────────────────────────────────────────────────┘
```

## 扩展详情

| 扩展 | 工具数 | Python 后端 | 说明 |
|------|:---:|------|------|
| science-extension | 13 | chemistry_server + physics_server | 化学：配平/元素/溶液/pH/热力学/平衡/动力学/电化学；物理：力学/电磁/量子/热力学/光学/误差 |
| memory-extension | 5 | memory_server | remember/recall/forget/summarize_session/memory_stats；三层记忆 + context hook 注入 |
| rag-extension | 4 | rag_server | rag_search/rag_ingest/rag_stats/rag_remove；公式感知分块 |
| evaluation-extension | 2 | (纯TS) | evaluate/eval_stats；turn_end/agent_end/tool_call 钩子 |

## Phase 进度

- [x] **Phase 1** — 项目骨架 + 科学计算
- [x] **Phase 2** — 记忆 + RAG + 评估
- [ ] **Phase 3** — MCP 完整协议 + Web API + 前端（可选）

## Git 回退

```bash
cd D:\agent_workflow
git log --oneline                    # 查看历史
git checkout <commit-hash>           # 回退到任意版本
git checkout master                  # 回到最新
```
