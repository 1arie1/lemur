# Lemur development notes

## repo layout

Flat python package. CLIs are `lemur-sweep.py`, `lemur-stats.py` at root.
Shared code in `lemur/` package. Tests and sample traces in `tests/sample_traces/`.

## dependencies

Only external dep: `rich`. Listed in `requirements.txt`.
Venv at `.venv/`. System install also works via `python3 -m pip install --user --break-system-packages rich`.

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
- Output: Rich for TTY, CSV when piped. All tools support `-f csv|json|rich`
- Parsers are modular by tag. Adding a new CTRACE tag = new analyzer function in `stats.py`
- Lemma analysis lives in `lemma.py` (parsing) and `report.py` (rendering)
