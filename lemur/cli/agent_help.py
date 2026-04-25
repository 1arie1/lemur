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
  don't hand-edit SMT variant files. `lemur split` goes a step further
  — it discovers candidate Boolean splits automatically, probe-scores
  them, and emits a plan.json + leaf .smt2 files that `lemur sweep DIR/`
  consumes directly.

when bash is fine: one-off `z3 ... | grep sat`. when lemur wins: more
than one seed, more than one config, or you need structured output that
another lemur command will consume.
"""

SECTIONS = {
    'sweep': """\
lemur sweep BENCH.smt2 --seeds 0-15 --timeout 30
  (also: lemur sweep LEAVES_DIR/  — directory mode; reads plan.json written
   by `lemur split` and treats each non-pruned leaf as an implicit split;
   pruned leaves surface in the tally as pre-closed UNSAT; plan.json is the
   authoritative manifest so extra files in the dir are ignored.)
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
    --stop-on sat|unsat         abort whole sweep on first matching result
    --stop-on-per-split sat|unsat
                                scope --stop-on to each split: close a split
                                on its first matching run, skip that split's
                                remaining runs, continue others. requires
                                --split OR directory mode; incompatible with
                                --stop-on; composes with --fail-fast. the
                                canonical UNSAT-by-decomposition loop.
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
    'split': """\
lemur split BENCH.smt2 [--out DIR] [--max-leaves N]
  why: automatically discover good Boolean case-splits on hard SMT2 (the
       kind that manually cracks 60s-TO Certora VCs). enumerates
       candidate Bool predicates (--split-name-pattern, default BLK__\\d+,
       plus ITE guards), probe-scores each via simplification, greedy-
       nests up to log2(--max-leaves). emits a self-contained output dir
       with plan.json + leaf_*.smt2 files + a verbatim copy of the
       source. optional [split] extra: pip install 'lemur[split]'.

  key flags:
    --out DIR                 default: <BENCH-stem>_children/ next to source
    --max-leaves N            cap 2^k (default 32, floor 8)
    --split-score-threshold F (default 10)
    --split-probe-timeout S   per-candidate simplify timeout (default 5s)
    --split-name-pattern RE   reachability Bool regex (default BLK__\\d+)
    --plan-only               write plan.json only; no leaf files on disk
    --force                   overwrite an --out dir that already has plan.json

  version note: the z3 Python API used here can differ from the z3
  binary that `lemur sweep` invokes. That is fine: split only
  manipulates formulas. The solver of record is the binary.
""",
    'split-status': """\
lemur split-status DIR
  why: walks a recursive split tree (from `lemur split` then recursive
       re-splits) and reports aggregate stats: total leaves across the
       tree, emitted vs pruned, max depth, per-plan summary, and whether
       each plan has sweep results populated. Lets you see at a glance
       whether the whole decomposition closed.
  -v   list every leaf with its path and state
  -f plain|rich|json    json emits the full tree for scripting
""",
    'sgrep': """\
lemur sgrep FILE.smt2 [PATTERN] [--apply TACTIC]
  why: grep is line-oriented and breaks on multi-line `(let …)` nesting;
       `(div (ite c A B) k)` and `(ite c (div A k) (div B k))` look the
       same to a regex but are different shapes. sgrep walks the z3 AST
       (let-bindings already eliminated by the parser) and matches an
       s-expression pattern with capture variables. Run preprocessing
       first via --apply when you want to see what the solver sees.

  modes (mutually exclusive; default depends on whether PATTERN is given):
    --summary       file overview: asserts, decls-by-sort, top operators,
                    distinct-shape counts for the standard div/mod/ite
                    patterns, max nesting depth. default if no PATTERN.
    --count         number of matches; exit.
    --list          one match per line (default if PATTERN given).
    --distinct      --list with structurally-equal duplicates removed.

  pattern syntax:
    _                    wildcard
    ?name                capture (same name twice ⇒ id-equality unification)
    (head c1 c2 ...)     compound: head op-name + arity-matched children
    NAME                 bare literal: matches a 0-arity expr with that
                         decl name (e.g. POW2_64).
    type filters: ?c:Bool  ?k:Numeral  ?n:Var  ?e:Expr (default)
                  ?c:Eq  (matches `(= a b)`)
                  ?c:Comparison  (matches `<`, `<=`, `>`, `>=`)
    negation: !?n:Numeral  ≡  ?n:!Numeral  (XOR if both forms used).

  flags:
    --apply 'TACTIC'      pre-process via z3 tactic. Grammar (v1):
                          a single tactic name OR (then t1 t2 ...).
    --show captures       per-match: append `?name=full-binding` pairs.
    --show kind           per-match: replace expr with one-line kind
                          summary (Var(name) / Numeral(N) / Op(head))
                          for the match and each capture. Use this
                          when --distinct on compound guards would
                          otherwise dump huge subtrees.
    --format plain|json   json emits one match per line (or {count: N}
                          for --count; structured object for --summary).
    --expand-aliases      inline z3 let-aliases in printed output. Beware
                          exponential blowup on deeply-shared subterms.
""",
    'sdiff': """\
lemur sdiff A.smt2 B.smt2 [--apply TACTIC] [--pattern PATTERN]
  why: structural diff between two SMT2 files. Same shape table as
       `sgrep --summary`, run on both files, with A-count, B-count and
       delta. Tells you "encoder Pattern-3 went from 39 occurrences to
       0" without staring at unified diffs of mangled SMT2.

  default mode: full shape table. --pattern PATTERN restricts to one
  user-supplied sgrep-style pattern.

  flags:
    --apply 'TACTIC'      apply same tactic to both before diffing
                          (e.g. compare baseline-pv_se vs experimental).
    --apply-a 'TACTIC'    asymmetric mode: apply this tactic only to A.
    --apply-b 'TACTIC'    asymmetric mode: apply this tactic only to B.
                          Use both together to compare two preprocessing
                          pipelines against the *same* source file:
                            lemur sdiff F.smt2 F.smt2 \\
                              --apply-a 'simplify' \\
                              --apply-b '(then simplify propagate-values)'
                          --apply is mutually exclusive with --apply-a/-b.
    --show-same           include rows where A == B (default: hide).
    --format plain|json   json emits {a, b, rows: [{shape, a, b, delta}]}.
    --expand-aliases      same as sgrep.
""",
}


