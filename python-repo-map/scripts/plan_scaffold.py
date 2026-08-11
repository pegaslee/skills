"""Turn the evidence pack into a ranked backlog of candidate work items.

The pack says what is true. This says what to do about it, in what order, with the
blast radius already computed. An agent handed this is judging and sequencing
rather than discovering, which is the difference between a plan and a wish list.

Every item is a *candidate*. The agent is expected to reject some, merge others,
and fill in verification. Nothing here decides anything on its own.

    python scripts/plan_scaffold.py .repomap
"""
from __future__ import annotations

import argparse
import json
from collections import defaultdict, deque
from pathlib import Path

# Phase ordering. Doing these out of order is the usual way a refactor stalls:
# deletions shrink the problem, side effects block every move, cycles block every
# layering decision, and interfaces cannot be narrowed inside a cycle.
PHASES = {
    "delete-dead": (0, "Delete what is provably unused"),
    "unwind-import-effects": (1, "Unwind import-time side effects"),
    "cut-cycle": (2, "Cut import cycles"),
    "extract-shared-contract": (3, "Extract duplicated implicit contracts"),
    "narrow-interface": (4, "Narrow module interfaces"),
    "isolate-io": (5, "Separate I/O from logic"),
}


def transitive_dependents(reverse: dict, seeds, cap: int = 200) -> list[str]:
    seen, q = set(seeds), deque(seeds)
    out = set()
    while q and len(out) < cap:
        node = q.popleft()
        for dep in reverse.get(node, ()):
            if dep not in seen:
                seen.add(dep)
                out.add(dep)
                q.append(dep)
    return sorted(out)


def name_clusters(importers_by_name: dict[str, list[str]]) -> list[list[str]]:
    """Split a module's exposed names into groups that share no consumer.

    If the names a module exposes fall into disjoint clusters, no single consumer
    needs the whole surface — which means the module is really several modules
    that happen to share a file. Disjoint clusters are the split, ready made.
    """
    names = list(importers_by_name)
    parent = {n: n for n in names}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    by_importer: dict[str, list[str]] = defaultdict(list)
    for name, importers in importers_by_name.items():
        for imp in importers:
            by_importer[imp].append(name)
    for shared in by_importer.values():
        for other in shared[1:]:
            union(shared[0], other)

    groups: dict[str, list[str]] = defaultdict(list)
    for n in names:
        groups[find(n)].append(n)
    return sorted((sorted(v) for v in groups.values()), key=len, reverse=True)


