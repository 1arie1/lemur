"""
Auto-discover Boolean case-splits in an SMT2 benchmark.

Parses the benchmark via the z3 Python API, enumerates candidate Bool
predicates (reachability symbols matching `BLK__\\d+` + ITE guards),
probe-scores each via a short simplification tactic, and greedily picks a
plan of up to `log2(max_leaves)` splits. Leaves and a `plan.json` manifest
are emitted via `emit_leaves`.

Design notes:

- z3-solver is an OPTIONAL dependency declared under the `[split]` extra.
  All `import z3` calls happen inside functions so that `import lemur.split`
  does not require z3 at module load; `_import_z3()` raises a helpful
  SystemExit if it's missing.

- Probes are strictly syntactic + simplification — no solver calls. This
  keeps planning fast and bounded by `--split-probe-timeout`.

- The z3 Python API may be a different build than the z3 binary used by
  `lemur sweep` to actually solve leaves. That is FINE: split only
  manipulates formulas (parse, simplify, emit SMT2). The solver of record
  is the binary that re-parses the leaves.
"""

from __future__ import annotations

import json
import re
import shutil
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path


LEMUR_VERSION = "0.1.0"
PLAN_VERSION = 1


class SplitError(RuntimeError):
    """Expected error during splitting (missing plan.json, invalid SMT, etc.)."""


def _import_z3():
    try:
        import z3
        return z3
    except ImportError:
        raise SystemExit(
            "lemur split requires the z3-solver Python package.\n"
            "Install with:  pip install 'lemur[split]'"
        )


# --- Data model --------------------------------------------------------------


@dataclass
class Candidate:
    name: str
    score: float
    reduces_to_false_on: str | None  # "true" | "false" | None
    probe_ms: dict[str, float]       # {"true": ms, "false": ms}


@dataclass
class LeafSpec:
    valuation: dict[str, bool]       # {"BLK__25": true, "TB0": false, ...}
    file: str | None                 # leaf_T_F_T.smt2, None if pruned or plan_only
    pruned: bool
    reason: str | None


@dataclass
class Plan:
    source: str                      # basename of the source file as copied in
    source_abs: str                  # absolute path at emission time
    split_predicates: list[Candidate]
    leaves: list[LeafSpec]
    version: int = PLAN_VERSION
    lemur_version: str = LEMUR_VERSION
    results: dict | None = None      # reserved for Phase 2 sweep annotation


# --- Scoring -----------------------------------------------------------------


_SCORE_COLLAPSE = 10_000.0


@dataclass
class _Metrics:
    num_exprs: int = 0
    num_ites: int = 0
    num_divs: int = 0
    num_mods: int = 0
    arith_max_deg: int = 0
    is_qflia: bool = False


def _ast_walk_counts(z3, exprs):
    """Count ITE, IDIV, MOD ops by DAG-traversal of `exprs` list."""
    num_ites = num_divs = num_mods = 0
    seen = set()
    stack = list(exprs)
    while stack:
        e = stack.pop()
        eid = e.get_id()
        if eid in seen:
            continue
        seen.add(eid)
        if z3.is_app(e):
            k = e.decl().kind()
            if k == z3.Z3_OP_ITE:
                num_ites += 1
            elif k == z3.Z3_OP_IDIV:
                num_divs += 1
            elif k == z3.Z3_OP_MOD:
                num_mods += 1
            for i in range(e.num_args()):
                stack.append(e.arg(i))
    return num_ites, num_divs, num_mods


def _measure_goal(z3, goal) -> _Metrics:
    """Compute metrics on a Goal (or a list of subgoals summed)."""
    subgoals = [goal] if hasattr(goal, 'size') and not hasattr(goal, '__iter__') else list(goal)
    # Probes only work on Goal objects. If given ApplyResult, iterate subgoals.
    m = _Metrics()
    all_exprs = []
    for sg in subgoals:
        try:
            m.num_exprs += int(z3.Probe('num-exprs')(sg))
        except Exception:
            pass
        try:
            deg = int(z3.Probe('arith-max-deg')(sg))
            if deg > m.arith_max_deg:
                m.arith_max_deg = deg
        except Exception:
            pass
        try:
            is_lia = bool(z3.Probe('is-qflia')(sg))
            m.is_qflia = m.is_qflia or is_lia
        except Exception:
            pass
        for i in range(sg.size()):
            all_exprs.append(sg[i])
    ni, nd, nm = _ast_walk_counts(z3, all_exprs)
    m.num_ites = ni
    m.num_divs = nd
    m.num_mods = nm
    return m


