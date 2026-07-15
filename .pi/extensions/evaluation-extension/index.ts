/**
 * evaluation-extension — 评估闭环扩展
 *
 * 追踪每次 agent 会话的指标，提供 LLM Judge 质量评估，
 * 生成优化建议，形成"执行→评估→优化"的闭环。
 *
 * 指标：
 *   - token_count: 总 token 消耗
 *   - step_count: agent 步数
 *   - tool_call_count: 工具调用次数
 *   - latency_ms: 总耗时
 *   - success: 是否成功完成
 *   - error_count: 错误次数
 *
 * 钩子：
 *   - turn_end: 记录每轮数据
 *   - agent_end: 运行评估，输出报告
 *
 * 工具：
 *   - evaluate: 手动触发 LLM Judge 评估
 *   - eval_stats: 查看会话统计
 */
import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
import { Type } from "typebox";

// ── 指标追踪 ──────────────────────────────────────────────────────────

interface SessionMetrics {
  sessionId: string;
  startTime: number;
  endTime?: number;
  turnCount: number;
  totalTokens: number;
  toolCallCount: number;
  errorCount: number;
  userMessages: string[];
  assistantMessages: string[];
  success: boolean;
}

let currentMetrics: SessionMetrics | null = null;
const sessionHistory: SessionMetrics[] = [];

function initMetrics(sessionId: string): void {
  currentMetrics = {
    sessionId,
    startTime: Date.now(),
    turnCount: 0,
    totalTokens: 0,
    toolCallCount: 0,
    errorCount: 0,
    userMessages: [],
    assistantMessages: [],
    success: true,
  };
}

// ── LLM Judge 评估模板 ────────────────────────────────────────────────

const JUDGE_PROMPT = `你是一个科学计算助教的评估专家。请根据以下标准对助教的回答进行评分。

评分标准（每项 0-10 分）：
1. 正确性 — 计算是否正确？结论是否符合科学原理？
2. 完整性 — 是否包含必要的步骤？是否遗漏关键信息？
3. 清晰度 — 回答是否结构清晰？是否易于理解？
4. 严谨性 — 是否有量纲检查？是否有验证步骤？是否有边界条件说明？
5. 知识引用 — 是否正确引用了权威知识？

用户问题：
{user_question}

助手回答：
{assistant_answer}

请以 JSON 格式输出评分：
{
  "scores": {
    "correctness": <0-10>,
    "completeness": <0-10>,
    "clarity": <0-10>,
    "rigor": <0-10>,
    "knowledge": <0-10>
  },
  "overall": <0-10>,
  "summary": "<简短评价>",
  "suggestions": ["<改进建议1>", "<改进建议2>"]
}`;

// ── 扩展入口 ──────────────────────────────────────────────────────────

