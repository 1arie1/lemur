#!/usr/bin/env python3
"""
lemur-stats: Structured trace log analyzer.

Usage:
  lemur-stats trace.log
  lemur-stats trace.log --tag nla_solver
  lemur-stats trace.log --function check --format json
"""

import argparse
import sys

from lemur.stats import build_stats_output
from lemur.table import output, make_console


def main():
    parser = argparse.ArgumentParser(
        prog='lemur-stats',
        description='Parse Z3 trace files and output structured statistics.',
    )
    parser.add_argument('trace', help='Path to .z3-trace file')
    parser.add_argument('--tag', action='append', default=None,
                        help='Filter to specific tag(s). Repeatable.')
    parser.add_argument('--function', '--fn', action='append', default=None,
                        help='Filter to specific function(s). Repeatable.')
    parser.add_argument('--format', '-f', choices=['rich', 'csv', 'json'], default=None,
                        help='Output format (default: rich for TTY, csv otherwise)')
    parser.add_argument('--no-color', action='store_true',
                        help='Disable color output')

    args = parser.parse_args()

    from pathlib import Path
    trace_path = Path(args.trace)
    if not trace_path.exists():
        print(f"Error: trace file not found: {trace_path}", file=sys.stderr)
        sys.exit(1)

    stats = build_stats_output(trace_path, tags=args.tag, functions=args.function)

    console = make_console(no_color=args.no_color) if args.format != 'csv' and args.format != 'json' else None
    output(stats, fmt=args.format, console=console)


if __name__ == '__main__':
    main()
