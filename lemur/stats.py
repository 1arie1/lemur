"""
Statistics computation from parsed trace entries.

Computes per-tag and per-function summaries from trace data.
General-purpose — no lemma analysis (that lives in lemur nla).
"""

import re
from collections import Counter
from pathlib import Path

from lemur.parsers import TraceEntry, parse_trace, group_by_tag, group_by_function
from lemur.table import StatsOutput


def analyze_nla_solver(entries: list[TraceEntry]) -> list[tuple[str, str]]:
    """Analyze nla_solver trace entries — counts and function frequency only."""
    rows = []
    by_func = group_by_function(entries)

    rows.append(("Total entries", str(len(entries))))
    rows.append(("Unique functions", str(len(by_func))))

    # Check calls
    if 'check' in by_func:
        check_entries = by_func['check']
        call_nums = []
        for e in check_entries:
            m = re.search(r'calls\s*=\s*(\d+)', e.body)
            if m:
                call_nums.append(int(m.group(1)))
        if call_nums:
            rows.append(("Check calls", f"{len(check_entries)} entries, max call# = {max(call_nums)}"))

    # Lemma count (just the count, detail is in `lemur nla`)
    if '~lemma_builder' in by_func:
        rows.append(("Lemmas", str(len(by_func['~lemma_builder']))))

    # init_to_refine
    if 'init_to_refine' in by_func:
        mon_counts = []
        for e in by_func['init_to_refine']:
            m = re.search(r'(\d+)\s+mons?\s+to\s+refine', e.body)
            if m:
                mon_counts.append(int(m.group(1)))
        if mon_counts:
            mn, mx = min(mon_counts), max(mon_counts)
            avg = sum(mon_counts) / len(mon_counts)
            rows.append(("Monomials to refine", f"min={mn} avg={avg:.1f} max={mx} (n={len(mon_counts)})"))

    # Top functions
    func_counts = Counter(e.function for e in entries)
    rows.append(("", ""))
    rows.append(("Top functions", ""))
    for func, cnt in func_counts.most_common(10):
        pct = 100 * cnt / len(entries)
        rows.append((f"  {func}", f"{cnt} ({pct:.1f}%)"))

    return rows


def analyze_nra(entries: list[TraceEntry]) -> list[tuple[str, str]]:
    """Analyze nra trace entries."""
    rows = []
    by_func = group_by_function(entries)

    rows.append(("Total entries", str(len(entries))))

    if 'check' in by_func:
        check_entries = by_func['check']
        results = Counter()
        for e in check_entries:
            m = re.search(r'nra result (\S+)', e.body)
            if m:
                results[m.group(1)] += 1
        rows.append(("NRA checks", str(len(check_entries))))
        for res, cnt in results.most_common():
            pct = 100 * cnt / len(check_entries)
            rows.append((f"  {res}", f"{cnt} ({pct:.1f}%)"))

    if 'setup_solver_poly' in by_func:
        rows.append(("Constraints set up", str(len(by_func['setup_solver_poly']))))

    func_counts = Counter(e.function for e in entries)
    rows.append(("", ""))
    rows.append(("Top functions", ""))
    for func, cnt in func_counts.most_common(10):
        pct = 100 * cnt / len(entries)
        rows.append((f"  {func}", f"{cnt} ({pct:.1f}%)"))

    return rows


TAG_ANALYZERS = {
    'nla_solver': analyze_nla_solver,
    'nra': analyze_nra,
}


def analyze_generic(tag: str, entries: list[TraceEntry]) -> list[tuple[str, str]]:
    """Generic analysis for tags without a specific analyzer."""
    rows = []
    rows.append(("Total entries", str(len(entries))))

    func_counts = Counter(e.function for e in entries)
    rows.append(("Unique functions", str(len(func_counts))))

    source_files = Counter(Path(e.source_file).name for e in entries)
    rows.append(("Source files", ", ".join(f"{f} ({c})" for f, c in source_files.most_common(5))))

    rows.append(("", ""))
    rows.append(("Top functions", ""))
    for func, cnt in func_counts.most_common(10):
        pct = 100 * cnt / len(entries)
        rows.append((f"  {func}", f"{cnt} ({pct:.1f}%)"))

    return rows


def build_stats_output(trace_path: str | Path, tags: list[str] | None = None,
                       functions: list[str] | None = None) -> StatsOutput:
    """Parse a trace file and build a StatsOutput with per-tag analysis."""
    entries = list(parse_trace(trace_path))
    by_tag = group_by_tag(entries)

    out = StatsOutput()

    summary_rows = [
        ("Trace file", str(trace_path)),
        ("Total entries", str(len(entries))),
        ("Tags found", ", ".join(f"{t} ({len(es)})" for t, es in by_tag.items())),
    ]
    out.add_section("Summary", summary_rows)

    for tag, tag_entries in by_tag.items():
        if tags and tag not in tags:
            continue
        if functions:
            tag_entries = [e for e in tag_entries if e.function in set(functions)]
            if not tag_entries:
                continue

        analyzer = TAG_ANALYZERS.get(tag, lambda es: analyze_generic(tag, es))
        rows = analyzer(tag_entries)
        out.add_section(f"[{tag}]", rows)

    return out
