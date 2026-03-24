#!/usr/bin/env python3
"""
AI test planner — the core of zero-toil doc testing.

Takes a Markdown doc page and its extracted code blocks, calls an LLM to
produce:
  1. runnable.sh  — an executable test script suitable for CI
  2. job.yaml     — a Kubernetes Job spec for EKS/Karpenter execution

The AI handles everything that would otherwise require per-page human tweaking:
  - Detecting which OS the page targets (ubuntu:24.04 vs rockylinux:9 etc.)
  - Distinguishing runnable commands from output-only examples
  - Routing SQL blocks through psql heredocs instead of raw bash
  - Adding lightweight post-step assertions
  - Making interactive commands (e.g. `sudo -i -u postgres psql`) non-interactive

Usage:
  python ai_test_planner.py \\
      <expanded_doc.md> \\
      <extracted_blocks.txt> \\
      <repo_name> \\
      <relative_doc_path>

Outputs runnable.sh and job.yaml in the current directory.

Provider selection (via AI_PROVIDER env var):
  "anthropic"  (default) — requires ANTHROPIC_API_KEY
                           model: ANTHROPIC_MODEL (default: claude-sonnet-4-6)
  "github"               — uses built-in GITHUB_TOKEN, no extra secret needed
                           endpoint: https://models.inference.ai.azure.com
                           model: GITHUB_MODEL (default: gpt-4o)
  "openai"               — requires OPENAI_API_KEY
                           model: OPENAI_MODEL (default: gpt-4o)
"""
import os
import re
import sys

SYSTEM_PROMPT = """You are a CI test-script generator for Percona database documentation.
Your output is executed verbatim in automated CI. Be precise, non-interactive, and safe.
Never add explanatory prose outside the two fenced code blocks requested."""

USER_PROMPT_TEMPLATE = """Repository: {repo_name}
Doc page:   {doc_path}

## Page content
<page>
{doc_content}
</page>

## Extracted code blocks (SHELL and SQL markers added by extraction tool)
<blocks>
{blocks_content}
</blocks>

## Your task

Produce exactly two fenced code blocks with these exact language tags:

```runnable.sh
<bash script>
```

```job.yaml
<kubernetes job yaml>
```

### Rules for runnable.sh

CRITICAL — verbatim commands: Copy every shell command EXACTLY as written in
the extracted blocks, character for character, including any typos or errors.
Do NOT fix, correct, or improve commands. If the doc has a bug, the test must
fail because of that bug. That is the entire purpose of this pipeline.

EXCEPTION — placeholders: If a command contains a value that is clearly meant
to be replaced by the user (patterns like <hostname>, YOUR_PASSWORD, CHANGE_ME,
<your-value>, etc.), substitute a safe, realistic test value instead of copying
the placeholder literally. Prefer: host=localhost, port=3306, user=testuser,
password=testpass123, database=testdb. Document the substitution in a comment.
Note: Jinja2 variables like {{pgversion}} are already expanded before you see
the blocks — if you still see double-braces, treat them as placeholders too.

1. Start with `#!/bin/bash` and `set -euo pipefail`.
2. Export `DEBIAN_FRONTEND=noninteractive` when apt/apt-get is used.
3. Run SHELL blocks in document order.
4. Convert any interactive commands to non-interactive equivalents:
   - `sudo -i -u postgres psql`  →  `sudo -u postgres psql <<'PSQL' ... PSQL`
   - `sudo -i -u postgres` then later `psql`  →  merge into single psql heredoc
5. Where SQL blocks appear in the doc, write this exact line standalone (NOT
   inside any heredoc or subshell — just a bare line in the script):
     #SQL_PLACEHOLDER
   Do NOT open a psql heredoc yourself. Do NOT write any SQL. The actual SQL
   and its psql heredoc wrapper are injected by a post-processing step.
   Any assertions you want to add must go in a SEPARATE psql heredoc on a
   line AFTER #SQL_PLACEHOLDER, never before or inside it.
6. Skip blocks that are clearly showing expected output (not commands):
   - Blocks with no shell verbs (no `sudo`, `apt`, `wget`, `psql`, etc.)
   - Blocks that look like query result tables or log output
7. Add one simple assertion after each significant step:
   - After package install:  `command -v psql >/dev/null`
   - After DB/table create:  verify with a COUNT or `\\dt` inside a separate heredoc
   - After INSERT:           SELECT count(*) and check the result
8. Add the non-interactive flag to package manager install/upgrade commands:
   - apt / apt-get → add `-y`
   - yum / dnf     → add `-y`
   Do NOT add package index refresh commands (apt update, yum makecache, etc.)
   unless the doc page explicitly lists them.
9. The script must be fully self-contained.
10. After installing a database server package, explicitly start the service and
    wait up to 30 seconds for it to be ready before running client commands:
      sudo systemctl start postgresql
      for i in $(seq 1 30); do
        sudo -u postgres psql -c "SELECT 1" >/dev/null 2>&1 && break
        echo "Waiting for PostgreSQL... ($i/30)"; sleep 1
      done
      sudo -u postgres psql -c "SELECT 1" >/dev/null 2>&1 || exit 1

### Rules for job.yaml

1. Infer the correct container image from the guide's target OS:
   - Debian/Ubuntu apt guide   → `ubuntu:24.04`
   - RHEL/Rocky/CentOS yum guide → `rockylinux:9`
   - Default if unclear        → `ubuntu:24.04`
2. Use `restartPolicy: Never`.
3. Set `activeDeadlineSeconds: 900` (15 minutes).
4. Add node selector `karpenter.sh/capacity-type: spot`.
5. Run as root (`securityContext.runAsUser: 0`).
6. Mount the test script from a ConfigMap named `doc-test-script`
   at `/test/runnable.sh`, command `["/bin/bash", "/test/runnable.sh"]`.
7. Leave `namespace` as `doc-test` — the caller overrides it per PR.
8. Name the job `doc-test-{{slug}}` where slug is the doc filename without extension,
   lowercased, hyphens only (e.g. `doc-test-quick-start`).
9. Add label `percona.com/doc-test: "true"` for easy cleanup.

Respond with ONLY the two fenced code blocks. Nothing else.
"""


