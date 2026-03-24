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
│     PR branch checked out at $GITHUB_WORKSPACE.     │
│     percona-doc-ci tooling checked out at           │
│     $GITHUB_WORKSPACE/_doc-ci-tools.                │
└──────────────────────┬──────────────────────────────┘
                       │ PR diff + scenario index
                       ▼
┌─────────────────────────────────────────────────────┐
│  2. PLANNER AGENT (Claude Code, headless)           │
│     • Reads PR diff                                 │
│     • Fast path: glob-match diff against covers:    │
│       fields in each scenario file                  │
│     • AI second pass: reads diff + scenario         │
│       descriptions (_index.md) to catch non-obvious │
│       impacts (e.g. variables.yml change)           │
│     • Writes JSON to $GITHUB_OUTPUT                 │
│       scenarios=["pg_tde-install","pg_tde-encrypt"] │
└──────────────────────┬──────────────────────────────┘
                       │ matrix of scenario IDs
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

- `id`: unique across both scenario directories. If the same `id` exists in both the doc repo and percona-doc-ci, the doc-repo version wins (override semantics). IDs must match the filename stem (e.g. `pg_tde-install-new-user.md` → `id: pg_tde-install-new-user`).
- `description`: one sentence, read by the planner AI for its second-pass relevance check.
- `covers`: glob patterns matched against repo-root-relative paths from `git diff --name-only`. E.g. `docs/install/*.md` matches `docs/install/guide.md`.

---

## Scenario Index (`_index.md`)

Each scenarios directory contains an `_index.md` listing scenario IDs and descriptions. The planner's AI second pass reads the merged index to decide on non-obvious impacts — keeping the prompt small. The fast path reads the `covers:` fields from individual scenario files directly (not the index).

```markdown
# Scenario Index

- **pg_tde-install-new-user**: New user installs pg_tde and encrypts a table from scratch
- **pg_tde-keyring-setup**: User configures a file-based keyring for pg_tde
```

When merging the two indexes (percona-doc-ci + doc repo), duplicate IDs are deduplicated: the doc-repo entry takes precedence and replaces the percona-doc-ci entry in the merged index.

---

## Planner Agent

**Input:**
- PR diff (`git diff --name-only origin/main...HEAD`)
- All scenario files from both sources (for `covers:` fast-path matching)
- Merged `_index.md` (for AI second pass)

**Output contract:** The planner writes exactly one line to `$GITHUB_OUTPUT`:

```
scenarios=["pg_tde-install-new-user","pg_tde-keyring-setup"]
```

The planner prompt instructs Claude to write this line verbatim as its final output and nothing else after it. The CI step captures stdout, extracts the `scenarios=` line with `grep`, and writes it to `$GITHUB_OUTPUT`. If no line matching `^scenarios=\[` is found in stdout, CI treats it as `scenarios=[]` (empty — no runs).

**Two-phase selection:**

1. **Fast path** — glob-match changed files against each scenario's `covers:` frontmatter. Deterministic, no AI cost. Produces an initial candidate set.
2. **AI second pass** — planner reads the diff and merged `_index.md` descriptions. Adds scenarios whose descriptions suggest relevance even if no `covers:` glob matched (e.g. a `variables.yml` change, a renamed shared include). May also remove fast-path candidates if the diff is clearly unrelated (typo fix in a section not covered by any scenario). Final list is the union of both phases, deduplicated.

**Maximum scenarios:** 10 per run. If the combined list exceeds 10, the planner selects the 10 most relevant and notes the cap in its output. This prevents runaway cost on large refactor PRs.

If the final list is empty, the planner emits `scenarios=[]` and the workflow exits cleanly (no executor jobs, no PR comment).

---

## Executor Agent Behaviour

### Filesystem layout available to executor

```
$GITHUB_WORKSPACE/                   # doc repo root (PR branch)
$GITHUB_WORKSPACE/_doc-ci-tools/     # percona-doc-ci tooling
$GITHUB_WORKSPACE/_scenario.md       # resolved scenario file (copied by CI before run)
```

