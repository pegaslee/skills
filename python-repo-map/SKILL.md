---
name: python-repo-map
description: Map the real structure of a messy Python codebase into a text context pack (import graph, cycles, de facto module interfaces, symbol index, git co-change coupling, I/O boundaries, runtime evidence) that an LLM can reason over to plan a refactor. Use this whenever the user wants to understand, audit, document, diagram, or map a Python repo's structure or architecture; whenever they mention planning or scoping a refactor, reorganising modules, fixing circular imports, defining interfaces, splitting a monolith, deep modules, coupling, or untangling legacy code; and whenever they say the codebase is a mess or that they can't trust the tests. Use it even if they only asked for a diagram or a dependency graph — they almost always need the text artifacts more than the picture.
---

# Python repo map

Build an evidence pack about a Python codebase, then reason from it. The pack is
plain text — JSON, JSONL, markdown tables, DOT — because a rendered graph is for
the human and text is what an LLM can actually quote, diff and cite.

The scripts here are stdlib-only. They need no install, no virtualenv, no network,
and they never import the target code — everything is AST-level, so a repo that
does not even run can still be mapped.

## When the tests cannot be trusted

Assume that by default; it is why this skill exists. It changes the approach in
two concrete ways, and both should show up in whatever you produce:

- **Static evidence is not enough.** Get runtime evidence from real entry points
  before drawing conclusions about what is dead or unreachable
  (`references/runtime-evidence.md`).
- **Boundary enforcement replaces the test suite as the safety net.** Freeze the
  target structure as a machine-checked contract *before* moving code, not after
  (`references/planning.md`).

## Workflow

### 1. Orient before running anything

Find the repo root and the source layout. Check for `src/`, a top-level package,
`pyproject.toml`, and whether it is a monorepo with several packages. Look at
whether `tests/` exists and roughly how much of it there is. If the layout is
ambiguous, ask rather than guess — a wrong source root produces a plausible and
completely wrong map, which is worse than no map.

Ask about scope only if it is genuinely unclear: which package to map if there are
several, and whether generated or vendored directories should be excluded.

### 2. Build the pack

```bash
python scripts/map_repo.py /path/to/repo
```

Writes to `<repo>/.repomap/`. Useful flags:

| flag | when |
|---|---|
| `--source-root src --source-root plugins` | layout the auto-detection gets wrong, or a monorepo |
| `--package-depth 3` | deeply nested packages, where depth-2 squashing hides the structure |
| `--out /tmp/repomap` | do not want to write inside the user's repo |
| `--top 120` | large repo where the default 60 rows per table truncates too much |
| `--since 3.years.ago` | short recent history, or a repo that was migrated between VCSs |
| `--skip-git` | no git, or history that is one squashed import commit |
| `--include-tests` | mapping the test suite's own structure deliberately |
| `--coverage-json coverage.json` | after collecting runtime evidence (step 4) |

Individual scripts run standalone too, all with `--help`: `build_graph.py`,
`symbol_index.py`, `interfaces.py`, `git_signals.py`, `boundaries.py`,
`runtime.py`.

If `.repomap/` is going inside the user's repo, offer to add it to
`.gitignore` — or to commit it deliberately, which some teams want so the map is
reviewable.

### 3. Read the pack yourself, then report

Read `00-OVERVIEW.md` first, then `03-cycles.md`, `04-interfaces.md`, and
`07-git.md`. Do not dump the artifacts back at the user — summarise what the
evidence says, with numbers, and name the two or three structural problems that
actually explain the mess.

Sanity-check before believing any of it. If the module count or total loc is far
off what the repo obviously contains, the source root is wrong. If there are zero
edges, the package layout was not detected. Say so and fix it rather than
reporting a confident empty map.

### 4. Add runtime evidence

Static analysis is confidently wrong about registries, `getattr` dispatch and
dynamic imports. The overview flags string literals that look like module paths as
a hint, but only running the code settles it. Read
`references/runtime-evidence.md` and help the user collect coverage per entry
point, then rerun with `--coverage-json`.

Do this before proposing any deletion. "Nothing imports it" is not evidence that
nothing calls it.

### 5. Generate the candidate backlog

```bash
python scripts/plan_scaffold.py /path/to/repo/.repomap
```

Writes `10-BACKLOG.md`: ranked, dependency-ordered candidate work items with the
blast radius already computed and the evidence attached to each one. Flags
`--min-surface` (default 8 names before a module is worth narrowing) and
`--min-io-categories` (default 3).

This is the artifact that makes the pack actionable. Without it an agent reads the
evidence and invents priorities; with it, the agent's job is the much more
reliable one of judging candidates it did not have to find.

Items are candidates, not decisions. Expect to reject some — the scaffold marks
the ones it suspects are wrong, such as types and exceptions modules whose wide
surface is entirely normal.

### 6. Turn it into a plan

