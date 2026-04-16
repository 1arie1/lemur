#!/usr/bin/env python3
"""
lemur-sweep: Run Z3 on a benchmark across seeds and configurations.

Usage:
  lemur-sweep benchmark.smt2 --seeds 0-15 --timeout 30 \
    --config "baseline: smt.arith.nl.nra_incremental=0" \
    --config "mode1: smt.arith.nl.nra_incremental=1"
"""

import argparse
import sys
from pathlib import Path

from lemur.sweep import RunConfig, run_sweep, parse_seed_range
from lemur.table import output, make_console


def main():
    parser = argparse.ArgumentParser(
        prog='lemur-sweep',
        description='Run Z3 across seeds and configurations, collect results.',
    )
    parser.add_argument('benchmark', help='SMT2 benchmark file')
    parser.add_argument('--seeds', default='0-3',
                        help='Seed range: 0-15, 1,3,5, or 0-3,7 (default: 0-3)')
    parser.add_argument('--timeout', type=int, default=30,
                        help='Timeout per run in seconds (default: 30)')
    parser.add_argument('--config', action='append', default=[],
                        help='Config spec: "name: key=val key=val". Repeatable.')
    parser.add_argument('--z3', default=None,
                        help='Path to z3 binary (default: ~/ag/z3/z3-edge/build/z3)')
    parser.add_argument('--jobs', '-j', type=int, default=1,
                        help='Parallel jobs (default: 1)')
    parser.add_argument('--trace', default=None,
                        help='Comma-separated trace tags to enable (e.g., nla_solver,nra)')
    parser.add_argument('--verbosity', type=int, default=2,
                        help='Z3 verbosity level, -v:N (default: 2, 0 to disable)')
    parser.add_argument('--z3-log', action='store_true',
                        help='Enable z3 AST trace log (trace=true). Requires --save.')
    parser.add_argument('--save', default=None,
                        help='Directory to save raw outputs and traces')
    parser.add_argument('--format', '-f', choices=['rich', 'csv', 'json'], default=None,
                        help='Output format (default: rich for TTY, csv otherwise)')
    parser.add_argument('--no-color', action='store_true',
                        help='Disable color output')
    parser.add_argument('--no-commands', action='store_true',
                        help='Hide z3 command lines from output')

    args = parser.parse_args()

    # Resolve benchmark path
    benchmark = Path(args.benchmark).resolve()
    if not benchmark.exists():
        print(f"Error: benchmark file not found: {benchmark}", file=sys.stderr)
        sys.exit(1)

    # Resolve z3 binary
    z3_bin = args.z3 or str(Path.home() / 'ag/z3/z3-edge/build/z3')
    z3_path = Path(z3_bin)
    if not z3_path.exists():
        print(f"Error: z3 binary not found: {z3_bin}", file=sys.stderr)
        sys.exit(1)
    z3_bin = str(z3_path.resolve())

    # Parse seeds
    seeds = parse_seed_range(args.seeds)

    # Parse configs
    configs = []
    if args.config:
        for spec in args.config:
            configs.append(RunConfig.parse(spec))
    else:
        configs.append(RunConfig(name='default', params={}))

    # Parse trace tags
    trace_tags = None
    if args.trace:
        trace_tags = [t.strip() for t in args.trace.split(',')]

    # Validate --z3-log requires --save
    if args.z3_log and not args.save:
        print("Error: --z3-log requires --save (log files need a destination)", file=sys.stderr)
        sys.exit(1)

    # Determine output format
    fmt = args.format
    show_progress = (fmt is None or fmt == 'rich') and sys.stdout.isatty()

    console = make_console(no_color=args.no_color) if fmt != 'csv' and fmt != 'json' else None

    if show_progress and console:
        console.print(f"[bold]lemur-sweep[/bold] {benchmark.name}")
        console.print(f"  z3: {z3_bin}")
        console.print(f"  seeds: {seeds[0]}-{seeds[-1]} ({len(seeds)} seeds)")
        console.print(f"  configs: {', '.join(c.name for c in configs)}")
        console.print(f"  timeout: {args.timeout}s, jobs: {args.jobs}")
        if trace_tags:
            console.print(f"  trace: {', '.join(trace_tags)}")
        console.print()

    table, results = run_sweep(
        z3_bin=z3_bin,
        smt_file=str(benchmark),
        seeds=seeds,
        configs=configs,
        timeout=args.timeout,
        jobs=args.jobs,
        trace_tags=trace_tags,
        verbosity=args.verbosity,
        z3_log=args.z3_log,
        save_dir=args.save,
        show_progress=show_progress,
    )

    if show_progress and console:
        console.print()

    output(table, fmt=fmt, console=console)

    # Print command lines for manual re-run
    if results and not args.no_commands:
        # Show one command per config (seed is easy to change)
        seen_configs = set()
        cmds = []
        for r in results:
            if r.config not in seen_configs and r.cmdline:
                seen_configs.add(r.config)
                cmds.append((r.config, r.cmdline))

        if fmt == 'json':
            import json
            cmd_data = {config: cmdline for config, cmdline in cmds}
            print(json.dumps({"commands": cmd_data}, indent=2))
        elif fmt == 'csv':
            print()
            for config, cmdline in cmds:
                print(f"# {config}")
                print(cmdline)
        else:
            if console:
                console.print()
                from rich.panel import Panel
                from rich.text import Text
                lines = Text()
                for i, (config, cmdline) in enumerate(cmds):
                    if i > 0:
                        lines.append("\n")
                    lines.append(f"# {config}\n", style="bold dim")
                    lines.append(cmdline)
                console.print(Panel(lines, title="Commands (change seeds to re-run)",
                                    expand=False))


if __name__ == '__main__':
    main()
