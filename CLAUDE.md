# Lemur development notes

## setup

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e '.[dev]'
```

This installs `lemur-sweep` and `lemur-stats` as commands in the venv.

## repo layout

```
pyproject.toml              project config, deps, entry points
lemur/
  __init__.py
  cli/
    __init__.py
    sweep.py                lemur-sweep entry point
    stats.py                lemur-stats entry point
  parsers.py                trace block parser + varmap
  sweep.py                  sweep engine (subprocess pool, temp dirs)
  table.py                  Rich/plain/JSON output formatting
  stats.py                  per-tag statistics computation
  lemma.py                  ~lemma_builder structured parser
  report.py                 lemma rendering, humanization, strategy names
tests/sample_traces/        sample trace files for testing
```

Entry points defined in `pyproject.toml` under `[project.scripts]`.
CLI code in `lemur/cli/`, library code in `lemur/`.

## z3

Debug binary with tracing: `~/ag/z3/z3-edge/build/z3` (v4.17.0).
System z3 (`/opt/homebrew/bin/z3`) does NOT support tracing.
Benchmarks: `~/ag/z3/z3-edge/bench/`.

## trace format

TRACE macro output (`.z3-trace`):
```
-------- [TAG] function_name /path/file.cpp:LINE ---------
<body>
------------------------------------------------
```
STRACE has no header/footer (not parsed). Tags defined in `z3-edge/src/util/trace_tags.def`.

AST trace log (`z3.log`): line-oriented `[event-type] data`. Enabled by `trace=true`. All-or-nothing, no per-event filtering.

## conventions

- Use `python3 -m pip`, not `pip3` (multiple python envs on this machine)
- Output: Rich for TTY, plain when piped. All tools support `-f plain|json|rich`
- Parsers are modular by tag. Adding a new CTRACE tag = new analyzer function in `stats.py`
- Lemma analysis lives in `lemma.py` (parsing) and `report.py` (rendering)
