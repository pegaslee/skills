"""Find where the code touches the outside world.

A refactor toward deep modules is mostly about deciding which modules are allowed
to talk to the network, the database, the clock, the filesystem and the
environment. Modules that mix business logic with I/O are the ones that need
splitting; modules that touch nothing are already easy to move.

Also flags import-time side effects, which are what make a messy repo resist
being reorganised at all.
"""
from __future__ import annotations

import argparse
import ast
import json
from collections import defaultdict
from pathlib import Path

from build_graph import discover_source_roots, is_test_module, iter_py_files, module_name

# category -> substrings matched against the dotted call expression
CATEGORIES = {
    "env": ["os.environ", "os.getenv", "getenv", "dotenv"],
    "filesystem": ["open(", "pathlib", "Path(", "os.path", "shutil", "os.remove",
                   "os.mkdir", "os.makedirs", "tempfile", "glob."],
    "network": ["requests.", "httpx.", "urllib", "aiohttp", "socket.", "urlopen",
                "boto3", "grpc"],
    "database": ["sqlalchemy", "psycopg", "sqlite3", "pymongo", "redis", "cursor.execute",
                 "session.query", "objects.filter", "objects.get", "objects.all"],
    "clock": ["datetime.now", "datetime.utcnow", "time.time", "time.sleep", "date.today",
              "monotonic"],
    "randomness": ["random.", "uuid4", "uuid1", "secrets."],
    "process": ["subprocess", "os.system", "os.fork", "multiprocessing", "os.exec"],
    "console": ["print(", "sys.stdout", "sys.stderr", "input("],
    "reflection": ["getattr(", "setattr(", "importlib", "__import__", "eval(", "exec(",
                   "globals(", "locals(", "vars("],
}

SAFE_MODULE_LEVEL = (
    ast.Import, ast.ImportFrom, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef,
    ast.Expr, ast.Pass, ast.If, ast.Try, ast.AnnAssign,
)


def call_text(node: ast.Call) -> str:
    try:
        return ast.unparse(node.func) + "("
    except Exception:
        return ""


def import_time_effects(tree: ast.Module) -> list[dict]:
    """Module-level statements that do real work when the module is imported."""
    out = []
    for node in tree.body:
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            value = node.value
            if isinstance(value, ast.Call):
                text = call_text(value)
                if not text:
                    continue
                out.append({"line": node.lineno, "what": text.rstrip("("),
                            "why": "module-level call assigned at import"})
        elif isinstance(node, ast.Expr) and isinstance(node.value, ast.Call):
            text = call_text(node.value).rstrip("(")
            if text and not text.startswith(("logging.getLogger",)):
                out.append({"line": node.lineno, "what": text,
                            "why": "bare module-level call"})
        elif isinstance(node, (ast.For, ast.While, ast.With)):
            out.append({"line": node.lineno, "what": type(node).__name__.lower(),
                        "why": "module-level control flow"})
    return out


def scan(repo: Path, roots: list[Path], include_tests: bool):
    per_module: dict[str, dict] = {}
    totals = defaultdict(int)
    for root in roots:
        for path in iter_py_files(root):
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
                tree = ast.parse(text, filename=str(path))
            except SyntaxError:
                continue
            mod = module_name(path, root)
            if not mod or (is_test_module(mod, path) and not include_tests):
                continue
            hits: dict[str, list] = defaultdict(list)
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                text_ = call_text(node)
                if not text_:
                    continue
                for cat, needles in CATEGORIES.items():
                    if any(n in text_ for n in needles):
                        hits[cat].append({"line": node.lineno, "call": text_.rstrip("(")})
                        totals[cat] += 1
                        break
            effects = import_time_effects(tree)
            if hits or effects:
                per_module[mod] = {
                    "path": str(path.relative_to(repo)),
                    "categories": {k: v[:20] for k, v in sorted(hits.items())},
                    "n_categories": len(hits),
                    "import_time_effects": effects[:20],
                }
    return {"modules": per_module, "totals": dict(totals)}


def render_md(report: dict, top: int = 40) -> str:
    L = ["# System boundaries and side effects", ""]
    L.append("## Modules mixing the most concerns")
    L.append("")
    L.append("A module touching several of these categories is doing orchestration and")
    L.append("logic at once. Splitting the I/O out is usually the cheapest first move")
    L.append("toward a testable core.")
    L.append("")
    L.append("| module | categories touched | which |")
    L.append("|---|---|---|")
    mods = sorted(report["modules"].items(), key=lambda kv: -kv[1]["n_categories"])
    for mod, info in mods[:top]:
        if info["n_categories"] < 2:
            continue
        L.append(f"| `{mod}` | {info['n_categories']} | "
                 f"{', '.join(info['categories'])} |")

    L.append("")
    L.append("## Import-time side effects")
    L.append("")
    L.append("These run the moment the module is imported. They are the main reason a")
    L.append("reorganisation breaks in ways the tests don't catch, so plan to unwind them")
    L.append("before moving files.")
    L.append("")
    any_effects = False
    for mod, info in mods:
        if not info["import_time_effects"]:
            continue
        any_effects = True
        L.append(f"- `{mod}`")
        for e in info["import_time_effects"][:6]:
            L.append(f"  - L{e['line']}: `{e['what']}` ({e['why']})")
    if not any_effects:
        L.append("_none detected_")

    L.append("")
    L.append("## Totals by category")
    L.append("")
    for cat, n in sorted(report["totals"].items(), key=lambda kv: -kv[1]):
        L.append(f"- {cat}: {n}")
    return "\n".join(L) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("repo", nargs="?", default=".")
    ap.add_argument("--source-root", action="append", default=[])
    ap.add_argument("--include-tests", action="store_true")
    ap.add_argument("--json-out", default=None)
    ap.add_argument("-o", "--out", default=None)
    args = ap.parse_args()

    repo = Path(args.repo).resolve()
    roots = discover_source_roots(repo, args.source_root)
    report = scan(repo, roots, args.include_tests)
    if args.json_out:
        Path(args.json_out).write_text(json.dumps(report, indent=1, sort_keys=True))
    md = render_md(report)
    if args.out:
        Path(args.out).write_text(md)
        print(f"wrote {args.out}: {len(report['modules'])} modules with boundary hits")
    else:
        print(md)


if __name__ == "__main__":
    main()