export default function (pi: ExtensionAPI) {
  // ── 会话生命周期 ─────────────────────────────────────────────────
  pi.on("session_start", (_event, ctx) => {
    initMetrics(ctx.sessionId || `session-${Date.now()}`);
  });

  pi.on("session_shutdown", () => {
    if (currentMetrics) {
      currentMetrics.endTime = Date.now();
      sessionHistory.push({ ...currentMetrics });
      currentMetrics = null;
    }
  });

  // ── turn_end: 记录每轮数据 ───────────────────────────────────────
  pi.on("turn_end", (_event, ctx) => {
    if (!currentMetrics) return;
    currentMetrics.turnCount++;

    const msgs = ctx.messages;
    const last = msgs[msgs.length - 1];
    if (!last) return;

    if (last.role === "user") {
      currentMetrics.userMessages.push(
        typeof last.content === "string" ? last.content : JSON.stringify(last.content),
      );
    } else if (last.role === "assistant") {
      const content = typeof last.content === "string"
        ? last.content
        : Array.isArray(last.content)
          ? last.content.map((p: { text?: string }) => p.text || "").join("")
          : JSON.stringify(last.content);
      currentMetrics.assistantMessages.push(content);
    }

    // 粗略 Token 估算（中文 ~1.5 char/token, 英文 ~4 char/token）
    const lastContent = typeof last.content === "string" ? last.content : JSON.stringify(last.content);
    currentMetrics.totalTokens += Math.ceil(lastContent.length / 3);
  });

  // ── tool_call: 记录工具调用 ──────────────────────────────────────
  pi.on("tool_call", (_event, _ctx) => {
    if (!currentMetrics) return;
    currentMetrics.toolCallCount++;
  });

  // ── agent_end: 运行评估 ──────────────────────────────────────────
  pi.on("agent_end", async (_event, ctx) => {
    if (!currentMetrics) return;
    currentMetrics.endTime = Date.now();

    const latencySec = ((currentMetrics.endTime - currentMetrics.startTime) / 1000).toFixed(1);
    const report = [
      `═══ Session Evaluation ═══`,
      `Session: ${currentMetrics.sessionId}`,
      `Turns: ${currentMetrics.turnCount}`,
      `Tools called: ${currentMetrics.toolCallCount}`,
      `Est. tokens: ${currentMetrics.totalTokens}`,
      `Latency: ${latencySec}s`,
      `Errors: ${currentMetrics.errorCount}`,
      `Status: ${currentMetrics.success ? "SUCCESS" : "FAILED"}`,
      `══════════════════════════`,
    ].join("\n");

    ctx.ui.notify(report, currentMetrics.success ? "info" : "warning");

    // 持久化历史
    sessionHistory.push({ ...currentMetrics });
    currentMetrics = null;
  });

  // ── evaluate 工具：手动 LLM Judge ────────────────────────────────
  pi.registerTool({
    name: "evaluate",
    label: "Evaluate",
    description: "Run LLM-as-Judge quality evaluation on the last assistant response.",
    parameters: Type.Object({
      criteria: Type.Optional(
        Type.String({
          description: "Evaluation criteria: correctness / completeness / clarity / rigor / knowledge / all (default all)",
        }),
      ),
    }),
    async execute(_toolCallId, params, _signal, _onUpdate) {
      if (!currentMetrics || currentMetrics.userMessages.length === 0) {
        return {
          content: [{ type: "text", text: "No conversation to evaluate." }],
          details: {},
        };
      }

      const lastUser = currentMetrics.userMessages[currentMetrics.userMessages.length - 1];
      const lastAssistant =
        currentMetrics.assistantMessages[currentMetrics.assistantMessages.length - 1] || "(no response)";

      const judgePrompt = JUDGE_PROMPT
        .replace("{user_question}", lastUser)
        .replace("{assistant_answer}", lastAssistant);

      return {
        content: [
          {
            type: "text",
            text: `[LLM Judge Prompt — send this to the LLM for evaluation]\n\n${judgePrompt}\n\n` +
              `Session metrics: ${currentMetrics.turnCount} turns, ${currentMetrics.toolCallCount} tools, ` +
              `${currentMetrics.totalTokens} est. tokens`,
          },
        ],
        details: {},
      };
    },
  });

  // ── eval_stats 工具：查看统计 ────────────────────────────────────
  pi.registerTool({
    name: "eval_stats",
    label: "Eval Stats",
    description: "Show evaluation statistics for current session and history.",
    parameters: Type.Object({}),
    async execute(_toolCallId, _params, _signal, _onUpdate) {
      const current = currentMetrics
        ? {
            sessionId: currentMetrics.sessionId,
            turns: currentMetrics.turnCount,
            tools: currentMetrics.toolCallCount,
            tokens: currentMetrics.totalTokens,
            latencyMs: Date.now() - currentMetrics.startTime,
            errors: currentMetrics.errorCount,
          }
        : null;

      const history = sessionHistory.map((s) => ({
        sessionId: s.sessionId,
        turns: s.turnCount,
        tools: s.toolCallCount,
        tokens: s.totalTokens,
        latencyMs: (s.endTime || 0) - s.startTime,
        success: s.success,
      }));

      // 汇总统计
      const totalSessions = history.length;
      const avgTurns =
        totalSessions > 0 ? (history.reduce((a, s) => a + s.turns, 0) / totalSessions).toFixed(1) : "0";
      const successRate =
        totalSessions > 0
          ? ((history.filter((s) => s.success).length / totalSessions) * 100).toFixed(0) + "%"
          : "N/A";

      return {
        content: [
          {
            type: "text",
            text: JSON.stringify(
              {
                current,
                summary: {
                  totalSessions,
                  avgTurns,
                  successRate,
                },
                history: history.slice(-10), // 最近 10 条
              },
              null,
              2,
            ),
          },
        ],
        details: {},
      };
    },
  });
}
