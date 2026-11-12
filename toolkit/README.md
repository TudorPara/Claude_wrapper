# nexttech-toolkit

The team's Claude Code plugin marketplace. Every project enables it, so everyone works with the same agents, skills, rules and hooks.

| Plugin | Contents |
|---|---|
| `core` | agents `planner`, `worker`, `reviewer`, `onboarder`; skills `clean-code` (MIT, from sickn33/agentic-awesome-skills), `rule-precedence`; command `/core:bootstrap-project`; `guard.py` PreToolUse hook |
| `python` | skill `python-rules`; `ruff_on_edit.py` PostToolUse hook |

Install by hand (cw's `project add` writes this into each project's `.claude/settings.json` for you):

```
/plugin marketplace add your-org/nexttech-toolkit
/plugin install core@nexttech-toolkit
/plugin install python@nexttech-toolkit
```

Add a stack: create `plugins/<stack>/` with its rules skill and hooks, list it in `.claude-plugin/marketplace.json`, then add the stack to `stacks` in `~/.cw/config.json`. Bump `metadata.version` on every change and check with `claude plugin validate .`.