def _measure_apply_result(z3, result) -> _Metrics:
    """Sum-aggregate metrics across all subgoals of an ApplyResult."""
    subgoals = [result[i] for i in range(len(result))]
    return _measure_goal(z3, subgoals)


def _gain(base: _Metrics, leaf: _Metrics) -> float:
    return (
        0.1 * (base.num_exprs - leaf.num_exprs)
        + 5.0 * (base.num_ites - leaf.num_ites)
        + 3.0 * (base.arith_max_deg - leaf.arith_max_deg)
        + 2.0 * (base.num_divs - leaf.num_divs)
        + 2.0 * (base.num_mods - leaf.num_mods)
        + (50.0 if leaf.is_qflia and not base.is_qflia else 0.0)
    )


# --- Candidate enumeration ---------------------------------------------------


def _collect_bool_consts(z3, exprs):
    """Return {name: BoolRef} for all declared 0-ary Bool symbols that appear
    as constants in any of `exprs` (DAG-walk)."""
    out: dict[str, object] = {}
    seen = set()
    stack = list(exprs)
    while stack:
        e = stack.pop()
        eid = e.get_id()
        if eid in seen:
            continue
        seen.add(eid)
        if z3.is_const(e) and e.sort().kind() == z3.Z3_BOOL_SORT:
            if not z3.is_true(e) and not z3.is_false(e):
                name = e.decl().name()
                if name not in out:
                    out[name] = e
        if z3.is_app(e):
            for i in range(e.num_args()):
                stack.append(e.arg(i))
    return out


def _collect_ite_guards(z3, exprs):
    """Return set of Bool-constant names used as ITE guards in `exprs`.

    Only raw declared Bool symbols count (no compound guards)."""
    names: set[str] = set()
    seen = set()
    stack = list(exprs)
    while stack:
        e = stack.pop()
        eid = e.get_id()
        if eid in seen:
            continue
        seen.add(eid)
        if z3.is_app(e):
            if e.decl().kind() == z3.Z3_OP_ITE and e.num_args() == 3:
                guard, then_br, else_br = e.arg(0), e.arg(1), e.arg(2)
                if (z3.is_const(guard)
                        and guard.sort().kind() == z3.Z3_BOOL_SORT
                        and not z3.is_true(guard) and not z3.is_false(guard)
                        and then_br.get_id() != else_br.get_id()):
                    names.add(guard.decl().name())
            for i in range(e.num_args()):
                stack.append(e.arg(i))
    return names


def _enumerate_candidates(z3, goal, name_pattern: str) -> list[str]:
    """Return an ordered list of Bool candidate names."""
    asserts = [goal[i] for i in range(goal.size())]
    bool_consts = _collect_bool_consts(z3, asserts)
    ite_guards = _collect_ite_guards(z3, asserts)

    pat = re.compile(name_pattern)
    reachability = [n for n in bool_consts if pat.search(n)]
    guards = [n for n in ite_guards if n not in reachability]

    # Deterministic ordering: reachability first (sorted), then guards.
    return sorted(reachability) + sorted(guards)


# --- Tactics -----------------------------------------------------------------


def _simplify_tactic(z3, timeout_s: float):
    """Build the standard simplification tactic with a per-apply timeout."""
    t = z3.Then(
        z3.Tactic('simplify'),
        z3.Tactic('propagate-values'),
        z3.Tactic('solve-eqs'),
        z3.Tactic('elim-uncnstr'),
    )
    return z3.TryFor(t, int(timeout_s * 1000))


def _apply_with_assumption(z3, goal, name: str, value: bool, tactic):
    """Clone `goal`, add `C=value`, apply `tactic`. Return (ApplyResult | None,
    collapsed_to_false: bool, elapsed_ms: float). None result means the tactic
    raised (timeout / z3 exception)."""
    g2 = z3.Goal()
    for i in range(goal.size()):
        g2.add(goal[i])
    C = z3.Bool(name)
    g2.add(C if value else z3.Not(C))
    start = time.monotonic()
    try:
        r = tactic.apply(g2)
    except z3.Z3Exception:
        return None, False, (time.monotonic() - start) * 1000
    elapsed_ms = (time.monotonic() - start) * 1000
    collapsed = any(r[i].inconsistent() for i in range(len(r)))
    return r, collapsed, elapsed_ms


