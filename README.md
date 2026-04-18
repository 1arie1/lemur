# 🐒 Lemur — Z3 Trace Analysis & Debugging Toolkit

*Like lemma, but with better eyesight* 👀

Tools for analyzing Z3 trace logs, running parameter sweeps, and comparing
solver behavior across configurations. Pure Python, no Z3 dependency — just
text parsing.

## 🌴 Setup

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e '.[dev]'
```

This installs `lemur` as a command in the venv with subcommands `sweep` and `stats`.

## 🔧 Tools

### 🌀 lemur sweep — Seed/config sweep runner

Run Z3 on a benchmark across seeds and configurations, collect results in a
table. Each run uses an isolated temp directory for trace file safety.

```bash
# Basic sweep
lemur sweep problem.smt2 --seeds 0-15 --timeout 30

# Multiple configs, parallel, with trace capture
lemur sweep problem.smt2 --seeds 0-15 --timeout 30 \
  --config "baseline:" \
  --config "inc1: smt.arith.nl.nra_incremental=1" \
  --config "inc2: smt.arith.nl.nra_incremental=2 smt.arith.nl.nra_max_conflicts=1000" \
  --trace nla_solver,nra \
  --save ./sweep_results \
  -j 4
```

Config format: `"name: key=val key=val"` or `"name:"` for defaults.
Quoted names work: `"\"mode 1\": key=val"`.

Output includes copy-pasteable z3 command lines for manual re-run
(suppress with `--no-commands`).

**Saved files** (with `--save DIR`):
| File | Contents |
|------|----------|
| `config_sN.trace` | `.z3-trace` file (requires `--trace`) |
| `config_sN.stdout` | z3 stdout |
| `config_sN.stderr` | z3 stderr (includes `-v:2` verbose stats) |
| `config_sN.z3log` | AST trace log (requires `--z3-log`) |

**Key options:**
- `--z3 PATH` — z3 binary (default: `~/ag/z3/z3-edge/build/z3`)
- `--verbosity N` — z3 `-v:N` flag (default: 2, 0 to disable)
- `--z3-log` — enable AST trace log (`trace=true`), requires `--save`
- `--format plain|json|rich` — output format (auto-detects TTY)

### 🔍 lemur stats — Trace file analyzer

Parse `.z3-trace` files and display structured statistics with tag-specific
analysis for `nla_solver` and `nra`.

```bash
# Summary of a trace file
lemur stats .z3-trace

# Filter to specific tag
lemur stats .z3-trace --tag nla_solver

# List all lemmas, one per line
lemur stats .z3-trace --lemma-list

# Show detailed variable table for lemma #3
lemur stats .z3-trace --lemma-detail 3

# Show details for lemmas 1 through 5
lemur stats .z3-trace --lemma-details 1:5

# Machine-readable output
lemur stats .z3-trace -f json

# Ignore varmap, show raw j-variables
lemur stats .z3-trace --lemma-detail 3 --no-varmap
```

**Lemma analysis** (for `~lemma_builder` entries in `nla_solver` tag):
- Strategy distribution with short names (pseudo-lin, grob-q, ord-binom, div-mono, etc.)
- `--lemma-list` — all lemmas at a glance, one row per lemma
- `--lemma-detail N` / `--lemma-details 1:5` — full variable tables
- Variable tables: value, bounds, definition, root, basic flag, SMT name (via varmap)
- Monomial highlighting (cyan), root mismatch detection (red)
- Variable delta tracking across consecutive lemmas (bounds tightening, value changes)
- Large constants humanized in Rich mode: `16384` → `2^14`, `16383` → `2^14-1`,
  numbers >= 1M digit-grouped: `1_062_993_921`. Power-of-2 forms colored bright_cyan.
- Varmap support: maps internal LP variables (j25) to SMT expressions (R21).
  Pre-humanized so `(mod R2 2^64)` instead of truncated raw numbers.

## 🐾 Z3 Trace Format

Z3 debug builds support tracing via `-tr:TAG`. Output goes to `.z3-trace`
in the working directory. Each entry:

```
-------- [TAG] function_name /path/to/file.cpp:LINE ---------
<free-form body>
------------------------------------------------
```

Relevant tags: `nla_solver` (and variants like `nla_solver_details`), `nra`,
`nlsat_*`. Tags are defined in `src/util/trace_tags.def`.

Z3 also has a second trace mechanism (`trace=true`, output to `z3.log`) that
logs AST construction events. Captured with `--z3-log` in lemur sweep. This is
all-or-nothing with no per-event filtering.

## 🌿 Architecture

```
pyproject.toml              Project config, deps, entry points
lemur/
  cli/
    main.py                 lemur entry point (subcommand dispatch)
    sweep.py                lemur sweep subcommand
    stats.py                lemur stats subcommand
  parsers.py                Trace block parser (header/body/footer) + varmap
  sweep.py                  Sweep engine (subprocess pool, temp dirs)
  table.py                  Rich/plain/JSON output formatting
  stats.py                  Per-tag statistics computation
  lemma.py                  ~lemma_builder structured parser
  report.py                 Lemma rendering, humanization, strategy names
tests/sample_traces/        Sample trace files for testing
```
