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
import { existsSync } from "node:fs";
import { join } from "node:path";

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
 * 优先使用项目 venv（.venv/bin/python），回退到系统 python3/python。
 */
export function createMcpProcess(scriptPath: string, extraEnv?: Record<string, string>): McpProcess {
  let pythonExe: string;

  // WSL: 使用 Windows Python（通过 WSL interop，不依赖 WSL venv）
  // agent_learning 的所有依赖（sympy/chromadb/torch 等）已在 Windows Python 中安装
  if (process.platform === "linux") {
    // 必须用完整路径：避免 Git Bash / PATH 中的 WorkBuddy 托管 Python（3.13.12，无包）
    pythonExe = "/mnt/c/Users/Administrator/AppData/Local/Programs/Python/Python313/python.exe";
    // Windows Python 不认识 /mnt/d/... 路径，转换为 D:\...
    scriptPath = scriptPath.replace(/^\/mnt\/([a-z])\//i, (_, d) => `${d.toUpperCase()}:\\`);
  } else {
    // 非 WSL 场景：尝试项目 venv，回退到系统 python
    const venvPython = join(process.cwd(), ".venv", "bin", "python");
    const venvPythonWin = join(process.cwd(), ".venv", "Scripts", "python.exe");
    if (existsSync(venvPython)) {
      pythonExe = venvPython;
    } else if (existsSync(venvPythonWin)) {
      pythonExe = venvPythonWin;
    } else {
      pythonExe = "python";
    }
  }
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

// ── 路径白名单（沙箱）─────────────────────────────────────────────────

class BlockedPathError extends Error {
  constructor(
    message: string,
    public blockedPaths: string[],
  ) {
    super(message);
    this.name = "BlockedPathError";
  }
}

/** 终端交互确认：越权路径提请用户审批 */
async function requestConfirmation(paths: string[]): Promise<boolean> {
  const rl = createInterface({ input: process.stdin, output: process.stdout });
  try {
    const answer = await new Promise<string>((resolve) => {
      rl.question(
        `\n⚠️  以下路径超出白名单:\n${paths.map((p) => `  ${p}`).join("\n")}\n\n允许本次访问? [y/N] `,
        resolve,
      );
    });
    return answer.toLowerCase() === "y" || answer.toLowerCase() === "yes";
  } finally {
    rl.close();
  }
}

/** 允许 Python 工具读写的目录。越权调用的路径会被直接拦截。 */
const ALLOWED_ROOTS = [
  "D:\\agent_workflow",
  "D:\\agent_learning",
] as const;

function getAllowRoots(): string[] {
  // 临时放行：PI_ALLOW_ROOTS=D:\downloads;E:\temp
  const extra = process.env.PI_ALLOW_ROOTS;
  if (!extra) return [...ALLOWED_ROOTS];
  return [
    ...ALLOWED_ROOTS,
    ...extra.split(";").map((s) => s.trim()).filter(Boolean),
  ];
}

/**
 * 递归扫描参数中所有的字符串值。
 * 如果发现绝对路径且不在白名单内，直接抛错。
 *
 * 支持两种路径格式：
 *   Windows: D:\foo\bar
 *   WSL:     /mnt/d/foo/bar（自动转为 Windows 路径再校验）
 */
function validatePaths(obj: unknown, context: string): void {
  const blocked: string[] = [];
  _validate(obj, context, blocked);
  if (blocked.length > 0) {
    throw new BlockedPathError(
      `[路径越权] ${context}\n` +
      `  白名单: ${getAllowRoots().join(", ")}\n` +
      `  临时放行: PI_ALLOW_ROOTS="D:\\foo;E:\\bar" pi\n` +
      `  非交互:   PI_NO_CONFIRM=1 pi`,
      blocked,
    );
  }
}

function _validate(obj: unknown, context: string, blocked: string[]): void {
  if (obj === null || obj === undefined) return;

  if (typeof obj === "string") {
    // 优先处理 WSL 路径 → Windows 路径
    let normalized = obj;
    const mntMatch = obj.match(/^\/mnt\/([a-z])(\/.+)$/i);
    if (mntMatch) {
      normalized = `${mntMatch[1].toUpperCase()}:${mntMatch[2].replace(/\//g, "\\")}`;
    }

    // 仅校验绝对路径（跳过相对路径、URL、纯数值/标识符）
    const winAbs = normalized.match(/^[A-Z]:\\/);
    const unixAbs = normalized.match(/^\/(?!mnt\/)/); // 严格 Unix 绝对路径（非 /mnt/）
    if ((winAbs || unixAbs) && !isAllowed(normalized)) {
      blocked.push(obj);
    }
  } else if (Array.isArray(obj)) {
    for (const item of obj) _validate(item, context, blocked);
  } else if (typeof obj === "object") {
    for (const [key, value] of Object.entries(obj)) {
      _validate(value, `${context}.${key}`, blocked);
    }
  }
}

function isAllowed(normalized: string): boolean {
  const lower = normalized.toLowerCase();
  return getAllowRoots().some((root) => lower.startsWith(root.toLowerCase()));
}

// ── MCP 协议方法 ───────────────────────────────────────────────────────

/**
 * 异步版路径校验：越权时请求用户确认，拒绝则抛错。
 * 若 PI_NO_CONFIRM=1 则直接拒绝（非交互模式）。
 */
async function validatePathsAsync(obj: unknown, context: string): Promise<void> {
  try {
    validatePaths(obj, context);
  } catch (err) {
    if (!(err instanceof BlockedPathError)) throw err;
    if (process.env.PI_NO_CONFIRM === "1") throw err;
    const approved = await requestConfirmation(err.blockedPaths);
    if (!approved) throw err;
    // 用户确认放行，不做二次拦截
  }
}

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
 * 调用 MCP 工具（tools/call），含路径白名单校验 + 用户确认。
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
  // 路径白名单校验（越权则请求用户确认）
  await validatePathsAsync(args, `MCP tool "${toolName}" args`);
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
export async function callBridge(
  mcp: McpProcess,
  method: string,
  params: Record<string, unknown>,
  timeoutMs = 60000,
): Promise<unknown> {
  // 路径白名单校验（越权则请求用户确认）
  await validatePathsAsync(params, `Bridge method "${method}" params`);
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
