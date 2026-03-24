# Doc Navigation

You are reading documentation from a checked-out Percona doc repository. The docs are Markdown files, typically under a `docs/` directory. You have Read, Glob, and Grep available.

## Finding pages

```bash
# List all Markdown pages
# (use Glob with pattern docs/**/*.md)

# Search for a topic across all pages
# (use Grep with a keyword)
```

## Reading a page

Use the Read tool on any `.md` file. Pages use MkDocs Material syntax:
- Admonitions: `!!! note`, `!!! warning`
- Code blocks with language tags: ` ```bash `, ` ```sql `, ` ```{.bash data-prompt="$"} `
- Tabs: `=== "Tab name"`
- Cross-references: `[link text](relative/path.md)` — resolve these as file paths from the repo root

## Following cross-references

When a page says "see [Installation](../install/guide.md)" or "complete [Step 1](step1.md) first", follow those links. Resolve the path relative to the current file:

- `../install/guide.md` from `docs/getting-started/index.md` → `docs/install/guide.md`
- Absolute paths starting with `/` are relative to `docs/`

## Variables

Pages may use Jinja2 variables like `{{pgversion}}` or `{{dockertag}}`. Read `variables.yml` at the repo root to find their resolved values. Substitute them mentally when reading commands.

## Key principles

- Read prerequisite pages before the main page. If a page says "complete the installation first", find and read that install page.
- When a page references a configuration file created in a previous step, find and read that step.
- The docs are the only source of truth. Do not use your internal knowledge about how a product works to fill in missing steps.
- If a page says "refer to the X documentation for details", find the X page in this repo and read it.
