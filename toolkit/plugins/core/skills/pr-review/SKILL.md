---
name: pr-review
description: How Nexttech reviews a change before a pull request - apply every team rule strictly, name the rule each finding breaks, report only real findings. Use when reviewing a diff or a pull request.
---

# Pull request review

A review is the last check before other people read the code. Apply the team rules strictly: a rule the diff breaks is a finding, whoever wrote the code and however small the change.

## What to check, in order

1. **The request.** The diff does what was asked and what the plan said, no less and no more. Work nobody asked for is a finding.
2. **Correctness and security.** Logic errors, missed edge cases, broken callers, secrets, unvalidated input.
3. **Every rule in your system prompt.** Go through them one by one, do not sample: the project's conventions, `engineering`, the stack rules with their review checklist, and the topic rules for the files that changed.
4. **The pull request itself.** These come from the team's PR discipline:
   - Ship only the task. Development scripts stay local and gitignored; when a brief asks for something kept local, say so in the PR.
   - Never commit `.env`, data directories, uploads, model caches or generated files.
   - Explain anything non-obvious in the PR description: deviations from the brief, provider and resource lifecycle, model preloading, capability checks of a replacement model. Known gaps are named in the PR, not left for the reviewer to find.
   - `git status` and the full diff read line by line: only intended files, no dead code, no typos, no extra blank lines.
5. **Tests and checks.** The changed behaviour has a focused test. A check that passed before the change and fails now is blocking.

## How to report

- One comment per finding, on the line where it is: what is wrong, which rule it breaks (name the skill or section, for example "python-rules 6. Layers") and what to do instead. A worker who sees only your comment must be able to fix it.
- `blocking`: a broken rule, a defect, a security problem, a failing check, or changed behaviour without a test. `minor`: anything else worth saying.
- Only the diff. Problems that were already there are out of scope. No praise, and nothing the formatter already fixes.
- The verdict is `changes` when at least one finding is blocking, otherwise `approve`.
- The summary is read on the pull request: what the change does, what you checked, and what is left open.
