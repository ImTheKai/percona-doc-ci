# Scenario Reporting

When you have finished the scenario (either everything succeeded, something failed, or the scenario does not apply), write a structured result as the **final content** of your response.

## Required format

Your response must end with this exact structure. Nothing may appear after the closing details.

### PASS

````
```scenario-result
id: <scenario-id>
status: PASS
```

### What was tested
<1–3 sentences summarising the journey you completed and that all steps succeeded>
````

### FAIL

````
```scenario-result
id: <scenario-id>
status: FAIL
```

### What was tested
<1–3 sentences summarising how far you got before the failure>

### What failed
**Step:** `<the exact command that failed>`
**Page:** `<path/to/page.md>` (line ~N)
**Error:** `<the exact error message from the command output>`
**Cause:** <your analysis — what is wrong or missing in the documentation that caused this failure>
````

### SKIP

````
```scenario-result
id: <scenario-id>
status: SKIP
```

### Why skipped
<explanation of why this scenario does not apply — e.g. the feature is not documented for this OS, or the docs page referenced by the scenario does not exist>
````

## Rules

- The `scenario-result` block must be the **very last thing** in your response. No text, no explanation after the closing fence.
- `id` must exactly match the `id:` field from the scenario file frontmatter.
- `status` is exactly `PASS`, `FAIL`, or `SKIP` — uppercase, nothing else.
- For FAIL: focus the **Cause** on what is wrong in the documentation, not the CI environment. If a command fails because the docs have a typo or a missing prerequisite step, say that.
- For SKIP: only use this when the scenario genuinely does not apply. A command that fails is always `FAIL`, not `SKIP`. An environment mismatch (wrong OS) is a valid reason to `SKIP`.
