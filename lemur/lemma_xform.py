"""
Stable nlsat-call fingerprints from `-tr:nla_solver` alone, via per-lemma
varmap snapshots.

Each `~lemma_builder` block is followed (in trace order) by a single
`[nla_solver] false_case_of_check_nla` block whose body carries a
`varmap:` line. The varmap maps the lemma's j-vars to z3-internal R/I
identifiers (and composite expressions over them) AT THAT LEMMA'S
EMISSION TIME — a per-call snapshot, not a global table.

Resolving the lemma's preconditions + conclusion through that snapshot
yields an R/I-form expression set whose tokens (`R188`, `(* R188 R195)`,
...) are stable across nlsat invocations: R/I IDs are z3 enode IDs,
which are not recycled, unlike LP-column j-IDs.

This path produces the same stability guarantee as the older
`-tr:nra`-based fingerprinter without the 8x trace-size cost. The
[nra] path remains available as a fallback when the user has separately
captured an nra trace.
"""

from __future__ import annotations

import hashlib
import re
import statistics
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from lemur.nra_parsers import NraCall, XFormReport
from lemur.parsers import TraceEntry, parse_trace, parse_varmap_line


_J_VAR_RE = re.compile(r'\bj\d+\b')
_R_OR_I_VAR_RE = re.compile(r'\b[RI]\d+\b')

# --- Coarse ("structural") fingerprint normalization ------------------------
#
# Two passes turn a varmap-resolved lemma signature into a structural shape:
#
#   1. Alpha-rename `#NNN` aux Bool/Int IDs by first appearance within the
#      single signature being normalized, so two emissions that disagree
#      only on which Boolean atoms appear in disjunctions hash the same.
#      State is local to one signature — the next signature starts fresh
#      at #A0.
#   2. Collapse standalone integer / rational literals to the token `LIT`,
#      so threshold values that drift with the model snapshot stop
#      driving spurious uniqueness. Negative lookarounds preserve the
#      digit suffixes inside SMT identifiers (R188, I99, j26, x12,
#      CANON123) and the alpha-rename tokens (#A0) we just inserted —
#      those are stable structural identity, not snapshot data.
#
# See ../z3-research/lemur/structural-fingerprint-proposal.md.
_AUX_ID_RE = re.compile(r'#\d+')
_LITERAL_RE = re.compile(
    r'(?<![A-Za-z\d#_!])'                # no letter/digit/#/_/! before
    r'-?\d[\d_]*'                        # digit run (with bignum '_' separators)
    r'(?:\s*/\s*-?\d[\d_]*)?'            # optional rational denominator
    r'(?![A-Za-z_\d!])'                  # no letter/digit/_/! after
)
# `!` joins z3 enode-occurrence ids (`CANON123!!8`); excluding it keeps
# the trailing `8` glued to the identifier instead of collapsing to LIT.


def _coarse_signature(text: str) -> str:
    """Map a resolved lemma signature to its structural shape.

    Order matters: alpha-rename #NNN before collapsing literals, otherwise
    the digit-collapse pass would erase the very tokens step 1 needs.
    """
    seen: dict[str, str] = {}

    def _rename(m: re.Match) -> str:
        tok = m.group(0)
        if tok not in seen:
            seen[tok] = f'#A{len(seen)}'
        return seen[tok]

    text = _AUX_ID_RE.sub(_rename, text)
    text = _LITERAL_RE.sub('LIT', text)
    return text


def _resolve_jvars(expr: str, varmap: dict[str, str]) -> str:
    """Substitute every `j\\d+` token in `expr` with its varmap value.

    Re-runs the substitution until no j-tokens remain or no more change
    happens (the varmap may chain — j26 -> "(* R188 R195)" — but in
    practice z3 emits a single hop, so this loop usually runs once).
    Tokens not in varmap are left as-is; downstream the fingerprint
    will absorb them, surfacing missing-mapping cases as distinct
    fingerprints rather than crashes.
    """
    prev = None
    cur = expr
    for _ in range(8):  # safety cap on chain depth
        if cur == prev:
            break
        prev = cur
        cur = _J_VAR_RE.sub(
            lambda m: varmap.get(m.group(0), m.group(0)), cur
        )
    return cur


def _normalize(expr: str) -> str:
    """Collapse whitespace; deterministic single-line form for hashing."""
    return ' '.join(expr.split())


def _fingerprint(items: list[str]) -> str:
    h = hashlib.sha1()
    for line in sorted(items):
        h.update(line.encode('utf-8'))
        h.update(b'\n')
    return h.hexdigest()[:12]


def _extract_varmap(body: str) -> dict[str, str]:
    """Pull the single `varmap:` line out of an entry body, parse it."""
    for line in body.splitlines():
        s = line.strip()
        if s.startswith('varmap:'):
            return parse_varmap_line(s)
    return {}


_PRECOND_LINE_RE = re.compile(r'^\((?:\d+)\)\s*(.+)$')


