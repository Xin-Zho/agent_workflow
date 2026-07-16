/**
 * memory-extension — 三层记忆扩展
 *
 * 直接桥接 agent_learning 的 MemoryManager（通过 agent_learning_bridge.py），
 * 复用 D:\agent_learning\data\chroma 共享 ChromaDB 实例。
 *
 * Working Memory:  TypeScript 内存缓冲（会话级，max 20 turns）
 * Episodic Memory: ChromaDB "episodic_all" 集合（agent_learning 原生）
 * Semantic Memory: ChromaDB "semantic_all" 集合（agent_learning 原生）
 */
import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
import { Type } from "typebox";
import { join } from "node:path";
import {
  createMcpProcess,
  callBridge,
  killMcpProcess,
  type McpProcess,
} from "../shared/mcp-bridge";

// ── Working Memory（会话级内存缓冲）────────────────────────────────────

const MAX_WORKING_TURNS = 20;

interface WorkingMemoryEntry {
  role: "user" | "assistant" | "tool";
  content: string;
  timestamp: number;
}

const workingMemory: WorkingMemoryEntry[] = [];

function addWorkingMemory(role: WorkingMemoryEntry["role"], content: string): void {
  workingMemory.push({ role, content, timestamp: Date.now() });
  if (workingMemory.length > MAX_WORKING_TURNS) {
    workingMemory.shift();
  }
}

function getWorkingContext(): string {
  if (workingMemory.length === 0) return "";
  const recent = workingMemory.slice(-6);
  return recent.map((e) => `[${e.role}]: ${e.content.slice(0, 200)}`).join("\n");
}

// ── 扩展入口 ──────────────────────────────────────────────────────────

