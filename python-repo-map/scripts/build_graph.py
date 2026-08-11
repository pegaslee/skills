"""Build a first-party import graph for a Python repo.

Pure stdlib. Records symbol-level import detail (which names cross which edge),
because that is what lets you derive de facto module interfaces later.
"""
from __future__ import annotations

import argparse
import ast
import json
import os
import sys
from collections import defaultdict
from pathlib import Path

SKIP_DIRS = {
    ".git", ".hg", ".svn", "__pycache__", ".venv", "venv", ".env", "env",
    "node_modules", ".tox", ".nox", ".mypy_cache", ".pytest_cache", ".ruff_cache",
    "build", "dist", "site-packages", ".eggs", ".idea", ".vscode", ".repomap",
}


def discover_source_roots(repo: Path, explicit: list[str]) -> list[Path]:
    if explicit:
        return [(repo / p).resolve() for p in explicit]
    candidates = []
    for name in ("src", "lib"):
        d = repo / name
        if d.is_dir() and any(d.rglob("*.py")):
            candidates.append(d)
    if not candidates:
        return [repo]
    # Pick up sibling test trees too. Test modules get flagged, not dropped: a
    # symbol whose only consumer is a test is not actually public, and you only
    # know that if the tests are in the graph.
    for name in ("tests", "test", "testing"):
        d = repo / name
        if d.is_dir() and any(d.rglob("*.py")):
            candidates.append(d)
    return candidates


def iter_py_files(root: Path):
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".")]
        for fn in filenames:
            if fn.endswith(".py"):
                yield Path(dirpath) / fn


def module_name(path: Path, source_root: Path) -> str:
    rel = path.relative_to(source_root)
    parts = list(rel.parts)
    if parts[-1] == "__init__.py":
        parts = parts[:-1]
    else:
        parts[-1] = parts[-1][:-3]
    return ".".join(parts)


def is_test_module(mod: str, path: Path) -> bool:
    low = mod.lower()
    return (
        low.startswith("test") or ".test" in low or "tests." in low or low.endswith("_test")
        or path.name.startswith("test_") or path.name.endswith("_test.py")
        or "conftest" in path.name
    )


def parent_package(mod: str) -> str:
    return mod.rpartition(".")[0]


def resolve_relative(current: str, is_pkg: bool, level: int, base: str | None) -> str:
    """Resolve `from ..x import y` to an absolute dotted prefix."""
    pkg = current if is_pkg else parent_package(current)
    parts = pkg.split(".") if pkg else []
    up = level - 1
    if up:
        parts = parts[:-up] if up <= len(parts) else []
    if base:
        parts = parts + base.split(".")
    return ".".join(p for p in parts if p)


def longest_known_prefix(dotted: str, known: set[str]) -> str | None:
    parts = dotted.split(".")
    while parts:
        cand = ".".join(parts)
        if cand in known:
            return cand
        parts.pop()
    return None


def complexity(node: ast.AST) -> int:
    """Cheap cyclomatic-ish proxy: count decision points."""
    score = 1
    for n in ast.walk(node):
        if isinstance(n, (ast.If, ast.IfExp, ast.For, ast.AsyncFor, ast.While,
                          ast.ExceptHandler, ast.Assert, ast.With, ast.AsyncWith)):
            score += 1
        elif isinstance(n, ast.BoolOp):
            score += len(n.values) - 1
        elif isinstance(n, ast.comprehension):
            score += 1 + len(n.ifs)
        elif hasattr(ast, "match_case") and isinstance(n, ast.match_case):
            score += 1
    return score