def build_items(pack: Path, min_surface: int, min_io_categories: int) -> list[dict]:
    graph = json.loads((pack / "02-graph.json").read_text())
    iface = json.loads((pack / "04-interfaces.json").read_text())
    bnd = _maybe(pack / "08-boundaries.json")
    declarations = declaration_modules(pack / "05-symbols.jsonl")
    git = _maybe(pack / "07-git.json")
    run = _maybe(pack / "09-runtime.json")

    reverse = graph.get("reverse", {})
    modules = graph["modules"]
    items: list[dict] = []
    seq = [0]

    def add(kind: str, title: str, **kw) -> dict:
        seq[0] += 1
        item = {
            "id": f"RM-{seq[0]:03d}",
            "kind": kind,
            "phase": PHASES[kind][0],
            "phase_name": PHASES[kind][1],
            "title": title,
            "blocked_by": [],
            "unblocks": [],
            "verify": None,
            **kw,
        }
        items.append(item)
        return item

    # --- phase 0: deletions, only ever from runtime evidence -----------------
    if run:
        for mod in run.get("dead_code_candidates", []):
            meta = modules.get(mod, {})
            add("delete-dead", f"Delete `{mod}`",
                target=mod,
                evidence=[f"never executed under any recorded entry point (09-runtime.md)",
                          f"imported by nothing (02-graph.json reverse)",
                          f"{meta.get('loc', '?')} loc at {meta.get('path', '?')}"],
                blast_radius=[],
                effort_loc=meta.get("loc", 0),
                caveat="Grep the symbol name as a plain string across config, templates "
                       "and YAML before deleting; dynamic reachability does not show up "
                       "in either the graph or coverage of the entry points you ran.")

    # --- phase 1: import-time side effects -----------------------------------
    for mod, info in sorted((bnd or {}).get("modules", {}).items(),
                            key=lambda kv: -len(kv[1].get("import_time_effects", []))):
        effects = info.get("import_time_effects", [])
        if not effects:
            continue
        deps = transitive_dependents(reverse, [mod])
        add("unwind-import-effects", f"Make `{mod}` safe to import",
            target=mod,
            evidence=[f"{len(effects)} module-level statements run at import time"]
                     + [f"L{e['line']}: {e['what']} ({e['why']})" for e in effects[:5]],
            blast_radius=deps,
            effort_loc=modules.get(mod, {}).get("loc", 0),
            caveat="Until this is done, moving this module changes behaviour in ways "
                   "no test will report.")

    # --- phase 2: cycles ------------------------------------------------------
    cycle_members: dict[str, str] = {}
    for comp in graph.get("cycles", []):
        inner = []
        for src in comp:
            for tgt, info in graph["edges"].get(src, {}).items():
                if tgt in comp:
                    inner.append((len(info["names"]), src, tgt, info))
        inner.sort()
        thin = inner[0] if inner else None
        deps = transitive_dependents(reverse, comp)
        label = " <-> ".join(comp[:3]) + (f" (+{len(comp) - 3})" if len(comp) > 3 else "")
        ev = [f"{len(comp)} modules, {len(inner)} internal edges"]
        if thin:
            _n, src, tgt, info = thin
            ev.append(f"thinnest edge `{src}` -> `{tgt}` carries {_n} name(s): "
                      + ", ".join(info["names"][:5]) + f" (L{info['lines'][0]})")
        item = add("cut-cycle", f"Break cycle: {label}",
                   target=comp,
                   evidence=ev,
                   blast_radius=deps,
                   effort_loc=sum(modules.get(m, {}).get("loc", 0) for m in comp),
                   options=[
                       "Move the thin edge's symbol down into a leaf both sides can depend on "
                       "(cheapest; adds no indirection)",
                       "Invert the dependency: define the protocol in the lower layer, inject "
                       "the implementation from above",
                       "Move the caller rather than the callee — the cycle may simply be "
                       "telling you the function is in the wrong module",
                   ],
                   caveat="A deferred import inside a function hides the cycle without "
                          "removing the coupling. Do not count that as done.")
        for m in comp:
            cycle_members[m] = item["id"]

    # --- phase 3: duplicated implicit contracts ------------------------------
    for row in (git or {}).get("coupling", [])[:40]:
        if row["import_edge"]:
            continue
        add("extract-shared-contract",
            f"Reconcile `{row['a']}` and `{row['b']}`",
            target=[row["module_a"], row["module_b"]],
            evidence=[f"changed together in {row['co_changes']} commits "
                      f"(confidence {row['confidence']}) with no import between them"],
            blast_radius=transitive_dependents(
                reverse, [m for m in (row["module_a"], row["module_b"]) if m]),
            effort_loc=sum(modules.get(m, {}).get("loc", 0)
                           for m in (row["module_a"], row["module_b"]) if m),
            caveat="Find the shared thing first — a field list, a wire format, a magic "
                   "string, an ordering assumption. If you cannot name it, this pair is "
                   "coincidence and the item should be dropped.")

    # --- phase 4: interfaces --------------------------------------------------
    for mod, info in sorted(iface["modules"].items(), key=lambda kv: -kv[1]["n_used_names"]):
        if info["n_used_names"] < min_surface:
            continue
        clusters = name_clusters(info["used_names"])
        declares_only = mod in declarations
        ev = [f"{info['n_used_names']} distinct names reached for by "
              f"{info['n_importers']} modules",
              f"declares __all__: {'yes' if info['declared_all'] else 'no'}",
              f"{info['loc']} loc, fan-out {info['fan_out']}"]
        options = []
        note = None
        if declares_only:
            note = ("This looks like a declarations module — mostly types, exceptions, "
                    "protocols or constants rather than behaviour. A wide surface is "
                    "normal here and narrowing it buys little. Probably reject this item "
                    "unless the declarations themselves belong to different layers.")
        if len(clusters) > 1:
            sizes = ", ".join(str(len(c)) for c in clusters[:4])
            ev.append(f"exposed names fall into {len(clusters)} clusters sharing no "
                      f"consumer (sizes {sizes}) — no importer needs the whole surface")
            options.append("Split along the consumer clusters listed in `clusters`; they "
                           "are disjoint, so the split breaks nothing that currently works")
        if info["loc"] < 120 and info["n_used_names"] > 8 and not declares_only:
            options.append("Wide surface over little code — check whether this is a "
                           "pass-through layer that should be deleted rather than narrowed")
        options.append("Add an explicit `__all__` / package `__init__` interface first, "
                       "then move consumers onto it one at a time")
        item = add("narrow-interface", f"Narrow the surface of `{mod}`",
                   target=mod,
                   evidence=ev,
                   clusters=clusters if len(clusters) > 1 else None,
                   note=note,
                   likely_reject=declares_only,
                   consumers=info["importers"],
                   blast_radius=info["importers"],
                   effort_loc=info["loc"],
                   options=options)
        if mod in cycle_members:
            item["blocked_by"].append(cycle_members[mod])

    # --- phase 5: I/O isolation ----------------------------------------------
    for mod, info in sorted((bnd or {}).get("modules", {}).items(),
                            key=lambda kv: -kv[1]["n_categories"]):
        if info["n_categories"] < min_io_categories:
            continue
        cats = list(info["categories"])
        add("isolate-io", f"Separate I/O from logic in `{mod}`",
            target=mod,
            evidence=[f"touches {len(cats)} boundary categories: {', '.join(cats)}",
                      f"{modules.get(mod, {}).get('loc', 0)} loc, "
                      f"max function complexity {modules.get(mod, {}).get('max_complexity', 0)}"],
            blast_radius=reverse.get(mod, []),
            effort_loc=modules.get(mod, {}).get("loc", 0),
            options=["Push the I/O to the edge and leave a pure core that can be exercised "
                     "without the network, the clock or the database — this is what makes "
                     "the module testable without trusting the existing suite"])

    # backfill unblocks
    by_id = {i["id"]: i for i in items}
    for item in items:
        for dep in item["blocked_by"]:
            by_id[dep]["unblocks"].append(item["id"])

    items.sort(key=lambda i: (i["phase"], bool(i.get("likely_reject")),
                              -len(i.get("blast_radius", [])), i["id"]))
    return items


