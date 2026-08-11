# Optional tools

The bundled scripts are stdlib-only on purpose: they run on a repo whose
dependencies you cannot install, in a sandbox with no network, without touching
the target project's environment. Reach for these when a specific weakness of the
stdlib approach matters. Check availability before suggesting any of them, and
never install into the target project's environment without asking.

## Better import resolution

**`ruff analyze graph`** — if ruff is already available, this is a stronger
resolver than the bundled one: it is PEP 561-aware, understands namespace
packages, and handles `src` layouts without configuration. Emits a JSON map of
file to imported files. `--direction=dependents` gives the reverse graph directly.
Settings worth knowing: `analyze.detect-string-imports` catches dynamic imports
written as strings, and `analyze.include-dependencies` lets you declare edges the
resolver cannot see (a module reading a config file, for instance).

Use it to cross-check: diff its edge set against `02-graph.json`. Edges only ruff
finds are resolution gaps; edges only the bundled script finds are usually
relative-import or string-literal cases. Either way the disagreement is
informative and worth reporting.

**`grimp`** — the graph library underneath import-linter. Worth installing when
you need to interrogate the graph rather than dump it:
`find_shortest_chains(importer, imported)` answers "*why* does A depend on B" with
the actual chain, `find_illegal_dependencies_for_layers` tests a proposed layering
before you commit to it, and it can squash a package to a single node so you can
reason at the level you care about. The bundled `build_graph.py` exposes
`shortest_chain()` for the same purpose if installing is not an option.

## Enforcement

**`import-linter`** and **`tach`** — see `references/planning.md`. Note that tach
was unmaintained through much of 2025 and is now active again under the
`tach-org` organisation; a fork called `dtach` existed during the gap and has
since been archived back into upstream. If a repo already has a `tach.toml`, keep
using tach; otherwise either tool is a reasonable choice.

## Visuals for the human

The pack is deliberately text-only, because rendered graphs are for people and
text is for models. When the user wants something to look at:

- `dot -Tsvg .repomap/02-packages.dot -o packages.svg` — the bundled DOT file
  already colours cycle members and back-edges red.
- `pyreverse -o mmd -p myproj mypackage` (ships with pylint) — class and
  inheritance diagrams, and it can emit Mermaid, which renders in most places
  without GraphViz installed.
- `pydeps --max-bacon 3 --cluster mypackage` — good-looking module graphs, though
  it wants GraphViz and gets unreadable above a few hundred nodes.

Above roughly 150 nodes, no layout engine produces a legible picture. Filter to
one package, or stick to the tables.

## Type-aware cross-references

The bundled symbol index does not resolve *call* relationships, only imports —
because name-matching call graphs in Python produce enough false edges to be
misleading, which is worse than absent. When you genuinely need call-level truth:

- `scip-python` produces a SCIP index with real cross-references, resolved with
  type information.
- `pyright --outputjson` on a hover/definition query, or a language server driven
  programmatically, answers "where is this actually defined and used".
- `code2flow` and `pyan3` are quick and AST-based. Treat their output as hints to
  verify, never as evidence to cite in a plan.

## Complementary metrics

- `vulture` — dead code candidates. Noisy alone; strong when intersected with the
  runtime evidence in `09-runtime.md`.
- `radon cc` / `radon mi` — real cyclomatic complexity and maintainability index
  if you want defensible numbers instead of the bundled decision-point proxy.
- `deptry` — declared dependencies that are unused, and imports that are used but
  undeclared. Directly relevant when splitting a monolith into packages.
- `cohesion` — LCOM per class, which is the number to reach for when arguing that
  a god-class contains several unrelated objects.
- `ast-grep` — structural search when you need to find every instance of a
  pattern (all `getattr` dispatch sites, say) rather than every name.
