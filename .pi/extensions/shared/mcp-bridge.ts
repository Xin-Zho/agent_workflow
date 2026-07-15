/**
 * mcp-bridge — 共享 MCP 子进程桥接
 *
 * 提供标准 MCP JSON-RPC 2.0 over stdio 协议的完整客户端实现，
 * 以及 flat-method bridge（用于 agent_learning_bridge.py 等非 MCP 后端）。
 *
 * MCP 协议版本: 2024-11-05
 */

import { spawn, type ChildProcess } from "node:child_process";
import { createInterface } from "node:readline";

// ── 类型定义 ───────────────────────────────────────────────────────────

export type McpProcess = {
  proc: ChildProcess;
  requestId: number;
  pending: Map<number, PendingRequest>;
  /** stderr buffer for diagnostics */
  stderrChunks: string[];
};

type PendingRequest = {
  resolve: (v: unknown) => void;
  reject: (e: Error) => void;
};

/** MCP JSON-RPC 响应 */
type McpResponse =
  | { jsonrpc: "2.0"; id: number; result: unknown }
  | { jsonrpc: "2.0"; id: number; error: { code: number; message: string } };

/** MCP tools/list 响应 */
type McpToolListResult = {
  tools: McpToolDef[];
};

/** MCP 工具定义 */
export type McpToolDef = {
  name: string;
  description?: string;
  inputSchema?: {
    type: "object";
    properties?: Record<string, unknown>;
    required?: string[];
  };
};

/** MCP tools/call 响应 — content 数组 */
type McpToolCallResult = {
  content: McpContent[];
  isError?: boolean;
};

type McpContent = {
  type: "text";
  text: string;
};

// ── 子进程管理 ─────────────────────────────────────────────────────────

/**
 * 创建 MCP 子进程。
 * 注意：Python MCP servers 默认使用 python3，Windows 用 python。
 */
export function createMcpProcess(scriptPath: string, extraEnv?: Record<string, string>): McpProcess {
  const pythonExe = process.platform === "win32" ? "python" : "python3";
  const proc = spawn(pythonExe, [scriptPath], {
    stdio: ["pipe", "pipe", "pipe"],
    env: { ...process.env, PYTHONUNBUFFERED: "1", ...extraEnv },
  });

  const mcp: McpProcess = { proc, requestId: 1, pending: new Map(), stderrChunks: [] };

  const rl = createInterface({ input: proc.stdout! });
  rl.on("line", (line: string) => {
    if (!line.trim()) return;
    try {
      const msg = JSON.parse(line);
      if (typeof msg.id === "number" && mcp.pending.has(msg.id)) {
        const { resolve, reject } = mcp.pending.get(msg.id)!;
        mcp.pending.delete(msg.id);
        if (msg.error) {
          const errMsg = typeof msg.error === "string" ? msg.error : msg.error.message ?? JSON.stringify(msg.error);
          reject(new Error(`MCP error: ${errMsg}`));
        } else {
          resolve(msg.result);
        }
      }
    } catch {
      // ignore non-JSON lines (e.g. startup logs)
    }
  });

  proc.stderr!.on("data", (data: Buffer) => {
    mcp.stderrChunks.push(data.toString());
    // keep buffer bounded
    if (mcp.stderrChunks.length > 100) mcp.stderrChunks.shift();
  });

  proc.on("error", (err: Error) => {
    for (const [, { reject }] of mcp.pending) reject(err);
    mcp.pending.clear();
  });

  proc.on("exit", (code) => {
    if (code !== 0 && code !== null) {
      const stderr = mcp.stderrChunks.join("").slice(-500);
      for (const [, { reject }] of mcp.pending) {
        reject(new Error(`MCP process exited with code ${code}: ${stderr}`));
      }
      mcp.pending.clear();
    }
  });

  return mcp;
}

// ── MCP 协议方法 ───────────────────────────────────────────────────────

function sendRequest(mcp: McpProcess, method: string, params: Record<string, unknown> = {}): number {
  const id = mcp.requestId++;
  const request = JSON.stringify({ jsonrpc: "2.0", id, method, params });
  mcp.proc.stdin!.write(request + "\n");
  return id;
}

