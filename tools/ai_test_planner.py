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

1. Start with `#!/bin/bash` and `set -euo pipefail`.
2. Set `export DEBIAN_FRONTEND=noninteractive` near the top if apt is used.
3. Run SHELL blocks in document order.
4. Convert any interactive commands to non-interactive equivalents:
   - `sudo -i -u postgres psql`  →  `sudo -u postgres psql <<'PSQL' ... PSQL`
   - `sudo -i -u postgres` then later `psql`  →  merge into single psql heredoc
5. Put all consecutive SQL blocks into a single psql heredoc immediately following
   the last related SHELL block, connected as postgres user.
6. Skip blocks that are clearly showing expected output (not commands):
   - Blocks with no shell verbs (no `sudo`, `apt`, `wget`, `psql`, etc.)
   - Blocks that look like query result tables or log output
7. Add one simple assertion after each significant step:
   - After package install:  `command -v psql >/dev/null`
   - After DB/table create:  verify with a COUNT or `\\dt` inside the same psql heredoc
   - After INSERT:           SELECT count(*) and grep for expected number
8. Pass `-y` to all apt/yum/dnf commands.
9. Do NOT include `sudo apt update` unless the page explicitly lists it.
10. The script must be fully self-contained and idempotent where possible.

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
    return [{
        "role": "user",
        "content": USER_PROMPT_TEMPLATE.format(
            repo_name=repo_name,
            doc_path=doc_path,
            doc_content=doc_content,
            blocks_content=blocks_content,
        )
    }]


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
        model = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-6")
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
        model = os.environ.get("GITHUB_MODEL", "gpt-4o")
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
        model = os.environ.get("OPENAI_MODEL", "gpt-4o")
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

    with open("runnable.sh", "w") as f:
        f.write(script + "\n")
    os.chmod("runnable.sh", 0o755)

    with open("job.yaml", "w") as f:
        f.write(job_yaml + "\n")

    print("Generated: runnable.sh", file=sys.stderr)
    print("Generated: job.yaml",    file=sys.stderr)


if __name__ == "__main__":
    main()
