# Scenario Index — percona-doc-ci built-in scenarios

The planner reads this index during its AI second pass to understand what each scenario covers.
Individual scenario files contain the full `covers:` glob patterns used for fast-path matching.

---

- **pg_tde-install-new-user**: New user installs pg_tde on Ubuntu, enables transparent data encryption on a table, and verifies the encryption is active
