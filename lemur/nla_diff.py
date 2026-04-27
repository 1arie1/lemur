"""
Structural diff between two nla_solver traces.

`lemur nla-diff` compares headline NLA-investigation metrics across two
trace files: total entries, lemma counts, per-strategy distribution,
the is_patch_blocked rate, and top-fingerprint stability. The motivating
question is the kind a Certora investigator hits constantly — "why does
seed N close in 4s and seed M time out?" — where the answer comes from
deltas in solver behavior, not the SMT input.

Schema sketched in `~/ag/z3/z3-research/lemur/lemur-tooling-gaps-from-session.md`
(Gap C). Composes with the existing `lemur.lemma.LemmaAnalyzer` + the
`parse_trace` infrastructure in `lemur.parsers`.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from lemur.lemma import LemmaAnalyzer, LemmaRecord
from lemur.parsers import TraceEntry, group_by_function, parse_trace


# --- Metric extraction -------------------------------------------------------


@dataclass
class TraceMetrics:
    path: str
    total_entries: int = 0
    nla_entries: int = 0
    function_counts: Counter = field(default_factory=Counter)
    lemma_count: int = 0
    strategy_counts: Counter = field(default_factory=Counter)
    patch_blocked: int = 0       # bodies containing 'blocked' AND not 'no block'
    patch_blocked_total: int = 0 # all is_patch_blocked entries
    fingerprints: Counter = field(default_factory=Counter)
    var_counts: list = field(default_factory=list)
    precondition_counts: list = field(default_factory=list)
    monomial_counts: list = field(default_factory=list)


def _is_blocked_body(body: str) -> bool:
    """Body of an `is_patch_blocked` entry. `blocked, for ...` means true
    block; `u == m_patched_var, no block` is the negative case."""
    return 'blocked' in body and 'no block' not in body


def _fingerprint(rec: LemmaRecord) -> str:
    """Stable, human-readable identity for a lemma. Strategy + conclusion
    captures most of the duplicate-lemma structure that matters for
    "did the same family fire more times" questions."""
    concl = rec.conclusion or '<no-conclusion>'
    return f"{rec.strategy} ==> {concl}"


def compute_metrics(trace_path: str) -> TraceMetrics:
    """Single-pass metrics over a .z3-trace file."""
    m = TraceMetrics(path=trace_path)
    entries: list[TraceEntry] = list(parse_trace(trace_path))
    m.total_entries = len(entries)

    nla_entries = [e for e in entries if e.tag == 'nla_solver']
    m.nla_entries = len(nla_entries)

    by_fn = group_by_function(nla_entries)
    m.function_counts = Counter({fn: len(es) for fn, es in by_fn.items()})

    # is_patch_blocked rate: numerator counts bodies that actually mark the
    # variable as blocked. Denominator is every is_patch_blocked entry.
    for e in by_fn.get('is_patch_blocked', []):
        m.patch_blocked_total += 1
        if _is_blocked_body(e.body):
            m.patch_blocked += 1

    # Lemma extraction. The analyzer walks ~lemma_builder entries (which
    # appear under their own tag); pass the full entry list so it sees them.
    lemmas = list(LemmaAnalyzer(entries).extract())
    m.lemma_count = len(lemmas)
    for rec in lemmas:
        m.strategy_counts[rec.strategy or '<unknown>'] += 1
        m.fingerprints[_fingerprint(rec)] += 1
        m.var_counts.append(len(rec.variables))
        m.precondition_counts.append(len(rec.preconditions))
        m.monomial_counts.append(len(rec.monomials))

    return m


# --- Diff rendering ----------------------------------------------------------


@dataclass
class Row:
    label: str
    a: object
    b: object
    delta: str  # pre-formatted ('+12', '-6%', '+12pp', '=', etc.)


def _fmt_count_delta(a: int, b: int) -> str:
    if a == b:
        return '='
    d = b - a
    sign = '+' if d > 0 else ''
    if a > 0:
        pct = (d / a) * 100
        return f"{sign}{d} ({sign}{pct:.0f}%)"
    return f"{sign}{d}"


def _fmt_rate_delta(a_num: int, a_den: int, b_num: int, b_den: int) -> tuple[str, str, str]:
    a_pct = (a_num / a_den * 100) if a_den else 0.0
    b_pct = (b_num / b_den * 100) if b_den else 0.0
    delta = b_pct - a_pct
    sign = '+' if delta > 0 else ('' if delta == 0 else '')
    if delta == 0:
        delta_s = '='
    else:
        delta_s = f"{'+' if delta > 0 else ''}{delta:.0f}pp"
    return f"{a_pct:.0f}% ({a_num}/{a_den})", f"{b_pct:.0f}% ({b_num}/{b_den})", delta_s


def diff(a: TraceMetrics, b: TraceMetrics, *, top: int = 5) -> list[Row]:
    """Build the ordered row list. `top` caps the number of strategies
    and fingerprints surfaced."""
    rows: list[Row] = []

    rows.append(Row("total nla_solver entries", a.nla_entries, b.nla_entries,
                    _fmt_count_delta(a.nla_entries, b.nla_entries)))
    rows.append(Row("~lemma_builder entries (lemmas)",
                    a.lemma_count, b.lemma_count,
                    _fmt_count_delta(a.lemma_count, b.lemma_count)))

    # Function counts: surface 'check' explicitly (the most common headline
    # signal), then top remaining functions by max(A, B) count.
    if 'check' in a.function_counts or 'check' in b.function_counts:
        ca, cb = a.function_counts.get('check', 0), b.function_counts.get('check', 0)
        rows.append(Row("check function calls", ca, cb, _fmt_count_delta(ca, cb)))

    union_fns = set(a.function_counts) | set(b.function_counts)
    union_fns.discard('check')
    ranked = sorted(
        union_fns,
        key=lambda fn: -max(a.function_counts.get(fn, 0),
                            b.function_counts.get(fn, 0)),
    )
    for fn in ranked[:top]:
        ca, cb = a.function_counts.get(fn, 0), b.function_counts.get(fn, 0)
        rows.append(Row(f"function: {fn}", ca, cb, _fmt_count_delta(ca, cb)))

    # is_patch_blocked rate
    if a.patch_blocked_total or b.patch_blocked_total:
        a_disp, b_disp, d = _fmt_rate_delta(
            a.patch_blocked, a.patch_blocked_total,
            b.patch_blocked, b.patch_blocked_total)
        rows.append(Row("is_patch_blocked rate", a_disp, b_disp, d))

    # Strategy distribution: union of strategies, ranked by max(A, B).
    strategies = set(a.strategy_counts) | set(b.strategy_counts)
    ranked_strats = sorted(
        strategies,
        key=lambda s: -max(a.strategy_counts.get(s, 0),
                           b.strategy_counts.get(s, 0)),
    )
    for s in ranked_strats[:top]:
        ca, cb = a.strategy_counts.get(s, 0), b.strategy_counts.get(s, 0)
        rows.append(Row(f"strategy: {s}", ca, cb, _fmt_count_delta(ca, cb)))

    # Top fingerprints — use A's top-N as the ordering, surface B's count
    # at the same fingerprint. Annotate "stable" when the fingerprint is
    # also in B's top-N at the same rank.
    a_top = a.fingerprints.most_common(top)
    b_top_ranks = {fp: i for i, (fp, _) in enumerate(b.fingerprints.most_common(top))}
    for i, (fp, a_n) in enumerate(a_top):
        b_n = b.fingerprints.get(fp, 0)
        same_rank = i in b_top_ranks.values() and b_top_ranks.get(fp) == i
        note = '  (stable rank)' if same_rank else ''
        # Truncate long conclusions for display readability.
        label = fp if len(fp) <= 60 else fp[:57] + '...'
        rows.append(Row(f"top-fp({i+1}): {label}{note}",
                        a_n, b_n, _fmt_count_delta(a_n, b_n)))

    return rows


# --- Output ------------------------------------------------------------------


def render_plain(rows: list[Row], a_path: str, b_path: str) -> str:
    if not rows:
        return f"# A: {a_path}\n# B: {b_path}\n(no metrics)\n"

    label_w = max(len("metric"), max(len(r.label) for r in rows))
    a_w = max(len("A"), max(len(str(r.a)) for r in rows))
    b_w = max(len("B"), max(len(str(r.b)) for r in rows))
    d_w = max(len("delta"), max(len(r.delta) for r in rows))

    lines = [
        f"# A: {a_path}",
        f"# B: {b_path}",
        f"{'metric':<{label_w}}  {'A':>{a_w}}  {'B':>{b_w}}  {'delta':>{d_w}}",
        f"{'-' * label_w}  {'-' * a_w}  {'-' * b_w}  {'-' * d_w}",
    ]
    for r in rows:
        lines.append(
            f"{r.label:<{label_w}}  {str(r.a):>{a_w}}  "
            f"{str(r.b):>{b_w}}  {r.delta:>{d_w}}"
        )
    return '\n'.join(lines) + '\n'


def to_jsonable(rows: list[Row], a_path: str, b_path: str) -> dict:
    return {
        'a': str(Path(a_path).resolve()),
        'b': str(Path(b_path).resolve()),
        'rows': [
            {'label': r.label, 'a': r.a, 'b': r.b, 'delta': r.delta}
            for r in rows
        ],
    }
