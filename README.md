# agent_workflow — 材料科学文献研究工作流

> 基于 Pi Agent 的可恢复研究任务系统。从目标性能出发，逐层检索候选结构、
> 实验室可执行工艺体系及成分配比，并通过两个人工审批节点建立可信共享知识库。

当前版本新增了独立于模型进程的 SQLite 工作流核心：任务状态、论文候选、
提取版本、人工审核、暂停/恢复/回退、审计事件及 FIFO 队列均可持久化。
原有科学计算、三层记忆、RAG 和评估能力继续作为辅助工具。

## 快速开始

```bash
# 1. 环境检查 + 依赖安装
cd D:\agent_workflow
bash setup.sh

# 2. 验证桥接是否正常
cd python-tools
python verify_bridge.py

# 3a. CLI 模式（Pi TUI）
cd /d/agent_workflow
wsl pi

# 3b. Web API 模式（Pi 不在线时研究任务 API 仍可使用）
cd python-tools
python web_api_server.py --port 8000
# 浏览器打开 http://localhost:8000
```

研究工作流 API 说明见 [`docs/research-workflow-api.md`](docs/research-workflow-api.md)。

## 第一版研究流程

```text
任务澄清
→ 目标性能/结构/工艺/配方检索
→ 规则过滤
→ 嵌入召回与重排
→ 摘要候选列表
→ 用户确认
→ 全文解析与深读
→ 配方/工艺/性能提取
→ 可比性与证据检查
→ 综述和实验点
→ 人工审核
→ 共享知识库
```

默认优先最近五年文献并保留奠基工作，支持中英文。摘要分析始终标记为
低证据等级；只有任务发起者或管理员审核通过的全文提取结果才能进入共享库。

## 项目结构

```
D:\agent_workflow\
├── .pi/
│   ├── settings.json                  # ★ Phase 4 — 项目级配置（模型、压缩、重试）
│   ├── SYSTEM.md                      # ★ Phase 4 — 科学计算系统提示词
│   ├── extensions/
│   │   ├── shared/
│   │   │   └── mcp-bridge.ts          # 共享 MCP/JSON-RPC 桥接（标准 MCP 2024-11-05）
│   │   ├── science-extension/
│   │   │   └── index.ts               # 科学计算：13 个化学/物理工具
│   │   ├── mcp-extension/
│   │   │   ├── index.ts               # 通用 MCP 管理器（自动连接/发现/注册）
│   │   │   └── servers.json           # MCP server 配置
│   │   ├── memory-extension/
│   │   │   └── index.ts               # 三层记忆：桥接 agent_learning MemoryManager
│   │   ├── rag-extension/
│   │   │   └── index.ts               # RAG：桥接 agent_learning science_kb_ingest
│   │   └── evaluation-extension/
│   │       └── index.ts               # 评估：metrics/evaluate/stats
│   ├── skills/
│   │   └── scientific-method.md       # 科学解题方法论
│   └── prompts/
│       └── compute-verify-output.md   # 提示模板（/compute-verify-output）
├── python-tools/
│   ├── web_api_server.py              # FastAPI + Pi RPC Bridge (552行)
│   ├── workflow_engine.py              # 研究状态机、SQLite、FIFO与审计
│   ├── workflow_api.py                 # /api/research/* 路由
│   ├── agent_learning_bridge.py       # 统一桥接（复用 agent_learning 记忆/RAG）
│   ├── verify_bridge.py               # ★ Phase 4 — 桥接验证脚本
│   ├── chemistry_server.py            # 化学 MCP 服务器（sympy+mendeleev）
│   ├── physics_server.py              # 物理 MCP 服务器（sympy+pint）
│   ├── embedding_server.py            # BGE 嵌入服务（独立，可选）
│   ├── verify_tools.py                # 验证链工具（从 agent_learning 复用）
│   ├── science_kb_ingest.py           # 知识库摄入管道（从 agent_learning 复用）
│   └── requirements.txt               # Python 依赖
├── web/
│   └── index.html                     # ★ Phase 4 — Web UI（单文件，KaTeX+Markdown）
├── AGENTS.md                          # ★ Phase 4 — 项目上下文（Pi 自动发现）
├── setup.sh                           # ★ Phase 4 — 环境初始化脚本
├── tests/test_workflow_engine.py       # 工作流核心单元测试
├── docs/research-workflow-api.md       # API与队列契约
└── README.md
```

## 架构

