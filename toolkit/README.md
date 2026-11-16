# nexttech-toolkit

The team's Claude Code plugin marketplace. Every project enables it, so everyone works with the same agents, skills, rules and hooks.

| Plugin | Contents |
|---|---|
| `core` | agents `planner`, `worker`, `reviewer`, `onboarder`; skills `engineering` (the team's rules for any language), `pr-review` (how the reviewer applies them), `rule-precedence`, `clean-code` (MIT, from sickn33/agentic-awesome-skills); command `/core:bootstrap-project`; `guard.py` PreToolUse hook |
| `python` | skill `python-rules`, an index of `topics/`: the `nexttech-claude-kit` rules without the reviewers' names; `rules_on_touch.py` hook, which shows a topic the first time a file it covers is read or edited; `ruff_on_edit.py` PostToolUse hook |

## How the rules reach an agent

1. **Pinned.** A skill whose front matter says `pin: true` is put in full into every cw agent run for projects that load its plugin, and so is every skill an agent file lists under `skills:` (that is how the reviewer gets `pr-review`). Keep pinned skills short.
2. **By topic.** `python-rules/topics/topics.json` maps file names and folders to topic files. The hook shows a topic once per session, when the agent first touches a matching file. The same file lists, under `always`, the topics every agent gets (`core-rules` and `python-style`); under `new_project`, the topics every agent gets while a project has no code yet, and under `review`, the topics the reviewer always gets on top of those that cover the changed files.
3. **Mechanically.** Hooks enforce what a script can check: the guard blocks dangerous shell commands, ruff formats and lints edited files that were clean before.

## Install by hand

`cw project wire <name>` writes this into a project's `.claude/settings.json` for you.

```
/plugin marketplace add your-org/nexttech-toolkit
/plugin install core@nexttech-toolkit
/plugin install python@nexttech-toolkit
```

## Change it

- **A rule:** a file in `python-rules/topics/` is a copy of the `nexttech-claude-kit`: edit it in the kit, then copy it here again and remove the reviewers' and people's names (#13 stays, the name after it goes). Anything else: edit the skill. To add a topic, add `topics/<name>.md` and its patterns to `topics.json` (`folder/` matches a folder on the path, anything else is a file-name glob).
- **A stack:** create `plugins/<stack>/` with its rules skill and hooks, list it in `.claude-plugin/marketplace.json`, then add the stack to `stacks` in `~/.cw/config.json`.
- Bump the versions on every change and check with `claude plugin validate .`.
