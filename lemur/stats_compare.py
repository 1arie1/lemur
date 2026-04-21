"""
Compare z3 `-st` statistics across configs from a saved sweep directory.

Reads `<config>_s<seed>.stats.json` files written by `lemur sweep --stats --save`,
groups by config, and renders a side-by-side table of mean values per stat.
"""

import json
import re
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

from rich.console import Console
from rich.table import Table


_FNAME_RE = re.compile(r'^(?P<config>.+)_s(?P<seed>\d+)\.stats\.json$')


@dataclass
class StatsComparison:
    configs: list[str]            # ordered, as discovered
    # stat_key -> config -> list of values across seeds
    values: dict[str, dict[str, list[float]]]
    seed_counts: dict[str, int]   # config -> number of seeds contributing


def load_stats_dir(path: str) -> StatsComparison:
    """Load all `<config>_s<seed>.stats.json` files under `path`."""
    p = Path(path)
    if not p.is_dir():
        raise ValueError(f"{path}: not a directory")

    values: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    seeds_by_config: dict[str, set[int]] = defaultdict(set)
    config_order: list[str] = []

    for f in sorted(p.glob('*.stats.json')):
        m = _FNAME_RE.match(f.name)
        if not m:
            continue
        config = m.group('config')
        seed = int(m.group('seed'))
        if config not in config_order:
            config_order.append(config)
        try:
            stats = json.loads(f.read_text())
        except (json.JSONDecodeError, OSError):
            continue
        if not isinstance(stats, dict):
            continue
        seeds_by_config[config].add(seed)
        for key, val in stats.items():
            if isinstance(val, (int, float)):
                values[key][config].append(float(val))

    return StatsComparison(
        configs=config_order,
        values=dict(values),
        seed_counts={c: len(s) for c, s in seeds_by_config.items()},
    )


def _mean(vals: list[float]) -> float | None:
    return sum(vals) / len(vals) if vals else None


def _fmt(v: float | None) -> str:
    if v is None:
        return '—'
    if v == int(v) and abs(v) < 1e12:
        return f"{int(v)}"
    return f"{v:.3f}"


def render_rich(cmp: StatsComparison, console: Console, top: int | None = None) -> None:
    if not cmp.configs:
        console.print("[yellow]No .stats.json files found[/yellow]")
        return

    title = "z3 stats comparison (mean per config)"
    table = Table(title=title, pad_edge=True)
    table.add_column("stat", style="bold", no_wrap=True)
    for c in cmp.configs:
        n = cmp.seed_counts.get(c, 0)
        table.add_column(f"{c}\n(n={n})", justify="right")
    if len(cmp.configs) == 2:
        table.add_column("diff", justify="right")

    # Sort keys by max absolute mean descending (most-variable stats first);
    # falls back to key name as a tiebreak for stability.
    def key_sort(k: str) -> tuple[float, str]:
        means = [_mean(cmp.values[k].get(c, [])) or 0.0 for c in cmp.configs]
        return (-max(abs(m) for m in means) if means else 0.0, k)

    keys = sorted(cmp.values.keys(), key=key_sort)
    if top is not None:
        keys = keys[:top]

    for k in keys:
        row = [k]
        means = []
        for c in cmp.configs:
            m = _mean(cmp.values[k].get(c, []))
            means.append(m)
            row.append(_fmt(m))
        if len(cmp.configs) == 2:
            a, b = means
            if a is None or b is None or a == 0:
                row.append('—')
            else:
                pct = (b - a) / abs(a) * 100
                sign = '+' if pct >= 0 else ''
                row.append(f"{sign}{pct:.1f}%")
        table.add_row(*row)

    console.print(table)


def to_csv(cmp: StatsComparison, top: int | None = None) -> str:
    import csv
    import io
    buf = io.StringIO()
    w = csv.writer(buf)
    header = ['stat'] + cmp.configs
    if len(cmp.configs) == 2:
        header.append('diff_pct')
    w.writerow(header)

    keys = sorted(cmp.values.keys())
    if top is not None:
        keys = keys[:top]
    for k in keys:
        row = [k]
        means = []
        for c in cmp.configs:
            m = _mean(cmp.values[k].get(c, []))
            means.append(m)
            row.append('' if m is None else (f"{int(m)}" if m == int(m) and abs(m) < 1e12 else f"{m:.3f}"))
        if len(cmp.configs) == 2:
            a, b = means
            if a is None or b is None or a == 0:
                row.append('')
            else:
                row.append(f"{(b - a) / abs(a) * 100:.1f}")
        w.writerow(row)
    return buf.getvalue()


def to_json(cmp: StatsComparison, top: int | None = None) -> str:
    keys = sorted(cmp.values.keys())
    if top is not None:
        keys = keys[:top]
    out = {
        'configs': cmp.configs,
        'seed_counts': cmp.seed_counts,
        'stats': {
            k: {c: _mean(cmp.values[k].get(c, [])) for c in cmp.configs}
            for k in keys
        },
    }
    return json.dumps(out, indent=2)
