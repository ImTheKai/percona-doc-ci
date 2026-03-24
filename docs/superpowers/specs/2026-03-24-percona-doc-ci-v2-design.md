# percona-doc-ci v2 — Scenario-Based Doc Testing

**Date:** 2026-03-24
**Status:** Approved
**Replaces:** v1 single-page extract-plan-execute pipeline

---

## Problem with v1

The v1 pipeline reviews one doc page in isolation: extract code blocks → AI generates shell script → execute. The core limitation is that Percona docs are sequential, not independent. A user installing `pg_tde` follows steps across multiple pages (install guide → keyring setup → getting-started). Testing a single page misses cross-page gaps — a missing `CREATE EXTENSION` on one page causes a failure only visible if you follow the full journey.

---

## Design Goal

Run scenario-based tests on doc PRs. Each scenario represents a real user journey (e.g. "new user installs pg_tde and encrypts a table"). Claude Code acts as that user: it reads the docs freely across multiple pages, executes every command it encounters, and reports what worked and what failed — including which page and which step.

---

## Architecture

```
PR opened on a doc repo
         │
         ▼
┌─────────────────────────────────────────────────────┐
│  1. CHECKOUT                                        │
│     PR branch checked out. percona-doc-ci tooling   │
│     checked out alongside it (scenarios + skills).  │
└──────────────────────┬──────────────────────────────┘
                       │ PR diff + scenario index
                       ▼
┌─────────────────────────────────────────────────────┐
│  2. PLANNER AGENT (Claude Code, headless)           │
│     • Reads PR diff                                 │
│     • Matches changed files against scenario        │
│       frontmatter covers: fields (fast, deterministic)│
│     • AI second pass: catches non-obvious impacts   │
│       (e.g. variables.yml change affects all)       │
│     • Emits: JSON array of scenario IDs to run      │
└──────────────────────┬──────────────────────────────┘
                       │ ["pg_tde-install", "pg_tde-encrypt"]
                       ▼
┌─────────────────────────────────────────────────────┐
│  3. EXECUTOR AGENTS (GitHub matrix, parallel)       │
│     One Claude Code session per scenario.           │
│     Claude reads the scenario file, browses the     │
│     checked-out doc repo freely (multi-page),       │
│     executes commands in the CI environment,        │
│     and writes structured markdown output.          │
└──────────────────────┬──────────────────────────────┘
                       │ per-scenario output artifacts
                       ▼
┌─────────────────────────────────────────────────────┐
│  4. RESULT COLLECTION + PR COMMENT                  │
│     CI greps result blocks, aggregates pass/fail,   │
│     posts a single combined comment to the PR.      │
└─────────────────────────────────────────────────────┘
```

---

## Repository Layout

### percona-doc-ci (this repo)

```
percona-doc-ci/
├── .claude/
│   └── commands/                    # Shared Claude Code skills
│       ├── doc-navigate.md          # How to read/browse the doc checkout
│       ├── scenario-execute.md      # How to run commands + handle failures
│       └── scenario-report.md       # How to emit structured output
├── scenarios/                       # Built-in scenarios (bootstrapping)
│   ├── _index.md                    # Scenario registry (id + description)
│   └── pg_tde-install-new-user.md   # Example scenario
├── planner/
│   └── planner-prompt.md            # Planner agent instructions
└── .github/
    └── workflows/
        └── run-doc-tests.yml        # Reusable workflow (updated)
```

### Each doc repo (e.g. pg_tde-docs)

```
.github/
└── workflows/
    └── doc-tests.yml                # Calls the reusable workflow (no UX change)
scenarios/                           # Doc-repo-owned scenarios (optional)
    ├── _index.md
    └── pg_tde-encrypt-table.md
```

---

## Scenario File Format

```markdown
---
id: pg_tde-install-new-user
description: "New user installs pg_tde and encrypts a table from scratch"
covers:
  - docs/install/*.md
  - docs/pg_tde/getting-started.md
---

You are a new user who wants to try pg_tde...
[scenario instructions — written as a user persona + task]
```

- `id`: unique, used as the matrix key in CI
- `description`: read by the planner AI for its second-pass relevance check
- `covers`: glob patterns matched against PR diff for the fast-path filter

---

## Scenario Index (`_index.md`)

Each scenarios directory contains an `_index.md` listing all scenario IDs and descriptions. The planner reads this index (not the full scenario files) during its AI second pass — keeping the planner prompt small and fast.

```markdown
# Scenario Index

- **pg_tde-install-new-user**: New user installs pg_tde and encrypts a table from scratch
- **pg_tde-keyring-setup**: User configures a file-based keyring for pg_tde
```

---

## Planner Agent

