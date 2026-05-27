---
name: no-fallback-code
description: "NO FALLBACK code policy: enforce strict invariant-first implementation with no fallback behavior. Use for refactors where IDs, file paths, and storage ownership must be explicit, and failure paths must clean up placeholder state."
argument-hint: "Subsystem or files to harden"
user-invocable: true
disable-model-invocation: true
---

# No Fallback Code

## What This Skill Produces
This skill converts permissive or defensive fallback code into strict, explicit, invariant-driven code.

Expected result:
- Required inputs stay required.
- Ownership boundaries are explicit.
- Failure paths remove ghost state.
- Tests assert strict single-source-of-truth behavior.

## When To Use
Use this skill when you see any of these patterns:
- Required data modeled as optional (`None` defaults, empty-string defaults).
- Parameters inferred from unrelated outputs (for example, deriving input source from output token paths).
- Placeholder records/resources left alive after failed operations.
- Silent fallback behavior (`except: pass`, default values masking invalid state).
- Event/callback tests allowing ambiguous multiple results.

## Procedure
1. Define invariants first.
- List what must always exist for a valid operation.
- Mark each as required at API boundaries.
- Example categories: record id, input file path, mmap base path, repository ownership.

2. Map ownership and lifecycle.
- Identify which layer allocates placeholders.
- Identify which layer finalizes success state.
- Identify exact failure cleanup owner.

3. Remove fallback constructs.
- Remove optional defaults for required parameters.
- Remove fallback return values that hide invalid state.
- Remove silent exception swallowing.
- Replace inferred values with explicit parameters.

4. Enforce failure cleanup.
- If an operation pre-allocates placeholder state, cleanup must run on failure status.
- Ensure repository index/mapping state is removed together with owned runtime artifacts.

5. Validate call-chain integrity end-to-end.
- Verify all layers pass required parameters unchanged.
- Verify native/API signatures and Python wrappers match exactly.
- Verify no old signatures remain in call sites.

6. Harden tests to strict outcomes.
- Use single-result capture for single-operation workflows.
- Assert strict expected shape (one record id, record exists, runtime artifacts exist).
- Add failure-path checks for cleanup behavior.

## Decision Points
- If a value is required for correctness: make it required in the function signature.
- If ownership is ambiguous: stop and assign a single owner before coding.
- If fallback is currently masking unstable behavior: fail fast, then fix root cause.
- If operation emits one terminal result: use single-result capture, not list accumulation.

## Completion Checks
All checks must pass:
- No required field/arg is optional.
- No `None` default for required contract values.
- No empty-string fallback for required contract values.
- No silent `except` fallback.
- Placeholder state is removed on operation error.
- Tests verify strict single-result behavior and repository consistency.

## Suggested Prompt Examples
- "Apply no-fallback-code to parser input/output path ownership in this module."
- "Refactor this service to remove optional fallbacks and enforce required invariants."
- "Audit failure paths and ensure placeholder records are deleted on error statuses."
