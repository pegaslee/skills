"""Emit a JSONL symbol inventory of a Python repo.

One record per module / class / function. This is the highest-value artifact for
an LLM: it is the whole shape of the codebase at a fraction of the tokens of the
source, and you control the schema.
"""
from __future__ import annotations

import argparse
import ast
import json
from pathlib import Path

from build_graph import complexity, discover_source_roots, iter_py_files, is_test_module, module_name


def sig(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    try:
        args = ast.unparse(node.args)
    except Exception:
        args = "..."
    ret = ""
    if node.returns is not None:
        try:
            ret = " -> " + ast.unparse(node.returns)
        except Exception:
            ret = ""
    prefix = "async def " if isinstance(node, ast.AsyncFunctionDef) else "def "
    return f"{prefix}{node.name}({args}){ret}"


def deco_names(node) -> list[str]:
    out = []
    for d in node.decorator_list:
        try:
            out.append(ast.unparse(d))
        except Exception:
            pass
    return out


def first_line(doc: str | None) -> str | None:
    if not doc:
        return None
    return doc.strip().split("\n")[0][:200] or None


def dunder_all(tree: ast.Module) -> list[str] | None:
    for node in tree.body:
        targets = []
        if isinstance(node, ast.Assign):
            targets = node.targets
        elif isinstance(node, ast.AnnAssign):
            targets = [node.target]
        for t in targets:
            if isinstance(t, ast.Name) and t.id == "__all__":
                val = node.value
                if isinstance(val, (ast.List, ast.Tuple)):
                    return [e.value for e in val.elts
                            if isinstance(e, ast.Constant) and isinstance(e.value, str)]
    return None


def span(node) -> int:
    end = getattr(node, "end_lineno", None) or node.lineno
    return end - node.lineno + 1


def index_module(mod: str, path: Path, tree: ast.Module, rel: str, is_test: bool):
    records = []
    records.append({
        "kind": "module",
        "module": mod,
        "path": rel,
        "doc": first_line(ast.get_docstring(tree)),
        "all": dunder_all(tree),
        "lines": len(path.read_text(encoding="utf-8", errors="replace").splitlines()),
        "is_test": is_test,
    })

    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            bases = []
            for b in node.bases:
                try:
                    bases.append(ast.unparse(b))
                except Exception:
                    pass
            methods = []
            attrs = []
            for sub in node.body:
                if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    methods.append({
                        "sig": sig(sub),
                        "line": sub.lineno,
                        "lines": span(sub),
                        "cx": complexity(sub),
                        "decorators": deco_names(sub) or None,
                        "doc": first_line(ast.get_docstring(sub)),
                        "private": sub.name.startswith("_") and not sub.name.startswith("__"),
                    })
                elif isinstance(sub, ast.AnnAssign) and isinstance(sub.target, ast.Name):
                    attrs.append(sub.target.id)
                elif isinstance(sub, ast.Assign):
                    attrs += [t.id for t in sub.targets if isinstance(t, ast.Name)]
            records.append({
                "kind": "class",
                "module": mod,
                "path": rel,
                "name": node.name,
                "line": node.lineno,
                "lines": span(node),
                "bases": bases or None,
                "decorators": deco_names(node) or None,
                "doc": first_line(ast.get_docstring(node)),
                "class_attrs": attrs or None,
                "methods": methods,
                "n_methods": len(methods),
                "is_test": is_test,
            })
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            records.append({
                "kind": "function",
                "module": mod,
                "path": rel,
                "name": node.name,
                "sig": sig(node),
                "line": node.lineno,
                "lines": span(node),
                "cx": complexity(node),
                "decorators": deco_names(node) or None,
                "doc": first_line(ast.get_docstring(node)),
                "is_test": is_test,
            })
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for t in targets:
                if isinstance(t, ast.Name) and t.id.isupper():
                    records.append({
                        "kind": "constant", "module": mod, "path": rel,
                        "name": t.id, "line": node.lineno, "is_test": is_test,
                    })
    return records


def run(repo: Path, roots: list[Path], include_tests: bool = False):
    all_records = []
    for root in roots:
        for path in iter_py_files(root):
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
                tree = ast.parse(text, filename=str(path))
            except SyntaxError:
                continue
            mod = module_name(path, root)
            if not mod:
                continue
            is_test = is_test_module(mod, path)
            if is_test and not include_tests:
                continue
            all_records += index_module(mod, path, tree, str(path.relative_to(repo)), is_test)
    return all_records


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("repo", nargs="?", default=".")
    ap.add_argument("--source-root", action="append", default=[])
    ap.add_argument("--include-tests", action="store_true")
    ap.add_argument("-o", "--out", default=None)
    args = ap.parse_args()

    repo = Path(args.repo).resolve()
    roots = discover_source_roots(repo, args.source_root)
    records = run(repo, roots, args.include_tests)
    lines = "\n".join(json.dumps(r, sort_keys=True) for r in records)
    if args.out:
        Path(args.out).write_text(lines + "\n")
        kinds = {}
        for r in records:
            kinds[r["kind"]] = kinds.get(r["kind"], 0) + 1
        print(f"wrote {args.out}: " + ", ".join(f"{v} {k}" for k, v in sorted(kinds.items())))
    else:
        print(lines)


if __name__ == "__main__":
    main()
