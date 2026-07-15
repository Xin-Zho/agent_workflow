/**
 * memory-extension — 三层记忆扩展
 *
 * Working Memory:  内存缓冲（TypeScript 管理，会话级）
 * Episodic Memory: ChromaDB "episodic_memory" 集合（Python 管理）
 * Semantic Memory: ChromaDB "semantic_memory" 集合（Python 管理）
 *
 * 通过 Python memory_server.py 子进程操作 ChromaDB，
 * 嵌入使用 BGE-small-zh-v1.5。
 */
import type { ExtensionAPI, ToolCallContext, ToolExecutionContext } from "@earendil-works/pi-coding-agent";
import { Type } from "typebox";
import { join } from "node:path";
import {
  createMcpProcess,
  callMcpTool,
  initMcpServer,
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

  function getToolsDir(): string {
    return join(process.cwd(), "python-tools");
  }

  async function ensureMcp(): Promise<McpProcess> {
    if (!mcp) throw new Error("Memory server not running");
    return mcp;
  }

  // ── 会话生命周期 ─────────────────────────────────────────────────
  pi.on("session_start", async (_event, ctx) => {
    try {
      mcp = createMcpProcess(join(getToolsDir(), "memory_server.py"));
      const tools = await initMcpServer(mcp);
      ctx.ui.notify(`Memory server started (${tools.length} tools)`, "info");
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : String(err);
      ctx.ui.notify(`Memory server failed: ${msg}`, "error");
    }
  });

  pi.on("session_shutdown", () => {
    if (mcp) { killMcpProcess(mcp); mcp = null; }
  });

  // ── 上下文注入：每轮注入相关记忆 ──────────────────────────────────
  pi.on("context", async (_event, ctx) => {
    const lastUserMsg = getLastUserMessage(ctx.messages);
    const sections: string[] = [];

    // Working memory
    const wm = getWorkingContext();
    if (wm) sections.push(`## Working Memory (recent conversation)\n${wm}`);

    // Episodic + Semantic search
    if (lastUserMsg && mcp) {
      try {
        const [episodic, semantic] = await Promise.all([
          callMcpTool(mcp, "episodic_search", { query: lastUserMsg, n_results: 3 }),
          callMcpTool(mcp, "semantic_search", { query: lastUserMsg, n_results: 2 }),
        ]);

        const ep = episodic as { memories?: Array<{ content: string }> };
        const sm = semantic as { memories?: Array<{ content: string }> };

        if (ep.memories && ep.memories.length > 0) {
          sections.push(
            "## Relevant Past Experiences\n" +
              ep.memories.map((m) => `- ${m.content}`).join("\n"),
          );
        }
        if (sm.memories && sm.memories.length > 0) {
          sections.push(
            "## Relevant Knowledge\n" +
              sm.memories.map((m) => `- ${m.content}`).join("\n"),
          );
        }
      } catch {
        // memory search failed, continue without injection
      }
    }

    if (sections.length > 0) {
      const injected = `[Memory Context]\n${sections.join("\n\n")}`;
      ctx.systemPromptAppend(injected);
    }
  });

  // ── 跟踪对话，写入 Working Memory ─────────────────────────────────
  pi.on("turn_end", (_event, ctx) => {
    const msgs = ctx.messages;
    if (msgs.length > 0) {
      const last = msgs[msgs.length - 1];
      if (last.role === "user" || last.role === "assistant") {
        addWorkingMemory(
          last.role as "user" | "assistant",
          typeof last.content === "string" ? last.content : JSON.stringify(last.content),
        );
      }
    }
  });

  // ── 注册工具 ─────────────────────────────────────────────────────

  // remember — 智能存储记忆
  pi.registerTool({
    name: "remember",
    label: "Remember",
    description: "Store a memory. Auto-classifies as episodic (conversation event) or semantic (knowledge fact).",
    parameters: Type.Object({
      content: Type.String({ description: "Memory content to store" }),
      type: Type.Optional(Type.String({ description: "Memory type: episodic / semantic (auto if omitted)" })),
      topic: Type.Optional(Type.String({ description: "Topic tag for semantic memories" })),
      metadata: Type.Optional(Type.Record(Type.String(), Type.Unknown())),
    }),
    async execute(_toolCallId, params, _signal, _onUpdate) {
      const mem = await ensureMcp();
      const p = params as { content: string; type?: string; topic?: string; metadata?: Record<string, unknown> };
      const memType = p.type || (p.topic ? "semantic" : "episodic");

      if (memType === "semantic") {
        const result = await callMcpTool(mem, "semantic_store", {
          content: p.content,
          topic: p.topic || "general",
          metadata: p.metadata || {},
        });
        return {
          content: [{ type: "text", text: `Semantic memory stored: ${JSON.stringify(result)}` }],
          details: {},
        };
      } else {
        const result = await callMcpTool(mem, "episodic_store", {
          content: p.content,
          metadata: p.metadata || {},
        });
        return {
          content: [{ type: "text", text: `Episodic memory stored: ${JSON.stringify(result)}` }],
          details: {},
        };
      }
    },
  });

  // recall — 搜索所有记忆层
  pi.registerTool({
    name: "recall",
    label: "Recall",
    description: "Search all memory layers (working + episodic + semantic) for relevant information.",
    parameters: Type.Object({
      query: Type.String({ description: "Search query" }),
      n_results: Type.Optional(Type.Number({ description: "Max results per layer (default 5)" })),
    }),
    async execute(_toolCallId, params, _signal, _onUpdate) {
      const mem = await ensureMcp();
      const p = params as { query: string; n_results?: number };
      const n = p.n_results || 5;

      const [episodic, semantic] = await Promise.all([
        callMcpTool(mem, "episodic_search", { query: p.query, n_results: n }),
        callMcpTool(mem, "semantic_search", { query: p.query, n_results: n }),
      ]);

      const result = {
        working: workingMemory
          .filter((e) => e.content.includes(p.query))
          .slice(-3)
          .map((e) => ({ role: e.role, content: e.content.slice(0, 300) })),
        episodic: (episodic as { memories?: unknown[] }).memories || [],
        semantic: (semantic as { memories?: unknown[] }).memories || [],
      };

      return {
        content: [{ type: "text", text: JSON.stringify(result, null, 2) }],
        details: {},
      };
    },
  });

  // forget — 删除记忆
  pi.registerTool({
    name: "forget",
    label: "Forget",
    description: "Delete a memory by ID and collection name.",
    parameters: Type.Object({
      memory_id: Type.String({ description: "Memory ID to delete" }),
      collection: Type.Optional(Type.String({ description: "Collection: episodic_memory / semantic_memory" })),
    }),
    async execute(_toolCallId, params, _signal, _onUpdate) {
      const mem = await ensureMcp();
      const p = params as { memory_id: string; collection?: string };
      const result = await callMcpTool(mem, "memory_forget", {
        memory_id: p.memory_id,
        collection: p.collection || "episodic_memory",
      });
      return {
        content: [{ type: "text", text: JSON.stringify(result, null, 2) }],
        details: {},
      };
    },
  });

  // summarize_session — 压缩工作记忆 → 情景记忆
  pi.registerTool({
    name: "summarize_session",
    label: "Summarize",
    description: "Compress current working memory into a concise episodic memory and store it.",
    parameters: Type.Object({
      title: Type.Optional(Type.String({ description: "Session summary title" })),
    }),
    async execute(_toolCallId, params, _signal, _onUpdate) {
      const mem = await ensureMcp();
      const p = params as { title?: string };

      const summary = workingMemory
        .map((e) => `[${e.role}]: ${e.content.slice(0, 500)}`)
        .join("\n---\n");

      const result = await callMcpTool(mem, "episodic_store", {
        content: summary,
        metadata: {
          title: p.title || `Session ${Date.now()}`,
          turn_count: workingMemory.length,
          compressed_at: new Date().toISOString(),
        },
      });

      return {
        content: [{ type: "text", text: `Session summarized and stored: ${JSON.stringify(result)}` }],
        details: {},
      };
    },
  });

  // memory_stats — 查看记忆统计
  pi.registerTool({
    name: "memory_stats",
    label: "Mem Stats",
    description: "Get memory collection statistics and working memory size.",
    parameters: Type.Object({}),
    async execute(_toolCallId, _params, _signal, _onUpdate) {
      const mem = await ensureMcp();
      const stats = await callMcpTool(mem, "memory_stats", {});
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
