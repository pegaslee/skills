"""Assemble the whole context pack in one shot.

    python scripts/map_repo.py /path/to/repo --out /path/to/repo/.repomap

Everything is stdlib-only, so this works on a repo whose dependencies you cannot
or do not want to install. Skips git analysis gracefully if there is no history.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import boundaries  # noqa: E402
import build_graph  # noqa: E402
import interfaces  # noqa: E402
import symbol_index  # noqa: E402


def render_modules_md(graph: dict, top: int) -> str:
    mods = graph["modules"]
    edges = graph["edges"]
    reverse = graph["reverse"]
    L = ["# Module inventory", ""]
    L.append(f"{len(mods)} modules across source roots: "
             + ", ".join(f"`{r}`" for r in graph["source_roots"]))
    L.append("")
    L.append("Fan-in is how many modules import this one; fan-out is how many it imports.")
    L.append("High fan-in plus high fan-out means a module in the middle of everything —")
    L.append("those are the ones a refactor has to break apart or freeze first.")
    L.append("")
    L.append("| module | loc | fan-in | fan-out | max fn cx | purpose |")
    L.append("|---|---|---|---|---|---|")
    ranked = sorted(mods.items(),
                    key=lambda kv: -(len(reverse.get(kv[0], [])) + len(edges.get(kv[0], {}))))
    for mod, meta in ranked[:top]:
        if meta.get("is_test"):
            continue
        L.append(f"| `{mod}` | {meta['loc']} | {len(reverse.get(mod, []))} | "
                 f"{len(edges.get(mod, {}))} | {meta.get('max_complexity', 0)} | "
                 f"{(meta.get('doc') or '--')} |")
    L.append("")
    L.append("## Largest modules")
    L.append("")
    for mod, meta in sorted(mods.items(), key=lambda kv: -kv[1]["loc"])[:top]:
        L.append(f"- `{mod}` — {meta['loc']} loc — {meta['path']}")
    return "\n".join(L) + "\n"


def render_cycles_md(graph: dict) -> str:
    L = ["# Cycles", ""]
    L.append("Import cycles are the hard constraint on reorganisation: nothing inside a")
    L.append("cycle can be moved independently, so every cycle has to be cut before the")
    L.append("modules in it can be layered. For each one, the listed edges are the")
    L.append("candidate cut points — look for the edge carrying the fewest names.")
    L.append("")

    L.append(f"## Package-level cycles ({len(graph.get('package_cycles', []))})")
    L.append("")
    for comp in graph.get("package_cycles", []):
        L.append(f"- cycle among {len(comp)}: " + ", ".join(f"`{c}`" for c in comp))
    if not graph.get("package_cycles"):
        L.append("_none — package layering is already acyclic_")

    L.append("")
    L.append(f"## Module-level cycles ({len(graph.get('cycles', []))})")
    L.append("")
    for i, comp in enumerate(graph.get("cycles", []), 1):
        L.append(f"### Cycle {i} — {len(comp)} modules")
        L.append("")
        for mod in comp:
            L.append(f"- `{mod}`")
        L.append("")
        members = set(comp)
        inner = []
        for src in comp:
            for tgt, info in graph["edges"].get(src, {}).items():
                if tgt in members:
                    inner.append((len(info["names"]), src, tgt, info))
        inner.sort()
        L.append(f"Edges inside this cycle ({len(inner)}), thinnest first — the top of this")
        L.append("list is where the cycle is cheapest to cut:")
        L.append("")
        cap = 40
        for _n, src, tgt, info in inner[:cap]:
            names = ", ".join(f"`{n}`" for n in info["names"][:8])
            more = f" +{len(info['names']) - 8}" if len(info["names"]) > 8 else ""
            L.append(f"- `{src}` -> `{tgt}` (L{info['lines'][0]}): {names}{more}")
        if len(inner) > cap:
            L.append(f"- _...{len(inner) - cap} more edges; see `02-graph.json`_")
        L.append("")
    if not graph.get("cycles"):
        L.append("_none_")
    return "\n".join(L) + "\n"


def render_dot(graph: dict) -> str:
    lines = ["digraph packages {", '  rankdir=LR;', '  node [shape=box, fontname="Helvetica"];']
    cyc_nodes = {n for comp in graph.get("package_cycles", []) for n in comp}
    for node in sorted(set(graph["package_edges"]) |
                       {v for vs in graph["package_edges"].values() for v in vs}):
        attrs = ' style=filled fillcolor="#ffdddd"' if node in cyc_nodes else ""
        lines.append(f'  "{node}" [label="{node}"{attrs}];')
    for src, tgts in sorted(graph["package_edges"].items()):
        for tgt in tgts:
            back = tgt in graph["package_edges"] and src in graph["package_edges"][tgt]
            style = ' [color=red penwidth=2]' if back else ""
            lines.append(f'  "{src}" -> "{tgt}"{style};')
    lines.append("}")
    return "\n".join(lines) + "\n"


def render_overview(graph: dict, symbols: list[dict], iface: dict, extras: list[str]) -> str:
    mods = {m: v for m, v in graph["modules"].items() if not v.get("is_test")}
    tests = len(graph["modules"]) - len(mods)
    total_loc = sum(v["loc"] for v in mods.values())
    n_classes = sum(1 for r in symbols if r["kind"] == "class")
    n_funcs = sum(1 for r in symbols if r["kind"] == "function")
    stdlib = getattr(sys, "stdlib_module_names", frozenset())
    ext, std = Counter(), Counter()
    for _m, deps in graph["external"].items():
        for d in deps:
            (std if d in stdlib or d == "__future__" else ext)[d] += 1

    pkg_loc: dict[str, int] = defaultdict(int)
    depth = graph["package_depth"]
    for m, v in mods.items():
        parts = m.split(".")
        pkg_loc[".".join(parts[:depth]) if len(parts) > depth else m] += v["loc"]

    L = ["# Codebase map", ""]
    L.append("Generated by the `python-repo-map` skill. Read this file first, then open the")
    L.append("numbered files below as needed. Nothing here is a rendered picture; every")
    L.append("artifact is text you can quote, diff and reason over.")
    L.append("")
    L.append("## Scale")
    L.append("")
    L.append(f"- {len(mods)} source modules ({tests} test modules excluded), {total_loc:,} loc")
    L.append(f"- {n_classes} top-level classes, {n_funcs} top-level functions")
    L.append(f"- {sum(len(v) for v in graph['edges'].values())} first-party import edges")
    L.append(f"- {len(graph.get('cycles', []))} module cycles, "
             f"{len(graph.get('package_cycles', []))} package cycles")
    if graph.get("depth_note"):
        L.append(f"- {graph['depth_note']}")
    if graph.get("string_edges"):
        L.append(f"- {sum(len(v) for v in graph['string_edges'].values())} *possible* "
                 "dynamic imports found as string literals (see `02-graph.json` -> `string_edges`)")
    L.append("")
    L.append("## Where the code lives")
    L.append("")
    unit = "package" if len(pkg_loc) > 1 else "module"
    rows = (sorted(pkg_loc.items(), key=lambda kv: -kv[1]) if len(pkg_loc) > 1
            else sorted(((m, v["loc"]) for m, v in mods.items()), key=lambda kv: -kv[1]))
    L.append(f"| {unit} | loc | share |")
    L.append("|---|---|---|")
    for name, loc in rows[:25]:
        share = f"{100 * loc / total_loc:.0f}%" if total_loc else "-"
        L.append(f"| `{name}` | {loc:,} | {share} |")
    L.append("")
    L.append("## Widest package interfaces")
    L.append("")
    L.append("The number of distinct symbols each package exposes across its own boundary.")
    L.append("This is the surface a refactor has to preserve or deliberately break.")
    L.append("")
    if iface["packages"]:
        for pkg, info in sorted(iface["packages"].items(),
                                key=lambda kv: -kv[1]["n_public_symbols"])[:15]:
            L.append(f"- `{pkg}`: {info['n_public_symbols']} symbols to "
                     f"{len(info['consumers'])} consumers")
    else:
        L.append("No package boundaries exist to cross — the code is one flat namespace.")
        L.append("That is itself the finding: read the module surfaces in")
        L.append("`04-interfaces.md` and treat inventing a package structure as part of")
        L.append("the refactor rather than a rearrangement of an existing one.")
    L.append("")
    L.append("## Third-party surface")
    L.append("")
    L.append("Non-stdlib packages by how many modules import them. Each one is a dependency")
    L.append("that has leaked into that many places; the widely-spread ones constrain how")
    L.append("freely the code can be moved.")
    L.append("")
    for name, n in ext.most_common(20):
        L.append(f"- `{name}` in {n} modules")
    if not ext:
        L.append("_no third-party imports — the code depends only on the stdlib_")
    L.append("")
    L.append(f"Most-used stdlib modules: "
             + ", ".join(f"`{k}` ({v})" for k, v in std.most_common(8)))
    L.append("")
    L.append("## Contents of this pack")
    L.append("")
    L.append("- `01-modules.md` — inventory with fan-in/fan-out and complexity")
    L.append("- `02-graph.json` — the import graph: `edges`, `reverse`, `package_edges`, "
             "`cycles`, `string_edges`, per-edge symbol lists and line numbers")
    L.append("- `02-packages.dot` — package graph for GraphViz; back-edges in red")
    L.append("- `03-cycles.md` — every cycle with its candidate cut points")
    L.append("- `04-interfaces.md` / `.json` — de facto public surface per module and package")
    L.append("- `05-symbols.jsonl` — one record per module/class/function with signatures")
    for line in extras:
        L.append(line)
    L.append("")
    L.append("## How to use this when planning the refactor")
    L.append("")
    L.append("1. Cycles first. Read `03-cycles.md`; nothing can be layered until they are cut.")
    L.append("2. Then narrow the interfaces. `04-interfaces.md` ranks modules by how many")
    L.append("   distinct names outsiders reach for. A module exposing 40 names is not a")
    L.append("   module, it is a namespace.")
    L.append("3. Cross-check the intended structure against `07-git.md`. Where co-change")
    L.append("   disagrees with the directory layout, trust the co-change.")
    L.append("4. Decide which modules are allowed to do I/O using `08-boundaries.md`, and")
    L.append("   unwind import-time side effects before moving any files.")
    L.append("5. Do not trust the test suite as your safety net. Get runtime evidence")
    L.append("   (`09-runtime.md`) and freeze the target structure as an enforced contract")
    L.append("   before moving code. See `references/planning.md`.")
    return "\n".join(L) + "\n"


def choose_depth(module_names, requested: int | None) -> tuple[int, str]:
    """Pick a package-squash depth that actually groups things.

    Depth 2 is right for `pkg.subpkg.module` layouts but degenerate at both
    extremes: on a flat `pkg.module` repo it makes every module its own package,
    and on a deeply nested one it hides the structure. Choose the depth whose node
    count sits in a useful band relative to the module count.
    """
    mods = [m for m in module_names]
    n = len(mods) or 1
    if requested is not None:
        return requested, f"package depth {requested} (specified)"
    ceiling = max(3, int(0.4 * n))
    best = None
    for depth in (1, 2, 3, 4):
        count = len({".".join(m.split(".")[:depth]) for m in mods})
        if 2 <= count <= ceiling:
            best = (depth, count)
    if best is None:
        return 1, ("no useful package depth found - the repo has little or no subpackage "
                   "structure, so read the module-level tables rather than the package ones")
    return best[0], f"package depth {best[0]} chosen automatically ({best[1]} packages)"


def has_git(repo: Path) -> bool:
    try:
        r = subprocess.run(["git", "-C", str(repo), "rev-parse", "--git-dir"],
                           capture_output=True, text=True, timeout=20)
        return r.returncode == 0
    except (subprocess.SubprocessError, FileNotFoundError):
        return False


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("repo", nargs="?", default=".")
    ap.add_argument("--out", default=None, help="default <repo>/.repomap")
    ap.add_argument("--source-root", action="append", default=[])
    ap.add_argument("--package-depth", type=int, default=None,
                    help="default: chosen automatically from the layout")
    ap.add_argument("--top", type=int, default=60, help="rows per table in the markdown")
    ap.add_argument("--include-tests", action="store_true")
    ap.add_argument("--coverage-json", default=None)
    ap.add_argument("--since", default="18.months.ago")
    ap.add_argument("--skip-git", action="store_true")
    args = ap.parse_args()

    repo = Path(args.repo).resolve()
    out = Path(args.out) if args.out else repo / ".repomap"
    out.mkdir(parents=True, exist_ok=True)
    roots = build_graph.discover_source_roots(repo, args.source_root)
    print(f"repo: {repo}")
    print(f"source roots: {[str(r) for r in roots]}")

    probe = build_graph.build(repo, roots, True, 2)
    depth, depth_note = choose_depth(
        [m for m, v in probe["modules"].items() if not v.get("is_test")], args.package_depth)
    print(f"  {depth_note}")
    graph = probe if depth == 2 else build_graph.build(repo, roots, True, depth)
    graph["depth_note"] = depth_note
    adj = {k: list(v) for k, v in graph["edges"].items()}
    graph["cycles"] = build_graph.sccs(adj)
    graph["package_cycles"] = build_graph.sccs(graph["package_edges"])
    (out / "02-graph.json").write_text(json.dumps(graph, indent=1, sort_keys=True))
    (out / "02-packages.dot").write_text(render_dot(graph))
    (out / "01-modules.md").write_text(render_modules_md(graph, args.top))
    (out / "03-cycles.md").write_text(render_cycles_md(graph))
    print(f"  graph: {len(graph['modules'])} modules, "
          f"{sum(len(v) for v in graph['edges'].values())} edges, "
          f"{len(graph['cycles'])} cycles")

    symbols = symbol_index.run(repo, roots, args.include_tests)
    (out / "05-symbols.jsonl").write_text(
        "\n".join(json.dumps(r, sort_keys=True) for r in symbols) + "\n")
    print(f"  symbols: {len(symbols)} records")

    iface = interfaces.analyse(graph, symbols, depth)
    (out / "04-interfaces.md").write_text(interfaces.render_md(iface, args.top))
    (out / "04-interfaces.json").write_text(json.dumps(iface, indent=1, sort_keys=True))
    print(f"  interfaces: {len(iface['packages'])} packages with a boundary surface")

    extras = []
    bnd = boundaries.scan(repo, roots, args.include_tests)
    (out / "08-boundaries.md").write_text(boundaries.render_md(bnd, args.top))
    (out / "08-boundaries.json").write_text(json.dumps(bnd, indent=1, sort_keys=True))
    extras.append("- `08-boundaries.md` — I/O touchpoints and import-time side effects")
    print(f"  boundaries: {len(bnd['modules'])} modules touching the outside world")

    if not args.skip_git and has_git(repo):
        import git_signals
        commits = git_signals.git_log(repo, args.since, 4000)
        if commits:
            rep = git_signals.analyse(commits, graph, 25, 4, 0.4)
            (out / "07-git.md").write_text(git_signals.render_md(rep, args.top))
            (out / "07-git.json").write_text(json.dumps(rep, indent=1, sort_keys=True))
            hidden = sum(1 for r in rep["coupling"] if not r["import_edge"])
            extras.append("- `07-git.md` — churn x complexity hotspots and hidden "
                          "co-change coupling")
            print(f"  git: {len(commits)} commits, {hidden} coupled pairs with no import edge")
        else:
            print("  git: no commits in range, skipped")
    else:
        print("  git: skipped")

    if args.coverage_json:
        import runtime
        cov = json.loads(Path(args.coverage_json).read_text())
        rep = runtime.analyse(cov, graph)
        (out / "09-runtime.md").write_text(runtime.render_md(rep, graph, args.top))
        (out / "09-runtime.json").write_text(json.dumps(rep, indent=1, sort_keys=True))
        extras.append("- `09-runtime.md` — what actually executes, per entry point")
        print(f"  runtime: {len(rep['dead_code_candidates'])} dead-code candidates")
    else:
        extras.append("- `09-runtime.md` — NOT GENERATED. Collect coverage from real entry "
                      "points and rerun with `--coverage-json`; see `references/runtime-evidence.md`")

    (out / "00-OVERVIEW.md").write_text(render_overview(graph, symbols, iface, extras))
    print(f"\npack written to {out}")
    for f in sorted(out.iterdir()):
        print(f"  {f.name}  ({f.stat().st_size:,} bytes)")


if __name__ == "__main__":
    main()
