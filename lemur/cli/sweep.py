"""lemur sweep: Run Z3 on a benchmark across seeds and configurations."""

import csv
import json
import sys
from pathlib import Path

from rich.panel import Panel
from rich.text import Text

from lemur.sweep import RunConfig, run_sweep, parse_seed_range
from lemur.table import output, make_console


def register(subparsers):
    p = subparsers.add_parser('sweep', help='Run Z3 across seeds and configurations',
                               epilog='AI agents: use `lemur --agent` for terse usage guide.')
    p.add_argument('benchmark', help='SMT2 benchmark file')
    p.add_argument('--seeds', default='0-3',
                   help='Seed range: 0-15, 1,3,5, or 0-3,7 (default: 0-3)')
    p.add_argument('--timeout', type=int, default=30,
                   help='Timeout per run in seconds (default: 30)')
    p.add_argument('--config', action='append', default=[],
                   help='Config spec: "name: key=val key=val". Repeatable. '
                        'Quote values with whitespace: key="(then simplify smt)".')
    p.add_argument('--z3', default=None,
                   help='Path to z3 binary (default: ~/ag/z3/z3-edge/build/z3)')
    p.add_argument('--jobs', '-j', type=int, default=1,
                   help='Parallel jobs (default: 1)')
    p.add_argument('--trace', default=None,
                   help='Comma-separated trace tags to enable (e.g., nla_solver,nra)')
    p.add_argument('--verbosity', type=int, default=2,
                   help='Z3 verbosity level, -v:N (default: 2, 0 to disable)')
    p.add_argument('--z3-log', action='store_true',
                   help='Enable z3 AST trace log (trace=true). Requires --save.')
    p.add_argument('--save', default=None,
                   help='Directory to save raw outputs and traces')
    p.add_argument('--format', '-f', choices=['rich', 'plain', 'json'], default=None,
                   help='Output format (default: rich for TTY, plain otherwise)')
    p.add_argument('--no-color', action='store_true',
                   help='Disable color output')
    p.add_argument('--no-commands', action='store_true',
                   help='Hide z3 command lines from output')
    p.set_defaults(func=run)


def run(args):
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
    effective_fmt = fmt if fmt is not None else ('rich' if sys.stdout.isatty() else 'plain')
    show_progress = effective_fmt == 'rich' and sys.stdout.isatty()

    console = make_console(no_color=args.no_color) if effective_fmt == 'rich' else None

    if show_progress and console:
        console.print(f"[bold]lemur sweep[/bold] {benchmark.name}")
        console.print(f"  z3: {z3_bin}")
        console.print(f"  seeds: {seeds[0]}-{seeds[-1]} ({len(seeds)} seeds)")
        console.print(f"  configs: {', '.join(c.name for c in configs)}")
        console.print(f"  timeout: {args.timeout}s, jobs: {args.jobs}")
        if trace_tags:
            console.print(f"  trace: {', '.join(trace_tags)}")
        console.print()

    # Stream CSV rows to stdout as they complete (plain format only).
    on_result = None
    if effective_fmt == 'plain':
        writer = csv.writer(sys.stdout)
        writer.writerow(["config", "seed", "status", "time_s"])
        sys.stdout.flush()

        def on_result(r):
            writer.writerow([r.config, r.seed, r.status, f"{r.time_s:.3f}"])
            sys.stdout.flush()

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
        on_result=on_result,
    )

    if show_progress and console:
        console.print()

    # Plain rows were already streamed; only render final table for rich/json.
    if effective_fmt != 'plain':
        output(table, fmt=effective_fmt, console=console)

    # Print command lines for manual re-run
    if results and not args.no_commands:
        seen_configs = set()
        cmds = []
        for r in results:
            if r.config not in seen_configs and r.cmdline:
                seen_configs.add(r.config)
                cmds.append((r.config, r.cmdline))

        if fmt == 'json':
            cmd_data = {config: cmdline for config, cmdline in cmds}
            print(json.dumps({"commands": cmd_data}, indent=2))
        elif fmt == 'plain':
            print()
            for config, cmdline in cmds:
                print(f"# {config}")
                print(cmdline)
        else:
            if console:
                console.print()
                lines = Text()
                for i, (config, cmdline) in enumerate(cmds):
                    if i > 0:
                        lines.append("\n")
                    lines.append(f"# {config}\n", style="bold dim")
                    lines.append(cmdline)
                console.print(Panel(lines, title="Commands (change seeds to re-run)",
                                    expand=False))
