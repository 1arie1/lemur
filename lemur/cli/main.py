"""lemur: Z3 trace analysis and debugging toolkit."""

import argparse
import sys

from lemur.cli import sweep, stats, nla

AGENT_HELP = """\
lemur: z3 trace analysis. three subcommands.

lemur sweep BENCH.smt2 --seeds 0-15 --timeout 30
  run z3 across seeds/configs. find interesting seeds.
  --config "name: key=val" repeatable. -j N for parallel.
  --trace nla_solver,nra to capture .z3-trace. --save DIR to keep outputs.
  -f plain for machine output (csv). shows copy-pasteable z3 commands.

lemur stats TRACE
  general trace stats: tag counts, function frequency, entry counts.
  --tag TAG to filter. --fn FUNC to filter. -f plain for parsing.

lemur nla TRACE
  nla_solver lemma analysis. default: summary with strategy distribution.
  lemur nla TRACE --list         one line per lemma
  lemur nla TRACE --detail N     full variable table for Nth lemma
  lemur nla TRACE --details 1:5  range of lemmas
  lemur nla TRACE --no-varmap    show raw j-variables instead of SMT names

workflow:
1. lemur sweep bench.smt2 --seeds 0-15 --timeout 30 -f plain
2. lemur sweep bench.smt2 --seeds 3 --timeout 60 --trace nla_solver --save ./out
3. lemur stats ./out/default_s3.trace
4. lemur nla ./out/default_s3.trace --list -f plain
5. lemur nla ./out/default_s3.trace --detail 1

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
