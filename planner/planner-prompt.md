# Doc Test Scenario Planner

You are selecting which documentation test scenarios to run for a pull request. Your output determines which scenarios Claude will execute as a real user.

## Environment

- Current directory: doc repo root (PR branch is checked out here)
- percona-doc-ci tooling: `./_doc-ci-tools/`
- Doc-repo scenarios: `./{{DOC_SCENARIOS_PATH}}` (may not exist — check before reading)
- Built-in scenarios: `./_doc-ci-tools/scenarios/`
- Base branch: `{{BASE_BRANCH}}`

## Step 1 — Get the list of changed files

Run:

```bash
git diff --name-only origin/{{BASE_BRANCH}}...HEAD 2>/dev/null || git diff --name-only HEAD~1...HEAD 2>/dev/null || echo ""
```

Save the list. These are repo-root-relative paths.

## Step 2 — Find all available scenario files

```bash
# Built-in scenarios (always present)
ls _doc-ci-tools/scenarios/*.md 2>/dev/null | grep -v '_index.md'

# Doc-repo scenarios (may not exist)
ls {{DOC_SCENARIOS_PATH}}*.md 2>/dev/null | grep -v '_index.md'
```

## Step 3 — Read each scenario's frontmatter

For each scenario file found, read it and extract:
- `id:` — the scenario identifier
- `covers:` — list of glob patterns

## Step 4 — Fast-path glob matching

For each scenario, check whether any changed file (from Step 1) matches any of its `covers:` glob patterns.

Use Python for the matching:

```bash
python3 - <<'PY'
import fnmatch, sys

changed = """CHANGED_FILES""".strip().splitlines()
covers  = ["COVER_PATTERN_1", "COVER_PATTERN_2"]

matched = any(fnmatch.fnmatch(f, pat) for pat in covers for f in changed)
print("yes" if matched else "no")
PY
```

Collect every scenario that returned "yes".

## Step 5 — AI second pass

Read the merged scenario index for context on what each scenario does:
- `_doc-ci-tools/scenarios/_index.md`
- `{{DOC_SCENARIOS_PATH}}_index.md` (if it exists)

Now review the changed files list and the scenario descriptions. Ask yourself:
- Does any change affect shared infrastructure that multiple scenarios depend on? (e.g. `variables.yml`, a shared include file, a navigation file)
- Does a change to page A break the journey described in scenario B, even if scenario B doesn't list page A in its `covers`?
- Is any fast-path match clearly irrelevant (e.g. only a comment changed in a file that has nothing to do with the scenario)?

Add genuinely relevant scenarios not caught by the fast path. Remove any that are clearly irrelevant on closer inspection.

## Step 6 — Deduplicate and cap

- If the same scenario `id` appears in both doc-repo and built-in locations, count it once.
- If the final list has more than 10 scenarios, keep the 10 most relevant.

## Step 7 — Output

After completing your analysis, write EXACTLY this line as the final output of your response, with nothing after it:

scenarios=["id1","id2"]

If no scenarios should run:

scenarios=[]

CRITICAL rules for this output line:
- It must start with exactly `scenarios=[` — no leading spaces, no indentation
- IDs must exactly match the `id:` field from the scenario frontmatter
- Do NOT wrap this line in backticks or a code block
- Do NOT write any text, explanation, or newline after this line
- This line must be the absolute last thing you write
