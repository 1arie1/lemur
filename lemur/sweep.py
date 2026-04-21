"""
Sweep engine: run Z3 across seeds and configurations, collect results.

Each run gets its own temp directory (for trace file isolation).
Temp directories are cleaned up on completion and on unexpected exit.
"""

import atexit
import os
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from concurrent.futures.process import BrokenProcessPool
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
    cmdline: str = ''  # copy-pasteable z3 command for manual re-run


# Temp dirs created by in-flight runs, cleaned up on normal or abnormal exit.
_active_temp_dirs: set[str] = set()


def _cleanup_temp_dirs():
    for d in list(_active_temp_dirs):
        shutil.rmtree(d, ignore_errors=True)
    _active_temp_dirs.clear()


atexit.register(_cleanup_temp_dirs)


def _kill_process_group(proc: subprocess.Popen) -> None:
    """Best-effort termination of a subprocess's whole process group."""
    if proc.poll() is not None:
        return
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
    except (ProcessLookupError, OSError):
        pass
    try:
        proc.wait(timeout=2)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except (ProcessLookupError, OSError):
            pass
        try:
            proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            pass


def run_single(z3_bin: str, smt_file: str, seed: int, config: RunConfig,
               timeout: int, trace_tags: list[str] | None = None,
               verbosity: int = 2, z3_log: bool = False,
               save_dir: str | None = None,
               parent_tmpdir: str | None = None) -> RunResult:
    """Run a single Z3 invocation in a temp directory."""

    tmpdir = tempfile.mkdtemp(prefix=f"lemur_{config.name}_s{seed}_",
                              dir=parent_tmpdir)
    _active_temp_dirs.add(tmpdir)

    cmdline = ''
    proc: subprocess.Popen | None = None
    try:
        cmd = [z3_bin]

        # Add trace tags
        if trace_tags:
            for tag in trace_tags:
                cmd.append(f"-tr:{tag}")

        # Add config params
        for k, v in config.params.items():
            cmd.append(f"{k}={v}")

        # Enable AST trace log if requested (writes to z3.log in CWD)
        if z3_log:
            cmd.extend(['trace=true', 'trace_file_name=z3.log'])

        # Add verbosity, seed, and timeout
        cmd.extend([
            f"-v:{verbosity}",
            f"sat.random_seed={seed}",
            f"smt.random_seed={seed}",
            f"nlsat.seed={seed}",
            f"-T:{timeout}",
            smt_file,
        ])

        cmdline = shlex.join(cmd)

        start = time.monotonic()
        # start_new_session puts z3 in its own process group so we can
        # reliably kill it (and anything it spawns) on timeout or Ctrl-C.
        proc = subprocess.Popen(
            cmd,
            cwd=tmpdir,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
        )
        try:
            stdout, stderr = proc.communicate(timeout=timeout + 10)
        except subprocess.TimeoutExpired:
            _kill_process_group(proc)
            try:
                stdout, stderr = proc.communicate(timeout=2)
            except subprocess.TimeoutExpired:
                stdout, stderr = '', ''
            return RunResult(
                config=config.name, seed=seed, status='timeout',
                time_s=float(timeout),
                stdout=stdout or '',
                stderr=(stderr or '') + '\ntimeout (process killed)',
                trace_file=None, cmdline=cmdline,
            )
        elapsed = time.monotonic() - start

        # Parse status from stdout
        out = stdout.strip()
        first_line = out.split('\n')[0].strip() if out else ''
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

        # Save outputs when --save is used
        trace_dest = None
        if save_dir:
            prefix = Path(save_dir) / f"{config.name}_s{seed}"

            # Trace file
            trace_src = Path(tmpdir) / '.z3-trace'
            if trace_src.exists() and trace_src.stat().st_size > 0:
                trace_dest = prefix.with_suffix('.trace')
                shutil.copy2(trace_src, trace_dest)

            # stdout
            if stdout.strip():
                prefix.with_suffix('.stdout').write_text(stdout)

            # stderr
            if stderr.strip():
                prefix.with_suffix('.stderr').write_text(stderr)

            # z3 AST trace log
            z3_log_src = Path(tmpdir) / 'z3.log'
            if z3_log_src.exists() and z3_log_src.stat().st_size > 0:
                shutil.copy2(z3_log_src, prefix.with_suffix('.z3log'))

        return RunResult(
            config=config.name,
            seed=seed,
            status=status,
            time_s=elapsed,
            stdout=stdout,
            stderr=stderr,
            trace_file=trace_dest,
            cmdline=cmdline,
        )

    except (KeyboardInterrupt, SystemExit):
        if proc is not None:
            _kill_process_group(proc)
        raise
    except Exception as e:
        if proc is not None:
            _kill_process_group(proc)
        return RunResult(
            config=config.name, seed=seed, status='error',
            time_s=0.0, stdout='', stderr=str(e), trace_file=None,
            cmdline=cmdline,
        )
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
        _active_temp_dirs.discard(tmpdir)


