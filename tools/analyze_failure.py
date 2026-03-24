#!/usr/bin/env python3
"""
Analyze a failed doc test log and return a human-readable explanation.
Called by the CI workflow on test failure to enrich the PR comment.

Usage:
  python analyze_failure.py <log_file> <doc_path> <runnable_sh>

Prints a markdown-formatted failure summary to stdout.

Environment variables: same provider selection as ai_test_planner.py
  AI_PROVIDER, ANTHROPIC_API_KEY, GITHUB_TOKEN, OPENAI_API_KEY
"""
import os
import sys

PROMPT = """You are analyzing a failed automated doc test for Percona documentation.

Doc page: {doc_path}

The test script that ran:
<script>
{script}
</script>

The tail of the test log (last 100 lines):
<log>
{log}
</log>

In 3-5 sentences explain:
1. What went wrong (the root cause, not just the error message)
2. Which specific line in the doc or script caused the failure
3. What needs to be fixed

Be concrete and specific. Address the doc author, not a sysadmin.
Format as plain markdown, no headers."""


def call_llm(prompt: str) -> str:
    provider = os.environ.get("AI_PROVIDER", "anthropic").lower()

    if provider == "anthropic":
        import anthropic
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            return "_AI analysis unavailable (ANTHROPIC_API_KEY not set)_"
        model = os.environ.get("ANTHROPIC_MODEL") or "claude-sonnet-4-6"
        client = anthropic.Anthropic(api_key=api_key)
        msg = client.messages.create(
            model=model, max_tokens=512,
            messages=[{"role": "user", "content": prompt}]
        )
        return msg.content[0].text

    elif provider in ("github", "openai"):
        from openai import OpenAI
        if provider == "github":
            token = os.environ.get("GITHUB_TOKEN")
            if not token:
                return "_AI analysis unavailable (GITHUB_TOKEN not set)_"
            model  = os.environ.get("GITHUB_MODEL") or "gpt-4o"
            client = OpenAI(base_url="https://models.inference.ai.azure.com", api_key=token)
        else:
            api_key = os.environ.get("OPENAI_API_KEY")
            if not api_key:
                return "_AI analysis unavailable (OPENAI_API_KEY not set)_"
            model  = os.environ.get("OPENAI_MODEL") or "gpt-4o"
            client = OpenAI(api_key=api_key)
        resp = client.chat.completions.create(
            model=model, max_tokens=512,
            messages=[{"role": "user", "content": prompt}]
        )
        return resp.choices[0].message.content

    return "_AI analysis unavailable (unknown provider)_"


def main():
    if len(sys.argv) != 4:
        sys.exit(f"Usage: {sys.argv[0]} <log_file> <doc_path> <runnable_sh>")

    log_path    = sys.argv[1]
    doc_path    = sys.argv[2]
    script_path = sys.argv[3]

    with open(log_path)    as f: log    = f.read()
    with open(script_path) as f: script = f.read()

    # Trim log to last 100 lines to stay within token limits
    log_tail = "\n".join(log.splitlines()[-100:])

    prompt = (PROMPT
              .replace("{doc_path}", doc_path)
              .replace("{script}",   script)
              .replace("{log}",      log_tail))

    print(call_llm(prompt))


if __name__ == "__main__":
    main()
