"""
Sweep engine: run Z3 across seeds and configurations, collect results.

Each run gets its own temp directory (for trace file isolation).
Temp directories are cleaned up on completion and on unexpected exit.
"""

import atexit
import os
import shutil
import signal
import subprocess
import tempfile
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path

from rich.progress import Progress, SpinnerColumn, BarColumn, TextColumn, TimeElapsedColumn

from lemur.table import SweepTable, make_console


@dataclass
class RunConfig:
    """A named Z3 configuration (set of key=value params)."""
    name: str
    params: dict[str, str]

    @classmethod
    def parse(cls, spec: str) -> 'RunConfig':
        """Parse 'name: key=val key=val ...' or '"name with spaces": key=val ...'"""
        spec = spec.strip()
        # Handle quoted name
        if spec.startswith('"'):
            end_quote = spec.index('"', 1)
            name = spec[1:end_quote]
            rest = spec[end_quote + 1:].lstrip(': ')
        else:
            parts = spec.split(':', 1)
            name = parts[0].strip()
            rest = parts[1].strip() if len(parts) > 1 else ''

        params = {}
        for token in rest.split():
            if '=' in token:
                k, v = token.split('=', 1)
                params[k] = v
        return cls(name=name, params=params)


@dataclass
class RunResult:
    config: str
    seed: int
    status: str  # sat, unsat, unknown, timeout, error
    time_s: float
    stdout: str
    stderr: str
    trace_file: Path | None  # path to saved trace, if any


# Global set of temp dirs for cleanup on unexpected exit
_active_temp_dirs: set[str] = set()


def _cleanup_temp_dirs():
    for d in list(_active_temp_dirs):
        shutil.rmtree(d, ignore_errors=True)
    _active_temp_dirs.clear()


atexit.register(_cleanup_temp_dirs)

# Also clean up on SIGTERM/SIGINT
_orig_sigterm = signal.getsignal(signal.SIGTERM)
_orig_sigint = signal.getsignal(signal.SIGINT)


def _signal_cleanup(signum, frame):
    _cleanup_temp_dirs()
    # Re-raise original handler
    orig = _orig_sigterm if signum == signal.SIGTERM else _orig_sigint
    if callable(orig):
        orig(signum, frame)
    else:
        raise SystemExit(1)


# Only install signal handlers in the main process
if os.getpid() == os.getpid():  # always true, but signals set at import
    try:
        signal.signal(signal.SIGTERM, _signal_cleanup)
        signal.signal(signal.SIGINT, _signal_cleanup)
    except (OSError, ValueError):
        pass  # can't set signal handler in non-main thread


def run_single(z3_bin: str, smt_file: str, seed: int, config: RunConfig,
               timeout: int, trace_tags: list[str] | None = None,
               save_dir: str | None = None) -> RunResult:
    """Run a single Z3 invocation in a temp directory."""

    tmpdir = tempfile.mkdtemp(prefix=f"lemur_{config.name}_s{seed}_")
    _active_temp_dirs.add(tmpdir)

    try:
        cmd = [z3_bin]

        # Add trace tags
        if trace_tags:
            for tag in trace_tags:
                cmd.append(f"-tr:{tag}")

        # Add config params
        for k, v in config.params.items():
            cmd.append(f"{k}={v}")

        # Add seed and timeout
        cmd.extend([
            f"sat.random_seed={seed}",
            f"smt.random_seed={seed}",
            f"nlsat.seed={seed}",
            f"-T:{timeout}",
            smt_file,
        ])

        start = time.monotonic()
        proc = subprocess.run(
            cmd,
            cwd=tmpdir,
            capture_output=True,
            text=True,
            timeout=timeout + 10,  # grace period beyond z3's own timeout
        )
        elapsed = time.monotonic() - start

        # Parse status from stdout
        stdout = proc.stdout.strip()
        first_line = stdout.split('\n')[0].strip() if stdout else ''
        if first_line == 'sat':
            status = 'sat'
        elif first_line == 'unsat':
            status = 'unsat'
        elif first_line == 'timeout':
            status = 'timeout'
        elif first_line == 'unknown':
            status = 'unknown'
        else:
            status = 'error'

        # Handle trace file
        trace_src = Path(tmpdir) / '.z3-trace'
        trace_dest = None
        if trace_src.exists() and trace_src.stat().st_size > 0 and save_dir:
            trace_dest = Path(save_dir) / f"{config.name}_s{seed}.trace"
            shutil.copy2(trace_src, trace_dest)

        return RunResult(
            config=config.name,
            seed=seed,
            status=status,
            time_s=elapsed,
            stdout=proc.stdout,
            stderr=proc.stderr,
            trace_file=trace_dest,
        )

    except subprocess.TimeoutExpired:
        return RunResult(
            config=config.name, seed=seed, status='timeout',
            time_s=float(timeout), stdout='', stderr='timeout (process killed)',
            trace_file=None,
        )
    except Exception as e:
        return RunResult(
            config=config.name, seed=seed, status='error',
            time_s=0.0, stdout='', stderr=str(e), trace_file=None,
        )
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
        _active_temp_dirs.discard(tmpdir)


def run_sweep(z3_bin: str, smt_file: str, seeds: list[int],
              configs: list[RunConfig], timeout: int, jobs: int = 1,
              trace_tags: list[str] | None = None,
              save_dir: str | None = None,
              show_progress: bool = True) -> SweepTable:
    """Run a full sweep and return a populated SweepTable."""

    if save_dir:
        os.makedirs(save_dir, exist_ok=True)

    config_names = [c.name for c in configs]
    table = SweepTable(config_names, seeds)
    total = len(configs) * len(seeds)

    # Build work items
    work = [(config, seed) for config in configs for seed in seeds]

    if show_progress:
        console = make_console()
        progress = Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TextColumn("{task.completed}/{task.total}"),
            TimeElapsedColumn(),
            console=console,
        )
        task_id = progress.add_task("Sweeping", total=total)
    else:
        progress = None

    def process_result(result: RunResult):
        table.add_result(result.config, result.seed, result.status, result.time_s)
        if progress:
            desc = f"[dim]{result.config}[/dim] s{result.seed}: {result.status}"
            progress.update(task_id, advance=1, description=desc)

    ctx = progress if progress else _nullcontext()
    with ctx:
        if jobs == 1:
            for config, seed in work:
                result = run_single(z3_bin, smt_file, seed, config,
                                    timeout, trace_tags, save_dir)
                process_result(result)
        else:
            with ProcessPoolExecutor(max_workers=jobs) as executor:
                futures = {}
                for config, seed in work:
                    f = executor.submit(run_single, z3_bin, smt_file, seed,
                                        config, timeout, trace_tags, save_dir)
                    futures[f] = (config.name, seed)

                for f in as_completed(futures):
                    result = f.result()
                    process_result(result)

    return table


class _nullcontext:
    def __enter__(self):
        return self
    def __exit__(self, *args):
        pass


def parse_seed_range(spec: str) -> list[int]:
    """Parse seed specification: '0-15', '1,3,5', '0-3,7,10-12'."""
    seeds = []
    for part in spec.split(','):
        part = part.strip()
        if '-' in part:
            lo, hi = part.split('-', 1)
            seeds.extend(range(int(lo), int(hi) + 1))
        else:
            seeds.append(int(part))
    return seeds