def _init_worker():
    """Ensure SIGINT raises KeyboardInterrupt in pool workers so run_single's
    cleanup path runs and the z3 subprocess is killed before the worker dies."""
    signal.signal(signal.SIGINT, signal.default_int_handler)


def _shutdown_pool_workers(executor: ProcessPoolExecutor) -> None:
    """Give workers a moment to self-clean on SIGINT, then force any stragglers.

    Each worker's run_single kills its z3 child and removes its tmpdir when
    KeyboardInterrupt fires. We send SIGINT explicitly (not relying on the
    shell's group signal) and wait briefly before SIGTERM/SIGKILL."""
    procs = list(getattr(executor, '_processes', {}).values())
    for p in procs:
        try:
            p.send_signal(signal.SIGINT)
        except Exception:
            pass
    deadline = time.monotonic() + 3.0
    for p in procs:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        try:
            p.join(timeout=remaining)
        except Exception:
            pass
    for p in procs:
        try:
            if p.is_alive():
                p.terminate()
        except Exception:
            pass


def _run_parallel(work, jobs, process_result, *, z3_bin, smt_file, timeout,
                  trace_tags, verbosity, z3_log, save_dir, parent_tmpdir):
    executor = ProcessPoolExecutor(max_workers=jobs, initializer=_init_worker)
    futures = {}
    try:
        for config, seed in work:
            f = executor.submit(run_single, z3_bin, smt_file, seed, config,
                                timeout, trace_tags, verbosity, z3_log,
                                save_dir, parent_tmpdir)
            futures[f] = (config.name, seed)

        for f in as_completed(futures):
            try:
                result = f.result()
            except BrokenProcessPool:
                # A worker died unexpectedly; treat as interruption.
                raise KeyboardInterrupt from None
            process_result(result)
    except KeyboardInterrupt:
        for fut in futures:
            fut.cancel()
        _shutdown_pool_workers(executor)
        executor.shutdown(wait=False, cancel_futures=True)
        raise
    else:
        executor.shutdown(wait=True)


def run_sweep(z3_bin: str, smt_file: str, seeds: list[int],
              configs: list[RunConfig], timeout: int, jobs: int = 1,
              trace_tags: list[str] | None = None,
              verbosity: int = 2, z3_log: bool = False,
              save_dir: str | None = None,
              show_progress: bool = True) -> tuple[SweepTable, list[RunResult]]:
    """Run a full sweep and return a populated SweepTable and all RunResults."""

    if save_dir:
        os.makedirs(save_dir, exist_ok=True)

    config_names = [c.name for c in configs]
    table = SweepTable(config_names, seeds)
    all_results: list[RunResult] = []
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
        all_results.append(result)
        if progress:
            desc = f"[dim]{result.config}[/dim] s{result.seed}: {result.status}"
            progress.update(task_id, advance=1, description=desc)

    # Per-sweep parent tmpdir: every run_single tmpdir nests under this, so a
    # single rmtree at the end cleans up even if a worker was killed mid-cleanup.
    sweep_tmp = tempfile.mkdtemp(prefix='lemur_sweep_')
    _active_temp_dirs.add(sweep_tmp)

    ctx = progress if progress else _nullcontext()
    try:
        with ctx:
            if jobs == 1:
                for config, seed in work:
                    result = run_single(z3_bin, smt_file, seed, config,
                                        timeout, trace_tags, verbosity, z3_log,
                                        save_dir, parent_tmpdir=sweep_tmp)
                    process_result(result)
            else:
                _run_parallel(
                    work, jobs, process_result,
                    z3_bin=z3_bin, smt_file=smt_file, timeout=timeout,
                    trace_tags=trace_tags, verbosity=verbosity, z3_log=z3_log,
                    save_dir=save_dir, parent_tmpdir=sweep_tmp,
                )
    except KeyboardInterrupt:
        print("\n[interrupted] killing running z3 processes and cleaning up...",
              file=sys.stderr)
        _cleanup_temp_dirs()
        sys.exit(130)
    finally:
        shutil.rmtree(sweep_tmp, ignore_errors=True)
        _active_temp_dirs.discard(sweep_tmp)

    return table, all_results


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