**Input:** PR diff, merged scenario index (percona-doc-ci + doc repo)
**Output:** JSON array of scenario IDs, e.g. `["pg_tde-install-new-user"]`

**Two-phase selection:**

1. **Fast path** — glob-match changed files against each scenario's `covers:` frontmatter. Deterministic, no AI cost.
2. **AI second pass** — planner reads the diff and scenario descriptions. Catches non-obvious impacts: a change to `variables.yml`, a shared include, or a renamed concept that appears in several scenarios.

If the diff matches no scenarios, the planner emits an empty array and the workflow exits cleanly (no executor jobs run, no PR comment).

---

## Executor Agent Behaviour

Each executor Claude Code session:

- Receives: scenario file path, doc repo path, shared skills
- Reads docs freely across multiple pages — follows cross-references, checks prerequisite pages, reads config files referenced in the docs
- Does not consult external resources — only the checked-out doc repo
- Executes every command that applies to the scenario in the CI environment
- On failure: records what failed, the error output, and which doc page/line it came from — then stops (does not paper over failures or retry)

**Allowed tools:** `Bash`, `Read`, `Glob`, `Grep` — no web access, no writes outside `/tmp`

---

## Structured Output Format

The executor writes this at the end of its run:

````markdown
```scenario-result
id: pg_tde-install-new-user
status: FAIL
```

### What was tested
Followed the getting-started guide as a new user: installed pg_tde, initialized
the keyring, created an encrypted table.

### What failed
**Step:** `CREATE TABLE albums (...) USING tde_heap;`
**Page:** `docs/pg_tde/getting-started.md` (line ~47)
**Error:** `ERROR: extension "pg_tde" does not exist`
**Cause:** The install guide does not include `CREATE EXTENSION pg_tde;` before
table creation. The step is documented on a separate page not linked from
getting-started.
````

CI parses by grepping for ` ```scenario-result ` blocks and reading the `status:` line. Valid statuses: `PASS`, `FAIL`, `SKIP` (scenario not applicable after closer reading).

---

## CI Workflow Structure

Three jobs in the reusable workflow:

### Job 1: `planner`

```yaml
- Checkout doc repo (PR branch) + percona-doc-ci tooling
- Install Claude Code
- Merge scenario indexes from both sources
- Run planner agent → JSON array of scenario IDs
- Output: scenarios matrix
```

### Job 2: `execute` (matrix, parallel)

```yaml
strategy:
  matrix:
    scenario: ${{ fromJson(needs.planner.outputs.scenarios) }}
  fail-fast: false   # all scenarios run even if one fails

steps:
- Checkout doc repo + percona-doc-ci tooling
- Install Claude Code
- Locate scenario file (doc repo first, fall back to percona-doc-ci)
- Run: claude -p "$(cat scenario.md)" --allowedTools Bash,Read,Glob,Grep
- Upload output as artifact: scenario-{id}.md
```

### Job 3: `report`

```yaml
needs: execute
if: always()

steps:
- Download all scenario-*.md artifacts
- Parse scenario-result blocks
- Aggregate: N passed, M failed
- Post single combined PR comment
```

### Caller workflow (doc repos — no change in UX)

```yaml
jobs:
  doc-tests:
    uses: percona-lab/percona-doc-ci/.github/workflows/run-doc-tests.yml@v2
    with:
      repo-name: "pg_tde-docs"
    secrets:
      ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}
```

Optional new input: `scenarios-path` — override where Claude looks for doc-repo scenario files (default: `scenarios/`).

---

## Error Handling

| Situation | Behaviour |
|-----------|-----------|
| Planner returns empty array | Workflow exits cleanly, no comment posted |
| Executor times out (15 min) | Scenario marked FAIL with timeout note in comment |
| Claude Code exits non-zero without result block | Scenario marked FAIL with raw output in comment |
| No scenarios exist in either location | Warning comment: "No scenarios configured for this repo" |
| All scenarios return SKIP | Single comment: "No scenarios affected by this change" |

Tests are **warn-only** — they never block PR merge.

---

## What v1 Tooling Is Removed

The following v1 components are not carried forward into v2:

- `tools/universal-extract.lua` — Pandoc block extraction (Claude reads docs directly)
- `tools/ai_test_planner.py` — Script generation (Claude executes commands directly)
- `tools/analyze_failure.py` — Separate failure analysis (Claude reports inline)
- `tools/expand_vars.py` — May be retained if Claude needs pre-expanded docs, otherwise Claude can read `variables.yml` directly

The `tools/requirements.txt` and Pandoc install step are removed from the workflow.

---

## Migration

v1 and v2 are parallel branches. Doc repos calling `@main` continue to use v1. Doc repos wanting v2 pin to `@v2`. No breaking changes to the caller workflow interface.
