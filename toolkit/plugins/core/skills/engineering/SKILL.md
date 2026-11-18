---
name: engineering
description: Nexttech's engineering rules for any language - generic configuration, no over-engineering, how to work, naming, verifying by running. Use for any code, design or review task.
pin: true
---

# Engineering rules

The language-independent part of the team's rulebook, consolidated from past code reviews and a reference service. Where sources disagreed, the stricter reviewer ruling is the one written here. The stack rules (for example `python-rules`) add the language-specific part.

A project's own decisions (its brief, its reviewers' rulings, its `CLAUDE.md`) win over these defaults. When unsure, ask instead of guessing.

## How to work

The two rules above all others:

1. **Generic configuration.** Anything a coworker might configure differently (a provider, a model, a type, a limit, a path) comes from settings, never from code.
2. **No over-engineering.** The simplest code that is clean and correct, logically and syntactically. No abstraction, helper, layer or option the task does not need. The `ponytail` skill's ladder is how to get there.

Working rules:

- For complex or uncertain work, reason step by step before writing code: restate the task, list the rules and files involved, name what is unknown, then choose the simplest correct option.
- Do not hallucinate. If a file, package version or requirement is unknown, read it (the code, the lockfile, the installed package's source) before relying on library internals.
- Verify by running the lint and test commands you have. Say plainly what was not verified.
- Never print or commit `.env`. Open or edit it only when the task adds or changes a setting, and keep `.env.example` in step.
- Code must already satisfy every rule here; reviewers have flagged AI-written code applied as-is.

Order of work for any change:

1. Settle the contract: what goes in, what comes out, what can fail.
2. Write the narrowest thing that works. No abstraction yet.
3. Run it. "It should work" is not evidence.
4. Generalise only where a second caller already exists.

## Code and naming

- No magic numbers or magic strings: named module constants or settings. This includes path fragments, URL schemes, metadata keys, prefixes, router prefixes and tags.
- No dead code: no unused constants, helpers, imports or methods.
- Code must read as written by a person: no filler names, no docstrings that restate the code.

Naming:

- **Methods** are verb plus object in the vocabulary of their layer: `get_active_by_id`, `revoke_refresh_session`, `verify_password`. Never `handle`, `process`, `do_work`, `execute`, `run`, `manage`. A reviewer rejected `warm_up` in favour of `preload_models`.
- **Booleans and predicates** read as questions: `is_active`, `has_expired`, `_is_public`.
- **Constants name a decision**, not a value: `MAX_PASSWORD_LENGTH`, `REFRESH_COOKIE_PATH`. A constant whose name is its value in capitals should be inlined.
- **Units and scale live in the name**: `pool_timeout_seconds`, `access_token_expire_minutes`, `balance_minor`, `amount_minor`.
- **Banned as standalone names**: `data`, `info`, `obj`, `temp`, `result`, `value`, `item`, `manager`, `helper`, `util`.
- **One word per concept** across the codebase: pick `get` or `fetch` or `find` and never alternate.
- If a name needs a comment to be understood, fix the name.

After any IDE rename with "search in comments and strings", grep both the old and the new name in strings, prompts, env keys and docstrings; such a rename has rewritten an intent value, prompt text and a user-facing answer before.