def _extract_lemma_jform(body: str) -> tuple[list[str], str | None]:
    """Pull preconditions + conclusion from a ~lemma_builder body.

    Returns (precond_strings, conclusion_or_None). Anything after the
    `==>` line (variable assignments, monomial defs) is ignored — those
    are descriptive, not part of the lemma signature.
    """
    preconds: list[str] = []
    concl: str | None = None
    saw_concl = False
    for raw in body.splitlines():
        line = raw.rstrip()
        s = line.lstrip()
        if not s:
            continue
        if s.startswith('==>') or s.startswith('=>'):
            concl = s.lstrip('=').lstrip('>').strip()
            saw_concl = True
            continue
        if saw_concl:
            continue
        m = _PRECOND_LINE_RE.match(s)
        if m:
            preconds.append(m.group(1).strip())
            continue
        # Strategy line, free-form text — skip.
    return preconds, concl


def parse_lemma_xform_calls(
    trace_path: str | Path, *, coarse: bool = False,
) -> list[NraCall]:
    """One NraCall per ~lemma_builder block, with R/I-form fingerprints
    derived from the per-lemma varmap snapshot.

    Pairing rule: each ~lemma_builder is followed (in trace order, only
    nla_solver entries considered) by a `false_case_of_check_nla` entry
    whose body has the varmap line. Lemmas without a paired varmap are
    skipped — without one, fingerprinting would fall back to j-form,
    which is the broken state we're trying to escape.

    `coarse=True` switches to structural fingerprinting: aux Bool IDs
    are alpha-renamed and integer/rational literals collapse to LIT.
    Different threshold values and different aux-Bool atom orderings
    on the same monomial target then hash to one shape.
    """
    entries = [e for e in parse_trace(trace_path) if e.tag == 'nla_solver']
    return _calls_from_entries(entries, coarse=coarse)


def _calls_from_entries(
    entries: list[TraceEntry], *, coarse: bool = False,
) -> list[NraCall]:
    """Pair lemma_builder entries with varmap-bearing entries in FIFO order.

    z3 batches lemma emissions: it may flush several `~lemma_builder` blocks
    before flushing the corresponding `varmap:` blocks. Strict adjacency
    would drop ~78% of lemmas (467/2175 on the test trace) when in fact
    the counts match 1:1 globally. FIFO pairing recovers all of them.
    """
    lemmas = [e for e in entries if e.function == '~lemma_builder']
    varmaps = [e for e in entries if 'varmap:' in e.body]

    calls: list[NraCall] = []
    for lemma, vm_entry in zip(lemmas, varmaps):
        varmap = _extract_varmap(vm_entry.body)
        if not varmap:
            continue
        preconds_j, concl_j = _extract_lemma_jform(lemma.body)
        resolved: list[str] = []
        for p in preconds_j:
            resolved.append(_normalize(_resolve_jvars(p, varmap)))
        if concl_j is not None:
            resolved.append(_normalize(_resolve_jvars(concl_j, varmap)))
        if not resolved:
            continue
        # `variables` is computed from the un-coarsened resolved strings
        # so that nvars / vars-head columns stay meaningful (R/I IDs are
        # the structural variables, not the coarse-mode `LIT` token).
        vars_set: set[str] = set()
        for line in resolved:
            vars_set.update(_R_OR_I_VAR_RE.findall(line))
        sorted_vars = tuple(
            sorted(vars_set, key=lambda v: (v[0], int(v[1:])))
        )
        # The signature used for hashing: in coarse mode each line is
        # passed through _coarse_signature first. `constraints` (the
        # display-side tuple) follows the same transform so the top-
        # repeats representative shows the structural form, which is
        # what makes a coarse repeat actionable.
        sig_lines = [_coarse_signature(line) for line in resolved] \
            if coarse else resolved
        calls.append(NraCall(
            index=len(calls),
            constraints=tuple(sorted(sig_lines)),
            raw_constraints=tuple(resolved),
            variables=sorted_vars,
            result=None,
            fingerprint=_fingerprint(sig_lines),
            line_number=lemma.line_number,
        ))
    return calls


# --- Auto-detect facade ------------------------------------------------------


def parse_xform_calls(
    trace_path: str | Path, *, prefer: str = 'auto',
    nra_trace_path: str | Path | None = None,
    coarse: bool = False,
) -> tuple[list[NraCall], str]:
    """Pick the cheapest available x-form path; return (calls, source).

    `prefer='auto'` (default): try varmap first; fall back to [nra] data
    in `trace_path` (or `nra_trace_path`) if no varmap-bearing lemmas
    are present.

    `prefer='varmap'` / `prefer='nra'` force one path. `nra_trace_path`,
    if set, only matters when the [nra] path is selected.

    `coarse=True` swaps the per-call hashing for structural fingerprinting
    (see `_coarse_signature`). Both paths honor it.

    Returns the call list paired with the source label
    ('varmap' / 'nra') so callers can render an honest provenance note.
    """
    from lemur.nra_parsers import parse_nra_calls

    if prefer == 'varmap':
        return parse_lemma_xform_calls(trace_path, coarse=coarse), 'varmap'
    if prefer == 'nra':
        src = nra_trace_path if nra_trace_path else trace_path
        return parse_nra_calls(src, coarse=coarse), 'nra'

    # auto
    calls = parse_lemma_xform_calls(trace_path, coarse=coarse)
    if calls:
        return calls, 'varmap'
    src = nra_trace_path if nra_trace_path else trace_path
    return parse_nra_calls(src, coarse=coarse), 'nra'
