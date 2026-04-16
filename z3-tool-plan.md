# Lemur — Z3 Trace Analysis & Debugging Toolkit

**Name**: `lemur` (like lemma, but with better eyesight)
**Location**: `~/ag/lemur/` — separate git repo, independent of z3
**Language**: Python 3, no Z3 dependency, just text parsing

## Purpose

Tools for analyzing Z3 trace logs, running parameter sweeps, and comparing
solver behavior across configurations. Born from repeated manual grep/awk
pipelines during nlsat/nra_solver investigation.

---

## Tools to build (priority order)

### 1. `lemur-sweep` — seed/config sweep runner

Run Z3 on a benchmark across seeds and configurations, collect results.

```
lemur-sweep div-ceil-equiv.smt2 --seeds 0-15 --timeout 30 \
  --config "baseline: smt.arith.nl.nra_incremental=0" \
  --config "mode1: smt.arith.nl.nra_incremental=1" \
  --config "mode2: smt.arith.nl.nra_incremental=2 smt.arith.nl.nra_max_conflicts=1000"
```

Output: table with config x seed, showing status + wall time. Summary row
with solve counts. Runs seeds in parallel (subprocess pool).

Features:
- `--z3 PATH` to specify z3 binary (default: `z3` on PATH)
- `--jobs N` for parallelism
- `--trace TAGS` to enable tracing and capture trace output per run
- `--save DIR` to persist raw outputs for later analysis
- CSV/TSV output option for further processing

### 2. `lemur-stats` — structured trace log analyzer

Parse known CTRACE tags from a trace file, output structured statistics.

```
lemur-stats trace.log
```

Output:
```
bounded_nlsat calls: 42
  l_true: 28 (66.7%)  l_false: 8 (19.0%)  l_undef: 6 (14.3%)
  core sizes: min=2 avg=11.7 median=10 max=38
  hard/soft split: avg 45/107
nla refinement rounds: max 3, avg 1.4
```

Parsers are modular — one parser per trace tag. Adding a new CTRACE tag in Z3
means adding a small parser class in lemur.

Known tags to support initially:
- `bounded_nlsat` — result, core size, hard/soft counts
- `nla_core` — lemma info, refinement rounds
- `nra_solver` — constraint counts, mode info

### 3. `lemur-diff` — compare trace behavior across two runs

```
lemur-diff trace_mode1.log trace_mode2.log
```

Side-by-side comparison: lemma counts, core size distributions, shared vs
unique lemmas, l_true/l_false/l_undef ratios.

### 4. `lemur-core` — deep core/lemma content analysis

```
lemur-core trace.log --problem div-ceil-equiv.smt2
```

Map core literals back to original constraints. Identify "hub" constraints
that appear in many cores. Detect near-duplicate cores. Show literal frequency
distribution.

---

## Repo structure

```
~/ag/lemur/
├── lemur-sweep.py       # standalone entry point
├── lemur-stats.py       # standalone entry point
├── lemur-diff.py        # standalone entry point
├── lemur-core.py        # standalone entry point
├── lemur/
│   ├── __init__.py
│   ├── parsers.py       # trace tag parsers
│   ├── sweep.py         # sweep logic
│   ├── stats.py         # statistics computation
│   └── table.py         # table formatting / output
├── tests/
│   └── sample_traces/   # small trace snippets for testing
└── README.md
```

Each `lemur-*.py` is a thin CLI wrapper. Shared logic lives in `lemur/`.

---

## How the two-session workflow operates

This is the key architectural decision: **z3 analysis and tool development
happen in separate Claude Code sessions to protect context**.

### Session A — Z3 analysis (primary work)

- Working directory: `~/ag/z3/z3-edge`
- Focus: deep analysis of Z3 internals, coding nra_solver, nlsat, etc.
- **Uses** lemur tools via bash: `python3 ~/ag/lemur/lemur-sweep.py ...`
- Gets compact output (tables, summaries) — minimal context cost
- When a tool needs enhancement or has a bug:
  1. Note what's needed (e.g., "lemur-stats should show per-round breakdown")
  2. Either: ask user to fix in Session B, or spawn a subagent:
     ```
     Agent("fix lemur-stats to show per-round breakdown",
           prompt="Edit ~/ag/lemur/lemur-stats.py to add ...",
           subagent_type="general-purpose")
     ```
  3. The subagent's development context is discarded after it returns
  4. Continue z3 analysis with clean context

### Session B — Lemur development (tool work)

- Working directory: `~/ag/lemur`
- Focus: building and iterating on the tools themselves
- Has full context on: trace format, parser design, CLI interface
- Does NOT need deep z3 internals context
- Can be given sample trace files to test against
- User can paste example trace output from Session A to guide development

### Communication protocol

The two sessions communicate through:

1. **Files on disk** — lemur tools read trace files, z3 session writes them
2. **The tools themselves** — Session A calls tools that Session B builds
3. **This plan document** — both sessions can read it for shared context
4. **Sample traces** — save representative traces to `~/ag/lemur/tests/sample_traces/`
   so Session B can test without needing z3

When requesting tool changes from Session B, include:
- What the tool currently does wrong or is missing
- A concrete example of input and desired output
- A sample trace file if the parser needs updating

### Bootstrapping

Session B should:
1. Create `~/ag/lemur/` repo with `git init`
2. Build `lemur-sweep` first (highest ROI — replaces the for-loops)
3. Build `lemur-stats` second (replaces grep/awk pipelines)
4. Commit after each working tool

Session A can start using tools as soon as they exist on disk.

---

## Z3 trace format reference

For Session B's benefit — what the trace output looks like:

### bounded_nlsat tag
```
(bounded_nlsat result: l_false, core_size: 12, hard: 45, soft: 107)
(bounded_nlsat result: l_true)
(bounded_nlsat result: l_undef)
```

### nla_core tag (lemma tracing)
```
(nla_core lemma: <literal list>)
```

### Trace enablement
Z3 trace is enabled with: `z3 -tr:nla_solver ...`
Specific tags are compiled into the binary via `src/util/trace_tags.def`.

The trace is placed into `.z3-trace` in the current working directory. 
If multiple copies of z3 with tracing have to run in parallel, then they
must be started in separate working directories and their .z3-trace files
need to be collected and renamed.

**Note**: trace format may evolve as we add new CTRACE calls in z3. When adding
a new tag in z3, update the corresponding parser in lemur and add a sample to
`tests/sample_traces/`.

---

## Design principles

1. **Each tool is independently useful** — no mandatory setup, just run the script
2. **Compact output by default** — the whole point is saving context window space
3. **Verbose mode available** — `--verbose` for when you need the raw details
4. **Parsers are the core abstraction** — each CTRACE tag gets a parser; tools compose parsers
5. **No z3 dependency** — pure text processing, runs anywhere Python runs
6. **Iterate fast** — tools will be rough initially, refined through use
