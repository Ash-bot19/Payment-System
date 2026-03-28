---
name: gsd:phase-doc
description: Document phase code additions in Notion Build Log — file walkthrough with inline comments and connection diagram
argument-hint: "[phase number, e.g., '4']"
allowed-tools:
  - Read
  - Bash
  - Glob
  - Grep
  - mcp__claude_ai_Notion__notion-fetch
  - mcp__claude_ai_Notion__notion-create-pages
  - mcp__claude_ai_Notion__notion-search
---

<objective>
Create a Notion sub-page documenting this phase under the Payment System Build Log.

The page must match the established format exactly (phases 1–3 set the standard):
- Phase title + milestone + status header
- Plain-English "What this phase built" summary (3–5 sentences, no jargon)
- One section per new/modified file: path as heading → italic one-liner description → full code with inline comments on every non-obvious line
- "How the pieces connect" ASCII flow diagram showing data flow
- Closing line: "Phase N+1 picks up from…"

Parent page: https://www.notion.so/32de533b2140815ca3d7db8ed24bf82c
</objective>

<context>
Phase: $ARGUMENTS
- If provided: document that specific phase number
- If not provided: detect the most recently completed phase from .planning/ directory

Project root: D:/Github/Payment System/payment-backend/
Planning root: D:/Github/Payment System/.planning/
</context>

<process>

## Step 1 — Identify the phase

If $ARGUMENTS is provided, use it as the phase number. Otherwise read `.planning/STATE.md` or `ROADMAP.md` to find the most recently completed phase.

Read the phase's PLAN.md at `.planning/phases/{phase_num}/PLAN.md` to understand what was planned.

## Step 2 — Collect all changed files

Run: `git diff --name-only HEAD~1 HEAD` (or diff against the commit before this phase started) to get a list of files changed in this phase.

If git diff is inconclusive, read the PLAN.md tasks to identify which files were created or modified.

Exclude from documentation: test files (`tests/`), migration files (`db/migrations/`), `.env*`, `__pycache__`, `*.pyc`.

## Step 3 — Read every file to document

For each file in the changed set:
- Read the full file content
- Understand what it does in the context of the payment system
- Note how it connects to other files/services

Also read the phase's PLAN.md for the original intent — use it to write the "What this phase built" summary.

## Step 4 — Build the Notion page content

Construct the page following this exact structure:

```
# Phase {N} — {Phase Title}

**Milestone:** M{X} — {Milestone Name}

**Status:** ✅ Complete ({date})

**What this phase built:**

{3–5 sentence plain-English summary. Explain the purpose and outcome, not the implementation details. Write as if explaining to a smart non-engineer.}

---

## File: `{relative/path/from/payment-backend/}`

*{One sentence: what this file is and its role in the system.}*

```python
{full file content with inline comments added to every non-obvious line}
{comments explain WHY, not WHAT — focus on design decisions and data flow}
{use # ―― Section Header ―― style for logical blocks, matching phases 1–3}
```

{repeat for each file}

---

## How the pieces connect

```
{ASCII flow diagram showing the data path through this phase}
{match the arrow style from phases 1–3: → for flow, indentation for branches}
```

**Phase {N+1} picks up from {Kafka topic or service boundary}** — {one sentence on what the next phase does with this phase's output}.

---

## Human UAT

**Result:** ✅ Passed / ❌ Failed / ⚠️ Passed with fixes — {date}

### Checks performed

| # | What was tested | Result |
|---|---|---|
| {1} | {Specific check — e.g., "8/8 integration tests pass"} | ✅ Pass / ❌ Fail |
| {2} | {e.g., "Alembic migration 001 applied cleanly"} | ✅ Pass / ❌ Fail |

### Failures and fixes

{For each check that initially failed, document:}

**{Check name}**
- **Symptom:** {what broke or failed}
- **Root cause:** {why it happened}
- **Fix:** {what was changed to make it pass}

{If everything passed first time, write: "All checks passed on first run — no fixes required."}

### Final verification

{One sentence confirming the phase passed UAT and is production-ready.}
```

## Step 5 — Fetch existing page to confirm structure

Fetch https://www.notion.so/32de533b2140815ca3d7db8ed24bf82c to see current sub-pages and verify the new page won't duplicate an existing one.

## Step 6 — Create the Notion sub-page

Use `notion-create-pages` to create the page as a child of `32de533b2140815ca3d7db8ed24bf82c`.

Title format: `Phase {N} — {Phase Title} (M{X})`

This matches the existing sub-page naming: "Phase 1 — Foundation + Ingestion (M1)", "Phase 2 — Validation Layer + DLQ (M2)", etc.

## Step 7 — Confirm and report

After creating the page, output:
- The Notion URL of the new page
- A one-line summary of how many files were documented
- Any files that were skipped and why

</process>

<quality_rules>
- Every code block must have comments — no bare code
- Comments explain intent and data flow, not just restate the code
- The ASCII diagram must show the full data path, including the Kafka topic names (use the locked topic names from CLAUDE.md)
- "What this phase built" must be readable by someone who didn't write the code
- Do not document test files — tests are validated separately via /gsd:verify-work
- If a file was only minimally changed (e.g., one import added), still document it but note it was extended, not created
</quality_rules>
