# Material Research Workflow

Use this workflow for literature tasks involving material performance, structures,
manufacturing processes, formulations or composition ratios.

## Stage 1 — Clarify

Ask all missing questions in one batch. Produce a task definition containing target
metrics, hard constraints, optimization objectives, acceptable trade-offs, laboratory
constraints, paper target and language/year policy. Do not begin retrieval until the
user confirms this definition.

## Stage 2 — Retrieve and screen

Generate Chinese and English queries for:

1. target performance and measurement method;
2. candidate structures and working principles;
3. mature laboratory-feasible process systems;
4. components, ratios and formulation optimization.

Use database retrieval, deterministic filters, embedding recall and reranking in that
order. Allocate candidate slots by paper role instead of taking only the global top-K.
Treat abstract findings as low-evidence. Present the candidate list and pause for user
approval before any full-text workflow.

## Stage 3 — Read and extract

Browse the entire paper once. Then deeply read the manufacturing, formulation,
experiment and result sections. Extract every reported sample as a separate record.
Keep these variable groups separate:

- composition and ratio;
- process conditions;
- geometry/structure;
- test conditions;
- performance results.

For each field record evidence location and source type: explicit, derived, estimated,
inferred or missing. Search supplementary material and related versions before declaring
a field missing. Missing or ambiguous values must not enter quantitative comparisons.

## Stage 4 — Compare and report

Check metric definitions, ratio bases, units, geometry and test conditions before
comparison. Organize the report as:

target performance → structure → laboratory process system → composition and ratio.

Use chronology only as a secondary line within each section. Default outputs are the
paper list, evidence-backed review, formulation/process/performance table and a batch
of informative experiment points. Innovation suggestions are optional.

## Stage 5 — Review

The task owner or an administrator manually reviews extracted results. Only approved
objective data enter the shared knowledge base. Unreviewed data may be used only as a
retrieval lead.