CI resolves the scenario file before invoking Claude: it checks `$GITHUB_WORKSPACE/scenarios/{id}.md` first, then `$GITHUB_WORKSPACE/_doc-ci-tools/scenarios/{id}.md`. The resolved file is copied to `_scenario.md` so the executor always reads the same path regardless of source.

### Behaviour

Each executor Claude Code session:

- Is invoked as: `claude --print "$(cat _scenario.md)" --allowedTools Bash,Read,Glob,Grep`
- Working directory: `$GITHUB_WORKSPACE` (doc repo root)
- Reads docs freely across multiple pages — follows cross-references, reads config files referenced in docs
- Does not consult external resources — only files under `$GITHUB_WORKSPACE`
- Does not write to `$GITHUB_WORKSPACE` or `$GITHUB_WORKSPACE/_doc-ci-tools` — only `/tmp` for scratch files
- Executes every command that applies to the scenario in the CI environment
- On failure: records what failed, the error output, and which doc page/line it came from — then stops (does not paper over failures or retry with variations)
- Emits its structured result block as the last content in stdout

### When to use SKIP

The executor emits `SKIP` when, after reading the relevant doc pages, it determines the scenario does not apply to the current state of the docs — for example, the feature the scenario covers is documented as not yet available, or the prerequisite OS is not the CI environment. `SKIP` is not used for execution failures. A command that fails is always `FAIL`.

---

## Structured Output Format

The executor writes a `scenario-result` fenced block as the **last thing in its output**. CI captures the full stdout to `scenario-{id}.md` and parses this block.

### PASS

````markdown
```scenario-result
id: pg_tde-install-new-user
status: PASS
```

### What was tested
Followed the getting-started guide as a new user: installed pg_tde, initialized
the keyring, created an encrypted table. All steps completed successfully.
````

### FAIL

````markdown
```scenario-result
id: pg_tde-install-new-user
status: FAIL
```

### What was tested
Followed the getting-started guide as a new user: installed pg_tde, initialized
the keyring, then attempted to create an encrypted table.

### What failed
**Step:** `CREATE TABLE albums (...) USING tde_heap;`
**Page:** `docs/pg_tde/getting-started.md` (line ~47)
**Error:** `ERROR: extension "pg_tde" does not exist`
**Cause:** The install guide does not include `CREATE EXTENSION pg_tde;` before
table creation. The step is documented on a separate page not linked from
getting-started.
````

### SKIP

````markdown
```scenario-result
id: pg_tde-install-new-user
status: SKIP
```

### Why skipped
The scenario covers pg_tde installation on Rocky Linux 9. The CI environment
is Ubuntu 24.04 and the docs only provide Rocky Linux install instructions for
this feature. Skipping rather than failing on an environment mismatch.
````

**Parse rule:** CI reads the `status:` line from inside the fenced block. The block always starts with ` ```scenario-result ` and contains `id:` and `status:` as the first two lines. Everything after the closing fence is the human-readable report posted to the PR comment.

---

## CI Workflow Structure

Three jobs in the reusable workflow:

### Job 1: `planner`

```yaml
timeout-minutes: 5
outputs:
  scenarios: ${{ steps.plan.outputs.scenarios }}

steps:
  - Checkout doc repo (PR branch)
  - Checkout percona-doc-ci tooling at _doc-ci-tools
  - Install Claude Code (npm install -g @anthropic-ai/claude-code)
  - id: plan
    run: |
      # Capture planner output, extract the scenarios= line
      OUTPUT=$(claude --print "$(cat _doc-ci-tools/planner/planner-prompt.md)" \
        --allowedTools Read,Glob,Grep,Bash \
        ANTHROPIC_API_KEY=${{ secrets.ANTHROPIC_API_KEY }})
      LINE=$(echo "$OUTPUT" | grep '^scenarios=\[' | tail -1)
      if [ -z "$LINE" ]; then LINE='scenarios=[]'; fi
      echo "$LINE" >> "$GITHUB_OUTPUT"
