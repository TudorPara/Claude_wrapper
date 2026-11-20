# Benchmark: cost and quality of a cw version

Compares two versions of cw on the same prompts: what they cost (the 5-hour usage window, `cw stats`) and what quality they ship (`cw audit`). Run it for `main` and for a branch, with the same models.

## What it measures

| Number | Where it comes from |
|---|---|
| Cost per role and in total | `cw benchmark <project>` (the same per-run cost as `cw stats`, a resumed session counted once) |
| Reviews per model | `cw benchmark`: how many reviews ran on Opus and how many on the follow-up model |
| Quality | `cw audit <ticket>` on every merged ticket: one extra review of the final diff with the reviewer model; its blocking findings are what got through |
| Also | `cw fix` rounds, checks still failing, blocking findings left open, per ticket |

`cw audit` costs one reviewer run per ticket. It is kept out of the ticket's runs, so it never changes the cost being measured.

## Noise first

Agents decide differently from run to run. **Run the baseline twice.** If the two runs differ by 20%, a 10% saving between versions means nothing. Each full run costs about $9 of usage (the 2026-11-19 baseline was $9.04).

## Two versions side by side

Keep `main` in its own worktree, so both versions can run without switching branches:

```bash
git worktree add ../cw-main main
```

The branch's cw (with `cw audit` and `cw benchmark`) reads tickets written by either version.

Define one shell function per version, from the repository root (functions, not aliases, so `$CW` below can name either):

```bash
cw-main() { uv run --project ../cw-main/backend cw "$@"; }
```

```bash
cw-new() { uv run --project ./backend cw "$@"; }
```

Give every run its own project name and folder, so the runs never share an index, tickets or a GitHub repository: `bench-main-1`, `bench-main-2`, `bench-new-1`.

## The prompts

The same nine use cases as the 2026-11-19 manual test, in this order, for each run (set `P` to the run's project name and `CW` to `cw-main` or `cw-new`, for example `P=bench-main-1 CW=cw-main`):

1. `$CW new $P "A tiny shop API in Python with FastAPI. Deliberately minimal: one module app/main.py, products kept in an in-memory list, GET /products with page and size query parameters, and pytest tests. No database and no layers yet." --path ~/cw-bench --github private`, then approve, `pr`, `merge`.
2. Plant an off-by-one in the pagination slice by hand, commit and push it, then `$CW run $P "fix the off-by-one in pagination"`, `pr`, `merge`.
3. `$CW run $P "add orders: POST /orders with product ids and quantities, GET /orders and GET /orders/{id}, kept in memory, with tests"`; approve if it escalates; `pr`, `merge`.
4. `$CW run $P --plan "add a CSV export of orders"`; answer `Columns: order id, created at, product name, quantity, line total. Use whatever the team usually does for the date format.`; then `Make it three tasks: S1 the export service, S2 the route, S3 the tests. Give S3 to a second worker.`; approve; `pr`.
5. On that pull request, post the same three line comments as the manual test (stream the CSV, a test for zero items, a rename to `doTheExport`), then `fix`, `merge`.
6. `$CW run $P "add GET /products/{id} that returns 404 for an unknown id, with tests"`, `park`, then `$CW run <ticket> "also return 422 for a negative id"`, `pr`, `merge`.
7. `$CW run $P --plan "Restructure into layers: routes, services, repositories and schemas. Move products and orders from memory to PostgreSQL with SQLAlchemy and Alembic. The database goes in dev/docker-compose.yml with a healthcheck, on a host port picked for this project. Settings come from .env through pydantic-settings, and the tests use their own database."`; approve; `pr`, `merge`.
8. `$CW run $P --plan "Add a CSV export of products, and separately rename the /orders endpoints to /purchases. Each change on its own branch with its own pull request."`; `split`; for each: `resume`, `approve`, `pr`, `merge`.
9. `$CW run $P --plan "Make the CSV exports show amounts in the user's currency."`; approve; `pr`, `merge`.

Answer the agents' questions with the same words in every run. If a run needs a reply the others did not, note it next to the numbers.

## After each run

Audit every merged ticket with the branch's cw, then print the report:

```bash
for ticket in $(cw-new status --project $P --include-archived --json | jq -r '.[] | select(.status=="done") | .id'); do cw-new audit $ticket; done
```

```bash
cw-new benchmark $P
```

```bash
cw-new benchmark $P --json > bench-$P.json
```

## Reading the result

- **Cost:** the total, and per role. The review changes should show in the reviewer row; the context engine should show as fewer planner and worker turns on the later, bigger tickets.
- **Quality:** the sum of `audit_blocking` over the run. A cheaper run with more blocking findings left is not a win.
- **Sonnet re-reviews:** a follow-up review can mark an Opus finding as fixed. If the audit finds blocking issues the re-reviews closed, set `reviewer_followup` back to the reviewer model.
- Compare each version against the spread of its own two runs before calling a difference real.

## Clean up

```bash
git worktree remove ../cw-main
```

Delete the benchmark projects with `cw project remove <name> --purge --yes`, their GitHub repositories with `gh repo delete <name> --yes`, and `~/cw-bench`.