WORKFLOWS = """\
workflows:

# seed triage + per-config aggregation
  lemur sweep bench.smt2 --seeds 0-15 --timeout 30 --tally -f plain > out.csv
  lemur tally out.csv

# case-split UNSAT-proof by decomposition (close every split independently)
# --stop-on-per-split unsat: each split runs until one seed UNSATs, then skips
# remaining seeds for that split only; other splits keep running. tally shows
# whether the disjunction is fully closed.
  lemur sweep bench.smt2 --seeds 0-15 --timeout 30 --split 'BLK25:(assert BLK__25)' --split 'BLK26:(assert BLK__26)' --stop-on-per-split unsat --tally -f plain

# auto-discover case-splits, then sweep them (recursive-ready)
# `lemur split` emits leaves + plan.json; sweep DIR/ uses plan.json as
# manifest. If a leaf is still hard, re-run `lemur split` on it.
  lemur split bench.smt2 --out leaves/
  lemur sweep leaves/ --seeds 0-7 --timeout 10 --stop-on-per-split unsat --tally -f plain
  # recurse on a stubborn leaf:
  lemur split leaves/leaf_T_F_T.smt2
  lemur split-status leaves/

# config A vs B on stats counters
  lemur sweep bench.smt2 --seeds 0-7 --timeout 30 --stats --save ./out --config 'A: smt.arith.solver=2' --config 'B: smt.arith.solver=6'
  lemur stats-compare ./out --top 20

# nla lemma drill-down
  lemur sweep bench.smt2 --seeds 3 --timeout 60 --trace nla_solver --save ./out
  lemur nla ./out/default_s3.trace --strategy grob --list
  lemur nla ./out/default_s3.trace --top-by vars --top-n 5
  lemur search ./out/default_s3.trace 'calls' --fn '^check$' --entries -n

# structural inspection of an unfamiliar VC, then compare two preprocessing
# pipelines to see what actually changed at the AST level
  lemur sgrep bench.smt2 --summary
  lemur sgrep bench.smt2 --apply '(then simplify propagate-values solve-eqs)' \\
    '(div (ite ?c _ _) _)' --distinct --show captures   # find guards
  lemur sdiff before.smt2 after.smt2 \\
    --apply '(then simplify propagate-values solve-eqs)'
"""


def full() -> str:
    parts = [
        f"lemur: z3 trace + SMT2 analysis toolkit. {len(SECTIONS)} subcommands.",
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
