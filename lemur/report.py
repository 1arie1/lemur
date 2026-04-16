"""
Rich-based rendering for lemma analysis.

Replaces raw ANSI formatting with Rich tables/panels for human output,
with CSV/JSON fallbacks for agent-friendly output.
"""

import math
import re
from collections import Counter
from typing import Sequence

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from lemur.lemma import LemmaAnalyzer, LemmaRecord, Precondition, VariableAssignment

# --- Constants ---

HUGE_BOUND_THRESHOLD = 10 ** 18
POW2_THRESHOLD = 2 ** 16
_VARIABLE_NAME_RE = re.compile(r'^(?P<prefix>[A-Za-z_]+?)(?P<number>\d+)$')

# Regex matching LP variable tokens (j25, _t95, etc.) for varmap substitution
_LP_VAR_TOKEN_RE = re.compile(r'\b(j\d+|_t\d+)\b')

# Max length for inline SMT expression before truncation
_SMT_EXPR_MAX_LEN = 40


def _apply_varmap(text: str, varmap: dict[str, str]) -> str:
    """Replace LP variable tokens (j25, _t95, ...) with their SMT names.

    Returns text unchanged if varmap is empty or no tokens match.
    """
    if not varmap:
        return text
    def _repl(m: re.Match) -> str:
        token = m.group(0)
        smt = varmap.get(token)
        if smt is None:
            return token
        if len(smt) > _SMT_EXPR_MAX_LEN:
            return smt[:_SMT_EXPR_MAX_LEN - 3] + '...'
        return smt
    return _LP_VAR_TOKEN_RE.sub(_repl, text)


STRATEGY_SHORT_NAMES = {
    'check': 'nlsat',
    'propagate value - lower bound of range is above value': 'low>val',
    'propagate value - upper bound of range is below value': 'hi<val',
    'propagate fixed - infeasible lra': 'fixed-infeas',
    'basic_lemma_for_mon_neutral_from_monomial_to_product': 'neutral-mon',
}


def _pp_strategy(name: str) -> str:
    return STRATEGY_SHORT_NAMES.get(name, name)


# --- Lemma summary (integrated into stats) ---

def lemma_summary_rows(records: list[LemmaRecord],
                       lemma_limit: int = 5,
                       delta_limit: int = 5,
                       varmap: dict[str, str] | None = None,
                       ) -> list[tuple[str, str]]:
    """Generate key-value rows summarizing lemma records, for StatsOutput."""
    vm = varmap or {}
    rows: list[tuple[str, str]] = []
    rows.append(('Lemmas generated', str(len(records))))

    # Strategy distribution
    strategy_counts = Counter(r.strategy for r in records if r.strategy)
    for strategy, cnt in strategy_counts.most_common():
        pct = 100 * cnt / len(records)
        rows.append((f'  {_pp_strategy(strategy)}', f'{cnt} ({pct:.1f}%)'))

    # Lemma previews
    if records:
        rows.append(('', ''))
        rows.append(('Lemma previews', ''))
        for i, record in enumerate(records[:lemma_limit], 1):
            strategy = _pp_strategy(record.strategy) if record.strategy else '<?>'
            conclusion = record.conclusion or '<no conclusion>'
            conclusion = _apply_varmap(conclusion, vm)
            hint = _monomial_hint(record, vm)
            rows.append((f'  {i}. {strategy}', f'==> {conclusion}{hint}'))
        if len(records) > lemma_limit:
            rows.append(('', f'  ... {len(records) - lemma_limit} more'))

    # Variable deltas
    deltas = _collect_variable_deltas(records)
    if deltas:
        rows.append(('', ''))
        rows.append(('Variable changes', ''))
        for i, delta in enumerate(deltas):
            if i >= delta_limit:
                rows.append(('', f'  ... {len(deltas) - delta_limit} more'))
                break
            rows.append(('', delta))

    return rows


# --- Lemma list (one line per lemma) ---

def render_lemma_list_rich(records: list[LemmaRecord], console: Console,
                           varmap: dict[str, str] | None = None):
    """Render a table with one row per lemma."""
    vm = varmap or {}
    table = Table(title=f'All lemmas ({len(records)})', show_lines=False)
    table.add_column('#', justify='right', style='dim')
    table.add_column('Strategy')
    table.add_column('Conclusion')
    table.add_column('Monomials', style='cyan')
    table.add_column('Precond', justify='right')
    table.add_column('Vars', justify='right')

    for i, r in enumerate(records, 1):
        strategy = _pp_strategy(r.strategy) if r.strategy else '<?>'
        conclusion = _apply_varmap(r.conclusion or '', vm)
        hint = _monomial_hint_short(r, vm)
        table.add_row(
            str(i),
            strategy,
            conclusion,
            hint,
            str(len(r.preconditions)),
            str(len(r.variables)),
        )
    console.print(table)