def _build_messages(doc_content: str, blocks_content: str, repo_name: str, doc_path: str):
    content = (USER_PROMPT_TEMPLATE
               .replace("{repo_name}",     repo_name)
               .replace("{doc_path}",      doc_path)
               .replace("{doc_content}",   doc_content)
               .replace("{blocks_content}", blocks_content))
    return [{"role": "user", "content": content}]


def call_llm(doc_content: str, blocks_content: str, repo_name: str, doc_path: str) -> str:
    provider = os.environ.get("AI_PROVIDER", "anthropic").lower()
    messages  = _build_messages(doc_content, blocks_content, repo_name, doc_path)

    # ── Anthropic / Claude ───────────────────────────────────────────────────
    if provider == "anthropic":
        try:
            import anthropic
        except ImportError:
            sys.exit("anthropic SDK not installed: pip install anthropic")

        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            sys.exit("ANTHROPIC_API_KEY is not set")
        model = os.environ.get("ANTHROPIC_MODEL") or "claude-sonnet-4-6"
        print(f"Provider: Anthropic ({model})", file=sys.stderr)

        client  = anthropic.Anthropic(api_key=api_key)
        message = client.messages.create(
            model=model,
            max_tokens=4096,
            system=SYSTEM_PROMPT,
            messages=messages,
        )
        return message.content[0].text

    # ── GitHub Models (built-in GITHUB_TOKEN, no extra secret) ──────────────
    elif provider == "github":
        try:
            from openai import OpenAI
        except ImportError:
            sys.exit("openai SDK not installed: pip install openai")

        token = os.environ.get("GITHUB_TOKEN")
        if not token:
            sys.exit("GITHUB_TOKEN is not set (should be automatic in GitHub Actions)")
        model = os.environ.get("GITHUB_MODEL") or "gpt-4o"
        print(f"Provider: GitHub Models ({model})", file=sys.stderr)

        client   = OpenAI(base_url="https://models.inference.ai.azure.com", api_key=token)
        response = client.chat.completions.create(
            model=model,
            max_tokens=4096,
            messages=[{"role": "system", "content": SYSTEM_PROMPT}] + messages,
        )
        return response.choices[0].message.content

    # ── OpenAI ───────────────────────────────────────────────────────────────
    elif provider == "openai":
        try:
            from openai import OpenAI
        except ImportError:
            sys.exit("openai SDK not installed: pip install openai")

        api_key = os.environ.get("OPENAI_API_KEY")
        if not api_key:
            sys.exit("OPENAI_API_KEY is not set")
        model = os.environ.get("OPENAI_MODEL") or "gpt-4o"
        print(f"Provider: OpenAI ({model})", file=sys.stderr)

        client   = OpenAI(api_key=api_key)
        response = client.chat.completions.create(
            model=model,
            max_tokens=4096,
            messages=[{"role": "system", "content": SYSTEM_PROMPT}] + messages,
        )
        return response.choices[0].message.content

    else:
        sys.exit(f"Unknown AI_PROVIDER '{provider}'. Choose: anthropic, github, openai")


