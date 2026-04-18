# Lemur development notes

## setup

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e '.[dev]'
```

This installs `lemur` as a command in the venv with subcommands `sweep` and `stats`.

## repo layout

```
pyproject.toml              project config, deps, entry points
lemur/
  __init__.py
  cli/
    __init__.py
    main.py                 lemur entry point (subcommand dispatch)
    sweep.py                lemur sweep subcommand
    stats.py                lemur stats subcommand
  parsers.py                trace block parser + varmap
  sweep.py                  sweep engine (subprocess pool, temp dirs)
  table.py                  Rich/plain/JSON output formatting
  stats.py                  per-tag statistics computation
  lemma.py                  ~lemma_builder structured parser
  report.py                 lemma rendering, humanization, strategy names
tests/sample_traces/        sample trace files for testing
```

Single entry point `lemur` defined in `pyproject.toml`. Subcommands registered
via `register(subparsers)` pattern in `lemur/cli/sweep.py` and `lemur/cli/stats.py`.
To add a new subcommand: create `lemur/cli/foo.py` with `register()` and `run()`,
import and register in `main.py`.

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
