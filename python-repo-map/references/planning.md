# From map to plan

Read this once the pack in `.repomap/` exists and the task shifts from mapping to
deciding. The map is evidence; this is how to argue from it.

## Order of operations

Doing these out of order is the usual way a big refactor stalls.

1. **Unwind import-time side effects** (`08-boundaries.md`). While importing a
   module does real work, you cannot move files without changing behaviour, and
   nothing else on this list is safe.
2. **Cut cycles** (`03-cycles.md`). Layering is impossible while cycles exist, so
   every other decision is provisional until they are gone.
3. **Name the target layers** and write them down as an enforced contract *before*
   moving any code (see below).
4. **Narrow interfaces** (`04-interfaces.md`), one module at a time, smallest
   surface first to build momentum.
5. **Move code** last. Moving files is the visible part and the least important;
   if steps 1–4 are done, moves are mechanical.

## Cutting a cycle

For each cycle, the pack lists every internal edge with the symbols it carries.
Ranked by how well they usually work:

- **The thin edge.** An edge carrying one or two names is the cheapest cut. Ask
  what that symbol *is*. A constant or a type usually belongs in a lower layer
  that both sides can depend on — extract it downward and the cycle vanishes with
  no indirection added.
- **Dependency inversion.** If the edge carries a function the lower layer calls
  back into, define the protocol in the lower layer and pass an implementation in
  from above. Costs one indirection, buys a real boundary.
- **Move the caller, not the callee.** Sometimes the function is simply in the
  wrong module and the cycle is telling you so directly.
- **Deferred import inside a function.** This is what the codebase probably
  already does. It hides the cycle from the import graph without removing the
  coupling. Do not add more of these; when the map shows one, treat it as a
  recorded cycle, not a solution.

## Judging an interface

`04-interfaces.md` ranks modules by how many distinct names outsiders reach for.
The useful readings:

- **Wide surface, many consumers** — a namespace pretending to be a module. Split
  it by consumer: usually two or three disjoint clusters of names exist, and each
  cluster is a real module.
- **Wide surface, one consumer** — the two are one module that got split for
  aesthetic reasons. Consider merging.
- **Narrow surface, many consumers** — already a good module. Leave it alone and
  use it as the pattern to argue from.
- **Consumers reaching past a package's `__init__` into its internals** — the
  package has no interface at all. Adding one is a mechanical, low-risk first PR
  that makes every later change smaller.

Deep modules mean the ratio of internal complexity to interface width is high. The
pack gives you both numbers: `loc` and `max_complexity` for the inside,
`n_used_names` for the outside. Modules with low loc and wide surfaces are the
shallow ones; they are the pass-through layers worth deleting entirely.

## Freeze the structure as an executable contract

With untrustworthy tests, boundary enforcement is the only regression signal you
actually have. Establish it *before* moving code, so the target architecture is
machine-checked from day one rather than aspirational.

Two options, both fine:

- **import-linter** (config in `.importlinter`): declare layers and forbidden
  contracts. Mature, pure Python, built on `grimp`.
- **tach** (config in `tach.toml`): declare modules, dependencies, and explicit
  public interfaces; `tach check` fails CI on violation, and dependencies can be
  marked deprecated so existing violations are surfaced without blocking.

Recommended sequence either way: generate the config from the *current* state so
it passes immediately, commit that as the baseline, then tighten one contract per
PR. The baseline is a ratchet — it cannot get worse while you work — and each
tightening is a small reviewable change instead of one enormous restructuring
commit.

## Writing the plan itself

Structure the output as a sequence of independently mergeable steps, each with:

- **What changes** — files moved, symbols relocated, edges removed.
- **Why, with evidence** — cite the artifact and the number. "`app.core.models`
  has 31 names reaching across the boundary from 12 modules (`04-interfaces.md`)"
  is an argument; "this module is doing too much" is not.
- **Blast radius** — the reverse-dependency set from `02-graph.json`, plus the
  co-change partners from `07-git.md`, which catch the files that break without
  an import edge to explain it.
- **How it is verified** — which characterization test, which boundary contract,
  which entry point re-run.
- **What it unblocks** — makes the ordering argument explicit and shows the user
  why an unglamorous first step is worth doing.

Keep steps small enough to merge in a day. A refactor plan that requires a
long-lived branch has already failed; the map exists precisely so the work can be
decomposed into changes that land continuously.

## Honest limits to state in the plan

Say these out loud rather than letting the pack imply more confidence than it has:

- The import graph resolves static imports only; `string_edges` is a heuristic.
- Coverage evidence is bounded by which entry points were actually run.
- Co-change confidence degrades with a short or heavily-rewritten history, and
  bulk reformatting commits distort it (the analysis ignores commits touching more
  than 25 files for this reason).
- Complexity here is a decision-point count, not a measured hotspot. It ranks; it
  does not diagnose.
