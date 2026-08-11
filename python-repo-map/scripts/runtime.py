"""Fold runtime evidence into the map.

Static analysis lies in messy codebases: registries, `getattr`, plugin loaders and
dynamic imports are invisible to it. Coverage collected from *real entry points*
(not from the test suite, which you don't trust) tells you what actually runs, and
with `--show-contexts` it tells you which use case reaches which module.

Produce the input like this:

    coverage run --context=checkout-flow -m yourapp.cli checkout ...
    coverage run --context=nightly-job --append -m yourapp.jobs.nightly
    coverage json --show-contexts -o coverage.json
"""
from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path


def analyse(cov: dict, graph: dict) -> dict:
    path_to_mod = {meta["path"]: mod for mod, meta in graph["modules"].items()}
    files = cov.get("files", {})

    per_module = {}
    ctx_modules: dict[str, set] = defaultdict(set)

    for raw_path, data in files.items():
        norm = raw_path.lstrip("./")
        mod = path_to_mod.get(norm)
        if mod is None:
            # try suffix match, coverage paths are often absolute
            for p, m in path_to_mod.items():
                if norm.endswith(p):
                    mod, norm = m, p
                    break
        if mod is None:
            continue
        summary = data.get("summary", {})
        pct = summary.get("percent_covered", 0.0)
        per_module[mod] = {
            "path": norm,
            "percent_executed": round(pct, 1),
            "executed_lines": summary.get("covered_lines", len(data.get("executed_lines", []))),
            "statements": summary.get("num_statements", 0),
        }
        for _line, ctxs in (data.get("contexts") or {}).items():
            for ctx in ctxs:
                if ctx:
                    ctx_modules[ctx].add(mod)

    tracked = {m for m, meta in graph["modules"].items() if not meta.get("is_test")}
    never_run = sorted(tracked - set(per_module))
    cold = sorted((m for m, i in per_module.items() if i["percent_executed"] == 0.0))

    # modules nothing statically imports AND nothing executed -> strongest dead-code signal
    reverse = graph.get("reverse", {})
    dead_candidates = [m for m in set(never_run) | set(cold)
                       if not reverse.get(m) and m not in graph.get("string_edges", {})]

    return {
        "modules": per_module,
        "contexts": {k: sorted(v) for k, v in sorted(ctx_modules.items())},
        "never_loaded": never_run,
        "loaded_but_zero_lines": cold,
        "dead_code_candidates": sorted(dead_candidates),
    }


def render_md(report: dict, graph: dict, top: int = 60) -> str:
    L = ["# Runtime evidence", ""]
    n_ctx = len(report["contexts"])
    L.append(f"Contexts recorded: {n_ctx or 'none (run coverage with --context to get per-use-case reach)'}")
    L.append("")

    if report["contexts"]:
        L.append("## What each entry point actually reaches")
        L.append("")
        for ctx, mods in report["contexts"].items():
            L.append(f"- **{ctx}** — {len(mods)} modules")
        L.append("")

    L.append("## Dead code candidates")
    L.append("")
    L.append("Never executed under any recorded entry point *and* imported by nothing.")
    L.append("Highest-confidence deletions available. Confirm each one by grepping for its")
    L.append("name as a string before removing it.")
    L.append("")
    for mod in report["dead_code_candidates"][:top]:
        loc = graph["modules"].get(mod, {}).get("loc", "?")
        L.append(f"- `{mod}` ({loc} loc)")
    if not report["dead_code_candidates"]:
        L.append("_none_")

    L.append("")
    L.append("## Never loaded at all")
    L.append("")
    for mod in report["never_loaded"][:top]:
        L.append(f"- `{mod}`")
    if not report["never_loaded"]:
        L.append("_none_")

    L.append("")
    L.append("## Coldest executed modules")
    L.append("")
    L.append("| module | % lines executed | statements |")
    L.append("|---|---|---|")
    ranked = sorted(report["modules"].items(), key=lambda kv: kv[1]["percent_executed"])
    for mod, info in ranked[:top]:
        L.append(f"| `{mod}` | {info['percent_executed']} | {info['statements']} |")
    return "\n".join(L) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("coverage_json")
    ap.add_argument("--graph", required=True)
    ap.add_argument("--json-out", default=None)
    ap.add_argument("-o", "--out", default=None)
    args = ap.parse_args()

    cov = json.loads(Path(args.coverage_json).read_text())
    graph = json.loads(Path(args.graph).read_text())
    report = analyse(cov, graph)
    if args.json_out:
        Path(args.json_out).write_text(json.dumps(report, indent=1, sort_keys=True))
    md = render_md(report, graph)
    if args.out:
        Path(args.out).write_text(md)
        print(f"wrote {args.out}: {len(report['modules'])} modules with runtime data, "
              f"{len(report['dead_code_candidates'])} dead-code candidates")
    else:
        print(md)


if __name__ == "__main__":
    main()