def render_lemma_list_plain(records: list[LemmaRecord],
                            varmap: dict[str, str] | None = None) -> str:
    """Render one line per lemma as plain text."""
    vm = varmap or {}
    lines = []
    for i, r in enumerate(records, 1):
        strategy = _pp_strategy(r.strategy) if r.strategy else '<?>'
        conclusion = _apply_varmap(r.conclusion or '<no conclusion>', vm)
        hint = _monomial_hint_short(r, vm)
        parts = [f'{i}.', strategy, f'==> {conclusion}']
        if hint:
            parts.append(f'[{hint}]')
        lines.append(' '.join(parts))
    return '\n'.join(lines)


def _monomial_hint_short(record: LemmaRecord,
                         varmap: dict[str, str] | None = None) -> str:
    """Short monomial hint: just variable names, no definitions."""
    if not record.monomials:
        return ''
    vm = varmap or {}
    names = []
    for m in sorted(record.monomials, key=lambda x: _variable_name_sort_key(x.variable)):
        names.append(vm.get(m.variable, m.variable))
    return ', '.join(names)


# --- Lemma detail table (Rich) ---

def render_lemma_detail(record: LemmaRecord, index: int,
                        console: Console,
                        varmap: dict[str, str] | None = None):
    """Render a detailed lemma table with Rich."""
    vm = varmap or {}

    # Title
    title_parts = [_pp_strategy(record.strategy) if record.strategy else '<unknown>']
    if record.lemma_id is not None:
        title_parts.append(f'#{record.lemma_id}')
    title = f'Lemma {index}: {" ".join(title_parts)}'

    # Preconditions panel
    if record.preconditions:
        prec_text = Text()
        for i, cond in enumerate(sorted(record.preconditions, key=_precondition_sort_key)):
            if i > 0:
                prec_text.append('\n')
            if cond.index is not None:
                prec_text.append(f'({cond.index}) ', style='dim')
            prec_text.append(_apply_varmap(cond.expression, vm))
        console.print(Panel(prec_text, title=f'{title} — Preconditions', expand=False))

    # Conclusion
    if record.conclusion:
        conclusion_text = _apply_varmap(record.conclusion, vm)
        console.print(Panel(
            Text(f'==> {conclusion_text}', style='bold'),
            title='Conclusion', expand=False,
        ))

    # Variable table
    if not record.variables:
        console.print('[dim]No variable assignments captured.[/dim]')
        return

    monomial_names = {m.variable for m in record.monomials}
    has_varmap = bool(vm)

    table = Table(title=f'{title} — Variables', show_lines=False)
    table.add_column('Variable', style='bold')
    if has_varmap:
        table.add_column('SMT Name', style='green')
    table.add_column('Value', justify='right')
    table.add_column('Basic', justify='center')
    table.add_column('Bounds')
    table.add_column('Definition')
    table.add_column('Root')

    for var in sorted(record.variables, key=_variable_sort_key):
        # Highlight monomials in cyan
        name_text = Text(var.name)
        if var.name in monomial_names:
            name_text.stylize('cyan bold')

        # Highlight root mismatch in red
        root_text = Text(var.root or '')
        if var.root and var.root != var.name:
            root_text.stylize('red')

        smt_name = _truncate_smt(vm.get(var.name, '')) if has_varmap else None

        row = [name_text]
        if has_varmap:
            row.append(smt_name)
        row.extend([
            format_value(var.value),
            'Y' if var.is_basic else '',
            format_bounds(var.bounds),
            _apply_varmap(var.definition, vm) if var.definition else '',
            root_text,
        ])
        table.add_row(*row)

    console.print(table)