function awaitResponse(mcp: McpProcess, id: number, timeoutMs: number): Promise<unknown> {
  return new Promise((resolve, reject) => {
    mcp.pending.set(id, { resolve, reject });
    setTimeout(() => {
      if (mcp.pending.has(id)) {
        mcp.pending.delete(id);
        reject(new Error(`MCP call timed out after ${timeoutMs / 1000}s`));
      }
    }, timeoutMs);
  });
}

/**
 * MCP 初始化握手：initialize → notifications/initialized → tools/list
 *
 * 与 agent_learning client_manager.py 完全对齐。
 */
export async function initMcpServer(mcp: McpProcess): Promise<McpToolDef[]> {
  // 1. initialize
  const initId = sendRequest(mcp, "initialize", {
    protocolVersion: "2024-11-05",
    capabilities: {},
    clientInfo: { name: "agent-workflow", version: "1.0" },
  });
  await awaitResponse(mcp, initId, 30000);

  // 2. notifications/initialized（无 id，单向通知）
  mcp.proc.stdin!.write(JSON.stringify({ jsonrpc: "2.0", method: "notifications/initialized" }) + "\n");

  // 3. tools/list — 发现所有工具
  const listId = sendRequest(mcp, "tools/list", {});
  const result = (await awaitResponse(mcp, listId, 20000)) as McpToolListResult;
  return result.tools ?? [];
}

/**
 * 调用 MCP 工具（tools/call）。
 *
 * 自动解析标准 MCP 响应格式：
 *   result.content[].text → json.loads() → 返回 Python dict
 *
 * 与 agent_learning client_manager.py 的 call_tool 逻辑对齐。
 */
export async function callMcpTool(
  mcp: McpProcess,
  toolName: string,
  args: Record<string, unknown>,
  timeoutMs = 60000,
): Promise<unknown> {
  const id = sendRequest(mcp, "tools/call", { name: toolName, arguments: args });
  const result = (await awaitResponse(mcp, id, timeoutMs)) as McpToolCallResult;

  // 解析 content 数组（标准 MCP 格式）
  const content = result?.content;
  if (Array.isArray(content)) {
    for (const item of content) {
      if (item?.type === "text" && typeof item.text === "string") {
        try {
          return JSON.parse(item.text);
        } catch {
          return { result: item.text };
        }
      }
    }
  }

  // 降级：直接返回整个 result
  return result;
}

/**
 * 终止 MCP 子进程。先优雅 SIGTERM，3 秒后强杀 SIGKILL。
 */
export function killMcpProcess(mcp: McpProcess): void {
  try {
    mcp.proc.stdin?.end();
    mcp.proc.kill("SIGTERM");
    setTimeout(() => {
      if (!mcp.proc.killed) mcp.proc.kill("SIGKILL");
    }, 3000);
  } catch {
    // best effort
  }
}

// ── Flat JSON-RPC bridge（非 MCP，直接方法分发）───────────────────────

/**
 * 调用 flat-method bridge（如 agent_learning_bridge.py）。
 *
 * 协议：{id, method, params} → {id, result} | {id, error}
 *
 * 与 MCP 协议不同——不走 tools/list / tools/call，
 * 而是直接按 method name 分发到 Python handler。
 */
export function callBridge(
  mcp: McpProcess,
  method: string,
  params: Record<string, unknown>,
  timeoutMs = 60000,
): Promise<unknown> {
  const id = mcp.requestId++;
  const request = JSON.stringify({ id, method, params });
  mcp.proc.stdin!.write(request + "\n");

  return new Promise((resolve, reject) => {
    mcp.pending.set(id, { resolve, reject });
    setTimeout(() => {
      if (mcp.pending.has(id)) {
        mcp.pending.delete(id);
        reject(new Error(`Bridge method "${method}" timed out after ${timeoutMs / 1000}s`));
      }
    }, timeoutMs);
  });
}