Read `references/agent-handoff.md` for the prompts that drive an agent from the
backlog to a merged plan, and `references/planning.md` for the reasoning behind
the sequencing. The short version: unwind import-time side effects, cut cycles,
declare the target layers as an enforced contract, narrow interfaces, move files
last — and re-run the map after each step, because later items routinely become
unnecessary once earlier ones land.

## What is in the pack

| file | what it is for |
|---|---|
| `00-OVERVIEW.md` | scale, where the code lives, widest interfaces, third-party surface, contents |
| `01-modules.md` | inventory with fan-in, fan-out, loc, complexity, docstring purpose |
| `02-graph.json` | `edges` (with the symbol names and line numbers on each edge), `reverse`, `package_edges`, `cycles`, `string_edges`, `external` |
| `02-packages.dot` | package graph; cycle members shaded, back-edges red |
| `03-cycles.md` | every cycle with its internal edges as candidate cut points |
| `04-interfaces.md` / `.json` | de facto public surface per module and package, and names nobody outside uses |
| `05-symbols.jsonl` | one record per module/class/function: signature, decorators, bases, complexity, line span |
| `07-git.md` / `.json` | churn x complexity ranking, and co-change pairs with **no** import edge |
| `08-boundaries.md` / `.json` | I/O touchpoints by category, and import-time side effects |
| `09-runtime.md` / `.json` | per-entry-point module reach, dead code candidates (needs step 4) |
| `10-BACKLOG.md` / `10-backlog.json` | ranked candidate work items, phased, with blast radius and blockers |

Three of these do work the obvious tools do not, so lean on them:

**De facto interfaces** (`04-interfaces.md`). Computed from which names other
modules actually reach for, not from `__all__`. This is the contract the codebase
is really committed to, and its width per module is the single most useful number
for deciding what to refactor first. A module exposing forty names to twelve
consumers is a namespace, not a module.

**Hidden coupling** (`07-git.md`). Files that change together but have no import
between them. Each pair is a duplicated implicit contract, a missing abstraction,
or a boundary in the wrong place — and none of it appears in any dependency graph.
Where co-change disagrees with the directory layout, trust the co-change.

**Import-time side effects** (`08-boundaries.md`). Module-level work that runs on
import. This is the specific reason reorganisations break in ways tests do not
catch, so it gets unwound first.

## Handing the pack to another model or session

When the point is to feed a fresh context, give it: `00-OVERVIEW.md` whole,
`03-cycles.md` whole, the top of `04-interfaces.md`, and `02-graph.json` filtered
to the packages in scope. Add `05-symbols.jsonl` filtered to the modules being
touched — the whole file is usually too large and mostly irrelevant to any one
task.

For a large repo, filter rather than truncate. `jq` over `02-graph.json` to pull
one package's subgraph plus its reverse dependencies gives a complete small map,
which beats an arbitrary prefix of a complete large one.

## Guardrails

- **Read-only.** These scripts only read the repo and write to `.repomap/`. Do not
  start editing code because the map suggested something; mapping and refactoring
  are separate tasks with separate approval.
- **Cite the artifact and the number** when making a structural claim. "`core.db`
  is imported by 34 modules and exposes 22 names (`04-interfaces.md`)" is an
  argument. "This module is doing too much" is an opinion.
- **Do not propose deletions from static evidence alone.** Runtime evidence, then
  a grep for the name as a plain string in config, templates and YAML, then
  delete.
- **State the limits.** The graph resolves static imports only; `string_edges` is
  a heuristic; coverage is bounded by which entry points were run; co-change needs
  real history; the complexity number is a decision-point count that ranks but
  does not diagnose.
- **Never install into the target project's environment** without asking. The
  bundled scripts exist so you do not have to.

If you have no shell access to the repo — a chat with no filesystem, for
instance — do not improvise a partial answer from pasted snippets. Give the user
the commands to run and offer to interpret the pack when they paste
`00-OVERVIEW.md` back.

## Reference files

- `references/runtime-evidence.md` — collecting coverage per entry point, call
  tracing, characterization tests, and what to do when static and runtime evidence
  disagree. Read before step 4 or any deletion.
- `references/planning.md` — sequencing, cycle-cutting techniques, how to judge an
  interface, and setting up import-linter or tach as a ratchet. Read before writing
  a plan.
- `references/agent-handoff.md` — the prompts for triaging the backlog, writing the
  plan, setting up the enforcement ratchet, and executing one step. Read when the
  task is to produce or drive a refactor plan rather than just a map.
- `references/example-backlog.md` — a generated `10-BACKLOG.md` from a small
  example repo, showing the shape of every item kind. Read when you want to know
  what step 5 produces before running it.
- `references/optional-tools.md` — `ruff analyze graph`, `grimp`, `tach`,
  `pyreverse`, `pydeps`, `scip-python`, `vulture`, `radon`, `deptry`. Read when the
  stdlib scripts hit a specific limit, or when the user asks for a picture.
