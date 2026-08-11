"""Derive the *de facto* public interface of every module and package.

For each module: which names other modules actually reach in for, who reaches
for them, and which defined names nobody outside uses. For each package: which
names cross the package boundary — that set is the interface you are implicitly
committed to, and shrinking it is the whole point of the refactor.
"""
from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path


def defined_names(symbols: list[dict]) -> dict[str, set]:
    out: dict[str, set] = defaultdict(set)
    for rec in symbols:
        if rec["kind"] in ("class", "function", "constant"):
            out[rec["module"]].add(rec["name"])
    return out


def squash(mod: str, depth: int) -> str:
    parts = mod.split(".")
    return ".".join(parts[:depth]) if len(parts) > depth else mod


def analyse(graph: dict, symbols: list[dict], depth: int) -> dict:
    edges = graph["edges"]
    modules = graph["modules"]
    defined = defined_names(symbols)
    tests = {m for m, meta in modules.items() if meta.get("is_test")}

    # module -> imported name -> importers
    used: dict[str, dict[str, set]] = defaultdict(lambda: defaultdict(set))
    for src, targets in edges.items():
        for tgt, info in targets.items():
            for name in info["names"]:
                used[tgt][name].add(src)

    module_report = {}
    for mod, meta in sorted(modules.items()):
        if meta.get("is_test"):
            continue
        surface = used.get(mod, {})
        prod_importers = {s for names in surface.values() for s in names} - tests
        exported = {n: sorted(v) for n, v in sorted(surface.items()) if n != "<module>"}
        internal_only = sorted(defined.get(mod, set()) - set(exported)
                               - {n for n in defined.get(mod, set()) if n.startswith("_")})
        module_report[mod] = {
            "path": meta["path"],
            "loc": meta["loc"],
            "declared_all": None,
            "used_names": exported,
            "n_used_names": len(exported),
            "importers": sorted(prod_importers),
            "n_importers": len(prod_importers),
            "imported_as_module_by": sorted(surface.get("<module>", set())),
            "defined_but_unused_externally": internal_only,
            "fan_out": len(edges.get(mod, {})),
        }

    for rec in symbols:
        if rec["kind"] == "module" and rec.get("all") and rec["module"] in module_report:
            module_report[rec["module"]]["declared_all"] = rec["all"]

    # package-level boundary crossings
    pkg_surface: dict[str, dict[str, set]] = defaultdict(lambda: defaultdict(set))
    for src, targets in edges.items():
        if src in tests:
            continue
        for tgt, info in targets.items():
            a, b = squash(src, depth), squash(tgt, depth)
            if a == b:
                continue
            for name in info["names"]:
                key = f"{tgt}.{name}" if name != "<module>" else tgt
                pkg_surface[b][key].add(a)

    package_report = {}
    for pkg, surface in sorted(pkg_surface.items()):
        package_report[pkg] = {
            "n_public_symbols": len(surface),
            "public_symbols": {k: sorted(v) for k, v in sorted(surface.items())},
            "consumers": sorted({c for v in surface.values() for c in v}),
        }

    return {"modules": module_report, "packages": package_report, "package_depth": depth}


def render_md(report: dict, top: int = 40) -> str:
    L = ["# De facto interfaces", ""]
    L.append("Derived from real imports, not from `__all__`. `--` in the *declared*")
    L.append("column means the module never declared `__all__`, so everything in it is")
    L.append("effectively public.")
    L.append("")
    L.append("## Package boundary surface")
    L.append("")
    L.append("Wide surfaces are the modules that will be hardest to refactor behind an")
    L.append("interface. Attack these first.")
    L.append("")
    L.append("| package | symbols crossing boundary | consumer packages |")
    L.append("|---|---|---|")
    for pkg, info in sorted(report["packages"].items(),
                            key=lambda kv: -kv[1]["n_public_symbols"])[:top]:
        L.append(f"| `{pkg}` | {info['n_public_symbols']} | {len(info['consumers'])} |")

    L.append("")
    L.append("## Widest module surfaces")
    L.append("")
    L.append("| module | names reached for | importers | declared `__all__` | fan-out |")
    L.append("|---|---|---|---|---|")
    mods = sorted(report["modules"].items(), key=lambda kv: -kv[1]["n_used_names"])
    for mod, info in mods[:top]:
        if not info["n_used_names"]:
            continue
        declared = "yes" if info["declared_all"] else "--"
        L.append(f"| `{mod}` | {info['n_used_names']} | {info['n_importers']} | "
                 f"{declared} | {info['fan_out']} |")

    L.append("")
    L.append("## Leaf candidates (defined, never imported anywhere)")
    L.append("")
    L.append("Either genuinely internal (good — leave it), reachable only")
    L.append("dynamically, or dead. Cross-check against runtime coverage before deleting.")
    L.append("")
    orphans = [(m, i) for m, i in report["modules"].items()
               if i["n_importers"] == 0 and not i["imported_as_module_by"] and i["loc"] > 0]
    for mod, info in sorted(orphans, key=lambda kv: -kv[1]["loc"])[:top]:
        L.append(f"- `{mod}` ({info['loc']} loc) — {info['path']}")
    if not orphans:
        L.append("_none_")
    return "\n".join(L) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("graph", help="graph.json from build_graph.py")
    ap.add_argument("symbols", help="symbols.jsonl from symbol_index.py")
    ap.add_argument("--package-depth", type=int, default=2)
    ap.add_argument("--json-out", default=None)
    ap.add_argument("-o", "--out", default=None)
    args = ap.parse_args()

    graph = json.loads(Path(args.graph).read_text())
    symbols = [json.loads(ln) for ln in Path(args.symbols).read_text().splitlines() if ln.strip()]
    report = analyse(graph, symbols, args.package_depth)
    md = render_md(report)
    if args.json_out:
        Path(args.json_out).write_text(json.dumps(report, indent=1, sort_keys=True))
    if args.out:
        Path(args.out).write_text(md)
        print(f"wrote {args.out}: {len(report['packages'])} packages, "
              f"{len(report['modules'])} modules")
    else:
        print(md)


if __name__ == "__main__":
    main()
