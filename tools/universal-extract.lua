-- universal-extract.lua
-- Pandoc Lua filter that extracts runnable code blocks from Percona docs.
-- Handles all tag variants used across Percona doc repos:
--   postgresql-docs:   {.bash data-prompt="$"}
--   psmysql-docs:      ```shell
--   pxb-docs:          ```shell
--   pmm-doc:           ```sh
--   any repo:          ```bash
-- SQL blocks are extracted separately so the AI planner can route them
-- through psql rather than running them directly in bash.

local SHELL_CLASSES = { sh = true, shell = true, bash = true }
local SHELL_ATTRS   = { ["data-prompt"] = true }  -- MkDocs material prompt attr

function CodeBlock(block)
  local cls = block.classes[1] or ""

  local is_shell = SHELL_CLASSES[cls]
  if not is_shell then
    for k, _ in pairs(block.attributes) do
      if SHELL_ATTRS[k] then is_shell = true; break end
    end
  end

  if is_shell then
    print("#-----SHELL-----")
    io.stdout:write(block.text, "\n\n")
  elseif cls == "sql" or cls == "SQL" then
    print("#-----SQL-----")
    io.stdout:write(block.text, "\n\n")
  end
  -- All other block types (output examples, yaml, ini, etc.) are silently skipped.
end