def render_lemma_detail_plain(record: LemmaRecord, index: int,
                              varmap: dict[str, str] | None = None) -> str:
    """Render a lemma detail as plain text (for CSV/JSON modes)."""
    vm = varmap or {}
    lines = []
    title_parts = [_pp_strategy(record.strategy) if record.strategy else '<unknown>']
    if record.lemma_id is not None:
        title_parts.append(f'#{record.lemma_id}')
    lines.append(f'Lemma {index}: {" ".join(title_parts)}')

    if record.preconditions:
        lines.append('Preconditions:')
        for cond in sorted(record.preconditions, key=_precondition_sort_key):
            prefix = f'  ({cond.index}) ' if cond.index is not None else '  '
            lines.append(f'{prefix}{_apply_varmap(cond.expression, vm)}')

    if record.conclusion:
        lines.append(f'Conclusion: ==> {_apply_varmap(record.conclusion, vm)}')

    if record.variables:
        lines.append('Variables:')
        for var in sorted(record.variables, key=_variable_sort_key):
            parts = [f'  {var.name}']
            smt = vm.get(var.name)
            if smt:
                parts.append(f'({_truncate_smt(smt)})')
            if var.value:
                parts.append(f'= {format_value(var.value)}')
            if var.is_basic:
                parts.append('[basic]')
            if var.bounds:
                parts.append(format_bounds(var.bounds))
            if var.definition:
                parts.append(f':= {_apply_varmap(var.definition, vm)}')
            if var.root and var.root != var.name:
                parts.append(f'root={var.root}')
            lines.append(' '.join(parts))

    return '\n'.join(lines)


# --- Variable delta tracking ---

def _collect_variable_deltas(records: Sequence[LemmaRecord]) -> list[str]:
    previous: dict[str, VariableAssignment] = {}
    deltas: list[str] = []

    for idx, record in enumerate(records, 1):
        label = f'L{idx}'
        if record.strategy:
            label += f' ({_pp_strategy(record.strategy)})'

        for var in sorted(record.variables, key=_variable_sort_key):
            prev = previous.get(var.name)
            change = _describe_variable_change(prev, var)
            if change:
                deltas.append(f'{label}: {change}')
            previous[var.name] = var

    return deltas


def _describe_variable_change(prev: VariableAssignment | None,
                              curr: VariableAssignment) -> str | None:
    if prev is None:
        return None

    parts: list[str] = []

    if prev.bounds != curr.bounds:
        bounds_desc = _describe_bounds_change(prev.bounds, curr.bounds)
        if bounds_desc:
            parts.append(bounds_desc)

    if prev.value != curr.value:
        pv = format_value(prev.value) if prev.value else '<none>'
        cv = format_value(curr.value) if curr.value else '<none>'
        parts.append(f'value {pv} -> {cv}')

    if not parts:
        return None
    return f'{curr.name}: {"; ".join(parts)}'


# --- Bounds parsing and formatting ---

_BOUNDS_RE = re.compile(r'^\[(.*)\]$')


def _describe_bounds_change(prev_s: str | None, curr_s: str | None) -> str | None:
    if prev_s == curr_s:
        return None

    prev_t = _parse_bounds(prev_s)
    curr_t = _parse_bounds(curr_s)
    pp = format_bounds(prev_s) if prev_s else '<none>'
    pc = format_bounds(curr_s) if curr_s else '<none>'

    if prev_t is None or curr_t is None:
        return f'bounds {pp} -> {pc}'

    prev_lo, prev_hi = prev_t
    curr_lo, curr_hi = curr_t
    notes = []
    if prev_lo != curr_lo:
        tag = ' (raised)' if _both_finite(prev_lo, curr_lo) and curr_lo > prev_lo else ''
        notes.append(f'lower {_bound_repr(prev_lo)} -> {_bound_repr(curr_lo)}{tag}')
    if prev_hi != curr_hi:
        tag = ' (tightened)' if _both_finite(prev_hi, curr_hi) and curr_hi < prev_hi else ''
        notes.append(f'upper {_bound_repr(prev_hi)} -> {_bound_repr(curr_hi)}{tag}')
    if not notes:
        return None
    return f'bounds {pp} -> {pc} ({"; ".join(notes)})'


def _parse_bounds(bounds: str | None) -> tuple[float | None, float | None] | None:
    if not bounds:
        return None
    m = _BOUNDS_RE.match(bounds.strip())
    if not m:
        return None
    inner = m.group(1)
    if ',' not in inner:
        return None
    lo_s, hi_s = inner.split(',', 1)
    return _parse_bound(lo_s.strip()), _parse_bound(hi_s.strip())


