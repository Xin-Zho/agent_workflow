/**
 * rag-extension — RAG 知识检索扩展
 *
 * 提供 rag_search（检索）、rag_ingest（摄入）、rag_stats（统计）工具。
 * 通过 Python rag_server.py 子进程操作 ChromaDB，
 * 支持公式感知分块和权威来源标记。
 */
import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
import { Type } from "typebox";
import { join } from "node:path";
import {
  createMcpProcess,
  callMcpTool,
  initMcpServer,
  killMcpProcess,
  type McpProcess,
} from "../shared/mcp-bridge";

export default function (pi: ExtensionAPI) {
  let mcp: McpProcess | null = null;

  function getToolsDir(): string {
    return join(process.cwd(), "python-tools");
  }

  async function ensureMcp(): Promise<McpProcess> {
    if (!mcp) throw new Error("RAG server not running");
    return mcp;
  }

  // ── 会话生命周期 ─────────────────────────────────────────────────
  pi.on("session_start", async (_event, ctx) => {
    try {
      mcp = createMcpProcess(join(getToolsDir(), "rag_server.py"));
      const tools = await initMcpServer(mcp);
      ctx.ui.notify(`RAG server started (${tools.length} tools)`, "info");
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : String(err);
      ctx.ui.notify(`RAG server failed: ${msg}`, "error");
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
      "Use this to verify facts, look up formulas, or find trusted references before answering.",
    parameters: Type.Object({
      query: Type.String({ description: "Search query" }),
      n_results: Type.Optional(Type.Number({ description: "Max results (default 5)" })),
      topic: Type.Optional(Type.String({ description: "Filter by topic: chemistry / physics / math" })),
    }),
    async execute(_toolCallId, params, _signal, _onUpdate) {
      const rag = await ensureMcp();
      const p = params as { query: string; n_results?: number; topic?: string };
      const result = await callMcpTool(rag, "rag_search", {
        query: p.query,
        n_results: p.n_results || 5,
        topic: p.topic,
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
      "Supports formula-aware chunking: math/chemistry formulas are preserved intact. " +
      "Mark source authority level (textbook / paper / wikipedia / other).",
    parameters: Type.Object({
      content: Type.String({ description: "Document content to ingest" }),
      source: Type.String({ description: 'Source descriptor, e.g. "Atkins Physical Chemistry 10th Ed"' }),
      topic: Type.String({ description: "Topic: chemistry / physics / math / general" }),
      authority: Type.Optional(
        Type.String({
          description: "Authority level: textbook / peer_reviewed / wikipedia / course_material / other",
        }),
      ),
      metadata: Type.Optional(Type.Record(Type.String(), Type.Unknown())),
    }),
    async execute(_toolCallId, params, _signal, _onUpdate) {
      const rag = await ensureMcp();
      const p = params as {
        content: string;
        source: string;
        topic: string;
        authority?: string;
        metadata?: Record<string, unknown>;
      };
      const result = await callMcpTool(rag, "rag_ingest", {
        content: p.content,
        source: p.source,
        topic: p.topic,
        metadata: {
          ...(p.metadata || {}),
          authority: p.authority || "other",
        },
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
    description: "Get knowledge base statistics (total chunks, collection info).",
    parameters: Type.Object({}),
    async execute(_toolCallId, _params, _signal, _onUpdate) {
      const rag = await ensureMcp();
      const result = await callMcpTool(rag, "rag_stats", {});
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
    description: "Remove a document and all its chunks from the knowledge base.",
    parameters: Type.Object({
      doc_id: Type.String({ description: "Document ID to remove" }),
    }),
    async execute(_toolCallId, params, _signal, _onUpdate) {
      const rag = await ensureMcp();
      const p = params as { doc_id: string };
      const result = await callMcpTool(rag, "rag_remove", { doc_id: p.doc_id });
      return {
        content: [{ type: "text", text: JSON.stringify(result, null, 2) }],
        details: {},
      };
    },
  });
}
