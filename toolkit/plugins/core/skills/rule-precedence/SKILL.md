---
name: rule-precedence
description: Which rule wins when the project's conventions, the stack rules, ponytail and clean-code disagree. Use whenever two of them point in different directions.
---

# Rule precedence

Several rule sets are loaded at once. When they disagree, the higher one wins:

1. The project's own decisions: its `CLAUDE.md` and its `.cw/conventions.md` (decisions marked "keep theirs" override the toolkit).
2. The stack rules (for example `python-rules`).
3. ponytail: the laziest solution that works, no speculative abstraction.
4. clean-code: naming, small functions, readable structure.

Known conflicts and how they resolve:

- **Interfaces.** clean-code says "hide the implementation behind interfaces"; ponytail and the stack rules say no interface with one implementation. No interface until a second implementation exists.
- **Returning null.** clean-code says "don't return null"; the stack rules return `X | None` from repository lookups. Follow the stack rules.
- **Tests.** clean-code asks for strict TDD; ponytail asks for one runnable check per non-trivial piece of logic. Leave one focused test per behaviour you changed, using the project's test framework.
- **Comments.** Both clean-code and the stack rules prefer clear code over comments; the stack rules forbid comments in code entirely. Follow the stack rules.
- **Function length.** clean-code's "under 20 lines" is a heuristic, not a reason to split a function into helpers nobody else calls.

Never change behaviour outside the task to satisfy a lower-ranked rule.
