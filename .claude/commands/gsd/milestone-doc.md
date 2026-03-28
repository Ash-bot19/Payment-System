---
name: gsd:milestone-doc
description: Write milestone technical post-mortem in Notion — components, architecture decisions, trade-offs, bugs
argument-hint: "[milestone number, e.g., '2']"
allowed-tools:
  - Read
  - Bash
  - Glob
  - Grep
  - mcp__claude_ai_Notion__notion-fetch
  - mcp__claude_ai_Notion__notion-create-pages
---

<objective>
Create a Notion sub-page containing a technical post-mortem for the completed milestone under the Payment System Build Log.

This is a written-for-engineers document — honest, specific, and useful for someone onboarding later or picking up the next milestone. Not a summary of what was planned; a record of what actually happened and why decisions were made.

Parent page: https://www.notion.so/32de533b2140815ca3d7db8ed24bf82c
</objective>

<context>
Milestone: $ARGUMENTS
- If provided: document that specific milestone number (e.g., "2")
- If not provided: detect the most recently completed milestone from CLAUDE.md or .planning/ROADMAP.md

Project root: D:/Github/Payment System/payment-backend/
Planning root: D:/Github/Payment System/.planning/
CLAUDE.md: D:/Github/Payment System/payment-backend/CLAUDE.md
</context>

<process>

## Step 1 — Identify the milestone

If $ARGUMENTS is provided, use it. Otherwise read CLAUDE.md "Current Build Status" to find the most recently completed milestone and its phases.

Note: milestone number, name, completion date, and which phases it contained.

## Step 2 — Gather all source material

Read in order:
1. CLAUDE.md — for the milestone summary, known gotchas added during this milestone, and locked contracts
2. Each phase's PLAN.md under `.planning/phases/{N}/PLAN.md`
3. Each phase's VERIFICATION.md or UAT file if present (`.planning/phases/{N}/VERIFICATION.md`, `{N}-UAT.md`, `{N}-HUMAN-UAT.md`)
4. Git log for commits in this milestone: `git log --oneline` filtered to the milestone's date range
5. Any archived milestone doc at `.planning/archive/` if it exists

## Step 3 — Extract the content

From the material collected, identify:

**Components built:** Every service, module, or infrastructure piece added. Be specific — not "added validation" but "ValidationConsumer (port 8002) with Kafka poll loop, manual offset commit, and /health endpoint".

**Architectural decisions:** Choices that had real alternatives. For each: what was chosen, what the alternative was, and why this one won. Pull these from PLAN.md discussion sections, CLAUDE.md gotchas, or any notes in UAT files.

**Trade-offs accepted:** Constraints or shortcuts taken deliberately. Be honest — e.g., "no retry logic on DLQ consumer in this milestone, deferred to M5" or "rate limiting uses Redis INCR with minute buckets — approximate, not exact".

**Bugs encountered and fixed:** Pull from CLAUDE.md "Known Gotchas", UAT failure notes, and git commit messages (fix: commits). For each: what broke, what caused it, how it was fixed.

**What's locked (contracts):** List any LOCKED items that were established or enforced during this milestone (Kafka topics, idempotency strategy, state machine transitions, etc.) These are constraints future milestones must respect.

## Step 4 — Build the Notion page content

Structure the page exactly as follows:

```
# M{N} Technical Write-Up — {Milestone Name}

**Completed:** {date}
**Phases:** {list of phase numbers and names}

---

## What We Built

{One paragraph overview — the milestone in one breath. What the system can do now that it couldn't before.}

### Components

| Component | Type | Port / Location | Purpose |
|---|---|---|---|
| {name} | {Service/Module/Infra} | {port or path} | {one line} |

---

## Architectural Decisions

### {Decision Title}
**Chose:** {what was picked}
**Alternative:** {what was considered}
**Why:** {the actual reason — performance, simplicity, constraint, prior art}

{repeat for each significant decision}

---

## Trade-offs Accepted

- **{Trade-off name}:** {what was done and what was deferred/accepted as imperfect, and why it was acceptable at this stage}

{repeat}

---

## Bugs Encountered

### {Bug title}
**Symptom:** {what broke or failed}
**Root cause:** {why it happened}
**Fix:** {what changed}
**Prevention:** {what this means for future milestones, if anything}

{repeat for each bug}

---

## Locked Contracts Established

These are hard constraints that all future milestones must respect:

- **{Contract name}:** {one-line description of what is locked and where it's enforced}

{repeat}

---

## What the Next Milestone Inherits

{2–4 sentences on the exact state of the system at the end of this milestone — the starting point for M{N+1}. What's running, what's tested, what's pending human verification.}

---

## Human UAT Summary

{One paragraph covering the UAT story for the whole milestone. Include:
- Which phases required UAT iterations vs passed first time
- Any cross-phase issues that only surfaced during integration testing
- The final test count and pass rate when all UAT was signed off}

### UAT Timeline

| Phase | Initial result | Issues found | Final result |
|---|---|---|---|
| Phase {N} | ✅ Pass / ❌ Fail | {None / brief description} | ✅ Pass |

### Notable UAT failures

{For each significant failure caught during UAT — not already covered in Bugs Encountered — document:}

**{Failure title}**
- **When:** Phase {N} UAT, {date}
- **Symptom:** {what the human tester observed}
- **Root cause:** {why it happened}
- **Fix:** {what changed}
- **Impact:** {did this change any locked contracts, test strategy, or architecture?}

{If no significant UAT failures occurred, write: "All phases passed UAT with no architectural changes required."}
```

## Step 5 — Create the Notion sub-page

Use `notion-create-pages` to create the page as a child of `32de533b2140815ca3d7db8ed24bf82c`.

Title format: `M{N} Technical Write-Up — {Milestone Name}`

## Step 6 — Confirm and report

After creating the page, output:
- The Notion URL of the new page
- Count of decisions, trade-offs, and bugs documented
- Flag any section that was left thin due to missing source material

</process>

<quality_rules>
- Be specific and honest — vague statements like "improved reliability" are worthless; name the actual mechanism
- Bugs section must include root cause, not just symptom — "Redis wasn't healthy" is not a root cause; "Docker Compose health check used nc which isn't available in the Confluent image" is
- Architectural decisions must name the actual alternative that was considered, not a generic one
- Trade-offs must be honest about what was deferred and why, not justify every choice as perfect
- Pull real details from git commits, UAT files, and CLAUDE.md — do not invent or generalize
- If source material is thin for a section, write what you can and flag it explicitly at the end
</quality_rules>
