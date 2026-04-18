"""lemur stats: Structured trace log analyzer."""

import sys
from pathlib import Path

from lemur.stats import build_stats_output
from lemur.table import output, make_console
from lemur.report import (
    render_lemma_detail, render_lemma_detail_plain,
    render_lemma_list_rich, render_lemma_list_plain,
    parse_lemma_ranges, expand_lemma_ranges,
    humanize_varmap,
)


def register(subparsers):
    p = subparsers.add_parser('stats', help='Analyze Z3 trace files')
    p.add_argument('trace', help='Path to .z3-trace file')
    p.add_argument('--tag', action='append', default=None,
                   help='Filter to specific tag(s). Repeatable.')
    p.add_argument('--function', '--fn', action='append', default=None,
                   help='Filter to specific function(s). Repeatable.')
    p.add_argument('--format', '-f', choices=['rich', 'plain', 'json'], default=None,
                   help='Output format (default: rich for TTY, plain otherwise)')
    p.add_argument('--no-color', action='store_true',
                   help='Disable color output')

    lemma = p.add_argument_group('lemma analysis')
    lemma.add_argument('--lemma-limit', type=int, default=5,
                       help='Number of lemma previews to show (default: 5)')
    lemma.add_argument('--lemma-delta-limit', type=int, default=5,
                       help='Max variable change lines to show (default: 5)')
    lemma.add_argument('--lemma-list', action='store_true',
                       help='List all lemmas, one per line')
    lemma.add_argument('--lemma-detail', type=int, default=None,
                       help='Show full variable table for Nth lemma (1-based)')
    lemma.add_argument('--lemma-details', type=str, default=None,
                       help='Show detail for lemma ranges: 3, 5:10, 2-4, :5, 12:')
    lemma.add_argument('--no-varmap', action='store_true',
                       help='Ignore varmap data; show raw LP j-variables')
    p.set_defaults(func=run)


def run(args):
    trace_path = Path(args.trace)
    if not trace_path.exists():
        print(f"Error: trace file not found: {trace_path}", file=sys.stderr)
        sys.exit(1)

    stats_out, lemma_records, varmap = build_stats_output(
        trace_path, tags=args.tag, functions=args.function,
        lemma_limit=args.lemma_limit, delta_limit=args.lemma_delta_limit,
    )
    varmap = humanize_varmap(varmap)
    if args.no_varmap:
        varmap = {}

    fmt = args.format
    use_rich = fmt is None or fmt == 'rich'
    console = make_console(no_color=args.no_color) if use_rich else None

    # Collect lemma detail indices
    detail_ranges = []
    if args.lemma_details:
        detail_ranges.extend(parse_lemma_ranges(args.lemma_details))
    if args.lemma_detail is not None:
        detail_ranges.append((args.lemma_detail, args.lemma_detail))
    detail_indices = expand_lemma_ranges(detail_ranges, len(lemma_records)) if detail_ranges else []

    if not detail_indices and not args.lemma_list:
        output(stats_out, fmt=fmt, console=console)

    # Render lemma list
    if args.lemma_list and lemma_records:
        if console and use_rich:
            render_lemma_list_rich(lemma_records, console, varmap=varmap)
        else:
            print(render_lemma_list_plain(lemma_records, varmap=varmap))
    elif args.lemma_list and not lemma_records:
        print("No lemma records found (is nla_solver tag present?)", file=sys.stderr)

    # Render lemma details
    if detail_indices and lemma_records:
        for idx in detail_indices:
            i = idx - 1
            if i < 0 or i >= len(lemma_records):
                print(f"[warn] lemma index {idx} out of range (1-{len(lemma_records)})",
                      file=sys.stderr)
                continue
            if console and use_rich:
                console.print()
                render_lemma_detail(lemma_records[i], idx, console, varmap=varmap)
            else:
                print()
                print(render_lemma_detail_plain(lemma_records[i], idx, varmap=varmap))
    elif detail_indices and not lemma_records:
        print("No lemma records found (is nla_solver tag present?)", file=sys.stderr)
