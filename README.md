# Science Agent (based on Pi Agent)

以 [Pi Agent](https://github.com/earendil-works/pi) 为底座的科学计算 AI Agent。

## 目录结构

```
agent_workflow/
├── .pi/
│   ├── extensions/        # TypeScript 扩展（记忆、RAG、科学计算、MCP、评估）
│   ├── skills/            # Markdown 技能包（化学/物理解题流程）
│   └── templates/         # Prompt 模板（compute-verify-output）
├── python-tools/          # Python 子进程工具（sympy、化学、物理）
│   ├── requirements.txt
│   ├── calculator_server.py
│   ├── chemistry_server.py
│   ├── physics_server.py
│   └── verify_server.py
├── .gitignore
└── README.md
```

## 架构

```
Pi Agent CLI (底座)
  ├── 双层 Agent 循环（steering + followUp）
  ├── 树形会话（JSONL + 分支 + 压缩）
  ├── 40+ LLM 提供商统一 API
  └── Extensions 插件系统
       ├── memory-extension    → 三层记忆（Working/Episodic/Semantic）
       ├── rag-extension       → RAG 检索（ChromaDB + BGE 嵌入）
       ├── science-extension   → 科学计算（Python 子进程 sympy/pint）
       ├── mcp-extension       → MCP 协议工具
       ├── verify-extension    → 验证链（量纲/数量级/回代）
       └── eval-extension      → 评估闭环（metrics + LLM Judge）
```

## 开发

```bash
# 安装 Pi Agent
npm install -g @earendil-works/pi-coding-agent

# 安装 Python 工具依赖
cd python-tools && pip install -r requirements.txt

# 启动
cd /d/agent_workflow && pi
```

## 从 agent_learning 迁移的资产

| 原文件 | 复用方式 |
|--------|---------|
| `chemistry_server.py` | → `python-tools/chemistry_server.py`（MCP 子进程） |
| `physics_server.py` | → `python-tools/physics_server.py`（MCP 子进程） |
| `verify_tools.py` | → `python-tools/verify_server.py`（MCP 子进程） |
| `memory/` | → `.pi/extensions/memory-extension/`（TypeScript 重写） |
| `science_kb_ingest.py` | → `python-tools/science_kb_ingest.py`（保留 Python） |
| `evaluation/` | → `python-tools/eval_server.py`（保留 Python） |
| `engine.py` | ❌ 废弃（Pi 的 agent-loop 替代） |
| `server.py` | ❌ 废弃（Pi CLI 替代） |
