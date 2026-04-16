"""
Statistics computation from parsed trace entries.

Computes per-tag and per-function summaries from trace data.
"""

import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from lemur.parsers import TraceEntry, parse_trace, group_by_tag, group_by_function
from lemur.table import StatsOutput


@dataclass
class NumericStats:
    count: int
    total: float
    min_val: float
    max_val: float
    values: list[float]

    @property
    def avg(self) -> float:
        return self.total / self.count if self.count else 0

    @property
    def median(self) -> float:
        if not self.values:
            return 0
        s = sorted(self.values)
        n = len(s)
        if n % 2 == 1:
            return s[n // 2]
        return (s[n // 2 - 1] + s[n // 2]) / 2

    def summary(self) -> str:
        if self.count == 0:
            return "n/a"
        return f"min={self.min_val:.4g} avg={self.avg:.4g} median={self.median:.4g} max={self.max_val:.4g} (n={self.count})"


def extract_numbers(pattern: str, text: str) -> list[float]:
    """Extract numeric values matching a regex pattern from text."""
    return [float(m) for m in re.findall(pattern, text)]


def compute_tag_stats(entries: list[TraceEntry]) -> dict:
    """Compute summary statistics for a group of entries from the same tag."""
    by_func = group_by_function(entries)
    func_counts = Counter(e.function for e in entries)

    # Source file distribution
    source_files = Counter(
        Path(e.source_file).name for e in entries
    )

    return {
        'total_entries': len(entries),
        'functions': dict(func_counts.most_common()),
        'source_files': dict(source_files.most_common()),
    }


# --- Tag-specific analyzers ---

def analyze_nla_solver(entries: list[TraceEntry]) -> list[tuple[str, str]]:
    """Analyze nla_solver trace entries."""
    rows = []
    by_func = group_by_function(entries)

    rows.append(("Total entries", str(len(entries))))
    rows.append(("Unique functions", str(len(by_func))))

    # Check calls — extract call count
    if 'check' in by_func:
        check_entries = by_func['check']
        call_nums = []
        for e in check_entries:
            m = re.search(r'calls\s*=\s*(\d+)', e.body)
            if m:
                call_nums.append(int(m.group(1)))
        if call_nums:
            rows.append(("Check calls", f"{len(check_entries)} entries, max call# = {max(call_nums)}"))

    # Lemma builder — extract lemma types
    if '~lemma_builder' in by_func:
        lemma_entries = by_func['~lemma_builder']
        lemma_types = Counter()
        for e in lemma_entries:
            first_line = e.body.split('\n')[0].strip() if e.body else ''
            # e.g. "nla-pseudo-linear 2"
            parts = first_line.rsplit(' ', 1)
            if len(parts) >= 1:
                lemma_types[parts[0]] += 1
        rows.append(("Lemmas generated", str(len(lemma_entries))))
        for lt, cnt in lemma_types.most_common():
            rows.append((f"  {lt}", str(cnt)))

    # init_to_refine — mons to refine
    if 'init_to_refine' in by_func:
        refine_entries = by_func['init_to_refine']
        mon_counts = []
        for e in refine_entries:
            m = re.search(r'(\d+)\s+mons?\s+to\s+refine', e.body)
            if m:
                mon_counts.append(int(m.group(1)))
        if mon_counts:
            stats = _make_numeric_stats(mon_counts)
            rows.append(("Monomials to refine", stats.summary()))

    # Top functions by frequency
    func_counts = Counter(e.function for e in entries)
    rows.append(("", ""))  # spacer
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

    # Check results
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

    # Setup constraints
    if 'setup_solver_poly' in by_func:
        setup_entries = by_func['setup_solver_poly']
        rows.append(("Constraints set up", str(len(setup_entries))))

    # Top functions
    func_counts = Counter(e.function for e in entries)
    rows.append(("", ""))
    rows.append(("Top functions", ""))
    for func, cnt in func_counts.most_common(10):
        pct = 100 * cnt / len(entries)
        rows.append((f"  {func}", f"{cnt} ({pct:.1f}%)"))

    return rows


# Registry of tag-specific analyzers
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

    # File-level summary
    summary_rows = [
        ("Trace file", str(trace_path)),
        ("Total entries", str(len(entries))),
        ("Tags found", ", ".join(f"{t} ({len(es)})" for t, es in by_tag.items())),
    ]
    out.add_section("Summary", summary_rows)

    # Per-tag analysis
    for tag, tag_entries in by_tag.items():
        if tags and tag not in tags:
            continue

        # Filter by function if requested
        if functions:
            tag_entries = [e for e in tag_entries if e.function in set(functions)]
            if not tag_entries:
                continue

        analyzer = TAG_ANALYZERS.get(tag, lambda es: analyze_generic(tag, es))
        rows = analyzer(tag_entries)
        out.add_section(f"[{tag}]", rows)

    return out


def _make_numeric_stats(values: list[float | int]) -> NumericStats:
    if not values:
        return NumericStats(0, 0, 0, 0, [])
    fvals = [float(v) for v in values]
    return NumericStats(
        count=len(fvals),
        total=sum(fvals),
        min_val=min(fvals),
        max_val=max(fvals),
        values=fvals,
    )
