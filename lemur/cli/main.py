"""lemur: Z3 trace analysis and debugging toolkit."""

import argparse
import sys

from lemur.cli import sweep, stats


def main():
    parser = argparse.ArgumentParser(
        prog='lemur',
        description='Z3 trace analysis and debugging toolkit.',
    )
    sub = parser.add_subparsers(dest='command')

    sweep.register(sub)
    stats.register(sub)

    args = parser.parse_args()
    if args.command is None:
        parser.print_help()
        sys.exit(1)

    args.func(args)