# --- Planning ----------------------------------------------------------------


def _ceil_log2(n: int) -> int:
    n = max(1, n)
    return (n - 1).bit_length()


def build_plan(
    src_path: str,
    *,
    max_leaves: int = 32,
    threshold: float = 10.0,
    probe_timeout: float = 5.0,
    name_pattern: str = r'BLK__\d+',
) -> Plan:
    """Parse `src_path`, enumerate candidates, score + greedy plan."""
    z3 = _import_z3()

    try:
        asserts = z3.parse_smt2_file(src_path)
    except z3.Z3Exception as e:
        raise SplitError(f"failed to parse {src_path}: {e}") from e

    goal = z3.Goal()
    for a in asserts:
        goal.add(a)

    tactic = _simplify_tactic(z3, probe_timeout)

    current_goal = goal
    try:
        base_result = tactic.apply(current_goal)
        base_metrics = _measure_apply_result(z3, base_result)
    except z3.Z3Exception:
        base_metrics = _measure_goal(z3, current_goal)

    plan_cands: list[Candidate] = []
    picked_names: set[str] = set()
    max_splits = _ceil_log2(max_leaves)

    for _ in range(max_splits):
        names = [n for n in _enumerate_candidates(z3, current_goal, name_pattern)
                 if n not in picked_names]
        if not names:
            break

        scored: list[tuple[str, float, str | None, dict[str, float], bool | None]] = []
        # tuple: (name, score, reduces_on, probe_ms, harder_valuation)
        for n in names:
            r_t, c_t, ms_t = _apply_with_assumption(z3, current_goal, n, True, tactic)
            r_f, c_f, ms_f = _apply_with_assumption(z3, current_goal, n, False, tactic)

            gain_t = _SCORE_COLLAPSE if c_t else (
                _gain(base_metrics, _measure_apply_result(z3, r_t)) if r_t is not None else 0.0
            )
            gain_f = _SCORE_COLLAPSE if c_f else (
                _gain(base_metrics, _measure_apply_result(z3, r_f)) if r_f is not None else 0.0
            )

            reduces_on = 'true' if c_t else ('false' if c_f else None)

            # Score semantics:
            # - If either branch collapses to false, rate the split at
            #   SCORE_COLLAPSE (one side is free; just solve the other).
            # - Otherwise min(gain_t, gain_f): both leaves must be tractable
            #   for the UNSAT-by-decomposition workflow.
            if c_t or c_f:
                score = _SCORE_COLLAPSE
            else:
                score = min(gain_t, gain_f)

            # "Harder" branch for re-scoring context: the one without collapse,
            # or the one with smaller individual gain (worse simplification).
            if c_t and not c_f:
                harder = False
            elif c_f and not c_t:
                harder = True
            elif c_t and c_f:
                harder = None  # both collapse — split is pointless beyond this depth
            else:
                harder = gain_t < gain_f   # True-side harder when gain_t smaller

            scored.append((n, score, reduces_on,
                           {'true': round(ms_t, 1), 'false': round(ms_f, 1)},
                           harder))

        # Sort by score desc; tiebreak by name for determinism.
        scored.sort(key=lambda s: (-s[1], s[0]))
        best_name, best_score, best_reduces, best_ms, best_harder = scored[0]

        if best_score < threshold:
            break

        plan_cands.append(Candidate(
            name=best_name,
            score=round(best_score, 3),
            reduces_to_false_on=best_reduces,
            probe_ms=best_ms,
        ))
        picked_names.add(best_name)

        if best_harder is None:
            break

        # Step context: assume the harder branch, re-measure base.
        next_goal = z3.Goal()
        for i in range(current_goal.size()):
            next_goal.add(current_goal[i])
        C = z3.Bool(best_name)
        next_goal.add(C if best_harder else z3.Not(C))
        current_goal = next_goal
        try:
            br = tactic.apply(current_goal)
            base_metrics = _measure_apply_result(z3, br)
        except z3.Z3Exception:
            base_metrics = _measure_goal(z3, current_goal)

    # --- Build leaf manifest (2^k valuations over the chosen plan) ---
    leaves = _build_leaf_specs(z3, goal, plan_cands, tactic)

    return Plan(
        source=Path(src_path).name,
        source_abs=str(Path(src_path).resolve()),
        split_predicates=plan_cands,
        leaves=leaves,
    )