```

### Job 2: `execute` (matrix, parallel)

```yaml
needs: planner
if: needs.planner.outputs.scenarios != '[]'
timeout-minutes: 20

strategy:
  matrix:
    scenario: ${{ fromJson(needs.planner.outputs.scenarios) }}
  fail-fast: false   # all scenarios run even if one fails

steps:
  - Checkout doc repo + percona-doc-ci tooling at _doc-ci-tools
  - Install Claude Code
  - Resolve scenario file:
      # Doc repo takes precedence over percona-doc-ci
      SCENARIO_ID="${{ matrix.scenario }}"
      if [ -f "scenarios/${SCENARIO_ID}.md" ]; then
        cp "scenarios/${SCENARIO_ID}.md" _scenario.md
      else
        cp "_doc-ci-tools/scenarios/${SCENARIO_ID}.md" _scenario.md
      fi
  - Run executor:
      claude --print "$(cat _scenario.md)" \
        --allowedTools Bash,Read,Glob,Grep \
        > "scenario-${SCENARIO_ID}.md" 2>&1
  - Upload artifact: scenario-{id}.md
```

### Job 3: `report`

```yaml
needs: [planner, execute]   # planner listed explicitly so needs.planner.outputs is in scope
if: always() && needs.planner.result == 'success' && needs.planner.outputs.scenarios != '[]'

steps:
  - Download all scenario-*.md artifacts
  - Parse scenario-result blocks, aggregate status
  - Post single combined PR comment (warn-only, never blocks merge)
```

### Caller workflow (doc repos)

No changes needed for existing callers. The `doc-path` input from v1 is accepted but ignored in v2 (scenarios determine scope). New optional input:

```yaml
with:
  repo-name:      "pg_tde-docs"
  scenarios-path: "scenarios/"   # optional, default: "scenarios/"
```

`scenarios-path` is passed to both the planner and executor as the `DOC_SCENARIOS_PATH` environment variable. The workflow injects it into the planner prompt via `sed` substitution on `{{DOC_SCENARIOS_PATH}}` placeholder. The executor uses it in the scenario file resolution step (checking `$DOC_SCENARIOS_PATH$SCENARIO_ID.md` before falling back to `_doc-ci-tools/scenarios/`). Both uses default to `scenarios/` (with trailing slash).

---

## Error Handling

| Situation | Behaviour |
|-----------|-----------|
| Planner returns `[]` or no `scenarios=` line | Workflow exits cleanly, no comment posted |
| Planner times out (5 min) | Workflow fails with error; no executor jobs run; no PR comment |
| Planner returns malformed JSON | CI treats as `[]`, workflow exits cleanly |
| Scenario file not found in either location | Scenario marked FAIL: "Scenario file not found for ID: {id}" |
| Executor times out (20 min) | Scenario marked FAIL with timeout note in comment |
| Claude Code exits non-zero, no result block | Scenario marked FAIL with last 50 lines of output in comment |
| No scenarios exist in either location | Warning comment: "No scenarios configured for this repo" |
| All scenarios return SKIP | Single comment: "No scenarios affected by this change" |
| >10 scenarios selected | Planner caps at 10 most relevant, notes cap in comment |

Tests are **warn-only** — they never block PR merge.

---

## What v1 Tooling Is Removed

The following v1 components are not carried forward into v2:

- `tools/universal-extract.lua` — Pandoc block extraction (Claude reads docs directly)
- `tools/ai_test_planner.py` — Script generation (Claude executes commands directly)
- `tools/analyze_failure.py` — Separate failure analysis (Claude reports inline)
- `tools/expand_vars.py` — Claude reads `variables.yml` directly if needed

The `tools/requirements.txt` and Pandoc install step are removed from the workflow.

---

## Migration

v1 and v2 are separate branches. Doc repos calling `@main` continue to use v1. Doc repos wanting v2 pin to `@v2`. The `doc-path` input from v1 is accepted but ignored by v2, so existing caller workflows require no changes to be compatible.