def parse_output(response: str) -> tuple[str, str]:
    sh_match   = re.search(r"```runnable\.sh\n(.*?)```", response, re.DOTALL)
    yaml_match = re.search(r"```job\.yaml\n(.*?)```",    response, re.DOTALL)

    if not sh_match or not yaml_match:
        print("ERROR: AI response did not match expected format", file=sys.stderr)
        print("--- raw response ---", file=sys.stderr)
        print(response, file=sys.stderr)
        sys.exit(1)

    return sh_match.group(1).strip(), yaml_match.group(1).strip()


def extract_sql_lines(blocks_content: str) -> list[str]:
    """Return SQL lines from extracted blocks, verbatim, skipping \q."""
    lines, in_sql = [], False
    for line in blocks_content.splitlines():
        if line == "#-----SQL-----":
            in_sql = True
            continue
        if line == "#-----SHELL-----":
            in_sql = False
            continue
        if in_sql and line.strip() and not line.strip() == r"\q":
            lines.append(line)
    return lines


def inject_sql(script: str, sql_lines: list[str]) -> str:
    """Replace #SQL_PLACEHOLDER with a verbatim psql heredoc."""
    if not sql_lines or "#SQL_PLACEHOLDER" not in script:
        return script
    heredoc = "sudo -u postgres psql <<'PSQL'\n"
    heredoc += "\n".join(sql_lines) + "\n"
    heredoc += "PSQL"
    return script.replace("#SQL_PLACEHOLDER", heredoc)


def main():
    if len(sys.argv) != 5:
        sys.exit(
            f"Usage: {sys.argv[0]} "
            "<expanded_doc.md> <extracted_blocks.txt> <repo_name> <rel_doc_path>"
        )

    doc_path_arg    = sys.argv[1]
    blocks_path_arg = sys.argv[2]
    repo_name       = sys.argv[3]
    rel_doc_path    = sys.argv[4]

    with open(doc_path_arg)    as f: doc_content    = f.read()
    with open(blocks_path_arg) as f: blocks_content = f.read()

    response = call_llm(doc_content, blocks_content, repo_name, rel_doc_path)

    script, job_yaml = parse_output(response)

    # Inject SQL verbatim — never let the AI write SQL content
    sql_lines = extract_sql_lines(blocks_content)
    script = inject_sql(script, sql_lines)

    with open("runnable.sh", "w") as f:
        f.write(script + "\n")
    os.chmod("runnable.sh", 0o755)

    with open("job.yaml", "w") as f:
        f.write(job_yaml + "\n")

    print("Generated: runnable.sh", file=sys.stderr)
    print("Generated: job.yaml",    file=sys.stderr)


if __name__ == "__main__":
    main()
