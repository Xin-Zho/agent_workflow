---
name: scientific-method
description: Standard scientific computing workflow with verification
---

# Scientific Computing Methodology

When solving science problems, follow this workflow:

## Step 1: Compute
- Use the appropriate computational tool (mechanics, chemistry, physics)
- Perform ALL calculations with the tool, do NOT skip steps or approximate mentally
- Always show intermediate steps

## Step 2: Verify
After computation, verify the result:
1. **Dimensional check**: Ensure units are consistent
2. **Magnitude check**: Is the result in a reasonable order of magnitude?
3. **Back-substitution**: Plug the result back into original equations
4. **Knowledge verification**: Search for known reference values to cross-check

## Step 3: Search (if needed)
- Only search the web or knowledge base if:
  - You need a physical constant not provided
  - You need a formula you're uncertain about
  - Verification revealed an inconsistency

## Step 4: Output
Format the final answer with:
- Step-by-step reasoning
- Key formulas used
- Numerical result with units
- Verification results

## Rules
- ALL calculations must use tools, not mental math
- ALL results include proper units
- Use LaTeX formatting: $inline$ for inline, $$block$$ for display equations
- If a problem cannot be solved analytically, explicitly refuse and explain why
- Multi-electron quantum systems must be refused (suggest DFT/HF numerical methods)
