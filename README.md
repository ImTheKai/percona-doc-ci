# percona-doc-ci

AI-powered documentation testing for Percona doc repos. Shell and SQL commands
are extracted from Markdown pages, an LLM turns them into a runnable CI script,
and the script is executed — with zero per-page human configuration.

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
│     ai_test_planner.py sends the page + blocks to   │
│     an LLM (Claude, GitHub Models, or OpenAI).      │
│     The LLM produces:                               │
│       runnable.sh  — executable CI test script      │
│       job.yaml     — Kubernetes Job spec            │
│     The LLM handles what would otherwise need       │
│     human judgment:                                 │
│       • Which OS image to use (inferred from page)  │
│       • Rewriting interactive commands to heredocs  │
│       • Routing SQL blocks through psql             │
│       • Adding post-step assertions                 │
│       • Skipping output-only blocks                 │
└──────────────────────┬──────────────────────────────┘
                       │ runnable.sh + job.yaml
                       ▼
┌─────────────────────────────────────────────────────┐
│  4. EXECUTE                                         │
│     GitHub runner  — runs runnable.sh directly on  │
│                       ubuntu-latest (default).      │
│     K8s Job        — applies job.yaml to EKS;       │
│                       Karpenter schedules it on a   │
│                       spot instance and scales to   │
│                       zero when idle.               │
└──────────────────────┬──────────────────────────────┘
                       │ pass / fail
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
      repo-name:      "postgresql-docs"     # your repo name
      doc-path:       "docs/quick-start.md" # page to test
      variables-file: "variables.yml"       # path to Jinja2 vars (omit if none)
      runner-type:    "github"              # or "k8s"
      ai-provider:    "github"              # see Provider section below
    secrets:
      ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}  # if ai-provider=anthropic
```

### 2. Choose a provider (see below) and set the secret if needed

### 3. Open a PR that touches one of the listed pages — the test runs automatically

To add more pages later, add them to `paths:` and copy the job block. No other
changes required.

---

## LLM provider configuration

The AI planner supports three providers. Switch between them with the
`ai-provider` workflow input.

### `github` — GitHub Models (recommended starting point)

Uses the built-in `GITHUB_TOKEN`. **No extra secrets needed.**

```yaml
ai-provider: "github"
# ai-model: "gpt-4o"   # default; other options: gpt-4o-mini, Meta-Llama-3.1-70B-Instruct
```

Limitations: subject to GitHub Models rate limits (~15 req/min on free tier).
Fine for PR-triggered tests; may need a paid GitHub plan for high-frequency use.

### `anthropic` — Claude (best results)

Requires `ANTHROPIC_API_KEY` set as an **org-level** or **repo-level** secret.

```yaml
ai-provider: "anthropic"
# ai-model: "claude-sonnet-4-6"   # default
```

Set the secret once at the org level and all repos inherit it automatically.
Cost is roughly $0.01 per test run.

### `openai` — OpenAI

Requires `OPENAI_API_KEY` set as an org-level or repo-level secret.

```yaml
ai-provider: "openai"
# ai-model: "gpt-4o"   # default
```

### Comparison

| Provider    | Extra secret        | Cost per run | Notes                         |
|-------------|---------------------|--------------|-------------------------------|
| `github`    | None (GITHUB_TOKEN) | Free         | Rate-limited; good for pilot  |
| `anthropic` | ANTHROPIC_API_KEY   | ~$0.01       | Best on complex pages         |
| `openai`    | OPENAI_API_KEY      | ~$0.01       | Equivalent to github provider |

---

## Execution: GitHub runner vs Kubernetes

### `runner-type: "github"` (default)

Runs `runnable.sh` directly on a GitHub-hosted `ubuntu-latest` runner. Simple,
no infrastructure required, free tier minutes apply.

Best for: pilots, simple installation guides, any page targeting Ubuntu/Debian.

### `runner-type: "k8s"`

Submits a Kubernetes Job to an EKS cluster managed by Karpenter. The AI-
generated `job.yaml` already includes:

- Spot instance node selector (`karpenter.sh/capacity-type: spot`)
- 15-minute hard deadline
- Correct OS container image (inferred from the doc page)
- A fresh namespace per PR (`doc-test-pr-{repo}-{pr_number}`) for isolation

The namespace is deleted after the job finishes (pass or fail).

Requires: `KUBECONFIG` secret (base64-encoded kubeconfig for the EKS cluster).

```yaml
runner-type: "k8s"
secrets:
  KUBECONFIG: ${{ secrets.KUBECONFIG }}
```

---

## percona-dk sidecar (optional, K8s only)

[percona-dk](https://github.com/Percona-Lab/percona-dk) provides semantic
search over Percona doc repos via MCP. It is **not required** for the core
pipeline — the AI planner works fine from the page content alone.

When enabled (`use-percona-dk-sidecar: true`), percona-dk runs as a sidecar
container in the K8s Job and exposes itself at `http://localhost:8000`. The
test script can query it for cross-repo context if needed. This is a future
enhancement; leave it off for the pilot.

```yaml
runner-type:           "k8s"
use-percona-dk-sidecar: true
percona-dk-image:      "ghcr.io/percona-lab/percona-dk:latest"  # default
```

---

## Code block tag support

The Lua filter (`universal-extract.lua`) handles every tag variant used across
Percona doc repos without any per-repo configuration:

| Repo              | Tag syntax                      | Handled? |
|-------------------|---------------------------------|----------|
| postgresql-docs   | `` {.bash data-prompt="$"} ``   | ✅        |
| psmysql-docs      | `` ```shell ``                  | ✅        |
| pxb-docs          | `` ```shell ``                  | ✅        |
| pmm-doc           | `` ```sh ``                     | ✅        |
| any repo          | `` ```bash ``                   | ✅        |
| any repo          | `` ```sql ``                    | ✅ (routed through psql) |
| output examples   | anything else                   | skipped  |

---

## Variable expansion

If a doc page contains Jinja2 expressions like `{{pgversion}}` or
`{{dockertag}}`, add the `variables-file` input pointing at the repo's
`variables.yml`. The expander runs before extraction so resolved values
appear in the extracted blocks.

```yaml
variables-file: "variables.yml"   # relative to repo root
```

If no variables file exists or the path is wrong, the doc is used as-is
(non-fatal).

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
# and: sudo apt install pandoc  (or download from github.com/jgm/pandoc/releases)

# 1. Expand variables
python tools/expand_vars.py docs/quick-start.md variables.yml > /tmp/expanded.md

# 2. Extract code blocks
pandoc -i /tmp/expanded.md \
  --lua-filter tools/universal-extract.lua \
  -t html -o /dev/null > /tmp/blocks.txt

cat /tmp/blocks.txt   # inspect what was found

# 3. Run the AI planner
cd /tmp
export AI_PROVIDER=github          # or anthropic / openai
export GITHUB_TOKEN=ghp_...        # your PAT or use: gh auth token
# export ANTHROPIC_API_KEY=sk-ant-...
# export OPENAI_API_KEY=sk-...

python /path/to/tools/ai_test_planner.py \
  expanded.md blocks.txt "postgresql-docs" "docs/quick-start.md"

# → writes runnable.sh and job.yaml

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