def _parse_bound(token: str) -> float | None:
    if token in {'oo', '+oo', 'inf', '+inf'}:
        return math.inf
    if token in {'-oo', '-inf'}:
        return -math.inf
    if not token:
        return None
    try:
        return int(token)
    except ValueError:
        try:
            return float(token)
        except ValueError:
            return None


def _is_infinite(v) -> bool:
    if isinstance(v, float) and math.isinf(v):
        return True
    if isinstance(v, (int, float)):
        return abs(v) >= HUGE_BOUND_THRESHOLD
    return False


def _both_finite(a, b) -> bool:
    return a is not None and b is not None and not _is_infinite(a) and not _is_infinite(b)


def _bound_repr(v) -> str:
    if v is None:
        return '<none>'
    if _is_infinite(v):
        return 'oo' if v >= 0 else '-oo'
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v)


def format_bounds(bounds: str | None) -> str:
    if not bounds:
        return ''
    parsed = _parse_bounds(bounds)
    if parsed is None:
        return bounds
    lo, hi = parsed
    return f'[{_bound_repr(lo)}, {_bound_repr(hi)}]'


def format_value(value: str | None) -> str:
    """Format a numeric value, showing powers of 2 for large values."""
    if not value:
        return ''
    stripped = value.strip()
    if not stripped:
        return value
    try:
        number = int(stripped, 10)
    except ValueError:
        return value
    if number <= POW2_THRESHOLD or number <= 0:
        return value
    if number & (number - 1):
        return value
    exponent = number.bit_length() - 1
    # Preserve original whitespace
    prefix_len = len(value) - len(value.lstrip())
    suffix_len = len(value) - len(value.rstrip())
    prefix = value[:prefix_len]
    suffix = value[len(value) - suffix_len:] if suffix_len else ''
    return f'{prefix}2^{exponent}{suffix}'


# --- Sort keys ---

def _variable_sort_key(var: VariableAssignment) -> tuple:
    return _variable_name_sort_key(var.name or '')


def _variable_name_sort_key(name: str) -> tuple:
    m = _VARIABLE_NAME_RE.match(name)
    if m:
        return (0, m.group('prefix'), int(m.group('number')), name)
    return (1, name, 0, name)


def _precondition_sort_key(cond: Precondition) -> tuple:
    if cond.index is None:
        return (1, 0, cond.expression)
    return (0, cond.index, cond.expression)


def _truncate_smt(smt: str, max_len: int = _SMT_EXPR_MAX_LEN) -> str:
    """Truncate long SMT expressions for display."""
    if len(smt) <= max_len:
        return smt
    return smt[:max_len - 3] + '...'


def _monomial_hint(record: LemmaRecord,
                   varmap: dict[str, str] | None = None) -> str:
    if not record.monomials:
        return ''
    vm = varmap or {}
    parts = []
    for m in sorted(record.monomials, key=lambda x: _variable_name_sort_key(x.variable)):
        expr = _apply_varmap(m.expression, vm)
        var_display = vm.get(m.variable, m.variable)
        parts.append(f'{var_display} := {expr}')
    return f' [{"; ".join(parts)}]'


# --- Range parsing for --lemma-details ---

def parse_lemma_ranges(spec: str) -> list[tuple[int | None, int | None]]:
    """Parse range spec like '3', '5:10', '2-4', ':5', '12:' into (start, end) pairs."""
    ranges = []
    for part in spec.split(','):
        part = part.strip()
        if not part:
            continue
        if ':' in part:
            start_s, end_s = part.split(':', 1)
        elif '-' in part[1:]:
            start_s, end_s = part.split('-', 1)
        else:
            n = int(part)
            ranges.append((n, n))
            continue
        start = int(start_s.strip()) if start_s.strip() else None
        end = int(end_s.strip()) if end_s.strip() else None
        if start is not None and end is not None and end < start:
            raise ValueError(f"invalid range '{part}' (end before start)")
        ranges.append((start, end))
    return ranges


def expand_lemma_ranges(ranges: list[tuple[int | None, int | None]],
                        total: int) -> list[int]:
    """Expand ranges into a deduplicated list of 1-based indices."""
    indices = []
    for start, end in ranges:
        actual_start = 1 if start is None else start
        actual_end = total if end is None else min(end, total)
        if actual_end >= actual_start:
            indices.extend(range(actual_start, actual_end + 1))
        else:
            indices.append(actual_start)
    # Deduplicate preserving order
    seen = set()
    result = []
    for i in indices:
        if i not in seen:
            seen.add(i)
            result.append(i)
    return result
