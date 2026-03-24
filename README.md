# percona-doc-ci v2

Scenario-based documentation testing for Percona doc repos. Claude Code acts as a real user, follows multi-page documentation journeys, executes every command it encounters, and reports exactly what breaks and why — posted as a PR comment before the docs ship.

---

## Why v2?

v1 tested individual doc pages in isolation. The problem: Percona docs are sequential. Installing `pg_tde` spans an install page, a keyring configuration page, and a getting-started page. A missing `CREATE EXTENSION` on one page only fails if you follow the full journey.

v2 defines **scenarios** — named user journeys that Claude follows across as many pages as needed.

---

## How it works

```
PR opened on a doc repo
         │
         ▼
  ┌─────────────────────────────────────────────────┐
  │  PLANNER                                        │
  │  Reads PR diff. Glob-matches changed files      │
  │  against scenario covers: patterns (fast path), │
  │  then AI second pass for non-obvious impacts.   │
  │  Outputs: list of scenario IDs to run.          │
  └──────────────────────┬──────────────────────────┘
                         │ ["pg_tde-install-new-user"]
                         ▼
  ┌─────────────────────────────────────────────────┐
  │  EXECUTORS  (one per scenario, parallel)        │
  │  Claude reads the scenario file, browses the    │
  │  checked-out PR branch freely across multiple   │
  │  doc pages, executes every command it finds,    │
  │  and writes a structured result.                │
  └──────────────────────┬──────────────────────────┘
                         │ scenario-result blocks
                         ▼
  ┌─────────────────────────────────────────────────┐
  │  REPORTER                                       │
  │  Parses results, posts a single PR comment.     │
  └─────────────────────────────────────────────────┘
```

**Tests are warn-only** — they never block a PR from merging.

---

## Quick start: adding to postgresql-docs

### 1. Add the caller workflow

Create `.github/workflows/doc-tests.yml` in your doc repo:

```yaml
name: Doc scenario tests

on:
  pull_request:
  workflow_dispatch:

jobs:
  doc-tests:
    uses: ImTheKai/percona-doc-ci/.github/workflows/run-doc-tests.yml@v2
    with:
      repo-name: "postgresql-docs"
    secrets:
      ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}
```

### 2. Set the ANTHROPIC_API_KEY secret

Add `ANTHROPIC_API_KEY` as a secret in your repo (Settings → Secrets → Actions).
Set it at the org level and all repos inherit it automatically.

Cost: roughly $0.02–$0.10 per scenario run depending on doc complexity.

### 3. Open a PR that touches doc pages — the test runs automatically

On every PR, the planner checks which scenarios cover the changed files and runs only those. A one-liner change to a page that nothing covers → zero scenarios run, zero cost.

---

## Adding scenario files to your doc repo

By default, percona-doc-ci's built-in scenarios run for any repo that calls the workflow. To add your own:

### Create `scenarios/` in your doc repo

```
your-doc-repo/
└── scenarios/
    ├── _index.md                    # scenario registry (id + description)
    └── postgresql-quickstart.md     # a scenario file
```

### Write a scenario file

```markdown
---
id: postgresql-quickstart
description: "New user installs PostgreSQL from Percona repos and runs a first query"
covers:
  - docs/install/**/*.md
  - docs/quickstart*.md
  - variables.yml
---

You are a developer setting up PostgreSQL for the first time using Percona's
distribution. You want to install it and run a basic query.

Follow the quickstart documentation in this repo. Start by finding the
installation guide, then execute every step exactly as written.

When done, report your result.
```

### Add the scenario to your `_index.md`

```markdown
# Scenario Index

- **postgresql-quickstart**: New user installs PostgreSQL from Percona repos and runs a first query
```

That's it. On the next PR that touches `docs/install/` or `docs/quickstart*.md`, this scenario runs automatically.

---

## Scenario file format

| Field | Required | Description |
|-------|----------|-------------|
| `id` | Yes | Unique identifier. Must match the filename stem. |
| `description` | Yes | One sentence. Read by the planner AI for second-pass selection. |
| `covers` | Yes | List of glob patterns. Matched against repo-root-relative paths from `git diff --name-only`. |

The body of the file is the scenario prompt: written as a user persona + task description. Claude reads this and acts on it.

### `covers` glob patterns

Patterns are matched using Python's `fnmatch` against `git diff --name-only` output. Examples:

```yaml
covers:
  - docs/pg_tde/**/*.md      # any page under docs/pg_tde/
  - docs/install*.md         # docs/install.md, docs/install-guide.md, etc.
  - variables.yml            # exact file match
```

---

## PR comment format

### All scenarios passed

```
✅ Doc scenario tests for `postgresql-docs` — all 2 scenario(s) passed

| Scenario                    | Status |
|-----------------------------|--------|
| ✅ `postgresql-quickstart`  | PASS   |
| ✅ `postgresql-replication` | PASS   |

[View full run](...)
```