def build(repo: Path, source_roots: list[Path], detect_string_imports: bool = True,
          package_depth: int = 2) -> dict:
    files: dict[str, dict] = {}          # module -> meta
    trees: dict[str, ast.AST] = {}

    for root in source_roots:
        for path in iter_py_files(root):
            try:
                src = path.read_text(encoding="utf-8", errors="replace")
                tree = ast.parse(src, filename=str(path))
            except SyntaxError as exc:
                print(f"  ! parse failed {path}: {exc}", file=sys.stderr)
                continue
            mod = module_name(path, root)
            if not mod:
                continue
            if mod in files:
                continue
            loc = len([ln for ln in src.splitlines() if ln.strip() and not ln.strip().startswith("#")])
            files[mod] = {
                "path": str(path.relative_to(repo)),
                "loc": loc,
                "raw_lines": len(src.splitlines()),
                "is_package": path.name == "__init__.py",
                "is_test": is_test_module(mod, path),
                "doc": (ast.get_docstring(tree) or "").strip().split("\n")[0][:160] or None,
                "max_complexity": 0,
            }
            trees[mod] = tree

    known = set(files)
    imports: dict[str, dict[str, list]] = defaultdict(lambda: defaultdict(list))
    external: dict[str, set] = defaultdict(set)
    string_edges: dict[str, set] = defaultdict(set)

    for mod, tree in trees.items():
        meta = files[mod]
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                meta["max_complexity"] = max(meta["max_complexity"], complexity(node))

            if isinstance(node, ast.Import):
                for alias in node.names:
                    tgt = longest_known_prefix(alias.name, known)
                    if tgt and tgt != mod:
                        imports[mod][tgt].append({"line": node.lineno, "names": ["<module>"]})
                    elif not tgt:
                        external[mod].add(alias.name.split(".")[0])

            elif isinstance(node, ast.ImportFrom):
                base = node.module
                if node.level:
                    dotted = resolve_relative(mod, meta["is_package"], node.level, base)
                else:
                    dotted = base or ""
                if not dotted:
                    continue
                tgt_mod = longest_known_prefix(dotted, known)
                if tgt_mod is None:
                    if not node.level:
                        external[mod].add(dotted.split(".")[0])
                    continue
                for alias in node.names:
                    submod = f"{dotted}.{alias.name}"
                    if submod in known:
                        if submod != mod:
                            imports[mod][submod].append({"line": node.lineno, "names": ["<module>"]})
                    elif tgt_mod != mod:
                        imports[mod][tgt_mod].append({"line": node.lineno, "names": [alias.name]})

            elif detect_string_imports and isinstance(node, ast.Constant) and isinstance(node.value, str):
                v = node.value
                if "." in v and " " not in v and len(v) < 200:
                    tgt = longest_known_prefix(v, known)
                    if tgt and tgt != mod and "." in tgt:
                        string_edges[mod].add(tgt)

    # collapse duplicate edges, merge names
    edges: dict[str, dict[str, dict]] = {}
    for src, tgts in imports.items():
        edges[src] = {}
        for tgt, occ in tgts.items():
            names = sorted({n for o in occ for n in o["names"]})
            edges[src][tgt] = {"names": names, "lines": sorted({o["line"] for o in occ})}

    reverse: dict[str, list[str]] = defaultdict(list)
    for src, tgts in edges.items():
        for tgt in tgts:
            reverse[tgt].append(src)

    def squash(mod: str) -> str:
        parts = mod.split(".")
        return ".".join(parts[:package_depth]) if len(parts) > package_depth else mod

    pkg_edges: dict[str, set] = defaultdict(set)
    for src, tgts in edges.items():
        for tgt in tgts:
            a, b = squash(src), squash(tgt)
            if a != b:
                pkg_edges[a].add(b)

    return {
        "repo": str(repo),
        "source_roots": [str(r.relative_to(repo)) if r != repo else "." for r in source_roots],
        "package_depth": package_depth,
        "modules": files,
        "edges": edges,
        "reverse": {k: sorted(v) for k, v in reverse.items()},
        "external": {k: sorted(v) for k, v in external.items()},
        "string_edges": {k: sorted(v) for k, v in string_edges.items()},
        "package_edges": {k: sorted(v) for k, v in pkg_edges.items()},
    }


def sccs(adj: dict[str, list[str]]) -> list[list[str]]:
    """Tarjan. Returns non-trivial strongly connected components."""
    index: dict[str, int] = {}
    low: dict[str, int] = {}
    on_stack: set[str] = set()
    stack: list[str] = []
    out: list[list[str]] = []
    counter = [0]
    nodes = set(adj) | {v for vs in adj.values() for v in vs}

    for root in nodes:
        if root in index:
            continue
        work = [(root, iter(adj.get(root, ())))]
        index[root] = low[root] = counter[0]
        counter[0] += 1
        stack.append(root)
        on_stack.add(root)
        while work:
            node, it = work[-1]
            advanced = False
            for nxt in it:
                if nxt not in index:
                    index[nxt] = low[nxt] = counter[0]
                    counter[0] += 1
                    stack.append(nxt)
                    on_stack.add(nxt)
                    work.append((nxt, iter(adj.get(nxt, ()))))
                    advanced = True
                    break
                if nxt in on_stack:
                    low[node] = min(low[node], index[nxt])
            if advanced:
                continue
            work.pop()
            if work:
                low[work[-1][0]] = min(low[work[-1][0]], low[node])
            if low[node] == index[node]:
                comp = []
                while True:
                    w = stack.pop()
                    on_stack.discard(w)
                    comp.append(w)
                    if w == node:
                        break
                if len(comp) > 1:
                    out.append(sorted(comp))
    return sorted(out, key=len, reverse=True)


def shortest_chain(adj: dict[str, list[str]], src: str, dst: str) -> list[str] | None:
    from collections import deque
    seen = {src}
    q = deque([[src]])
    while q:
        path = q.popleft()
        for nxt in adj.get(path[-1], ()):
            if nxt == dst:
                return path + [dst]
            if nxt not in seen:
                seen.add(nxt)
                q.append(path + [nxt])
    return None


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("repo", nargs="?", default=".")
    ap.add_argument("--source-root", action="append", default=[])
    ap.add_argument("--package-depth", type=int, default=2)
    ap.add_argument("--no-string-imports", action="store_true")
    ap.add_argument("-o", "--out", default=None)
    args = ap.parse_args()

    repo = Path(args.repo).resolve()
    roots = discover_source_roots(repo, args.source_root)
    graph = build(repo, roots, not args.no_string_imports, args.package_depth)
    adj = {k: list(v) for k, v in graph["edges"].items()}
    graph["cycles"] = sccs(adj)
    graph["package_cycles"] = sccs(graph["package_edges"])

    text = json.dumps(graph, indent=1, sort_keys=True)
    if args.out:
        Path(args.out).write_text(text)
        print(f"wrote {args.out}: {len(graph['modules'])} modules, "
              f"{sum(len(v) for v in graph['edges'].values())} edges, "
              f"{len(graph['cycles'])} cycles")
    else:
        print(text)


if __name__ == "__main__":
    main()
