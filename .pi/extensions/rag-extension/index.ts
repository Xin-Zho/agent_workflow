/**
 * rag-extension — RAG 知识检索扩展
 *
 * 直接桥接 agent_learning 的 science_kb_ingest 模块（通过 agent_learning_bridge.py），
 * 复用 D:\agent_learning\data\chroma 共享 ChromaDB 实例。
 *
 * 支持公式感知分块、权威来源标记、混合检索（BM25 + 语义 → RRF）。
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

export default function (pi: ExtensionAPI) {
  let mcp: McpProcess | null = null;

  function getBridgePath(): string {
    return join(process.cwd(), "python-tools", "agent_learning_bridge.py");
  }

  async function ensureMcp(): Promise<McpProcess> {
    if (!mcp) throw new Error("RAG bridge not running");
    return mcp;
  }

  // ── 会话生命周期 ─────────────────────────────────────────────────
  pi.on("session_start", async (_event, ctx) => {
    try {
      mcp = createMcpProcess(getBridgePath());
      ctx.ui.notify("RAG bridge started (agent_learning science_kb_ingest)", "info");
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : String(err);
      ctx.ui.notify(`RAG bridge failed: ${msg}`, "error");
    }
  });

  pi.on("session_shutdown", () => {
    if (mcp) { killMcpProcess(mcp); mcp = null; }
  });

  // ── rag_search ───────────────────────────────────────────────────
  pi.registerTool({
    name: "rag_search",
    label: "RAG Search",
    description:
      "Search the science knowledge base for authoritative information. " +
      "Uses agent_learning's hybrid retrieval (BM25 + semantic → RRF fusion). " +
      "Results include authority level and confidence scores.",
    parameters: Type.Object({
      query: Type.String({ description: "Search query" }),
      top_k: Type.Optional(Type.Number({ description: "Max results (default 5)" })),
      collection: Type.Optional(
        Type.String({
          description: "Collection: all / science_kb / user_uploads (default all)",
        }),
      ),
    }),
    async execute(_toolCallId, params, _signal, _onUpdate) {
      const rag = await ensureMcp();
      const p = params as { query: string; top_k?: number; collection?: string };
      const result = await callBridge(rag, "rag_search", {
        query: p.query,
        top_k: p.top_k || 5,
        collection: p.collection || "all",
      });
      return {
        content: [{ type: "text", text: JSON.stringify(result, null, 2) }],
        details: {},
      };
    },
  });

  // ── rag_ingest ───────────────────────────────────────────────────
  pi.registerTool({
    name: "rag_ingest",
    label: "RAG Ingest",
    description:
      "Ingest a document into the knowledge base. " +
      "Uses agent_learning's formula-aware chunking (math/chemistry formulas preserved intact). " +
      "Auto-detects source authority level (textbook / peer_reviewed / wikipedia / course_material).",
    parameters: Type.Object({
      content: Type.String({ description: "Document content to ingest" }),
      source: Type.String({ description: 'Source URL or descriptor, e.g. "https://openstax.org/..."' }),
      title: Type.Optional(Type.String({ description: "Document title" })),
      collection: Type.Optional(
        Type.String({ description: "Target collection: science_kb / user_uploads (default science_kb)" }),
      ),
    }),
    async execute(_toolCallId, params, _signal, _onUpdate) {
      const rag = await ensureMcp();
      const p = params as { content: string; source: string; title?: string; collection?: string };
      const result = await callBridge(rag, "rag_ingest", {
        content: p.content,
        source: p.source,
        title: p.title || "",
        collection: p.collection || "science_kb",
      });
      return {
        content: [{ type: "text", text: JSON.stringify(result, null, 2) }],
        details: {},
      };
    },
  });

  // ── rag_stats ────────────────────────────────────────────────────
  pi.registerTool({
    name: "rag_stats",
    label: "RAG Stats",
    description: "Get knowledge base statistics (total chunks per collection).",
    parameters: Type.Object({
      collection: Type.Optional(
        Type.String({ description: "Collection: all / science_kb / user_uploads (default all)" }),
      ),
    }),
    async execute(_toolCallId, params, _signal, _onUpdate) {
      const rag = await ensureMcp();
      const p = params as { collection?: string };
      const result = await callBridge(rag, "rag_stats", {
        collection: p.collection || "all",
      });
      return {
        content: [{ type: "text", text: JSON.stringify(result, null, 2) }],
        details: {},
      };
    },
  });

  // ── rag_remove ───────────────────────────────────────────────────
  pi.registerTool({
    name: "rag_remove",
    label: "RAG Remove",
    description: "Remove an entire collection from the knowledge base. Use with caution.",
    parameters: Type.Object({
      collection: Type.String({ description: "Collection name to remove" }),
    }),
    async execute(_toolCallId, params, _signal, _onUpdate) {
      const rag = await ensureMcp();
      const p = params as { collection: string };
      const result = await callBridge(rag, "rag_remove", {
        collection: p.collection,
      });
      return {
        content: [{ type: "text", text: JSON.stringify(result, null, 2) }],
        details: {},
      };
    },
  });
}
