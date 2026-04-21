"""lemur: Z3 trace analysis and debugging toolkit."""

import argparse
import sys

from lemur.cli import sweep, stats, nla, tally, stats_compare, search
from lemur.cli import split as split_cli, split_status
from lemur.cli import agent_help


def main():
    parser = argparse.ArgumentParser(
        prog='lemur',
        description='Z3 trace analysis and debugging toolkit.',
        epilog='AI agents: use --agent (on any subcommand) for terse usage guide.',
    )
    parser.add_argument('--agent', action='store_true',
                        help='Show agent-friendly usage guide (all subcommands)')
    sub = parser.add_subparsers(dest='command')

    sweep.register(sub)
    stats.register(sub)
    nla.register(sub)
    tally.register(sub)
    stats_compare.register(sub)
    search.register(sub)
    split_cli.register(sub)
    split_status.register(sub)

    args, remaining = parser.parse_known_args()

    if args.agent:
        print(agent_help.full())
        sys.exit(0)

    if args.command is None:
        parser.print_help()
        sys.exit(1)

    # Re-parse fully now that we know it's not top-level --agent
    args = parser.parse_args()
    args.func(args)
