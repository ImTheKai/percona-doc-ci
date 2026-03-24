---
id: pg_tde-install-new-user
description: "New user installs pg_tde on Ubuntu, enables transparent data encryption on a table, and verifies the encryption is active"
covers:
  - docs/install*.md
  - docs/install/**/*.md
  - docs/pg_tde/**/*.md
  - docs/getting-started*.md
  - docs/getting-started/**/*.md
  - docs/quickstart*.md
  - docs/quick-start*.md
  - docs/keyring/**/*.md
  - docs/key-management/**/*.md
  - variables.yml
---

You are a developer who has set up a fresh Ubuntu system with PostgreSQL already installed. You have heard about pg_tde — Percona's Transparent Data Encryption extension for PostgreSQL — and you want to try it out by following the official documentation.

Your goal is to:
1. Install the pg_tde extension by following the installation guide in the docs
2. Configure it (keyring setup, shared_preload_libraries, restart PostgreSQL)
3. Enable the extension in a database
4. Create an encrypted table
5. Insert some data and verify the encryption is working

## Instructions

Start by exploring the documentation. Look for installation guides, getting-started pages, or quickstart guides. Common locations: `docs/install/`, `docs/getting-started/`, `docs/quickstart.md`.

Read the documentation carefully from the beginning. You are a first-time user — follow every step exactly as written. If the docs tell you to complete a prerequisite step first (like setting up a keyring provider), find that documentation and complete it before proceeding.

Execute every command you encounter that applies to this scenario on Ubuntu with a default PostgreSQL installation. Follow the documentation across as many pages as needed.

When you are done (or when a step fails), report your result using the scenario-report format.
