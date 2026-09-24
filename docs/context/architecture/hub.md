---
title: The tool hub — tools a maker publishes, made of other tools
status: built (phases 1–10, 2026-09-09/14; pricing flexibility 9.1–9.5 (`docs/hub-pricing-decisions.md`), listing + public run log 10.1–10.5 (`docs/hub-listing-decisions.md`)); behind `hub_enabled` (TREG_HUB_ENABLED), off in production until the final merge
sources:
  - src/treg/domain/hub/__init__.py
  - src/treg/domain/hub/manifest.py
  - src/treg/domain/hub/refs.py
  - src/treg/domain/hub/graph.py
  - src/treg/application/hub/__init__.py
  - src/treg/application/hub/runner.py
  - src/treg/application/hub/sandbox.py
  - src/treg/application/hub/limits.py
  - src/treg/application/hub/health.py
  - src/treg/hub_sandbox.py
  - src/treg/routers/hub.py
  - src/treg/routers/catalog.py
  - src/treg/routers/web.py
  - src/treg/application/call/service.py
  - src/treg/domain/money/__init__.py
  - src/treg/mcp.py
  - frontend/src/state/hub.js
  - frontend/src/pages/HubPage.vue
  - frontend/src/pages/HubRunPage.vue
  - src/treg/cli.py
  - src/treg/worker.py
  - src/treg/models.py
  - src/treg/web/index.html
  - src/treg/web/skill.md
  - src/treg/web/llms.txt
  - src/treg/alembic/versions/0041_hub_tools.py
  - src/treg/alembic/versions/0042_hub_runs.py
  - src/treg/alembic/versions/0043_hubtool_check_result.py
  - src/treg/alembic/versions/0044_hubrun_output.py
  - src/treg/alembic/versions/0045_hubtool_data.py
  - src/treg/alembic/versions/0046_hubtool_listed_public_log.py
  - docs/hub-recipes/data-sheets/run.js
  - docs/hub-recipes/data-csv/run.js
  - docs/hub-recipes/engineering-team-size/run.js
  - tests/test_hub.py
  - tests/test_hub_sandbox.py
  - tests/callmatrix/test_hub_run.py
related:
  - architecture/proxy-model.md
  - architecture/money.md
  - architecture/catalog.md
  - architecture/import-boundaries.md
---

# The tool hub

A **hub tool** is a tool a maker publishes on treg, made of other tools: either a JSON list of
steps, or a script that runs in a sandbox. Every step is an ordinary treg call, so identity, the
access rules, key injection and money already exist once per call; the hub adds the graph, the
sandbox, the maker's road, the seller's price, and the surfaces. The decisions are in
`docs/HUB-DECISIONS.md` (50 questions, settled 2026-09-09); the case study (a real Supabase
table served as a tool, walked by hand) is recorded at the end of this file.

Everything sits behind `hub_enabled` (`TREG_HUB_ENABLED`, default off): with the flag off every
hub route answers 404, the call road never asks the hub, the agent files carry no hub text, and
the dashboard shows no Hub entry. With the flag on, `TREG_HUB_TEAMS` (a comma-separated list of
team slugs, default empty) is the middle stage between off and open (owner, 2026-09-24): every
gate that has a caller (`hub_app.enabled_for(slug)`: the hub router, `/call/` of a hub id) answers
404 to a team outside the list, exactly as with the flag off, while the public contract (catalog
get, catalog search, the share page, the agent files) keeps the plain flag and stays readable. An
empty list means every team. The flag flips in production at the final merge, with the list set
to the owner's team first.

## Vocabulary

| word | means |
|---|---|
| maker | the team that publishes a hub tool |
| caller | the team that runs it |
| version | one row of `HubTool`: the four (five) files as published; `<id>@N` pins one |
| run | one execution: a `HubRun` row, the parent call, and one child call per step |
| step | one call inside a run (a JSON step, or one `ctx.call`); a unit against the caps |
| the check | `check.json` run once for real at publish, and on the schedule |

## The four files, and the fifth

