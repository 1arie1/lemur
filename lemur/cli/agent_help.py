"""
Agent-friendly usage guides. Shared by `lemur --agent` and every subcommand's
`--agent` flag.

Each subcommand has its own section. `full()` composes them all. `section(name)`
prints one focused section.
"""

import sys


WHY = """\
why reach for lemur instead of ad-hoc bash/grep/awk/python:

- trace-aware. the parser understands z3 TRACE block structure
  (header/body/footer); filters act on tag/function as structural fields,
  not substrings of bytes. grep can't distinguish a `[nla_solver]` header
  from the string "nla_solver" inside someone's body.
- orchestration with clean shutdown. `sweep` runs z3 in a process pool,
  each z3 in its own process group so Ctrl-C, timeouts, and --stop-on all
  kill every child cleanly. no orphaned z3 processes. no trace-file
  collisions between parallel runs (each run gets its own tmpdir).
- streaming + early abort. CSV rows flush as each run finishes, so you
  can pipe to a live viewer; --stop-on / --fail-fast cancel remaining
  work the instant a condition is met.
- structured records. `nla` parses lemmas into LemmaRecord (strategy,
  preconditions, conclusion, variable table, monomials) — regex alone
  doesn't get nesting right. `stats-compare` parses z3's -st S-expr into
  typed key/value pairs with side-by-side means + diff%.
- reproducible saves. `--save DIR` writes canonical files per (split,
  config, seed): .trace / .stdout / .stderr / .stats.json / .z3log.
  post-hoc analysis (tally, stats-compare, nla, search) reads the dir
  shape directly.
- case-split workflow. `--split` rewrites (check-sat) in-place, runs
  the cross-product, and reports whether the disjunction is closed. you
  don't hand-edit SMT variant files.

when bash is fine: one-off `z3 ... | grep sat`. when lemur wins: more
than one seed, more than one config, or you need structured output that
another lemur command will consume.
"""

SECTIONS = {
    'sweep': """\
lemur sweep BENCH.smt2 --seeds 0-15 --timeout 30
  why: parallel z3 pool with process-group kill on Ctrl-C/timeout/--stop-on,
       per-run tmpdir isolation (traces don't collide), streaming CSV.

  config expansion:
    --config "name: key=val"    named config; repeatable; quote ws values
    --grid key=v1,v2,v3         cross-products; repeatable; combined with --config
    --split "name:<smt>"        inject before (check-sat); cross-products
                                splits × configs × seeds; adds `split` column;
                                prints per-split closure summary
  execution:
    -j N|auto                   parallel jobs (auto = os.cpu_count())
    --stop-on sat|unsat         abort on first matching result
    --fail-fast                 abort on first timeout/unknown/error
  output:
    --tally                     per-config aggregation after CSV
    --trace nla_solver,nra      capture .z3-trace (requires --save for files)
    --stats                     add z3 -st; with --save writes .stats.json
    --save DIR                  save stdout/stderr/trace/.stats.json per run
    -f plain                    streaming CSV (row-per-run); safe for pipes
  sweep prints copy-pasteable z3 commands at end unless --no-commands.
""",
    'tally': """\
lemur tally SWEEP.csv
  why: structured aggregation of a sweep CSV. counts by status AND
       tracks fastest-sat / fastest-unsat (time, seed) per config, which
       is tedious in awk. recognizes the optional `split` column from
       `sweep --split` and groups by (split, config) with a per-split
       closure summary.
  -f plain|rich|json
""",
    'stats-compare': """\
lemur stats-compare SAVE_DIR
  why: z3 -st output is an S-expression (`(:key value ...)`). this
       subcommand parses it into typed numeric dicts and shows per-config
       means side by side, with a diff % column when exactly two configs.
       "nra-calls 139 vs 22" at a glance, not via jq+awk.
  reads <config>_s<seed>.stats.json from `sweep --stats --save`.
  --top N   cap rows to N highest-magnitude stats
  -f plain|rich|json
""",
    'stats': """\
lemur stats TRACE
  why: aggregates trace entries by tag and function. block-aware, so
       counts are of actual trace entries, not line occurrences.
  --tag TAG     filter to tag(s); repeatable
  --fn FUNC     filter to function(s); repeatable
  -f plain for parsing.
""",
    'nla': """\
lemur nla TRACE
  why: parses nla_solver / ~lemma_builder entries into structured
       LemmaRecord (strategy, preconditions, conclusion, variable
       assignments with bounds/definition/root, detected monomials).
       regex alone misses the structure; this tool gives you a typed
       view + filters.

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
""",
    'search': """\
lemur search TRACE [PATTERN] [--tag RE] [--fn RE] [-n] [--entries]
  why: trace-aware grep. --tag/--fn are regexes on structural fields,
       so --tag '^nla' matches every [nla_*] entry header, not just
       occurrences of the string. --entries prints the whole matching
       block (header + body + footer) so you keep context.
       line numbers are absolute into the file, so editor-jumpable.
  PATTERN optional: omit to dump every line in filtered entries.
  -i/-v/-c/--max-count standard. exit 0 match, 1 no match, 2 regex error.
""",
}


WORKFLOWS = """\
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
"""


def full() -> str:
    parts = [
        "lemur: z3 trace analysis toolkit. six subcommands.",
        "",
        WHY,
        "",
    ]
    for name, body in SECTIONS.items():
        parts.append(body)
    parts.append(WORKFLOWS)
    parts.append("use --help (or --agent) on any subcommand for details.")
    return "\n".join(parts)


def section(name: str) -> str:
    body = SECTIONS.get(name, '')
    return (
        body
        + "\n"
        + f"for the full toolkit (all subcommands + rationale + workflows),\n"
        + f"run `lemur --agent`.\n"
    )


def add_agent_flag(parser, subcommand_name: str) -> None:
    """Attach `--agent` to a subparser. The handler must be invoked before
    required-arg validation runs, so we short-circuit via a custom Action."""

    import argparse

    class _AgentAction(argparse.Action):
        def __init__(self, option_strings, dest, **kwargs):
            super().__init__(option_strings, dest, nargs=0,
                             default=argparse.SUPPRESS, **kwargs)

        def __call__(self, parser, namespace, values, option_string=None):
            sys.stdout.write(section(subcommand_name))
            parser.exit(0)

    parser.add_argument(
        '--agent', action=_AgentAction,
        help="Show agent-friendly usage guide for this subcommand",
    )
