#!/usr/bin/env python3
"""
Expand Jinja2 variables in a Markdown doc before code block extraction.
Reads variables from a YAML file (typically variables.yml or mkdocs.yml extra:).

Usage:
  python expand_vars.py <doc.md> <variables.yml> > expanded.md

Unknown variables are left as empty string (non-fatal) so a partial
variables.yml doesn't break extraction for unrelated blocks.
"""
import sys
import yaml

try:
    from jinja2 import Environment, Undefined
except ImportError:
    sys.exit("jinja2 not installed: pip install jinja2")


def expand(doc_path: str, vars_path: str) -> str:
    with open(vars_path) as f:
        raw = yaml.safe_load(f) or {}

    # Flatten nested keys (e.g. extra.pgversion → pgversion) for mkdocs.yml style
    variables: dict = {}
    for k, v in raw.items():
        if isinstance(v, dict):
            variables.update(v)
        else:
            variables[k] = v

    with open(doc_path) as f:
        content = f.read()

    # Silently ignore undefined variables rather than raising
    env = Environment(undefined=Undefined)
    try:
        return env.from_string(content).render(**variables)
    except Exception:
        # If Jinja2 chokes on non-template syntax (e.g. raw {{ in code blocks),
        # fall back to returning the file unchanged.
        return content


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit(f"Usage: {sys.argv[0]} <doc.md> <variables.yml>")
    print(expand(sys.argv[1], sys.argv[2]), end="")
