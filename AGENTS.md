# Lemur tools — for agents

Z3 trace analysis. Two tools. Run with `python3`.

## lemur-sweep: run z3 across seeds/configs

```
python3 ~/ag/lemur/lemur-sweep.py BENCH.smt2 --seeds 0-15 --timeout 30 \
  --config "name: key=val key=val" \
  -j 4 -f plain --save DIR
```

- `--config` repeatable. `"name:"` for defaults
- `--trace nla_solver,nra` enables CTRACE, saves .trace files
- `--save DIR` saves .trace .stdout .stderr .z3log per run
- `--verbosity N` z3 verbose level (default 2, stderr output)
- `--z3-log` capture AST trace log (large). requires `--save`
- `-f plain` for machine output. `-f json` for structured. default auto-detects tty
- z3 binary: `~/ag/z3/z3-edge/build/z3` (debug build, supports tracing)
- shows copy-pasteable z3 commands. `--no-commands` to hide

plain output format:
```
config,seed,status,time_s
baseline,0,sat,1.234
```

## lemur-stats: analyze .z3-trace files

```
python3 ~/ag/lemur/lemur-stats.py TRACE_FILE
python3 ~/ag/lemur/lemur-stats.py TRACE_FILE --tag nla_solver
python3 ~/ag/lemur/lemur-stats.py TRACE_FILE --lemma-list
python3 ~/ag/lemur/lemur-stats.py TRACE_FILE --lemma-list -f plain
python3 ~/ag/lemur/lemur-stats.py TRACE_FILE --lemma-detail 3
python3 ~/ag/lemur/lemur-stats.py TRACE_FILE --lemma-details 1:5
python3 ~/ag/lemur/lemur-stats.py TRACE_FILE --no-varmap --lemma-detail 3
```

- parses `-------- [TAG] func file:line ---------` blocks
- `--tag TAG` filter. `--fn FUNC` filter. repeatable
- `--lemma-list` one line per lemma (strategy, conclusion, monomials)
- `--lemma-detail N` full variable table for Nth lemma (1-based)
- `--lemma-details 1:5,10` ranges
- `--no-varmap` show raw LP j-variables instead of SMT names
- `-f plain` or `-f json` for machine output

strategy short names: nlsat, pseudo-lin, grob-q, grob-f, grob-eq,
ord-binom, ord-acbd, ord-factor, mono<, mono>, tan1, tan2, tan-plane,
low>val, hi<val, sign-1mon, neutral-mon, neutral-fac, div-mono, pl-mon,
add, nex, pdd. long unknowns truncated with ~

## trace format

z3 debug build: `z3 -tr:nla_solver problem.smt2`
trace goes to `.z3-trace` in cwd. parallel runs need separate dirs (sweep handles this).

tags: `nla_solver`, `nra`, `nlsat_*`. defined in `z3-edge/src/util/trace_tags.def`.

## typical agent workflow

1. sweep to find interesting seeds: `lemur-sweep.py bench.smt2 --seeds 0-15 --timeout 30 -f plain`
2. re-run interesting case with tracing: `lemur-sweep.py bench.smt2 --seeds 3 --timeout 60 --trace nla_solver --save ./out`
3. analyze trace: `lemur-stats.py ./out/default_s3.trace`
4. list all lemmas: `lemur-stats.py ./out/default_s3.trace --lemma-list -f plain`
5. inspect specific lemma: `lemur-stats.py ./out/default_s3.trace --lemma-detail 1`
