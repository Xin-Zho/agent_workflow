/**
 * science-extension — 科学计算扩展
 *
 * 通过 MCP 标准协议桥接 Python 科学计算子进程（sympy/pint/mendeleev），
 * 向 Pi Agent 注册 13 个科学计算工具。
 *
 * 依赖：shared/mcp-bridge.ts（标准 MCP JSON-RPC 2.0 over stdio）
 */

import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
import { Type } from "typebox";
import { join } from "node:path";
import {
  createMcpProcess,
  initMcpServer,
  callMcpTool,
  killMcpProcess,
  type McpProcess,
} from "../shared/mcp-bridge";

// ── 工具参数类型定义 ──────────────────────────────────────────────────

const chemistryParams = {
  balance_equation: Type.Object({
    equation: Type.String({ description: 'Chemical equation, e.g. "CH4 + O2 -> CO2 + H2O"' }),
  }),
  element_lookup: Type.Object({
    query: Type.String({ description: 'Element symbol "H"/"Fe" or formula "H2SO4"' }),
  }),
  solution_chem: Type.Object({
    acid: Type.String({ description: 'Acid formula "HCl"/"CH3COOH"' }),
    concentration: Type.Number({ description: "Concentration in mol/L" }),
  }),
  thermo_calc: Type.Object({
    reactants: Type.String({ description: "Reactants, comma-separated formulas" }),
    products: Type.String({ description: "Products, comma-separated formulas" }),
    temperature: Type.Optional(Type.Number({ description: "Temperature in Kelvin" })),
  }),
  equilibrium: Type.Object({
    reaction: Type.String({ description: "Reaction equation" }),
    initial_concentrations: Type.String({ description: "JSON array of initial concentrations" }),
  }),
  kinetics: Type.Object({
    order: Type.Number({ description: "Reaction order: 0, 1, or 2" }),
    k: Type.Number({ description: "Rate constant" }),
    concentration_0: Type.Number({ description: "Initial concentration" }),
    time: Type.Number({ description: "Time" }),
  }),
  electrochem: Type.Object({
    half_reaction: Type.String({ description: 'Half-reaction, e.g. "Zn2+ + 2e- -> Zn"' }),
    concentration: Type.Number({ description: "Ion concentration in mol/L" }),
    temperature: Type.Optional(Type.Number({ description: "Temperature in K, default 298" })),
  }),
};

const physicsParams = {
  mechanics: Type.Object({
    u: Type.Optional(Type.Number({ description: "Initial velocity (m/s)" })),
    v: Type.Optional(Type.Number({ description: "Final velocity (m/s)" })),
    a: Type.Optional(Type.Number({ description: "Acceleration (m/s²)" })),
    t: Type.Optional(Type.Number({ description: "Time (s)" })),
    s: Type.Optional(Type.Number({ description: "Displacement (m)" })),
  }),
  electromagnetism: Type.Object({
    q1: Type.Number({ description: "Charge 1 (C)" }),
    q2: Type.Number({ description: "Charge 2 (C)" }),
    r: Type.Number({ description: "Distance (m)" }),
  }),
  quantum: Type.Object({
    system: Type.String({ description: "System: infinite_well / harmonic_oscillator / hydrogen_atom" }),
    n: Type.Optional(Type.Number({ description: "Quantum number" })),
    L: Type.Optional(Type.Number({ description: "Well width (m) for infinite_well" })),
  }),
  thermodynamics: Type.Object({
    Th: Type.Number({ description: "Hot reservoir temperature (K)" }),
    Tc: Type.Number({ description: "Cold reservoir temperature (K)" }),
  }),
  optics: Type.Object({
    u: Type.Optional(Type.Number({ description: "Object distance (m)" })),
    v: Type.Optional(Type.Number({ description: "Image distance (m)" })),
    f: Type.Optional(Type.Number({ description: "Focal length (m)" })),
  }),
  error_propagation: Type.Object({
    values: Type.String({ description: "JSON array of measured values" }),
    uncertainties: Type.String({ description: "JSON array of uncertainties" }),
    operation: Type.String({ description: "Operation: add / subtract / multiply / divide" }),
  }),
};

