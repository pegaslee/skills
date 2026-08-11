# Handing the pack to an agent

The pack is evidence and `10-BACKLOG.md` is a ranked set of candidates. Neither is
a plan. This is how to get from there to something a team can actually merge.

The core move is to stop the agent discovering priorities from vibes. It already
has the ranked candidates, the blast radius and the evidence; its job is to
*judge* — reject what does not survive contact with the code, merge overlapping
items, decide the cut, and specify verification. That is a much smaller and much
more reliable job than "read this codebase and tell me how to refactor it".

## Prompt 1 — triage the backlog

Give the agent repo access plus the pack. Paste this:

> You have a codebase map in `.repomap/`. Read `00-OVERVIEW.md`, then
> `10-BACKLOG.md`, then whichever of `03-cycles.md`, `04-interfaces.md`,
> `07-git.md`, `08-boundaries.md` and `09-runtime.md` the items you care about
> cite.
>
> The backlog is candidates, not decisions. For each item, open the actual source
> it names and decide: **keep**, **merge into another item**, or **reject**. Say
> why in one line, citing what you found in the code — not what the backlog said.
> Reject anything you cannot justify from the source.
>
> Do not change any code yet. Do not propose new architecture yet. Output a table
> of item IDs with your verdict and reason, and flag anything in the code that the
> map missed entirely — dynamic dispatch, registries, config-driven imports,
> anything that would break a reorganisation silently.

Triage first is deliberate. It forces the agent through the real source before it
has committed to a narrative, and the "what did the map miss" question reliably
surfaces the dynamic-dispatch landmines that no static tool found.

## Prompt 2 — the plan

> Using the items you kept, write a refactor plan as a sequence of independently
> mergeable steps.
>
> Rules:
> - Each step must be small enough to review and merge in a day. If it is not,
>   split it.
> - Each step must be mergeable on its own — the codebase works after every step,
>   and no step depends on a later one.
> - Every structural claim cites an artifact and a number. "`core.db` is imported
>   by 34 modules and exposes 22 names (04-interfaces.md)" is an argument. "This
>   module does too much" is not. Drop any claim you cannot cite.
> - Every step names how it is verified without relying on the existing test
>   suite, which we do not trust: a characterization test at an outer boundary, an
>   enforced boundary contract, or a re-run of a specific entry point compared
>   against captured output.
> - No deletion is justified by static evidence alone.
>
> For each step give: **What changes** (files, symbols, edges) / **Why** (cited
> evidence) / **Blast radius** (from `02-graph.json` reverse deps plus co-change
> partners in `07-git.md`) / **Verification** / **What it unblocks**.
>
> Order the steps. Then state, in a short section at the end, what you are least
> confident about and what evidence would settle it.

The last instruction matters more than it looks. Without it you get uniform
confidence across steps that deserve very different amounts of it.

## Prompt 3 — set the ratchet before touching anything

> Before we move any code, generate a boundary contract that captures the
> *current* structure so it passes today — an `.importlinter` config or a
> `tach.toml`, whichever suits this repo. Commit that as the baseline and wire it
> into CI.
>
> Then, for each step in the plan, say which contract line it tightens. A step
> that does not tighten anything is either not structural or not specified yet.

This is the substitute for a trustworthy test suite. The baseline is a ratchet:
structure cannot get worse while the work happens, and each step becomes a small
reviewable diff to the contract rather than a large one to the architecture.

## Prompt 4 — execute one step

> Implement step N only. Do not touch anything the step does not name.
>
> Before you start, capture the current behaviour of the entry points in the
> step's blast radius as characterization fixtures. These assert what the code
> currently does, bugs included — that is intended; behaviour changes come later
> as separate visible commits.
>
> After the change: re-run those entry points and diff, run the boundary contract
> check, and re-run `python scripts/map_repo.py .` and
> `python scripts/plan_scaffold.py .repomap`. Report what changed in the map —
> cycles closed, surfaces narrowed, items that disappeared.

Re-running the map after each step is what keeps the plan honest. Later items
routinely become unnecessary once earlier ones land, and a plan that is not
re-derived from the current state slowly becomes fiction.

## What good output looks like

Bad, and common:

> **Step 3: Refactor the core module.** `core.py` has grown too large and handles
> too many responsibilities. We should split it into focused modules following
> single responsibility principle, improving maintainability and testability.

Nothing here is checkable, the size is unbounded, and no evidence is cited.

Good:

> **Step 3: Split `app.core.models` along its consumer clusters.**
> *What*: move `User` and `load_user` to `app.core.user`; leave `DEFAULT_ROLE` in
> a new `app.core.constants`. Update 3 import sites.
> *Why*: 3 names reached for by 3 modules, falling into 2 clusters that share no
> consumer (`10-BACKLOG.md` RM-007) — no importer needs both halves, so the split
> breaks nothing that currently works. Also removes the `app.util.helpers` ->
> `app.core.models` edge that closes the cycle in `03-cycles.md`.
> *Blast radius*: `app`, `app.core.service`, `app.util.helpers` (02-graph.json
> reverse); no co-change partners outside that set (07-git.md).
> *Verification*: `tach check` — the `core` -> `util` contract line goes from
> deprecated to forbidden; plus the `cli-checkout` entry point re-run diffs clean
> against the captured fixture.
> *Unblocks*: RM-008, and the layering in step 5.

## Guardrails to state to the agent

- Mapping and refactoring are separate tasks with separate approval. An agent that
  read the map should not start editing on the strength of it.
- The map resolves static imports only. `string_edges` in `02-graph.json` is a
  heuristic. Coverage is bounded by which entry points were actually run.
- If the agent cannot cite an artifact for a claim, the claim gets cut rather than
  softened. Hedged unsupported claims are worse than absent ones, because they
  survive review.