def declaration_modules(symbols_path: Path, ratio: float = 0.7) -> set[str]:
    """Modules that mostly declare things rather than do things.

    Types, protocols, exceptions and constants modules legitimately expose many
    names, so flagging them as bloated interfaces is bad advice. Detect them by
    the shape of what they define: classes with no real behaviour, plus constants.
    """
    if not symbols_path.exists():
        return set()
    total: dict[str, int] = defaultdict(int)
    declarative: dict[str, int] = defaultdict(int)
    for line in symbols_path.read_text().splitlines():
        if not line.strip():
            continue
        rec = json.loads(line)
        kind = rec.get("kind")
        if kind not in ("class", "function", "constant"):
            continue
        mod = rec["module"]
        total[mod] += 1
        if kind == "constant":
            declarative[mod] += 1
        elif kind == "class":
            bases = " ".join(rec.get("bases") or [])
            decos = " ".join(rec.get("decorators") or [])
            shape = any(k in bases for k in ("Protocol", "Exception", "Error", "TypedDict",
                                             "NamedTuple", "Enum", "ABC", "BaseModel",
                                             "TypeVar", "Generic"))
            shape = shape or "dataclass" in decos
            real = [m for m in rec.get("methods") or []
                    if not m["sig"].split("(")[0].endswith(("__init__", "__repr__", "__str__"))]
            if shape or len(real) <= 1:
                declarative[mod] += 1
        elif kind == "function" and (rec.get("sig") or "").rstrip().endswith("..."):
            declarative[mod] += 1
    return {m for m, n in total.items() if n >= 4 and declarative[m] / n >= ratio}