def _build_leaf_specs(z3, original_goal, plan_cands: list[Candidate], tactic) -> list[LeafSpec]:
    """For each valuation tuple, detect pruning. Emit LeafSpec list."""
    k = len(plan_cands)
    specs: list[LeafSpec] = []
    if k == 0:
        # No splits were found; sweep will just run the original benchmark.
        return specs

    for bits in range(1 << k):
        valuation: dict[str, bool] = {}
        label_parts: list[str] = []
        for i, cand in enumerate(plan_cands):
            v = bool(bits & (1 << i))
            valuation[cand.name] = v
            label_parts.append('T' if v else 'F')
        label = '_'.join(label_parts)

        # Check if this valuation reduces to false under simplification.
        g = z3.Goal()
        for i in range(original_goal.size()):
            g.add(original_goal[i])
        for name, v in valuation.items():
            C = z3.Bool(name)
            g.add(C if v else z3.Not(C))

        pruned = False
        reason = None
        try:
            r = tactic.apply(g)
            if any(r[i].inconsistent() for i in range(len(r))):
                pruned = True
                reason = "reduces to false"
        except Exception:
            pass

        specs.append(LeafSpec(
            valuation=valuation,
            file=None if pruned else f"leaf_{label}.smt2",
            pruned=pruned,
            reason=reason,
        ))

    return specs


# --- Emission ----------------------------------------------------------------


def emit_leaves(plan: Plan, src_path: str, out_dir: str, plan_only: bool = False) -> None:
    """Copy source, write plan.json, emit non-pruned leaves as SMT2 files."""
    from lemur.smt_inject import make_split_smt

    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)

    # Copy source verbatim into the output directory.
    shutil.copy2(src_path, out / plan.source)

    if plan_only:
        # Strip filenames; files don't exist on disk.
        for leaf in plan.leaves:
            if not leaf.pruned:
                leaf.file = None
    else:
        for leaf in plan.leaves:
            if leaf.pruned or leaf.file is None:
                continue
            injection_lines = []
            for cand in plan.split_predicates:
                v = leaf.valuation[cand.name]
                if v:
                    injection_lines.append(f"(assert {cand.name})")
                else:
                    injection_lines.append(f"(assert (not {cand.name}))")
            injection = '\n'.join(injection_lines)
            make_split_smt(src_path, injection, str(out / leaf.file))

    (out / 'plan.json').write_text(_plan_to_json(plan))


# --- plan.json (de)serialization --------------------------------------------


def _plan_to_json(plan: Plan) -> str:
    d = {
        "version": plan.version,
        "lemur_version": plan.lemur_version,
        "source": plan.source,
        "source_abs": plan.source_abs,
        "split_predicates": [asdict(c) for c in plan.split_predicates],
        "leaves": [asdict(l) for l in plan.leaves],
        "results": plan.results,
    }
    return json.dumps(d, indent=2) + "\n"


def read_plan(path_or_dir: str) -> Plan:
    """Read a plan.json from a directory or a direct file path."""
    p = Path(path_or_dir)
    if p.is_dir():
        p = p / 'plan.json'
    if not p.exists():
        raise SplitError(f"plan.json not found at {p}")
    try:
        d = json.loads(p.read_text())
    except (OSError, json.JSONDecodeError) as e:
        raise SplitError(f"cannot read plan.json at {p}: {e}") from e
    if not isinstance(d, dict):
        raise SplitError(f"{p}: plan.json root is not an object")
    ver = d.get('version')
    if ver is not None and int(ver) > PLAN_VERSION:
        raise SplitError(
            f"{p}: plan.json version {ver} is newer than this lemur supports "
            f"(max {PLAN_VERSION}). Upgrade lemur."
        )
    cands = [Candidate(**c) for c in d.get('split_predicates', [])]
    leaves = [LeafSpec(**l) for l in d.get('leaves', [])]
    return Plan(
        source=d.get('source', ''),
        source_abs=d.get('source_abs', ''),
        split_predicates=cands,
        leaves=leaves,
        version=d.get('version', PLAN_VERSION),
        lemur_version=d.get('lemur_version', LEMUR_VERSION),
        results=d.get('results'),
    )
