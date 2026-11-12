---
name: bootstrap-project
description: Start a new project the team way - git repo, CLAUDE.md, toolkit settings, first commit, remote repository on GitHub or Bitbucket, and push. An extended /init. Run it only when the user asks for it.
disable-model-invocation: true
argument-hint: "<project-name> [github|bitbucket] [private|public]"
---

# Bootstrap a project

Arguments: `$ARGUMENTS` (project name, code host, visibility). Ask for anything missing before creating anything.

1. **Confirm first.** Show the user the name, the host (GitHub or Bitbucket), the visibility and, for Bitbucket, the workspace. Creating a remote repository is visible to others, so wait for an explicit yes.
2. **Local repository.** If the folder is not a git repository, run `git init -b main`. Add a `.gitignore` for the stack (always include `.env`, `.venv/`, `__pycache__/`, `node_modules/`, `target/`, `.cw/tmp/`).
3. **CLAUDE.md.** Explore the folder the way `/init` does and write a short `CLAUDE.md`: what the project is, how to install, run, lint and test it, and where the main code lives. Keep it under 60 lines.
4. **Toolkit settings.** Create or merge `.claude/settings.json` so the project uses the team toolkit:
   - `extraKnownMarketplaces`: `nexttech-toolkit` (the team's GitHub repo), `ponytail` (`DietrichGebert/ponytail`) and `claude-plugins-official` (`anthropics/claude-plugins-official`).
   - `enabledPlugins`: `core@nexttech-toolkit`, the stack plugin (for example `python@nexttech-toolkit`), `ponytail@ponytail`, and the stack's language server (`pyright-lsp@claude-plugins-official` for Python, `jdtls-lsp@claude-plugins-official` for Java).
   - `permissions.deny`: `Edit(.env*)`, `Write(.env*)`, `Read(.env)`.
   Keep every key that is already there.
5. **First commit.** `git add -A` and `git commit -m "chore: bootstrap project"`.
6. **Remote and push.**
   - GitHub: `gh repo create <name> --<visibility> --source . --remote origin --push`.
   - Bitbucket Cloud: create the repository with `curl -u "$BITBUCKET__EMAIL:$BITBUCKET__API_TOKEN" -X POST https://api.bitbucket.org/2.0/repositories/<workspace>/<name> -H 'Content-Type: application/json' -d '{"scm":"git","is_private":true}'`, then `git remote add origin git@bitbucket.org:<workspace>/<name>.git` and `git push -u origin main`. Never print the token.
7. **Register with cw.** Tell the user they can now run `cw project add <path>` (or use Add project in FRIDAY) to onboard it.

Report what was created and the repository URL.