def _maybe(path: Path):
    return json.loads(path.read_text()) if path.exists() else None


def render_md(items: list[dict]) -> str:
    L = ["# Candidate backlog", ""]
    L.append("Generated from the evidence pack. Every item is a **candidate**: reject the")
    L.append("ones that do not survive contact with the code, merge overlapping ones, and")
    L.append("fill in the verification for the ones you keep. An item with no verification")
    L.append("is not ready to work on.")
    L.append("")
    L.append("Phases are a dependency order, not a preference. Items in a later phase")
    L.append("often become unnecessary once earlier ones land, so re-run the map rather")
    L.append("than working the whole list top to bottom.")
    L.append("")

    by_phase: dict[int, list] = defaultdict(list)
    for item in items:
        by_phase[item["phase"]].append(item)

    L.append("| phase | items | total loc in scope |")
    L.append("|---|---|---|")
    for phase in sorted(by_phase):
        group = by_phase[phase]
        L.append(f"| {phase}. {group[0]['phase_name']} | {len(group)} | "
                 f"{sum(i.get('effort_loc', 0) for i in group):,} |")
    L.append("")

    for phase in sorted(by_phase):
        group = by_phase[phase]
        L.append(f"## Phase {phase} — {group[0]['phase_name']}")
        L.append("")
        for item in group:
            L.append(f"### {item['id']} — {item['title']}")
            L.append("")
            if item.get("note"):
                L.append(f"- **Note**: {item['note']}")
            for e in item["evidence"]:
                L.append(f"- **Evidence**: {e}" if e is item["evidence"][0] else f"  - {e}")
            radius = item.get("blast_radius") or []
            shown = ", ".join(f"`{m}`" for m in radius[:8])
            more = f" (+{len(radius) - 8} more)" if len(radius) > 8 else ""
            L.append(f"- **Blast radius**: {len(radius)} modules"
                     + (f" — {shown}{more}" if radius else ""))
            if item.get("clusters"):
                L.append("- **Consumer clusters** (disjoint — each is a candidate module):")
                for i, c in enumerate(item["clusters"][:5], 1):
                    L.append(f"  {i}. {', '.join('`' + n + '`' for n in c[:12])}"
                             + (f" (+{len(c) - 12})" if len(c) > 12 else ""))
            for opt in item.get("options", []) or []:
                L.append(f"- **Option**: {opt}")
            if item.get("caveat"):
                L.append(f"- **Caveat**: {item['caveat']}")
            if item["blocked_by"]:
                L.append(f"- **Blocked by**: {', '.join(item['blocked_by'])}")
            if item["unblocks"]:
                L.append(f"- **Unblocks**: {', '.join(item['unblocks'])}")
            L.append("- **Verify**: _fill in — which characterization test, which boundary "
                     "contract, which entry point re-run_")
            L.append("")
    return "\n".join(L) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("pack", nargs="?", default=".repomap")
    ap.add_argument("--min-surface", type=int, default=8,
                    help="minimum exposed names before a module is worth narrowing")
    ap.add_argument("--min-io-categories", type=int, default=3)
    args = ap.parse_args()

    pack = Path(args.pack).resolve()
    if not (pack / "02-graph.json").exists():
        raise SystemExit(f"no pack at {pack} — run map_repo.py first")
    items = build_items(pack, args.min_surface, args.min_io_categories)
    (pack / "10-BACKLOG.md").write_text(render_md(items))
    (pack / "10-backlog.json").write_text(json.dumps(items, indent=1, sort_keys=True))
    counts: dict[str, int] = defaultdict(int)
    for i in items:
        counts[i["kind"]] += 1
    print(f"wrote {pack / '10-BACKLOG.md'}: {len(items)} candidate items")
    for kind, n in sorted(counts.items(), key=lambda kv: PHASES[kv[0]][0]):
        print(f"  {kind}: {n}")


if __name__ == "__main__":
    main()
