"""Churn and temporal coupling from git history.

Files that change together are coupled whether or not an import connects them.
Pairs with high co-change and *no* import edge are the most interesting output of
this whole exercise: they are hidden coupling that the directory layout is lying
about, and they usually mark where the real module boundaries want to be.
"""
from __future__ import annotations

import argparse
import json
import subprocess
from collections import Counter, defaultdict
from itertools import combinations
from pathlib import Path


def git_log(repo: Path, since: str, max_commits: int):
    cmd = ["git", "-C", str(repo), "log", f"--since={since}", f"-n{max_commits}",
           "--no-merges", "--name-only", "--pretty=format:%x01%H%x02%an%x02%at"]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=180).stdout
    except (subprocess.SubprocessError, FileNotFoundError) as exc:
        raise SystemExit(f"git log failed: {exc}")
    commits = []
    for chunk in out.split("\x01"):
        if not chunk.strip():
            continue
        header, _, body = chunk.partition("\n")
        parts = header.split("\x02")
        if len(parts) < 3:
            continue
        files = [ln.strip() for ln in body.splitlines()
                 if ln.strip().endswith(".py")]
        commits.append({"sha": parts[0], "author": parts[1], "ts": int(parts[2]),
                        "files": files})
    return commits


def analyse(commits, graph: dict, max_files: int, min_support: int, min_confidence: float):
    churn = Counter()
    authors = defaultdict(set)
    last_touch = {}
    pair = Counter()

    for c in commits:
        files = sorted(set(c["files"]))
        for f in files:
            churn[f] += 1
            authors[f].add(c["author"])
            last_touch[f] = max(last_touch.get(f, 0), c["ts"])
        if 1 < len(files) <= max_files:
            for a, b in combinations(files, 2):
                pair[(a, b)] += 1

    path_to_mod = {meta["path"]: mod for mod, meta in graph["modules"].items()}
    linked = set()
    for src, tgts in graph["edges"].items():
        for tgt in tgts:
            linked.add(frozenset((src, tgt)))

    coupling = []
    for (a, b), n in pair.items():
        conf = n / min(churn[a], churn[b])
        if n < min_support or conf < min_confidence:
            continue
        ma, mb = path_to_mod.get(a), path_to_mod.get(b)
        has_import = bool(ma and mb and frozenset((ma, mb)) in linked)
        coupling.append({
            "a": a, "b": b, "module_a": ma, "module_b": mb,
            "co_changes": n, "confidence": round(conf, 2),
            "import_edge": has_import,
        })
    coupling.sort(key=lambda r: (-r["co_changes"], -r["confidence"]))

    hotspots = []
    for path, mod in path_to_mod.items():
        meta = graph["modules"][mod]
        hotspots.append({
            "path": path, "module": mod, "loc": meta["loc"],
            "commits": churn.get(path, 0),
            "authors": len(authors.get(path, ())),
            "max_complexity": meta.get("max_complexity", 0),
            "risk": churn.get(path, 0) * meta.get("max_complexity", 0),
        })
    hotspots.sort(key=lambda r: -r["risk"])
    return {"n_commits": len(commits), "coupling": coupling, "hotspots": hotspots}


def render_md(report: dict, top: int = 40) -> str:
    L = [f"# Git signals ({report['n_commits']} commits analysed)", ""]
    L.append("## Hidden coupling: co-changes with NO import edge")
    L.append("")
    L.append("These files move together but nothing connects them statically. Each row is")
    L.append("either a shared implicit contract (a format, a schema, a magic string), a")
    L.append("missing abstraction, or a boundary drawn in the wrong place.")
    L.append("")
    hidden = [r for r in report["coupling"] if not r["import_edge"]]
    if hidden:
        L.append("| file A | file B | co-changes | confidence |")
        L.append("|---|---|---|---|")
        for r in hidden[:top]:
            L.append(f"| `{r['a']}` | `{r['b']}` | {r['co_changes']} | {r['confidence']} |")
    else:
        L.append("_none above threshold_")

    L.append("")
    L.append("## Refactor risk: churn x complexity")
    L.append("")
    L.append("High churn and high complexity together. Refactoring a stable, ugly file")
    L.append("buys little; these are the files where structure actually costs you.")
    L.append("")
    L.append("| file | loc | commits | authors | max fn complexity | risk |")
    L.append("|---|---|---|---|---|---|")
    for r in report["hotspots"][:top]:
        if not r["risk"]:
            continue
        L.append(f"| `{r['path']}` | {r['loc']} | {r['commits']} | {r['authors']} | "
                 f"{r['max_complexity']} | {r['risk']} |")
    return "\n".join(L) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("repo", nargs="?", default=".")
    ap.add_argument("--graph", required=True)
    ap.add_argument("--since", default="18.months.ago")
    ap.add_argument("--max-commits", type=int, default=4000)
    ap.add_argument("--max-files-per-commit", type=int, default=25,
                    help="commits touching more files are ignored for coupling (bulk renames)")
    ap.add_argument("--min-support", type=int, default=4)
    ap.add_argument("--min-confidence", type=float, default=0.4)
    ap.add_argument("--json-out", default=None)
    ap.add_argument("-o", "--out", default=None)
    args = ap.parse_args()

    repo = Path(args.repo).resolve()
    graph = json.loads(Path(args.graph).read_text())
    commits = git_log(repo, args.since, args.max_commits)
    report = analyse(commits, graph, args.max_files_per_commit,
                     args.min_support, args.min_confidence)
    if args.json_out:
        Path(args.json_out).write_text(json.dumps(report, indent=1, sort_keys=True))
    md = render_md(report)
    if args.out:
        Path(args.out).write_text(md)
        hidden = sum(1 for r in report["coupling"] if not r["import_edge"])
        print(f"wrote {args.out}: {report['n_commits']} commits, "
              f"{len(report['coupling'])} coupled pairs ({hidden} without import edge)")
    else:
        print(md)


if __name__ == "__main__":
    main()
