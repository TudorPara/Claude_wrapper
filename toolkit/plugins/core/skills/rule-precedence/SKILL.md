---
name: rule-precedence
description: Which rule wins when the project's conventions, the Nexttech rules, ponytail and clean-code disagree, and how the Nexttech rules apply inside a cw run. Use whenever two of them point in different directions.
---

# Rule precedence

Several rule sets can be loaded at once. When they disagree, the higher one wins:

1. The project's own decisions: its `CLAUDE.md` and the project conventions in your system prompt (decisions marked "keep theirs" override the toolkit). For the Nexttech rules these are the project's rulings.
2. The Nexttech rules for the stack (for Python, the `python-rules` topics: `core-rules`, `python-style` and the rest), then `engineering`.
3. ponytail: the laziest solution that works, no speculative abstraction.
4. clean-code: naming, small functions, readable structure.

**ponytail is one of the most important skills for writing code.** Apply its ladder before every piece of code you write or plan: does it need to exist, is it already in the codebase, does the standard library or an installed package do it, can it be one line, and only then the least code that works. In a review, code the ladder would have removed is a finding. ponytail ranks third only for the few points where the Nexttech rules say otherwise, and `core-rules` and `python-style` list them: no comments (not even `ponytail:` ones), tests only where the project has a test command, `X | None` from lookups, no splitting into helpers with one caller.

## The Nexttech rules inside a cw run

The Nexttech rules were written for a developer working with an agent. In a cw run, cw plays the developer's part, so these points replace what the rules say:

- **Names.** A `nexttech-<topic>` skill is the topic file `<topic>` in your system prompt (`nexttech-core-rules` is `core-rules`), and `PR_review.md` is `pr-review-procedure`. `nexttech-project-rulings` and `.claude/CLAUDE.md` mean the project's `CLAUDE.md` and the project conventions in your system prompt. "The project lead" is the developer who runs cw.
- **Skills.** You cannot invoke a skill. The ones you need, `ponytail` included, are already in your system prompt, and a topic arrives when you touch a file it covers. Where a rule says to run `/ponytail`, apply the ponytail skill you have.
- **Git.** Never `git add`, commit, push, merge, rebase or switch branches. cw makes the ticket's one commit, merges the base branch, pushes and opens the pull request. The branch name and the commit message are cw's; you write `commit_message` in your answer.
- **Questions.** "Ask the project lead" means: return a question in your answer, where questions are allowed; otherwise choose the simplest option and say what you assumed.
- **Commands.** Run only the lint and test commands in your prompt, and read-only git. Where the rules say `make lint`, booting the app or calling endpoints, use those commands and say plainly what you could not run. cw runs the checks itself after every task.
- **Tests.** When the project has a test command, every behaviour you add or change gets one focused test in the project's framework. When it has none, add no test framework unasked (`core-rules`).
- **GitHub and Linear.** You never read or write them. Everything you need is in your prompt.
- **Reviews.** The request, the plan, the workers' notes, the check results and the diff are in your prompt. Skip the steps of `pr-review-procedure` that fetch them (`gh`, `git fetch`, Linear), and answer in cw's JSON format, with the severities `pr-review` gives, not the procedure's answer format. cw posts your findings on the pull request; the rule that a review never posts anything is about you, not cw.
- **The PR description.** cw writes it from the ticket. Put what the rules want in it (deviations, stand-ins, known gaps, what was not verified) in your `summary` instead.

Never change behaviour outside the task to satisfy a lower-ranked rule.
