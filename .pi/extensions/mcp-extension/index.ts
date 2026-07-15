/**
 * mcp-extension — 通用 MCP 协议桥接扩展
 *
 * 读取 servers.json 配置，自动连接 MCP servers、发现工具、注册为 Pi 工具。
 * 与 agent_learning 的 MCPClientManager + ToolRegistry 架构对齐。
 *
 * servers.json 格式：
 * {
 *   "servers": {
 *     "<name>": {
 *       "command": ["python3", "path/to/server.py"],
 *       "env": { "KEY": "value" },
 *       "enabled": true
 *     }
 *   }
 * }
 */

import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
import { Type } from "typebox";
import { join } from "node:path";
import { readFileSync, existsSync } from "node:fs";
import {
  createMcpProcess,
  initMcpServer,
  callMcpTool,
  killMcpProcess,
  type McpProcess,
  type McpToolDef,
} from "../shared/mcp-bridge";

// ── 配置类型 ───────────────────────────────────────────────────────────

type McpServerConfig = {
  command: string[];
  env?: Record<string, string>;
  enabled?: boolean;
};

type McpServersConfig = {
  servers: Record<string, McpServerConfig>;
};

type RunningServer = {
  name: string;
  mcp: McpProcess;
  config: McpServerConfig;
  tools: McpToolDef[];
};

// ── 配置加载 ───────────────────────────────────────────────────────────

function loadConfig(): McpServersConfig {
  const configPath = join(process.cwd(), ".pi", "extensions", "mcp-extension", "servers.json");
  if (!existsSync(configPath)) {
    return { servers: {} };
  }
  const raw = readFileSync(configPath, "utf-8");
  return JSON.parse(raw);
}

// ── TypeBox schema 转换 ────────────────────────────────────────────────

/**
 * 将 MCP tool inputSchema 转为 TypeBox schema（宽松转换）。
 * 优先用 inputSchema，降级时生成自由 JSON 参数。
 */
function inputSchemaToTypeBox(schema: McpToolDef["inputSchema"]) {
  if (!schema?.properties) {
    // 回退：任意 JSON object
    return Type.Object({}, { additionalProperties: true });
  }

  const props: Record<string, ReturnType<typeof Type.String>> = {};
  for (const [key, prop] of Object.entries(schema.properties)) {
    const p = prop as Record<string, unknown>;
    switch (p.type) {
      case "number":
      case "integer":
        props[key] = Type.Number({ description: (p.description as string) ?? key });
        break;
      case "boolean":
        props[key] = Type.Boolean({ description: (p.description as string) ?? key });
        break;
      case "string":
      default:
        props[key] = Type.String({ description: (p.description as string) ?? key });
    }
  }

  return Type.Object(props);
}

// ── 扩展入口 ──────────────────────────────────────────────────────────

export default function (pi: ExtensionAPI) {
  const running: Map<string, RunningServer> = new Map();
  const config = loadConfig();

  async function connectServer(name: string, cfg: McpServerConfig): Promise<void> {
    if (running.has(name)) return;

    const [cmd, ...args] = cfg.command;
    if (args.length > 0) {
      // 多参数 command（如 ["python3", "server.py"]）
      const mcp = createMcpProcess(args[args.length - 1], cfg.env);
      running.set(name, { name, mcp, config: cfg, tools: [] });
      const tools = await initMcpServer(mcp);
      running.get(name)!.tools = tools;

      // 自动注册所有发现的工具
      for (const tool of tools) {
        const fullName = `${name}_${tool.name}`;
        pi.registerTool({
          name: fullName,
          label: tool.name,
          description: tool.description ?? `${name}: ${tool.name}`,
          parameters: inputSchemaToTypeBox(tool.inputSchema),
          async execute(_toolCallId, params, _signal, _onUpdate) {
            const server = running.get(name);
            if (!server) throw new Error(`MCP server "${name}" not connected`);
            const result = await callMcpTool(server.mcp, tool.name, params as Record<string, unknown>);
            return {
              content: [{ type: "text", text: JSON.stringify(result, null, 2) }],
              details: {},
            };
          },
        });
      }
    }
  }

  function disconnectServer(name: string): void {
    const server = running.get(name);
    if (!server) return;
    killMcpProcess(server.mcp);
    running.delete(name);
  }

  // 会话生命周期
  pi.on("session_start", async (_event, ctx) => {
    const enabled = Object.entries(config.servers)
      .filter(([, cfg]) => cfg.enabled !== false);

    if (enabled.length === 0) {
      ctx.ui.notify("No MCP servers configured. Add to .pi/extensions/mcp-extension/servers.json", "warning");
      return;
    }

    for (const [name, cfg] of enabled) {
      try {
        await connectServer(name, cfg);
        const server = running.get(name)!;
        ctx.ui.notify(
          `MCP "${name}" connected (${server.tools.length} tools)`,
          "info",
        );
      } catch (err: unknown) {
        const msg = err instanceof Error ? err.message : String(err);
        ctx.ui.notify(`MCP "${name}" failed: ${msg}`, "error");
      }
    }
  });

  pi.on("session_shutdown", () => {
    for (const name of running.keys()) {
      disconnectServer(name);
    }
  });

  // ── 管理工具 ─────────────────────────────────────────────────────
  pi.registerTool({
    name: "mcp_list",
    label: "MCP List",
    description: "List all connected MCP servers and their tools",
    parameters: Type.Object({}),
    async execute() {
      const servers = Array.from(running.entries()).map(([name, s]) => ({
        name,
        tools: s.tools.map((t) => t.name),
        toolCount: s.tools.length,
      }));
      return {
        content: [{ type: "text", text: JSON.stringify({ servers }, null, 2) }],
        details: {},
      };
    },
  });

  pi.registerTool({
    name: "mcp_reconnect",
    label: "MCP Reconnect",
    description: "Reconnect a specific MCP server by name",
    parameters: Type.Object({
      server: Type.String({ description: "MCP server name to reconnect" }),
    }),
    async execute(_toolCallId, params) {
      const srv = params as { server: string };
      const server = running.get(srv.server);
      if (!server) return { content: [{ type: "text", text: `Server "${srv.server}" not found` }], details: {} };

      disconnectServer(srv.server);
      await connectServer(srv.server, server.config);
      const reconnected = running.get(srv.server)!;
      return {
        content: [{ type: "text", text: `Reconnected "${srv.server}" (${reconnected.tools.length} tools)` }],
        details: {},
      };
    },
  });
}