// ── 工具元数据 ────────────────────────────────────────────────────────

const toolMeta: Record<string, { desc: string; label: string; category: "chemistry" | "physics" }> = {
  balance_equation:  { desc: "Balance a chemical equation (e.g. CH4 + O2 -> CO2 + H2O)", label: "Balance Eq",   category: "chemistry" },
  element_lookup:    { desc: "Look up element info or calculate molar mass for a formula",     label: "Elem Lookup", category: "chemistry" },
  solution_chem:     { desc: "Calculate pH of acid solution given acid and concentration",      label: "Solution pH", category: "chemistry" },
  thermo_calc:       { desc: "Calculate thermodynamics (ΔH/ΔG/ΔS) for a reaction",             label: "Thermo Calc", category: "chemistry" },
  equilibrium:       { desc: "Chemical equilibrium calculation",                               label: "Equilibrium", category: "chemistry" },
  kinetics:          { desc: "Reaction kinetics (order 0/1/2)",                                label: "Kinetics",    category: "chemistry" },
  electrochem:       { desc: "Nernst equation: calculate cell potential",                      label: "Electrochem", category: "chemistry" },
  mechanics:         { desc: "Kinematics: provide 3 of {u, v, a, t, s}",                      label: "Mechanics",   category: "physics" },
  electromagnetism:  { desc: "Coulomb force F = k*q1*q2/r²",                                  label: "E&M",        category: "physics" },
  quantum:           { desc: "Analytically solvable QM systems",                               label: "Quantum",    category: "physics" },
  thermodynamics:    { desc: "Carnot cycle efficiency η = 1 - Tc/Th",                         label: "Thermo",     category: "physics" },
  optics:            { desc: "Lens equation 1/f = 1/u + 1/v",                                 label: "Optics",     category: "physics" },
  error_propagation: { desc: "Error propagation for add/subtract/multiply/divide",             label: "Error Prop", category: "physics" },
};

// ── 扩展入口 ──────────────────────────────────────────────────────────

export default function (pi: ExtensionAPI) {
  let chemMcp: McpProcess | null = null;
  let physMcp: McpProcess | null = null;

  function getToolsDir(): string {
    return join(process.cwd(), "python-tools");
  }

  async function startServers(): Promise<void> {
    const toolsDir = getToolsDir();

    chemMcp = createMcpProcess(join(toolsDir, "chemistry_server.py"));
    physMcp  = createMcpProcess(join(toolsDir, "physics_server.py"));

    const [chemTools, physTools] = await Promise.all([
      initMcpServer(chemMcp),
      initMcpServer(physMcp),
    ]);
  }

  function stopServers(): void {
    if (chemMcp) { killMcpProcess(chemMcp); chemMcp = null; }
    if (physMcp)  { killMcpProcess(physMcp);  physMcp = null; }
  }

  pi.on("session_start", async (_event, ctx) => {
    try {
      await startServers();
      ctx.ui.notify("Science servers started (chemistry + physics)", "info");
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : String(err);
      ctx.ui.notify(`Science servers failed: ${msg}`, "error");
    }
  });

  pi.on("session_shutdown", () => {
    stopServers();
  });

  // 注册所有科学计算工具
  for (const [name, meta] of Object.entries(toolMeta)) {
    const isChemistry = meta.category === "chemistry";
    const paramSchema = isChemistry
      ? (chemistryParams as Record<string, unknown>)[name]
      : (physicsParams as Record<string, unknown>)[name];

    pi.registerTool({
      name,
      label: meta.label,
      description: meta.desc,
      parameters: paramSchema,
      async execute(_toolCallId, params, _signal, _onUpdate) {
        const mcp = isChemistry ? chemMcp : physMcp;
        if (!mcp) throw new Error(`${meta.category} server not running`);

        const result = await callMcpTool(mcp, name, params as Record<string, unknown>);
        return {
          content: [{ type: "text", text: JSON.stringify(result, null, 2) }],
          details: {},
        };
      },
    });
  }
}
