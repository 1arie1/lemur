"""Tests for the x-form upgrade of lemur nla-diff."""

from pathlib import Path

from lemur.nla_diff import compute_metrics, diff


SAMPLES = Path(__file__).parent / 'sample_traces'
BASIC = SAMPLES / 'nra_xform_basic.trace'      # real, 6 unique nlsat calls
REPEATS = SAMPLES / 'nra_xform_repeats.trace'  # synthetic, 4 calls, fp count 3+1


def _label_to_row(rows, prefix):
    """Find the first row whose label starts with prefix."""
    for r in rows:
        if r.label.startswith(prefix):
            return r
    raise AssertionError(f"no row {prefix!r} in {[r.label for r in rows]}")


def test_compute_metrics_picks_up_nra_in_same_trace():
    m = compute_metrics(str(BASIC))
    assert m.nlsat_calls == 6
    assert len(m.nlsat_fingerprints) == 6


def test_compute_metrics_handles_separate_nra_path():
    # Use REPEATS as a separately-captured nra trace; main trace BASIC
    # contributes its own nlsat calls plus REPEATS adds 4 more — but we
    # only read nra from one source, the override.
    m = compute_metrics(str(BASIC), nra_trace_path=str(REPEATS))
    assert m.nlsat_calls == 4
    assert len(m.nlsat_fingerprints) == 2


def test_diff_surfaces_top_nlsat_fp_when_either_side_has_repeats():
    # Both sides use REPEATS so both have the count=3 fingerprint.
    m_a = compute_metrics(str(REPEATS))
    m_b = compute_metrics(str(REPEATS))
    rows = diff(m_a, m_b, top=5)
    nls = _label_to_row(rows, "nlsat calls (x-form)")
    assert nls.a == 4 and nls.b == 4
    fp_row = _label_to_row(rows, "top-nlsat-fp(1)")
    assert fp_row.a == 3 and fp_row.b == 3
    assert "size=2" in fp_row.label


def test_diff_surfaces_b_only_repeats():
    # A has unique calls only; B has the count=3 repeat.
    m_a = compute_metrics(str(BASIC))
    m_b = compute_metrics(str(REPEATS))
    rows = diff(m_a, m_b, top=5)
    fp_row = _label_to_row(rows, "top-nlsat-fp(1)")
    assert fp_row.a == 0 and fp_row.b == 3


def test_diff_no_repeats_says_so():
    # BASIC vs BASIC: every call unique on both sides; no top-nlsat-fp(N)
    # rows surface, but a single "no repeated" row does.
    m_a = compute_metrics(str(BASIC))
    m_b = compute_metrics(str(BASIC))
    rows = diff(m_a, m_b, top=5)
    rendered = [r.label for r in rows]
    assert not any(r.startswith("top-nlsat-fp(") for r in rendered)
    assert any("no repeated nlsat" in r.delta for r in rows
               if r.label == "top-nlsat-fp")


def test_diff_handles_no_nra_data():
    # Construct metrics with empty nlsat_calls on both sides — a trace
    # captured without -tr:nra.
    m_a = compute_metrics(str(BASIC))
    m_b = compute_metrics(str(BASIC))
    m_a.nlsat_calls = 0
    m_a.nlsat_fingerprints.clear()
    m_a.nlsat_fp_to_call.clear()
    m_b.nlsat_calls = 0
    m_b.nlsat_fingerprints.clear()
    m_b.nlsat_fp_to_call.clear()
    rows = diff(m_a, m_b, top=5)
    nls = _label_to_row(rows, "nlsat calls (x-form)")
    assert nls.a == "n/a" and nls.b == "n/a"
    assert "neither trace had" in nls.delta
