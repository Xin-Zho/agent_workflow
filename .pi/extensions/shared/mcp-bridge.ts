/**
 * mcp-bridge — 共享 MCP 子进程桥接
 *
 * 提供 createMcpProcess / callMcpTool / initMcpServer / killMcpProcess，
 * 供 science-extension / memory-extension / rag-extension 共用。
 */
import { spawn, type ChildProcess } from "node:child_process";
import { createInterface } from "node:readline";

export type McpProcess = {
  proc: ChildProcess;
  requestId: number;
  pending: Map<number, { resolve: (v: unknown) => void; reject: (e: Error) => void }>;
};

export function createMcpProcess(scriptPath: string): McpProcess {
  const pythonExe = process.platform === "win32" ? "python" : "python3";
  const proc = spawn(pythonExe, [scriptPath], {
    stdio: ["pipe", "pipe", "pipe"],
    env: { ...process.env, PYTHONUNBUFFERED: "1" },
  });

  const mcp: McpProcess = { proc, requestId: 1, pending: new Map() };

  const rl = createInterface({ input: proc.stdout! });
  rl.on("line", (line: string) => {
    try {
      const msg = JSON.parse(line);
      if (msg.id !== undefined && mcp.pending.has(msg.id)) {
        const { resolve, reject } = mcp.pending.get(msg.id)!;
        mcp.pending.delete(msg.id);
        if (msg.error) reject(new Error(msg.error.message ?? String(msg.error)));
        else resolve(msg.result);
      }
    } catch {
      // ignore non-JSON lines
    }
  });

  proc.on("error", (err: Error) => {
    for (const [, { reject }] of mcp.pending) reject(err);
    mcp.pending.clear();
  });

  return mcp;
}

export async function callMcpTool(
  mcp: McpProcess,
  toolName: string,
  args: Record<string, unknown>,
  timeoutMs = 60000,
): Promise<unknown> {
  const id = mcp.requestId++;
  const request = JSON.stringify({
    jsonrpc: "2.0",
    id,
    method: "tools/call",
    params: { name: toolName, arguments: args },
  });
  mcp.proc.stdin!.write(request + "\n");

  return new Promise((resolve, reject) => {
    mcp.pending.set(id, { resolve, reject });
    setTimeout(() => {
      if (mcp.pending.has(id)) {
        mcp.pending.delete(id);
        reject(new Error(`MCP tool "${toolName}" timed out after ${timeoutMs / 1000}s`));
      }
    }, timeoutMs);
  });
}

export async function initMcpServer(mcp: McpProcess): Promise<string[]> {
  const id = mcp.requestId++;
  const initReq = JSON.stringify({ jsonrpc: "2.0", id, method: "initialize", params: { capabilities: {} } });
  mcp.proc.stdin!.write(initReq + "\n");
  await new Promise<void>((resolve, reject) => {
    mcp.pending.set(id, { resolve: () => resolve(), reject: (e: Error) => reject(e) });
  });

  const listId = mcp.requestId++;
  const listReq = JSON.stringify({ jsonrpc: "2.0", id: listId, method: "tools/list", params: {} });
  mcp.proc.stdin!.write(listReq + "\n");
  const result = (await new Promise((resolve, reject) => {
    mcp.pending.set(listId, { resolve, reject });
  })) as { tools: Array<{ name: string }> };
  return result.tools.map((t) => t.name);
}

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

// ── Flat JSON-RPC bridge (non-MCP, direct method dispatch) ───────────

/**
 * 调用 flat-method bridge（如 agent_learning_bridge.py）。
 * 发送 {id, method, params}，接收 {id, result} | {id, error}。
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
