# Candidate backlog

Generated from the evidence pack. Every item is a **candidate**: reject the
ones that do not survive contact with the code, merge overlapping ones, and
fill in the verification for the ones you keep. An item with no verification
is not ready to work on.

Phases are a dependency order, not a preference. Items in a later phase
often become unnecessary once earlier ones land, so re-run the map rather
than working the whole list top to bottom.

| phase | items | total loc in scope |
|---|---|---|
| 0. Delete what is provably unused | 3 | 5 |
| 1. Unwind import-time side effects | 1 | 26 |
| 2. Cut import cycles | 1 | 39 |
| 3. Extract duplicated implicit contracts | 1 | 2 |
| 4. Narrow module interfaces | 2 | 35 |
| 5. Separate I/O from logic | 1 | 26 |

## Phase 0 — Delete what is provably unused

### RM-001 — Delete `app.core.schema_v1`

- **Evidence**: never executed under any recorded entry point (09-runtime.md)
  - imported by nothing (02-graph.json reverse)
  - 1 loc at src/app/core/schema_v1.py
- **Blast radius**: 0 modules
- **Caveat**: Grep the symbol name as a plain string across config, templates and YAML before deleting; dynamic reachability does not show up in either the graph or coverage of the entry points you ran.
- **Verify**: _fill in — which characterization test, which boundary contract, which entry point re-run_

### RM-002 — Delete `app.util.orphan`

- **Evidence**: never executed under any recorded entry point (09-runtime.md)
  - imported by nothing (02-graph.json reverse)
  - 3 loc at src/app/util/orphan.py
- **Blast radius**: 0 modules
- **Caveat**: Grep the symbol name as a plain string across config, templates and YAML before deleting; dynamic reachability does not show up in either the graph or coverage of the entry points you ran.
- **Verify**: _fill in — which characterization test, which boundary contract, which entry point re-run_

### RM-003 — Delete `app.web.form_v1`

- **Evidence**: never executed under any recorded entry point (09-runtime.md)
  - imported by nothing (02-graph.json reverse)
  - 1 loc at src/app/web/form_v1.py
- **Blast radius**: 0 modules
- **Caveat**: Grep the symbol name as a plain string across config, templates and YAML before deleting; dynamic reachability does not show up in either the graph or coverage of the entry points you ran.
- **Verify**: _fill in — which characterization test, which boundary contract, which entry point re-run_

## Phase 1 — Unwind import-time side effects

### RM-004 — Make `app.core.models` safe to import

- **Evidence**: 1 module-level statements run at import time
  - L7: os.environ.get (module-level call assigned at import)
- **Blast radius**: 6 modules — `app`, `app.core.service`, `app.util.helpers`, `app.web.render`, `app.web.views`, `test_models`
- **Caveat**: Until this is done, moving this module changes behaviour in ways no test will report.
- **Verify**: _fill in — which characterization test, which boundary contract, which entry point re-run_

## Phase 2 — Cut import cycles

### RM-005 — Break cycle: app.core.models <-> app.util.helpers <-> app.web.render

- **Evidence**: 3 modules, 3 internal edges
  - thinnest edge `app.core.models` -> `app.web.render` carries 1 name(s): render_user (L4)
- **Blast radius**: 4 modules — `app`, `app.core.service`, `app.web.views`, `test_models`
- **Option**: Move the thin edge's symbol down into a leaf both sides can depend on (cheapest; adds no indirection)
- **Option**: Invert the dependency: define the protocol in the lower layer, inject the implementation from above
- **Option**: Move the caller rather than the callee — the cycle may simply be telling you the function is in the wrong module
- **Caveat**: A deferred import inside a function hides the cycle without removing the coupling. Do not count that as done.
- **Unblocks**: RM-007, RM-008
- **Verify**: _fill in — which characterization test, which boundary contract, which entry point re-run_

## Phase 3 — Extract duplicated implicit contracts

### RM-006 — Reconcile `src/app/core/schema_v1.py` and `src/app/web/form_v1.py`

- **Evidence**: changed together in 6 commits (confidence 1.0) with no import between them
- **Blast radius**: 0 modules
- **Caveat**: Find the shared thing first — a field list, a wire format, a magic string, an ordering assumption. If you cannot name it, this pair is coincidence and the item should be dropped.
- **Verify**: _fill in — which characterization test, which boundary contract, which entry point re-run_

## Phase 4 — Narrow module interfaces

### RM-007 — Narrow the surface of `app.core.models`

- **Evidence**: 3 distinct names reached for by 3 modules
  - declares __all__: no
  - 26 loc, fan-out 1
  - exposed names fall into 2 clusters sharing no consumer (sizes 2, 1) — no importer needs the whole surface
- **Blast radius**: 3 modules — `app`, `app.core.service`, `app.util.helpers`
- **Consumer clusters** (disjoint — each is a candidate module):
  1. `User`, `load_user`
  2. `DEFAULT_ROLE`
- **Option**: Split along the consumer clusters listed in `clusters`; they are disjoint, so the split breaks nothing that currently works
- **Option**: Add an explicit `__all__` / package `__init__` interface first, then move consumers onto it one at a time
- **Blocked by**: RM-005
- **Verify**: _fill in — which characterization test, which boundary contract, which entry point re-run_

### RM-008 — Narrow the surface of `app.util.helpers`

- **Evidence**: 2 distinct names reached for by 2 modules
  - declares __all__: no
  - 9 loc, fan-out 1
- **Blast radius**: 2 modules — `app.core.service`, `app.web.render`
- **Option**: Add an explicit `__all__` / package `__init__` interface first, then move consumers onto it one at a time
- **Blocked by**: RM-005
- **Verify**: _fill in — which characterization test, which boundary contract, which entry point re-run_

## Phase 5 — Separate I/O from logic

### RM-009 — Separate I/O from logic in `app.core.models`

- **Evidence**: touches 3 boundary categories: clock, database, env
  - 26 loc, max function complexity 5
- **Blast radius**: 4 modules — `app`, `app.core.service`, `app.util.helpers`, `test_models`
- **Option**: Push the I/O to the edge and leave a pure core that can be exercised without the network, the clock or the database — this is what makes the module testable without trusting the existing suite
- **Verify**: _fill in — which characterization test, which boundary contract, which entry point re-run_

