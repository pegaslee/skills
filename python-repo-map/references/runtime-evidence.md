# Runtime evidence

Read this when the static map is built and you need to know what the code
*actually* does — especially when the test suite is not trustworthy, which is the
normal case in a codebase being untangled.

Static analysis is confidently wrong about messy Python. Plugin registries,
`getattr` dispatch, `importlib.import_module` on a config string, Django/Celery
autodiscovery, entry-point plugins, `__init_subclass__` hooks, dependency
injection containers — all invisible to the import graph. The scripts flag string
literals that look like module paths (`string_edges` in `02-graph.json`) as a
hint, but a hint is all it is.

## 1. Coverage from real entry points, not from the test suite

The point is not coverage percentage. The point is: which modules does each real
use case touch? Run the application the way users run it, one context per
workflow.

```bash
coverage run --context=cli-checkout   --source=src -m yourapp.cli checkout --dry-run
coverage run --context=cli-report     --source=src --append -m yourapp.cli report
coverage run --context=nightly-job    --source=src --append -m yourapp.jobs.nightly
coverage run --context=web-smoke      --source=src --append -m yourapp.wsgi &  # then hit some URLs
coverage json --show-contexts -o coverage.json
```

Then feed it in:

```bash
python scripts/map_repo.py . --coverage-json coverage.json
# or just the runtime slice:
python scripts/runtime.py coverage.json --graph .repomap/02-graph.json -o .repomap/09-runtime.md
```

What you get, in order of value:

1. **Dead code candidates** — never executed under any entry point *and* imported
   by nothing. Before deleting, grep the name as a plain string across the repo
   including config, templates and YAML; that catches dynamic reachability.
2. **Per-context module sets** — the real feature boundaries. If two contexts
   touch almost disjoint module sets, that is a seam the directory layout may not
   reflect. If every context touches the same core module, that module is the
   bottleneck the refactor exists to fix.
3. **Modules loaded but with zero executed lines** — imported for side effects
   only. These usually mark registry patterns and import-time coupling.

Caveat: absence of coverage is weak evidence unless your entry points really
cover the product. Note explicitly in the plan which workflows were *not*
exercised, so nothing gets deleted on the strength of a gap in the sampling.

## 2. Call traces when you need ordering, not just reach

Coverage tells you *what* ran, not in what order or from where. When you need the
actual call structure of one workflow:

- `viztracer -o trace.json -- python -m yourapp.cli checkout` then
  `vizviewer trace.json`. Good for one deep workflow; the output is large.
- `py-spy dump --pid <pid>` or `py-spy record` against a running process. Zero
  instrumentation, works in production, sampling so it misses fast paths.
- `sys.monitoring` (3.12+) for a hand-rolled collector when you want something
  narrow, like "every cross-package call" — filter by module prefix and record
  caller/callee pairs. That produces a real call graph across the boundaries you
  care about, at a fraction of the noise of a full trace.

## 3. Characterization tests before moving anything

Since the existing tests do not tell you what the behaviour *should* be, generate
tests that assert what it currently *is*. They are not a statement of intent;
they are a tripwire.

Practical approach: pick each entry point exercised above, capture its
input/output as fixtures, and assert byte equality. Prefer the outermost boundary
you can reach — CLI stdout, HTTP response bodies, the rows written to a database
— because assertions there survive the reorganisation, whereas assertions on
internal functions are exactly what you are about to break.

Where output is non-deterministic (timestamps, UUIDs, dict ordering, floats),
normalise it in the assertion rather than pinning the implementation.

Be explicit with the user that these tests encode existing bugs. That is
intended: a refactor should preserve behaviour including the bugs, and the bugs
get fixed as separate, visible changes afterwards.

## 4. What to do when evidence conflicts

- **Import edge but never executed** → probably a dead branch or a legacy path.
  Candidate for deletion, but only after grepping for dynamic use.
- **Executed but no import edge** → dynamic dispatch. Find it and write it down
  in the plan; these are the changes that break silently.
- **Co-changes in git but no import edge and no shared runtime path** → a
  duplicated implicit contract, e.g. two places that both know a field list or a
  wire format. Prime candidate for extraction into one owned definition.