```
┌──────────────────────────────────────────────────────────────────────┐
│  Pi Agent（CLI / TUI / Web API）                                     │
│  ┌────────────────────────────────────────────────────────────────┐ │
│  │  双层 Agent 循环（steering + followUp）                          │ │
│  └────────────────────────────────────────────────────────────────┘ │
│  ┌────────────────────────────────────────────────────────────────┐ │
│  │  Extension 层                                                   │ │
│  │  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐          │ │
│  │  │ Science  │ │ MCP Mgr  │ │ Memory   │ │ Eval     │          │ │
│  │  │ 13 tools │ │通用管理器│ │ 5 tools  │ │ 2 tools  │          │ │
│  │  └────┬─────┘ └────┬─────┘ └────┬─────┘ └────┬─────┘          │ │
│  └───────┼────────────┼────────────┼────────────┼────────────────┘ │
│  ┌───────┼────────────┼────────────┼────────────┼────────────────┐ │
│  │ MCP Bridge (标准 JSON-RPC 2.0 2024-11-05)                     │ │
│  └───────┼────────────┼────────────┼────────────┼────────────────┘ │
│          ▼            ▼            ▼            ▼                   │
│  ┌──────────┐ ┌──────────┐ ┌─────────────────┐ ┌──────────────┐   │
│  │Chemistry │ │ Physics  │ │ agent_learning   │ │ Evaluation   │   │
│  │Server.py │ │Server.py │ │ Bridge.py        │ │ (纯 TS)      │   │
│  │sympy     │ │sympy     │ │ → MemoryManager  │ │ hooks +      │   │
│  │mendeleev │ │pint      │ │ → science_kb     │ │ LLM Judge    │   │
│  └──────────┘ └──────────┘ └─────────────────┘ └──────────────┘   │
│  ┌──────────────────────┐  ┌──────────────────────────────────────┐│
│  │ Web API + Web UI ★  │  │ D:\agent_learning\data\chroma\       ││
│  │ FastAPI :8000       │  │ ├── episodic_all                     ││
│  │ Pi RPC Bridge       │  │ ├── semantic_all                     ││
│  │ SSE + WebSocket     │  │ └── science_kb                       ││
│  └──────────────────────┘  └──────────────────────────────────────┘│
└──────────────────────────────────────────────────────────────────────┘
```

## 扩展详情

| 扩展 | 工具数 | Python 后端 | 说明 |
|------|:---:|------|------|
| science-extension | 13 | chemistry_server + physics_server | 化学：配平/元素/溶液/pH/热力学/平衡/动力学/电化学；物理：力学/电磁/量子/热力学/光学/误差 |
| mcp-extension | 2 管理 | 通用 MCP 客户端 | `mcp_list` / `mcp_reconnect`；自动连接 servers.json 配置的 MCP servers |
| memory-extension | 5 | agent_learning_bridge → MemoryManager | remember/recall/forget/consolidate/stats；三层记忆 + 上下文注入 |
| rag-extension | 4 | agent_learning_bridge → science_kb_ingest | search/ingest/stats/remove；公式感知分块、权威来源 |
| evaluation-extension | 2 | (纯TS) | evaluate/eval_stats；turn_end/agent_end/tool_call 钩子 |

## Web API

```bash
# 启动
python python-tools/web_api_server.py --port 8000

# 浏览器访问 http://localhost:8000 → Web UI
# 或 API 调用：
POST /api/auth/token       # 获取 JWT token
POST /api/chat             # SSE 流式对话
POST /api/chat/sync         # 同步对话
GET  /api/sessions          # 会话状态
POST /api/sessions/new      # 新建会话
WebSocket /ws               # 实时双向通信（prompt/steer/abort）
GET  /api/health            # 健康检查
GET  /                      # Web UI（单页面）
```

## Pi 配置

| 文件 | 作用 |
|------|------|
| `.pi/settings.json` | 项目级配置（模型: ollama/qwen2.5:7b，压缩，重试） |
| `.pi/SYSTEM.md` | 科学计算系统提示词（工具说明 + 规则） |
| `AGENTS.md` | 项目上下文（Pi 自动注入 system prompt） |
| `.pi/extensions/` | 扩展自动发现（无需注册） |
| `.pi/skills/` | Skills 自动发现 |
| `.pi/prompts/` | 提示模板自动发现（输入 `/compute-verify-output` 使用） |

## 关键设计决策

- **记忆/RAG 不重写**：直接桥接 agent_learning 成熟实现，通过 `agent_learning_bridge.py`（stdin/stdout JSON-RPC）
- **共享 ChromaDB**：复用 `D:\agent_learning\data\chroma`，所有 collection 在原位
- **MCP 标准协议**：完整实现 MCP 2024-11-05 握手，与 agent_learning 的 client_manager.py 完全对齐
- **science-extension 已重构**：消除重复代码，复用 shared/mcp-bridge.ts
- **mcp-extension 通用管理**：读取 servers.json 自动连接任意 MCP server
- **Web API**：FastAPI 通过 Pi RPC 模式（JSONL stdin/stdout）桥接，支持 SSE + WebSocket + JWT
- **Web UI**：单文件 HTML，KaTeX 渲染 LaTeX 公式，marked.js 渲染 Markdown

## Phase 进度

- [x] **Phase 1** — 项目骨架 + 科学计算（13 工具）
- [x] **Phase 2** — 记忆 + RAG + 评估（11 工具，桥接 agent_learning）
- [x] **Phase 3** — MCP 完整协议 + Web API（552行 FastAPI 服务器）
- [x] **Phase 4** — 配置 + 可运行化
   - [x] Pi 配置文件（settings.json + SYSTEM.md + AGENTS.md）
   - [x] 目录修复（templates → prompts）
   - [x] 环境初始化脚本（setup.sh）
   - [x] 桥接验证脚本（verify_bridge.py）
   - [x] Web UI（单文件 HTML + KaTeX + WebSocket）

## Git 回退

```bash
cd D:\agent_workflow
git log --oneline                    # 查看历史
git checkout <commit-hash>           # 回退到任意版本
git checkout master                  # 回到最新
```