`recipe.json` (the manifest), `run.js` (script recipes), `check.json`, `README.md`, and
optionally `data.csv`. `treg hub init <name> [--script]` writes a vendor-neutral skeleton.
`docs/hub-recipes/` holds three worked recipes, each with a matrix test: a public sheet served as a
tool, an uploaded CSV, and `engineering-team-size`, a script ladder over three catalog tools
(free identity resolution, CrustData's role headcount, PDL's R&D class) priced `per_call`.
Four more, one per road, chosen from what customers call most and ask for, are live-verified but
carry no matrix test yet: `seo-domain-snapshot` (JSON steps, `percent`), `brand-mentions` (a script
over Reddit and routed X search that drops loose matches, `per_result`), `email-list-hygiene`
(a script over an uploaded domain list, no tool call) and `hn-mentions` (a script over a no-key own
tool). A JSON step's `input` is an object, so DataForSEO's array-bodied live endpoints need a script.

**The manifest** (`domain/hub/manifest.py`) is validated by pure rules; every refusal is
`ManifestError(field, rule)`, a dotted path into the file plus the rule it broke, because the
maker is usually an agent fixing a file. Fields: `name` (`<team-slug>.<name>` becomes the id),
`summary` (≤200), `writes`, `inputs`, `uses`, `limits`, `pricing` (or a flat `price_usd`), exactly
one of `steps` | `script: "run.js"`, `output`. Rules carried from Crawl4AI's recipes: an input is required by
having no `default` (a `required` key is refused); every non-secret input carries an `example`
or a `default`; an `int` input names a `max`. `uses` is the security boundary: each entry is a
catalog id or one of the maker's own tool names; a hub id is refused (depth one); a script that
serves only its data may leave it empty. Every step's `call` must be in `uses` (`<tool>/<path>`
for an own tool, optional `method`, `allow_fail`). Limits: `steps` ≤ 20, `wall_s` ≤ 120,
the money fields 0–100 dollars with at most six decimals. **Pricing** (`docs/hub-pricing-decisions.md`,
2026-09-14, renamed 2026-09-18): a `pricing` block with one `mode` per version, in the maker's words,
and only the maker's part (the provider fees, the catalog steps, are billed to the caller on top).
`per_call` (`price_usd`, a fixed price per successful run; a file with only a top-level `price_usd`
still means per_call, both together is refused); `per_result` (`per_result_usd` times the integer
`results` the run returns, so `results` (or the older `units`) must be a declared output field;
`results_from` names the `int` input whose `max` bounds one run); `percent` (`percent` of the run's
provider fees, so `uses` must name at least one catalog id). `max_price_usd` is optional on the two
variable modes, a lower cap on the maker's part, never required: the hold comes from what bounds
the run (below). The 2026-09-14 names `flat`, `per_unit`, `cost_plus`, `per_unit_usd` and
`markup_percent` are accepted and stored canonically (`canon_pricing`). Only the mode's fields are
read. The validator normalizes the block to micro-integers (`Validated.pricing`;
`pricing_micro(manifest)` and `price_label(manifest)` read a stored one; `range_label` and
`seller_part_micro` build the headline range below). References must parse and the graph is built at
publish, so a cycle or an unknown step is refused before anyone pays. `validate_check` pins
`check.json` to the manifest; `validate_data` checks `data.csv` (≤ 5 MB, a header and one row; the rows travel into the engine on every run).

## The reference language and the graph (JSON road)

`domain/hub/refs.py` is complete and deliberately small: `$input.<field>`, `$<step>.<path>`
(any depth, `[0]` for one item), `$<step>[]` (every answer of a repeated step),
`$<step>.length`, `$0.<path>` (the position alias), `$<as>.<field>` inside a repeat. A string
that is exactly one reference resolves to the value; a template resolves to text. A missing
field reads as `None`; an unknown root is refused at publish. No arithmetic, no condition, no
function: a recipe that needs those is a script. `domain/hub/graph.py` derives the edges from the
references (never declared), refuses cycles and unknown steps, and keeps each step's `wave`
(its depth) for the trace.

## The runner (`application/hub/runner.py`)

`POST /call/<team>.<name>` with the inputs as the JSON body. The runner coerces the inputs once
(defaults, types, ints clamped to `min..max`, unknown or missing required → 422
`hub_input_invalid` naming the field), reads the ceiling from `X-Treg-Run-Max-Cost`, else from the tool's own `limits.cost_usd` (its
maker knows what one run costs), else $1.00 (price plus steps, counting what is in flight), opens the price hold, then runs the road:

