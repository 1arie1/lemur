"""lemur: Z3 trace analysis and debugging toolkit."""

import argparse
import sys

from lemur.cli import sweep, stats, nla, tally, stats_compare, search

AGENT_HELP = """\
lemur: z3 trace analysis. six subcommands.

lemur sweep BENCH.smt2 --seeds 0-15 --timeout 30
  run z3 across seeds/configs. csv streams row-per-run (-f plain).
  config expansion:
    --config "name: key=val"    named config; repeatable; quote values with ws
    --grid key=v1,v2,v3         cross-products into configs; repeatable;
                                combines with --config as bases
    --split "name:<smt>"        inject SMT before (check-sat); cross-products
                                splits × configs × seeds; adds `split` column;
                                per-split closure summary at end; repeatable
  execution:
    -j N|auto                   parallel jobs (auto = os.cpu_count())
    --stop-on sat|unsat         abort on first matching result
    --fail-fast                 abort on first timeout/unknown/error
  output:
    --tally                     per-config aggregation after CSV
    --trace nla_solver,nra      capture .z3-trace (requires --save for files)
    --stats                     add z3 -st; with --save writes .stats.json
    --save DIR                  save stdout/stderr/trace/.stats.json per run
  sweep prints copy-pasteable z3 commands unless --no-commands.

lemur tally SWEEP.csv
  aggregate a saved sweep CSV. counts sat/unsat/to/unk/err and fastest-sat /
  fastest-unsat per config. recognizes an optional `split` column (from
  `sweep --split`) and groups by (split, config), with a per-split
  closure summary.

lemur stats-compare SAVE_DIR
  side-by-side mean of z3 -st stats across configs (reads
  <config>_s<seed>.stats.json from `sweep --stats --save`). diff % column
  when exactly two configs. --top N to cap rows.

lemur stats TRACE
  general trace stats: tag counts, function frequency, entry counts.
  --tag TAG to filter; --fn FUNC to filter; -f plain for parsing.

lemur nla TRACE
  nla_solver lemma analysis. default: summary with strategy distribution.
  modes (mutually exclusive):
    --list / -l              one line per lemma
    --detail N / -d N        full variable table for Nth lemma
    --details RANGE          ranges: 3, 5:10, 2-4, :5, 12:
    --no-varmap              show raw j-vars instead of SMT names
  filters (compose; renumber from 1):
    --strategy SUB           keep lemmas whose strategy contains SUB (repeatable)
    --min-vars N             keep lemmas with >= N variables
    --min-preconds N         keep lemmas with >= N preconditions
    --min-monomials N        keep lemmas with >= N monomials
    --top-by FIELD --top-n N  sort by {vars,preconds,monomials} desc, keep top N

lemur search TRACE [PATTERN] [--tag RE] [--fn RE] [-n] [--entries]
  regex search over trace body lines (grep-like). exit 0 match, 1 no match,
  2 regex error. --tag/--fn are also regexes (re.search; anchor with ^/$).
  PATTERN is optional — omit to dump every body line from filtered entries.
  --entries prints full header+body; -n prefixes with trace line numbers;
  -i/-v/-c/--max-count standard.

workflows:
# seed triage + per-config aggregation
  lemur sweep bench.smt2 --seeds 0-15 --timeout 30 --tally -f plain > out.csv
  lemur tally out.csv

# case-split investigation (prove disjunction UNSAT)
  lemur sweep bench.smt2 --seeds 0-7 --timeout 30 --split 'BLK25:(assert BLK__25)' --split 'BLK26:(assert BLK__26)' --stop-on unsat -f plain

# config A vs B on stats counters
  lemur sweep bench.smt2 --seeds 0-7 --timeout 30 --stats --save ./out --config 'A: smt.arith.solver=2' --config 'B: smt.arith.solver=6'
  lemur stats-compare ./out --top 20

# nla lemma drill-down
  lemur sweep bench.smt2 --seeds 3 --timeout 60 --trace nla_solver --save ./out
  lemur nla ./out/default_s3.trace --strategy grob --list
  lemur nla ./out/default_s3.trace --top-by vars --top-n 5
  lemur search ./out/default_s3.trace 'calls' --fn '^check$' --entries -n

use --help on any subcommand for full parameter details.
"""


def main():
    parser = argparse.ArgumentParser(
        prog='lemur',
        description='Z3 trace analysis and debugging toolkit.',
        epilog='AI agents: use --agent for terse usage guide.',
    )
    parser.add_argument('--agent', action='store_true',
                        help='Show agent-friendly usage guide')
    sub = parser.add_subparsers(dest='command')

    sweep.register(sub)
    stats.register(sub)
    nla.register(sub)
    tally.register(sub)
    stats_compare.register(sub)
    search.register(sub)

    args, remaining = parser.parse_known_args()

    if args.agent:
        print(AGENT_HELP)
        sys.exit(0)

    if args.command is None:
        parser.print_help()
        sys.exit(1)

    # Re-parse fully now that we know it's not --agent
    args = parser.parse_args()
    args.func(args)
