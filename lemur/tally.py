"""
Aggregate sweep results into a per-config tally.

Counts by status (sat, unsat, timeout, unknown, error) plus the fastest
sat / unsat time per config (with the seed that produced it).
"""

import csv
import io
import json
from dataclasses import dataclass, field

from rich.console import Console
from rich.table import Table


@dataclass
class TallyRow:
    config: str
    total: int = 0
    sat: int = 0
    unsat: int = 0
    timeout: int = 0
    unknown: int = 0
    error: int = 0
    # (time_s, seed) of fastest successful run per status, or None
    fastest_sat: tuple[float, int] | None = None
    fastest_unsat: tuple[float, int] | None = None


@dataclass
class Tally:
    rows: list[TallyRow] = field(default_factory=list)


def compute_tally(results) -> Tally:
    """Build a Tally from an iterable of (config, seed, status, time_s) tuples.

    Accepts either RunResult objects or plain tuples/dicts with those keys.
    """
    by_config: dict[str, TallyRow] = {}
    for r in results:
        if hasattr(r, 'config'):
            config, seed, status, time_s = r.config, r.seed, r.status, r.time_s
        elif isinstance(r, dict):
            config, seed, status, time_s = r['config'], r['seed'], r['status'], r['time_s']
        else:
            config, seed, status, time_s = r
        row = by_config.setdefault(config, TallyRow(config=config))
        row.total += 1
        if status == 'sat':
            row.sat += 1
            if row.fastest_sat is None or time_s < row.fastest_sat[0]:
                row.fastest_sat = (float(time_s), int(seed))
        elif status == 'unsat':
            row.unsat += 1
            if row.fastest_unsat is None or time_s < row.fastest_unsat[0]:
                row.fastest_unsat = (float(time_s), int(seed))
        elif status == 'timeout':
            row.timeout += 1
        elif status == 'unknown':
            row.unknown += 1
        else:
            row.error += 1
    return Tally(rows=list(by_config.values()))


def _fmt_fastest(val: tuple[float, int] | None) -> str:
    if val is None:
        return 'n/a'
    time_s, seed = val
    return f"{time_s:.3f}s (seed {seed})"


def render_rich(tally: Tally, console: Console) -> None:
    table = Table(title="Tally", pad_edge=True)
    table.add_column("config", style="bold", no_wrap=True)
    table.add_column("total", justify="right")
    table.add_column("sat", justify="right", style="green")
    table.add_column("unsat", justify="right", style="cyan")
    table.add_column("to", justify="right", style="red")
    table.add_column("unknown", justify="right", style="yellow")
    table.add_column("err", justify="right")
    table.add_column("fastest-sat", justify="right")
    table.add_column("fastest-unsat", justify="right")
    for row in tally.rows:
        table.add_row(
            row.config,
            str(row.total),
            str(row.sat),
            str(row.unsat),
            str(row.timeout),
            str(row.unknown),
            str(row.error),
            _fmt_fastest(row.fastest_sat),
            _fmt_fastest(row.fastest_unsat),
        )
    console.print(table)


def to_csv(tally: Tally) -> str:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(['config', 'total', 'sat', 'unsat', 'timeout', 'unknown',
                'error', 'fastest_sat_time_s', 'fastest_sat_seed',
                'fastest_unsat_time_s', 'fastest_unsat_seed'])
    for row in tally.rows:
        fs = row.fastest_sat
        fu = row.fastest_unsat
        w.writerow([
            row.config, row.total, row.sat, row.unsat, row.timeout,
            row.unknown, row.error,
            f"{fs[0]:.3f}" if fs else '', fs[1] if fs else '',
            f"{fu[0]:.3f}" if fu else '', fu[1] if fu else '',
        ])
    return buf.getvalue()


def to_json(tally: Tally) -> str:
    data = []
    for row in tally.rows:
        fs = row.fastest_sat
        fu = row.fastest_unsat
        data.append({
            'config': row.config,
            'total': row.total,
            'sat': row.sat,
            'unsat': row.unsat,
            'timeout': row.timeout,
            'unknown': row.unknown,
            'error': row.error,
            'fastest_sat': {'time_s': round(fs[0], 3), 'seed': fs[1]} if fs else None,
            'fastest_unsat': {'time_s': round(fu[0], 3), 'seed': fu[1]} if fu else None,
        })
    return json.dumps(data, indent=2)


def read_sweep_csv(path: str) -> list[dict]:
    """Read a sweep CSV (columns: config,seed,status,time_s) into row dicts."""
    rows = []
    with open(path, newline='') as f:
        reader = csv.DictReader(f)
        required = {'config', 'seed', 'status', 'time_s'}
        missing = required - set(reader.fieldnames or [])
        if missing:
            raise ValueError(
                f"{path}: missing columns {sorted(missing)}; "
                f"expected sweep CSV with columns {sorted(required)}"
            )
        for r in reader:
            rows.append({
                'config': r['config'],
                'seed': int(r['seed']),
                'status': r['status'],
                'time_s': float(r['time_s']),
            })
    return rows