- **JSON road:** every ready step starts, four at a time, waiting on whichever finishes first;
  `for_each` fans a step into counted units; `skip_if_empty`; `allow_fail`.
- **Script road:** `run.js` in the sandbox; every `ctx.call` is the same child call.

Each unit is one `execute_call` under the child hold `{run}:s{n}` (`{run}:s{n}.{i}` for an
item): the same gates, the same key injection, the same reserve-and-settle as a direct call.
**Two teams meet in one run:** a catalog step runs as the caller (their money, their rules,
their key if they hold one, else treg's); a step on one of the maker's own tools runs as the
maker (their registered key, unmetered), which is how a shared recipe uses a key the caller
never holds. Two rules from the case study: a step is never answered compressed (the runner
reads the bytes itself; `Accept-Encoding: identity` on every child), and a script may set
headers on `ctx.call` minus identity and framing headers (`authorization`, `cookie`, `apikey`,
`host`, `content-length`, `x-treg-*`; the tool's binding always wins).

Failure: when a step fails, nothing new starts, in-flight steps finish, and the run answers 424
`hub_run_failed` with `{error: hub_step_failed | hub_script_failed | hub_output_invalid | hub_step_cap,
step, status, trace, charged_micro, price_micro: 0}`; a global refusal (balance, caps) keeps its
own kind and status; passing the ceiling is 402 `hub_run_max_cost`. Money spent on completed
steps stays spent; a failed step's own hold releases by the endpoint's normal rule; the price is
released. Four runs at a time per team (`application/hub/limits.py`, in-process, exact for the
one-process production deploy): the fifth is 429 `hub_busy` with `retry_after_s`.

The reply: `{run_id, recipe: "<id>@<v>", output, usage: {cost_micro, steps_micro, price_micro,
steps, ms}, trace, log}` with `X-Treg-Run-Id` (= the parent call id), `X-Treg-Steps`,
`X-Treg-Cost-Micro` (the total). `Idempotency-Key` covers the whole run through the parent's
store. `HubRun` (migrations 0042, 0044) keeps one row per run: status, steps, cost, price,
duration, masked inputs, trace, log, error, and the output of a successful run; deleted with the
calling team.

## The sandbox (`application/hub/sandbox.py`, `treg/hub_sandbox.py`)

`run.js` runs in a separate short-lived process per run, `python -m treg.hub_sandbox`, spawned
with `runner.py`'s discipline: a scrubbed environment (never the server's), a private temporary
HOME, its own process group, POSIX rlimits (CPU, file size, no core, RLIMIT_AS on Linux), a
wall-clock kill, the whole group killed on every exit. Inside, QuickJS (the `quickjs` package,
server extra) has no network, no file system, no `require`, no `process`, no timers; the engine's
heap is capped at 64 MB. The whole surface a script gets: `ctx.inputs`, `ctx.call(target,
{method, query, body, headers})` → `{status, headers, json, text, truncated, cost_usd}` (the x-treg-*
headers are removed; `cost_usd` is what that call charged, so a script can keep its own budget and
stop before the ceiling: a run that passes it is stopped by treg and returns nothing), `ctx.charge(usd, label)` → nothing (bills the caller for an own-key step, below), `ctx.csv(text)` → rows
keyed by the header (RFC 4180), `ctx.data` → the rows of `data.csv` (≤ 5 MB, parsed once per run in the
parent), `ctx.log(text)` (50 lines × 2 KB). `ctx.call` crosses to the parent as one JSON line
over stdin/stdout; the parent enforces `uses` per call (a call outside the list is refused and
the run stops), the 20-call cap, the ceiling, and the output (one JSON object ≤ 2 MB carrying
every field in `output.fields`, else 424 `hub_output_invalid`). A memory bomb ends as `memory`,
an endless loop as `timeout` (the parent's kill; the engine cannot call into Python with its own
time limit set), a throw as `script`, each one line the maker reads in the run log.

`target` has three shapes: a catalog id; `<tool>/<path>` for an own tool; a full URL, allowed
only when it starts with the base URL of an own tool named in `uses` (its query merges under the
call's) and refused for any other host. "My script needs my own server" is answered by that
door: the server is an own tool (`treg tool add my-api --base-url https://api.mine.com`, secret
optional; a public Google Sheet needs none); treg makes the request; the sandbox never opens a
socket. `ctx.call` returns a promise and does not block the engine: the child keeps pumping the
job queue and settles each promise when the parent's reply for that id arrives, so five calls in
one `Promise.all` are five in flight, run by the parent as tasks four at a time (`MAX_PARALLEL`,
the JSON road's width) and answered in whatever order they finish. A script that awaits one call
at a time behaves as before. `timeout_s` in ctx.call's options is the script's own limit for that
one call: passing it answers `{status: 0, timed_out: true}` instead of ending the run, the cancelled
child releases its hold, and the trace records the step as `timeout`. Added 2026-09-23 for the AI
visibility tool: five answer engines at 30-47 s each could not fit 120 s one after another, and one
engine that never answers must not cost the other four. **A security
review of the sandbox is scheduled as its own pass before release** (the owner's note).

## ctx.charge: an own-key step's cost, billed to the caller

An own-key step costs the caller nothing (rule 1) and the MAKER real money at a vendor treg cannot
see; a waterfall over four such vendors costs the maker $0.001 one run and $0.50 the next, and no
manifest table can say which branch ran. So the script says it: `ctx.charge(usd, label)` after the
step, once it has seen the answer (a vendor that bills only on a hit is charged only on a hit). The
manifest declares `pricing.max_charge_usd`, the most all charges may total in ONE run, allowed with
every mode and only on a script tool; without it `ctx.charge` is refused and the run stops. The
runner holds that cap with the fee on the same `{run}:price` hold, the parent refuses a charge that
would pass it (and a 21st charge), each charge is a `charged` line in the trace with the maker's
label, `usage.charged_micro` is their sum, and the price settled to the maker is fee + charges. A run
that fails releases them with the fee. The price label reads "... + own-key steps up to $X per run"
so the caller sees the cap before running (owner + Jason, 2026-09-24).

## The maker's road (`routers/hub.py`, `application/hub/__init__.py`)

`POST /hub/tools` (member+) takes the files as fields (`manifest`, `script`, `check`, `readme`,
`data`), validates them against the team's world (catalog ids, the team's own tools, existing
hub ids; 422 `{error: manifest_invalid, field, rule}`), stores the next version as `checking`,
then **runs `check.json` once for real**: an in-process request to `POST /call/<id>@<version>`
carrying the maker's identity headers, so the steps are charged to the maker's balance at the
normal prices and never the seller's price. Pass (every `check.fields` present and non-empty,
`min_rows` met; `health.verdict_from` is the one rule) ⇒ `live`, and the 201 carries the call
line and the share page; fail ⇒ the version is kept as `failed` with the reason in
`check_result` (migration 0043). `PUT /hub/tools/{id}` publishes a new version (the name must
match the id). `POST /hub/run` is the dry run behind `treg hub run .`: the files plus `inputs`,
run for real as the maker, nothing stored, version 0 on every trace. `PATCH /hub/tools/{id}`
`{price_usd}` changes the newest live version's price for later runs, no version bump. `DELETE
/hub/tools/{id}` retires every version (off the call road at once; rows kept). `GET
/hub/tools/mine` (every version with derived health and 30-day numbers), `GET
/hub/tools/{id}[@N]`, `GET /hub/tools/{id}/health`, `GET /hub/tools/{id}/earnings?days=N[&format=csv]`,
`GET /hub/runs/{run_id}` (the caller sees what it paid, inputs, trace, output; the maker sees
inputs with secrets masked, trace, log and the full error; a caller's trace and error never
carry an upstream error body; any other team 404).

Versions: the newest `live` serves `/call/<id>`; `<id>@N` pins one, and a pinned old version
stays callable for 30 days after a newer live one exists (`application/hub.tool_for`). The call
road's resolution order is unchanged for everything that exists today and gains a third, last
step: an own tool wins, then a catalog id, then the hub (`application/call/service.py`).

Over MCP (`/mcp/`): `hub_create`, `hub_update`, `hub_mine`; calling stays `call`. `hub_create`'s
description carries the owner's rule: a credential the team does not hold is never hard-coded
into a script; register it first, then name the tool. The CLI mirrors it: `treg hub init |
run | publish | ls | earnings | price | retire`; a refusal prints the field, the rule and the
fix commands with the missing tool's name, one command per line.

## Health and the scheduled check (`application/hub/health.py`)

Health is derived, never stored: a version is `failing` when its last three runs (callers'
runs and scheduled checks alike) all failed, `ok` otherwise, `unknown` before any run. A
failing tool stays callable; the public page, `catalog_get` and the maker's health view say the
state; the next passing run clears it. `treg-worker hub check` (cron it every 6 hours) runs
every live tool's newest `check.json` once as its maker: the identity is rebuilt from the
database (the publisher's membership, else an owner's), the run goes through the runner charged
to the maker at step prices and never the seller's price, the row is a `HubRun` with
`caller_email = "hub-check"`, the verdict lands on the version with `scheduled: true`. A failing
check never retires a tool by itself.

## The seller's money (`domain/money/__init__.py`)

A maker's price rides the same primitives as a step: one extra hold `{run}:price` on the caller
at run start (402 `hub_price_unaffordable` with the amount before any step), settled on success,
released on any failure or stop. **What is held** (`_worst_case`): the most the maker can earn on
THIS run, derived, never declared: per_call, the price; percent, that percent of the largest provider
fee that fits under the caller's ceiling (fees + part ≤ ceiling, so part ≤ p/(1+p) of it);
per_result, the per-result price times the caller's `results_from` input (already clamped to its
`max`), else the declared cap. A `max_price_usd` lowers any of these. **What is settled:**
`_final_price(pricing, output, spent, held)` after a successful run: per_call, the price;
per_result, `results × per_result_usd`; percent, that percent of the run's provider fees (`spent`);
never above the hold nor a declared cap. A per_result run whose `results` is missing or not an
integer of 0 or more fails 424 `hub_units_invalid` (so the publish check rejects such a tool). The settle is the one cross-team money movement in treg:
`settle_to_in_transaction(db, call_id, payee_org_id, actual_micro=)` consumes the settled amount
from the caller's hold, refunds the rest of the hold to the caller, and in the same transaction
credits the maker's team with an `earned` block of the settled amount (a `settle` entry on the
payer naming the payee, a `grant` entry on the payee naming the payer's run); `actual_micro=None`
settles the full hold, which is the per_call case. The invariant holds on both teams at every instant.
No margin, no platform share in the MVP. Not charged when the caller is the maker (their own
runs, the check). `earned` spends after the free kinds and before purchased money
(`_KIND_ORDER`). Withdrawal is backlog. The earnings view is sales only (the maker's own runs
excluded), counts and amounts, never who called; `avg_price_micro` (earned ÷ successful runs, per
day and overall; `avg_price_usd` in the CSV) is how a maker sees where a variable price lands.
`treg hub price` sets one per-call number and normalizes the tool to `per_call`; a variable price is
set by publishing a version with a `pricing` block.

## The surfaces

- **The front door for agents:** `skill.md` and `llms.txt` carry a hub section inside
  `<!--hub-->…<!--/hub-->` blocks that `routers/web.py` strips while the flag is off (as it
  strips the routed-discovery blocks); `scripts/build_plugin.py` drops the block the same way and
  `--with-hub` keeps it at the final merge. `GET /catalog/endpoints/<id>` (behind `catalog_get`,
  `treg catalog get`) answers for a hub id with the public contract (`kind: "hub"`, summary,
  inputs, output, the price line (`price_range` leads: what recent successful runs cost, steps
  and seller price together, from `application/hub.price_ranges` over 30 days of every successful
  run, checks included, with `price_samples`; before any run the declared worst case plus
  "+ steps"; then `price_label`: the mode and the worst case; `cost.usd` is the
  worst case), health, version, `call_template`, the page URL, the readme);
  never the script, the maker's tools or a key. Search lists a hub tool only when its maker set
  `listed` (10.2): the newest live version is scored by `catalog_store.score_extra` with the catalog's
  own tokens, aliases, platform boost, idf and admission gate, then merged by score with no boost
  (`merge_by_score`, catalog rows first on a tie). The row is the public contract plus its 30-day ok
  rate from runs by others; unlisted, failed and retired never appear.
- **The public share page** `GET /hub/<id>` (and `.md`; `@N`): the contract for a person or an
  agent on the public stylesheet; the price as the mode and the worst case ("seller $X per unit,
  up to $Y per run"; the schema.org Offer carries the worst case); the RUN LOG when the maker left
  `public_log` on (10.3): the last 20 runs by others and runs per day over 30 days, each row time,
  outcome, ms, steps, `units` and the price paid, and never the caller, the inputs or the output;
  the check trace as shape only (never what each step called);
  "made of N tools (names and keys hidden)"; reliability over 30 days; older versions still
  callable; readable without sign-in; `noindex`, not in the sitemap.
- **The dashboard** (`frontend/`: `state/hub.js`, `pages/HubPage.vue`, `pages/HubRunPage.vue`):
  a Hub view for the maker (the list; a detail with Overview, Versions, Price, Listing, Earnings,
  Runs & log, Health; copy call line, copy share URL, retire) and the run page
  `/app/runs/<run_id>`, opened on load in both sign-in modes. The Hub entry shows when
  `/hub/tools/mine` answers for the active team, and is probed again on a team switch. Files are
  read-only in the dashboard: a new version comes from the terminal or the agent. The frozen
  legacy dashboard carries the same view until its retirement.
- **The CLI:** `treg hub init` scaffolds a `pricing` block (`per_call`, 0); `treg hub ls` shows the price
  label; `treg hub earnings` prints the average price per successful run; `treg hub list | unlist`
  and `treg hub log --public on|off` flip the two distribution switches (`HubTool.listed`,
  `HubTool.public_log`, migration 0046), which the dashboard's Listing tab also carries.

## The case study (2026-09-09) and what it taught

The owner and the assistant walked the maker's road by hand against a real Supabase table
(2,592 SEO rank-tracking rows in schema `hubdemo` of the owner's `krew-saas` project) on a local
server with the flag on: `treg hub init` → the four files written together → a run with no tool
registered (refused: `uses[0]`, the rule, the fix commands) → `treg secret add` + `treg tool add
supabase` with two bindings (`Authorization: Bearer` and `apikey`) → `treg hub run .` → `treg hub
publish .` (check passed, 0 µ$: the only step ran on the maker's own key) → a second team called
`unclecode-superdesign-dev.keyword-rankings` and got rows; neither the Supabase URL nor any key
material appeared in the reply. It found the compression rule and the header rule above, the
vendor-neutral `init` template, and the house-style CLI output. Two more walks are planned
before release: the owner by hand with the CSV recipe, and a Claude Code agent over MCP.

## Backlog (owner's order)

Templates for more data providers incl. `treg hub init --from <template>`; withdrawal; the
listing road (pull request + verification); a private-to-team switch; a platform fee; a script
editor in the dashboard; live polling of a run; billing compute; Crawl4AI as a built-in; a
benchmark/auditor; job queue, recipe-calls-recipe, retry, live progress, team sandbox.

## The security pass (8.1, 2026-09-10) and what it changed

Two reviewers plus hostile probes; the findings that matter for launch were fixed in one PR,
the rest went to the backlog. What is different since: the price hold closes on every path
(a guard around the whole run: a refusal or a disconnect releases it, a crash becomes 424
`hub_run_crashed`, never a 500 with an open hold); a step's answer is read into memory up to
1 MB and the stream is closed beyond it (`truncated` on the script's reply, a failed step on
the JSON road); the child's cost comes from the call record, never from an upstream
`X-Treg-Cost-Micro`; a child call never resolves to a hub tool (`CallInput.child_of`) and an
own tool named with a dot cannot be in `uses` (no nested runs); the caller's trace carries the
kind of tool and no upstream error body, on the 200 body, the 424 detail and the idempotent
replay; `GET /hub/tools/{id}` on another team's tool returns the public contract only; the
public page never prints the check's sample inputs; own-tool steps audit as
`hub-caller:<org>`; a version under check is visible only to its maker and a crashed check
leaves it `failed`; the wall clock keeps running during a bridge call and the JSON road has
one too; publish and the scheduled check hold no database connection during the run; the
ceiling header, `price_usd`, the run body (depth, `NaN`) and header names are validated to
4xx; server tracebacks never reach the maker's log; `..` is refused in a call path;
`data.csv` is capped at 5 MB. Backlog: the platform margin on the seller's price (margin is 0),
bridge state in writable globals, output-root validation at publish, concurrent publish 409,
slugs starting with `http`, the shortfall under-pay note, the 402 ceiling path's missing run row.
