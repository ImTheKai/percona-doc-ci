# percona-doc-ci

AI-powered documentation testing for Percona doc repos. Shell and SQL commands
are extracted from Markdown pages, an LLM turns them into a runnable CI script,
and the script is executed — with zero per-page human configuration.

When the test fails, the PR comment explains exactly what is wrong in the
documentation, not in the CI infrastructure.

---

## Why this exists

The [PMM-doc](https://github.com/percona/pmm-doc) team proved that Pandoc +
a Lua filter can extract shell blocks from docs and run them in CI. The problem
with scaling that approach across all Percona doc repos is the toil:

- Someone has to write a new workflow file for every page that needs testing.
- Every repo uses different code-block tags (`{.bash data-prompt="$"}`,
  `shell`, `sh`, `bash` …).
- Jinja2 variables like `{{pgversion}}` need expanding from `variables.yml`
  before anything can run.
- Interactive commands (`sudo -i -u postgres psql`) need manual rewriting for
  non-interactive CI.

This repo removes all of that toil. Add a page path to a `paths:` filter once
— the AI handles the rest, even when the page changes.

---

## How it works

```
PR opened on a doc repo (e.g. postgresql-docs)
         │
         ▼
┌─────────────────────────────────────────────────────┐
│  1. EXPAND VARIABLES                                │
│     expand_vars.py reads variables.yml and          │
│     resolves Jinja2 expressions like {{pgversion}}  │
│     before extraction runs.                         │
└──────────────────────┬──────────────────────────────┘
                       │ expanded .md
                       ▼
┌─────────────────────────────────────────────────────┐
│  2. EXTRACT CODE BLOCKS                             │
│     Pandoc + universal-extract.lua reads the page   │
│     and emits every runnable block with a marker:   │
│       #-----SHELL-----  for bash/sh/shell blocks    │
│       #-----SQL-----    for sql blocks              │
│     All other blocks (output examples, yaml, ini)   │
│     are silently skipped.                           │
└──────────────────────┬──────────────────────────────┘
                       │ extracted_blocks.txt
                       ▼
┌─────────────────────────────────────────────────────┐
│  3. AI TEST PLANNER                                 │
│     ai_test_planner.py sends the page + SHELL       │
│     blocks to an LLM. The LLM handles:             │
│       • OS image inference                          │
│       • Non-interactive rewrites (psql heredocs)    │
│       • Placeholder substitution                    │
│       • Skipping output-only blocks                 │
│       • Post-step assertions                        │
│       • Service startup / readiness wait            │
│     SQL blocks are NEVER sent to the LLM —          │
│     they are injected verbatim by post-processing   │
│     to guarantee the doc's exact SQL is tested.     │
└──────────────────────┬──────────────────────────────┘
                       │ runnable.sh + job.yaml
                       ▼
┌─────────────────────────────────────────────────────┐
│  4. VERBATIM CHECK                                  │
│     Every SQL line from extracted_blocks.txt must   │
│     appear unchanged in runnable.sh. If the AI      │
│     silently "fixed" a typo, this catches it and    │
│     the plan job aborts before any test runs.       │
└──────────────────────┬──────────────────────────────┘
                       │ verified runnable.sh
                       ▼
┌─────────────────────────────────────────────────────┐
│  5. EXECUTE                                         │
│     GitHub runner  — runs runnable.sh directly on  │
│                       ubuntu-latest (default).      │
│     K8s Job        — applies job.yaml to EKS;       │
│                       Karpenter spot instance,      │
│                       scales to zero when idle.     │
└──────────────────────┬──────────────────────────────┘
                       │ pass / fail + log
                       ▼
┌─────────────────────────────────────────────────────┐
│  6. AI FAILURE ANALYSIS (on failure only)           │
│     analyze_failure.py receives the raw extracted   │
│     blocks and the test log. It explains what is    │
│     wrong in the documentation (not the CI), e.g.: │
│     "CREATE DATBASE should be CREATE DATABASE"      │
└──────────────────────┬──────────────────────────────┘
                       │ markdown explanation
                       ▼
              PR comment posted (warn only —
              tests never block merge)
```

---

## Repository layout

```
percona-doc-ci/
├── tools/
│   ├── universal-extract.lua   # Pandoc Lua filter — handles all tag variants
│   ├── expand_vars.py          # Jinja2 variable expansion from variables.yml
│   ├── ai_test_planner.py      # Calls LLM → produces runnable.sh + job.yaml
│   ├── analyze_failure.py      # Calls LLM on failure → explains doc issue
│   └── requirements.txt        # Python dependencies
└── .github/
    └── workflows/
        └── run-doc-tests.yml   # Reusable workflow — called by each doc repo
```

Each doc repo adds **one file**: `.github/workflows/doc-tests.yml` that calls
the reusable workflow. That is the only per-repo configuration needed.

---

## Onboarding a new doc repo

### 1. Add the caller workflow

Create `.github/workflows/doc-tests.yml` in the doc repo:

```yaml
name: Doc tests

on:
  pull_request:
    paths:
      - 'docs/quick-start.md'   # list every page you want tested
  workflow_dispatch:

jobs:
  quick-start:
    name: "Test: quick-start.md"
    uses: percona-lab/percona-doc-ci/.github/workflows/run-doc-tests.yml@main
    with:
      repo-name:      "postgresql-docs"
      doc-path:       "docs/quick-start.md"
      variables-file: "variables.yml"       # omit if no Jinja2 variables
      runner-type:    "github"              # or "k8s"
      ai-provider:    "github"              # see Provider section below
    secrets:
      ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}
```

### 2. Choose a provider and set any required secret

### 3. Open a PR that touches a listed page — the test runs automatically

---

## LLM provider configuration

| Provider    | Extra secret        | Cost per run | Notes                         |
|-------------|---------------------|--------------|-------------------------------|
| `github`    | None (GITHUB_TOKEN) | Free         | Rate-limited; good for pilot  |
| `anthropic` | ANTHROPIC_API_KEY   | ~$0.01       | Best on complex pages         |
| `openai`    | OPENAI_API_KEY      | ~$0.01       | Equivalent to github provider |

### `github` — GitHub Models (recommended starting point)

Uses the built-in `GITHUB_TOKEN`. **No extra secrets needed.**

```yaml
ai-provider: "github"
# ai-model: "gpt-4o"   # default
```

Rate limit: ~15 requests/min on free tier. Fine for PR-triggered tests.

### `anthropic` — Claude

Requires `ANTHROPIC_API_KEY` as an org-level or repo-level secret.

```yaml
ai-provider: "anthropic"
# ai-model: "claude-sonnet-4-6"   # default
```

Set the secret once at the org level and all repos inherit it automatically.

### `openai` — OpenAI

Requires `OPENAI_API_KEY` as an org-level or repo-level secret.

```yaml
ai-provider: "openai"
# ai-model: "gpt-4o"   # default
```

---

## How SQL correctness is guaranteed

The LLM never writes SQL. Instead:

1. The AI planner emits `#SQL_PLACEHOLDER` where SQL should go.
2. After the LLM responds, `ai_test_planner.py` extracts all SQL lines directly
   from `extracted_blocks.txt` and builds a psql heredoc from them verbatim.
3. A verbatim check then confirms every SQL line from the doc appears unchanged
   in `runnable.sh`. If the AI ignored the placeholder and wrote SQL itself,
   the check fails and the job aborts before any test runs.

This means a typo like `CREATE DATBASE` in the doc will cause psql to fail —
which is the correct and intended behaviour.

---

## Placeholder handling

Not all commands in a doc are meant to be run literally. Two cases are handled:

**Jinja2 variables** (`{{pgversion}}`, `{{dockertag}}`): resolved by
`expand_vars.py` before extraction. These are never seen by the LLM.

**User-facing placeholders** (`<hostname>`, `YOUR_PASSWORD`, `CHANGE_ME`):
the LLM substitutes a safe test value (e.g. `localhost`, `testpass123`) and
adds an inline comment explaining the substitution. The original placeholder
text is not executed.

---

## Execution: GitHub runner vs Kubernetes

### `runner-type: "github"` (default)

Runs `runnable.sh` directly on a GitHub-hosted `ubuntu-latest` runner.

**Note:** GitHub-hosted runners come with a pre-installed PostgreSQL (from the
pgdg repository). The workflow removes it before running the test to prevent
package conflicts with Percona packages.

### `runner-type: "k8s"`

Submits a Kubernetes Job to an EKS cluster managed by Karpenter:
- Spot instance node selector (`karpenter.sh/capacity-type: spot`)
- 15-minute hard deadline (`activeDeadlineSeconds: 900`)
- Correct OS container image inferred from the doc (ubuntu:24.04 / rockylinux:9)
- Fresh namespace per PR (`doc-test-pr-{repo}-{pr_number}`) — deleted after run

Requires: `KUBECONFIG` secret (base64-encoded kubeconfig for the EKS cluster).

---

## PR comments

Every run posts a comment to the PR.

**On success:**
> ✅ **Doc test passed** for `docs/quick-start.md`
>
> All commands in the doc ran successfully.

**On failure:**
> ⚠️ **Doc test failed (non-blocking)** for `docs/quick-start.md`
>
> **What went wrong:**
>
> The SQL command `CREATE DATBASE test;` contains a typo — `DATBASE` should
> be `DATABASE`. PostgreSQL rejected the statement with a syntax error.
>
> [View full run log](…)

The failure explanation is generated by `analyze_failure.py`, which receives
the raw extracted doc commands (not the CI wrapper script) and the test log.
This ensures the explanation refers to doc content, not CI infrastructure.

Tests are **warn-only** — they never block a PR from merging.

---

## percona-dk sidecar (optional, K8s only)

[percona-dk](https://github.com/Percona-Lab/percona-dk) provides semantic
search over Percona doc repos via MCP. It is not required for the core pipeline.

When enabled, it runs as a sidecar container in the K8s Job and is reachable
at `http://localhost:8000` from the test script.

```yaml
runner-type:            "k8s"
use-percona-dk-sidecar: true
percona-dk-image:       "ghcr.io/percona-lab/percona-dk:latest"   # default
```

---

## Code block tag support

| Repo              | Tag syntax                      | Handled? |
|-------------------|---------------------------------|----------|
| postgresql-docs   | `` {.bash data-prompt="$"} ``   | ✅        |
| psmysql-docs      | `` ```shell ``                  | ✅        |
| pxb-docs          | `` ```shell ``                  | ✅        |
| pmm-doc           | `` ```sh ``                     | ✅        |
| any repo          | `` ```bash ``                   | ✅        |
| any repo          | `` ```sql ``                    | ✅ (verbatim via heredoc) |
| output examples   | anything else                   | skipped  |

---

## Variable expansion

If a doc page uses Jinja2 expressions (`{{pgversion}}`, `{{dockertag}}`),
point `variables-file` at the repo's `variables.yml`. Expansion runs before
extraction, so resolved values appear in the blocks the LLM sees.

```yaml
variables-file: "variables.yml"   # relative to repo root
```

If the file doesn't exist, the doc is used as-is (non-fatal).

---

## All workflow inputs

| Input                   | Default        | Description |
|-------------------------|----------------|-------------|
| `repo-name`             | *(required)*   | Short repo name, used in job labels |
| `doc-path`              | *(required)*   | Repo-relative path to the doc page |
| `variables-file`        | `variables.yml`| Path to Jinja2 variables file |
| `runner-type`           | `github`       | `github` or `k8s` |
| `k8s-namespace`         | `doc-test`     | Base namespace for K8s Jobs |
| `ai-provider`           | `anthropic`    | `anthropic`, `github`, or `openai` |
| `ai-model`              | *(per provider)*| Override default model |
| `use-percona-dk-sidecar`| `false`        | Attach percona-dk sidecar (K8s only) |
| `percona-dk-image`      | `ghcr.io/percona-lab/percona-dk:latest` | Sidecar image |
| `tooling-ref`           | `main`         | Git ref of this repo to use |

| Secret              | When required            |
|---------------------|--------------------------|
| `ANTHROPIC_API_KEY` | `ai-provider: anthropic` |
| `OPENAI_API_KEY`    | `ai-provider: openai`    |
| `KUBECONFIG`        | `runner-type: k8s`       |
| `GITHUB_TOKEN`      | Always (automatic)       |

---

## Running the tools locally

```bash
# Install dependencies
pip install -r tools/requirements.txt
# Install pandoc: https://github.com/jgm/pandoc/releases

# 1. Expand variables
python tools/expand_vars.py docs/quick-start.md variables.yml > /tmp/expanded.md

# 2. Extract code blocks
pandoc -i /tmp/expanded.md \
  --lua-filter tools/universal-extract.lua \
  -t html -o /dev/null > /tmp/blocks.txt

cat /tmp/blocks.txt   # inspect what was found

# 3. Run the AI planner
cd /tmp
export AI_PROVIDER=github
export GITHUB_TOKEN=$(gh auth token)

python /path/to/tools/ai_test_planner.py \
  expanded.md blocks.txt "postgresql-docs" "docs/quick-start.md"

cat runnable.sh   # review before running

# 4. Optionally run the generated script
bash runnable.sh
```

---

## Design decisions

**Why Pandoc + Lua rather than a Python markdown parser?**
Pandoc's AST-level parsing handles all MkDocs Material tag variants robustly.
A regex approach would need constant maintenance as tag styles drift. The same
Lua filter already worked in PMM-doc, so this extends proven tooling.

**Why does the AI generate a script rather than running blocks directly?**
Raw extracted blocks are not directly runnable: they contain interactive
commands, split bash+SQL sequences, and output-only examples mixed in.
Generating an intermediate script makes the AI's decisions inspectable —
the script is uploaded as a workflow artefact on every run, so failures
are debuggable without re-running.

**Why does the AI never write SQL?**
Early iterations let the AI rewrite SQL blocks for CI convenience, and it
silently "fixed" typos like `CREATE DATBASE` — producing false positives where
broken documentation appeared to pass. SQL is now injected verbatim from the
extracted blocks, bypassing the LLM entirely for SQL content.

**Why warn-only and never block merge?**
Doc test failures are often environment issues (network, package versions)
rather than doc errors. Blocking merges on flaky infrastructure tests would
create friction without improving doc quality. The goal is signal, not a gate.

**Why not require percona-dk?**
The AI planner already has the full page content from the git checkout.
Fetching the same content from a remote search index would be redundant.
percona-dk adds value for cross-repo dependency detection (future work) and
for human search, not for single-page test planning.

**Why a central reusable workflow rather than per-repo tooling?**
One place to update the Lua filter, the AI prompt, and the execution logic.
Adding a new doc repo costs one file. Fixing a bug in extraction fixes it
everywhere simultaneously.

**What about commands with user-facing placeholders?**
Jinja2 variables (`{{pgversion}}`) are expanded before extraction. Remaining
placeholders like `<hostname>` or `YOUR_PASSWORD` are substituted by the AI
with safe test values (e.g. `localhost`, `testpass123`). The substitution is
documented in a comment in the generated script.

**Why are package manager flags (`-y`) added by the AI but not package refresh commands?**
The `-y` flag is a pure CI concern (suppress prompts) — it does not change
what gets installed or tested. Package index refresh commands (`apt update`,
`yum makecache`) on the other hand change the environment and add latency; if
the doc does not list them, neither should the test.
