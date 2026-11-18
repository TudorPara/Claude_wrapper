---
name: pr-review
description: How Nexttech reviews a change before a pull request - check the request, correctness and the team rules, name the rule each finding breaks, and weigh each finding as blocking or minor. Use when reviewing a diff or a pull request.
---

# Pull request review

A review is the last check before other people read the code. A rule the diff breaks is a finding, whoever wrote the code and however small the change.

## What to check, in order

1. **The request.** The diff does what was asked and what the plan said, no less and no more.
2. **Correctness and security.** Logic errors, missed edge cases, broken callers, secrets, unvalidated input.
3. **The rules in your system prompt.** The project's conventions, `engineering`, the stack's review checks and the topic rules for the files that changed.
4. **The pull request itself.** Only the task's files. No `.env`, data directories, uploads, model caches or generated files; development scripts stay local. Read the whole diff: no dead code, no typos, no stray blank lines.
5. **Tests and checks.** When the project has a test command, the changed behaviour has a focused test. A check that passed before the change and fails now is blocking.
6. **Over-engineering.** Ask of every new function, class, option and dependency whether the `ponytail` ladder would have removed it.

## How to report

- One comment per finding, on the line where it is: what is wrong, which rule it breaks (name the skill or section, for example "python-style 3. Naming") and the smallest fix. A worker who sees only your comment must be able to fix it.
- `blocking`: a defect (wrong behaviour, a broken caller, data loss, an edge case the request needs), a security or access problem, a secret in the diff, a failing check, changed behaviour without a test where the project has a test command, or work nobody asked for. In the review checks' levels, this is Blocking.
- `minor`: everything else worth saying: a broken style or naming rule, dead code, over-engineering, a question (written as a question). In the review checks' levels: Should fix, Nit and Question. cw posts them and sends them to the worker with the next blocking fix; on their own they do not hold up the merge.
- Only the diff. Problems that were already there are out of scope. No praise, and nothing the formatter already fixes.
- The summary is read on the pull request: what the change does, what you checked, and what is left open, including what the workers could not verify.