### A scenario failed

```
⚠️ Doc scenario tests for `postgresql-docs` — 1 scenario(s) failed (non-blocking)

| Scenario                    | Status |
|-----------------------------|--------|
| ❌ `postgresql-quickstart`  | FAIL   |

<details>
<summary>postgresql-quickstart — failure details</summary>

### What was tested
Followed the quickstart guide to install PostgreSQL and run a first query.
Reached the point of connecting to the database.

### What failed
**Step:** `psql -U postgres -c "SELECT version();"`
**Page:** `docs/quickstart.md` (line ~43)
**Error:** `psql: error: connection to server on socket "/var/run/postgresql/.s.PGSQL.5432" failed`
**Cause:** The quickstart does not include a step to start the PostgreSQL service
after installation. The `systemctl start postgresql` step is missing.

</details>

[View full run](...)
```

---

## Caller workflow inputs

| Input | Default | Description |
|-------|---------|-------------|
| `repo-name` | *(required)* | Short repo name, used in comment headings |
| `scenarios-path` | `scenarios/` | Path to doc-repo scenario files (with trailing slash) |
| `tooling-ref` | `v2` | Git ref of percona-doc-ci to use |
| `doc-path` | *(ignored)* | Accepted for v1 compatibility, not used in v2 |

| Secret | Required | Description |
|--------|----------|-------------|
| `ANTHROPIC_API_KEY` | Yes | Claude API key (claude-sonnet-4-6 by default) |

---

## Built-in scenarios

These scenarios are defined in `scenarios/` in this repo and run for any doc repo that calls the workflow:

| ID | Description | Covers |
|----|-------------|--------|
| `pg_tde-install-new-user` | New user installs pg_tde on Ubuntu, enables TDE on a table, verifies encryption | `docs/install/**`, `docs/pg_tde/**`, `docs/getting-started*` |

---

## Repository layout

```
percona-doc-ci/
├── .claude/
│   └── commands/
│       ├── doc-navigate.md       # skill: how to read and navigate the doc checkout
│       ├── scenario-execute.md   # skill: how to execute commands (rules, edge cases)
│       └── scenario-report.md    # skill: exact output format (PASS / FAIL / SKIP)
├── scenarios/
│   ├── _index.md                 # registry of built-in scenarios
│   └── pg_tde-install-new-user.md
├── planner/
│   └── planner-prompt.md         # planner agent instructions (uses {{PLACEHOLDERS}})
└── .github/
    └── workflows/
        └── run-doc-tests.yml     # reusable workflow (called by each doc repo)
```

---

## How scenario selection works

**Phase 1 — fast path (no AI cost):**
Each scenario file declares `covers:` glob patterns. The planner glob-matches the PR's changed files against these patterns using Python's `fnmatch`. If a changed file matches a pattern, the scenario is selected.

**Phase 2 — AI second pass:**
The planner reads the PR diff and the scenario index descriptions. It adds scenarios not caught by Phase 1 if the diff has non-obvious impacts — for example, a change to `variables.yml` might affect version numbers used across many scenarios, even though `variables.yml` isn't listed in any `covers:` field.

Maximum 10 scenarios per run. If more are selected, the planner keeps the 10 most relevant.

---

## Running locally

To test a scenario locally before pushing:

```bash
# 1. Check out the doc repo you want to test
git clone https://github.com/percona/postgresql-docs
cd postgresql-docs

# 2. Check out percona-doc-ci tooling alongside it
git clone https://github.com/ImTheKai/percona-doc-ci _doc-ci-tools --branch v2

# 3. Build the executor prompt
cat _doc-ci-tools/.claude/commands/doc-navigate.md \
    _doc-ci-tools/.claude/commands/scenario-execute.md \
    _doc-ci-tools/.claude/commands/scenario-report.md \
    _doc-ci-tools/scenarios/pg_tde-install-new-user.md > /tmp/prompt.md

# 4. Run Claude Code
export ANTHROPIC_API_KEY=your-key
claude -p "$(cat /tmp/prompt.md)" --dangerously-skip-permissions
```

---

## Writing good scenarios

**A good scenario:**
- Has a clear user persona ("you are a developer who...")
- States a concrete goal ("your goal is to encrypt a table and verify it works")
- Covers a complete journey, not just one page
- Has `covers:` patterns that genuinely reflect which pages affect the journey

**A bad scenario:**
- Too narrow ("test that the CREATE EXTENSION command works") — this is a unit test, not a journey
- Too broad ("test all PostgreSQL features") — this would run on every PR
- Missing `covers:` patterns — the planner can't fast-path select it

**On failure quality:**
Claude reports what the docs said to do and exactly why it failed. The failure analysis refers to doc content, not CI infrastructure. A missing step in the docs produces a clear explanation: "The docs do not include X before Y, which causes Z."
