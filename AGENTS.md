# Lemur tools — for agents

Z3 trace analysis. Two tools. Run with `python3`.

## lemur-sweep: run z3 across seeds/configs

```
python3 ~/ag/lemur/lemur-sweep.py BENCH.smt2 --seeds 0-15 --timeout 30 \
  --config "name: key=val key=val" \
  -j 4 -f csv --save DIR
```

- `--config` repeatable. `"name:"` for defaults
- `--trace nla_solver,nra` enables CTRACE, saves .trace files
- `--save DIR` saves .trace .stdout .stderr per run
- `-f csv` for machine output. default auto-detects tty
- z3 binary: `~/ag/z3/z3-edge/build/z3` (debug build, supports tracing)
- shows copy-pasteable z3 commands. `--no-commands` to hide

csv output format:
```
config,seed,status,time_s
baseline,0,sat,1.234
```

## lemur-stats: analyze .z3-trace files

```
python3 ~/ag/lemur/lemur-stats.py TRACE_FILE -f csv
python3 ~/ag/lemur/lemur-stats.py TRACE_FILE --tag nla_solver
python3 ~/ag/lemur/lemur-stats.py TRACE_FILE --lemma-detail 3
python3 ~/ag/lemur/lemur-stats.py TRACE_FILE --lemma-details 1:5
```

- parses `-------- [TAG] func file:line ---------` blocks
- `--tag TAG` filter. `--fn FUNC` filter. repeatable
- `--lemma-detail N` shows variable table for Nth lemma
- `-f csv` or `-f json` for machine output

## trace format

z3 debug build: `z3 -tr:nla_solver problem.smt2`
trace goes to `.z3-trace` in cwd. parallel runs need separate dirs (sweep handles this).

tags: `nla_solver`, `nra`, `nlsat_*`. defined in `z3-edge/src/util/trace_tags.def`.

## typical agent workflow

1. sweep to find interesting seeds: `lemur-sweep.py bench.smt2 --seeds 0-15 --timeout 30 -f csv`
2. re-run interesting case with tracing: `lemur-sweep.py bench.smt2 --seeds 3 --timeout 60 --trace nla_solver --save ./out`
3. analyze trace: `lemur-stats.py ./out/default_s3.trace -f csv`
4. inspect specific lemma: `lemur-stats.py ./out/default_s3.trace --lemma-detail 1`