export default function (pi: ExtensionAPI) {
  let mcp: McpProcess | null = null;

  function getBridgePath(): string {
    return join(process.cwd(), "python-tools", "agent_learning_bridge.py");
  }

  async function ensureMcp(): Promise<McpProcess> {
    if (!mcp) throw new Error("Memory bridge not running");
    return mcp;
  }

  // ── 会话生命周期 ─────────────────────────────────────────────────
  pi.on("session_start", async (_event, ctx) => {
    try {
      mcp = createMcpProcess(getBridgePath());
      ctx.ui.notify("Memory bridge started (agent_learning MemoryManager)", "info");
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : String(err);
      ctx.ui.notify(`Memory bridge failed: ${msg}`, "error");
    }
  });

  pi.on("session_shutdown", () => {
    if (mcp) { killMcpProcess(mcp); mcp = null; }
  });

  // ── 上下文注入：每轮注入相关记忆 ──────────────────────────────────
  pi.on("context", async (_event, ctx) => {
    // Pi v0.80+ context 事件可能不传 messages（API 变更）
    const messages = ctx?.messages;
    if (!messages || messages.length === 0) return;
    const lastUserMsg = getLastUserMessage(messages);

    // 直接调用 agent_learning 的 get_context_for_prompt
    if (lastUserMsg && mcp) {
      try {
        const result = await callBridge(mcp, "memory_get_context", {
          query: lastUserMsg,
        });
        const ctx_result = result as { context: string };
        if (ctx_result.context && ctx_result.context.trim()) {
          ctx.systemPromptAppend(ctx_result.context);
        }
      } catch {
        // memory search failed, continue without injection
      }
    }
  });

  // ── 跟踪对话，写入 Working Memory ─────────────────────────────────
  pi.on("turn_end", (_event, ctx) => {
    const msgs = ctx?.messages;
    if (!msgs || msgs.length === 0) return;
    const last = msgs[msgs.length - 1];
    if (last.role === "user" || last.role === "assistant") {
      addWorkingMemory(
        last.role as "user" | "assistant",
        typeof last.content === "string" ? last.content : JSON.stringify(last.content),
      );
    }
  });

  // ── 注册工具 ─────────────────────────────────────────────────────

  // remember — 智能存储记忆（调用 agent_learning MemoryManager.add）
  pi.registerTool({
    name: "remember",
    label: "Remember",
    description:
      "Store a memory. Auto-classifies as episodic (conversation event) or semantic (knowledge/fact). " +
      "Backed by agent_learning MemoryManager with importance scoring and TTL management.",
    parameters: Type.Object({
      content: Type.String({ description: "Memory content to store" }),
      memory_type: Type.Optional(
        Type.String({ description: "Memory type: episodic / semantic (auto if omitted)" }),
      ),
      importance: Type.Optional(Type.Number({ description: "Importance score 0.0-1.0 (default 0.5)" })),
      metadata: Type.Optional(Type.Record(Type.String(), Type.Unknown())),
    }),
    async execute(_toolCallId, params, _signal, _onUpdate) {
      const mem = await ensureMcp();
      const p = params as {
        content: string;
        memory_type?: string;
        importance?: number;
        metadata?: Record<string, unknown>;
      };
      const result = await callBridge(mem, "memory_add", {
        content: p.content,
        memory_type: p.memory_type || "episodic",
        importance: p.importance ?? 0.5,
        metadata: p.metadata,
      });
      return {
        content: [{ type: "text", text: `Memory stored: ${JSON.stringify(result)}` }],
        details: {},
      };
    },
  });

  // recall — 搜索所有记忆层
  pi.registerTool({
    name: "recall",
    label: "Recall",
    description:
      "Search all memory layers (working + episodic + semantic) for relevant information. " +
      "Uses agent_learning's hybrid retrieval with importance/recency weighting.",
    parameters: Type.Object({
      query: Type.String({ description: "Search query" }),
      limit: Type.Optional(Type.Number({ description: "Max results (default 5)" })),
      memory_types: Type.Optional(
        Type.Array(Type.String(), { description: 'Filter: ["episodic", "semantic"] (default both)' }),
      ),
    }),
    async execute(_toolCallId, params, _signal, _onUpdate) {
      const mem = await ensureMcp();
      const p = params as { query: string; limit?: number; memory_types?: string[] };
      const result = await callBridge(mem, "memory_search", {
        query: p.query,
        limit: p.limit || 5,
        memory_types: p.memory_types,
      });
      const searchResult = result as { items: Array<{ id: string; content: string; memory_type: string }> };
      const response = {
        working: workingMemory
          .filter((e) => e.content.includes(p.query))
          .slice(-3)
          .map((e) => ({ role: e.role, content: e.content.slice(0, 300) })),
        long_term: searchResult.items,
      };
      return {
        content: [{ type: "text", text: JSON.stringify(response, null, 2) }],
        details: {},
      };
    },
  });

  // forget — 执行遗忘策略
  pi.registerTool({
    name: "forget",
    label: "Forget",
    description:
      "Run the agent_learning forgetting strategy. Removes low-importance / stale episodic memories. " +
      "Strategies: importance_based (default), time_based.",
    parameters: Type.Object({
      strategy: Type.Optional(
        Type.String({ description: "Forgetting strategy: importance_based / time_based" }),
      ),
    }),
    async execute(_toolCallId, params, _signal, _onUpdate) {
      const mem = await ensureMcp();
      const p = params as { strategy?: string };
      const result = await callBridge(mem, "memory_forget", {
        strategy: p.strategy || "importance_based",
      });
      return {
        content: [{ type: "text", text: `Forgetting complete: ${JSON.stringify(result)}` }],
        details: {},
      };
    },
  });

  // summarize_session — 压缩 + 固化
  pi.registerTool({
    name: "summarize_session",
    label: "Summarize",
    description:
      "Consolidate high-importance episodic memories into semantic memory. " +
      "Uses agent_learning MemoryManager.consolidate().",
    parameters: Type.Object({
      threshold: Type.Optional(
        Type.Number({ description: "Minimum importance to consolidate (default 0.5)" }),
      ),
    }),
    async execute(_toolCallId, params, _signal, _onUpdate) {
      const mem = await ensureMcp();
      const p = params as { threshold?: number };
      const result = await callBridge(mem, "memory_consolidate", {
        threshold: p.threshold ?? 0.5,
      });
      return {
        content: [{ type: "text", text: `Consolidation complete: ${JSON.stringify(result)}` }],
        details: {},
      };
    },
  });

  // memory_stats — 统计信息
  pi.registerTool({
    name: "memory_stats",
    label: "Mem Stats",
    description: "Get memory statistics from agent_learning (episodic/semantic counts + working memory size).",
    parameters: Type.Object({}),
    async execute(_toolCallId, _params, _signal, _onUpdate) {
      const mem = await ensureMcp();
      const stats = await callBridge(mem, "memory_stats", {});
      const result = {
        ...(stats as object),
        working_memory_turns: workingMemory.length,
      };
      return {
        content: [{ type: "text", text: JSON.stringify(result, null, 2) }],
        details: {},
      };
    },
  });
}

// ── 辅助函数 ──────────────────────────────────────────────────────────

function getLastUserMessage(messages: Array<{ role: string; content: unknown }>): string {
  for (let i = messages.length - 1; i >= 0; i--) {
    if (messages[i].role === "user") {
      const content = messages[i].content;
      if (typeof content === "string") return content;
      if (Array.isArray(content)) {
        const textParts = content.filter((p: { type: string }) => p.type === "text");
        return textParts.map((p: { text: string }) => p.text).join("\n");
      }
      return JSON.stringify(content);
    }
  }
  return "";
}
