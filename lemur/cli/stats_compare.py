"""lemur stats-compare: Compare z3 stats across configs from a saved sweep dir."""

import sys
from pathlib import Path

from lemur.stats_compare import load_stats_dir, render_rich, to_csv, to_json
from lemur.table import make_console


def register(subparsers):
    p = subparsers.add_parser(
        'stats-compare',
        help='Compare z3 -st statistics across configs from a saved sweep dir',
        epilog='AI agents: use `lemur --agent` for terse usage guide.',
    )
    p.add_argument('directory',
                   help='Directory with <config>_s<seed>.stats.json files '
                        '(as written by `lemur sweep --stats --save DIR`)')
    p.add_argument('--top', type=int, default=None,
                   help='Limit output to the N stats with the largest mean magnitude')
    p.add_argument('--format', '-f', choices=['rich', 'plain', 'json'], default=None,
                   help='Output format (default: rich for TTY, plain otherwise)')
    p.add_argument('--no-color', action='store_true', help='Disable color output')
    p.set_defaults(func=run)


def run(args):
    path = Path(args.directory).resolve()
    if not path.exists():
        print(f"Error: directory not found: {path}", file=sys.stderr)
        sys.exit(1)

    try:
        cmp = load_stats_dir(str(path))
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    if not cmp.configs:
        print(f"Error: no *.stats.json files found in {path}", file=sys.stderr)
        sys.exit(1)

    fmt = args.format
    effective_fmt = fmt if fmt is not None else ('rich' if sys.stdout.isatty() else 'plain')

    if effective_fmt == 'rich':
        render_rich(cmp, make_console(no_color=args.no_color), top=args.top)
    elif effective_fmt == 'plain':
        print(to_csv(cmp, top=args.top), end='')
    elif effective_fmt == 'json':
        print(to_json(cmp, top=args.top))
