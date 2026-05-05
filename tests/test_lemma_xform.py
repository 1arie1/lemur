"""Tests for varmap-resolved x-form fingerprinting (no -tr:nra needed)."""

from pathlib import Path

import pytest

from lemur.lemma_xform import (
    parse_lemma_xform_calls,
    parse_xform_calls,
    _resolve_jvars,
    _extract_lemma_jform,
    _coarse_signature,
)
from lemur.nra_parsers import build_xform_report


SAMPLES = Path(__file__).parent / 'sample_traces'
NLA_VARMAP = SAMPLES / 'nla_varmap_basic.trace'      # 3 lemmas, 2 unique
NRA_ONLY = SAMPLES / 'nra_xform_repeats.trace'       # synthetic, [nra] entries only


def test_resolve_jvars_substitutes_simple():
    vm = {'j16': 'R188', 'j21': 'R195'}
    assert _resolve_jvars('j16 + j21 <= 0', vm) == 'R188 + R195 <= 0'


def test_resolve_jvars_handles_composite_expr():
    vm = {'j26': '(* R188 R195)'}
    assert _resolve_jvars('j26 >= 1', vm) == '(* R188 R195) >= 1'


def test_resolve_jvars_chained_substitution():
    # If varmap chains (j87 -> "(* R188 j82)" -> j82 -> "(div I176 R188)"),
    # the loop should resolve transitively.
    vm = {'j87': '(* R188 j82)', 'j82': '(div I176 R188)'}
    assert _resolve_jvars('j87 >= 1', vm) == '(* R188 (div I176 R188)) >= 1'


def test_extract_lemma_jform_basic():
    body = (
        "propagate value - lower bound of range is above value 1\n"
        "(81) j16 >= 1\n"
        "(193) j21 >= 1\n"
        " ==> j26 >= 1\n"
        "j21 =   3112          [1, oo]\n"
    )
    preconds, concl = _extract_lemma_jform(body)
    assert preconds == ['j16 >= 1', 'j21 >= 1']
    assert concl == 'j26 >= 1'


def test_parse_lemma_xform_calls_full_pass():
    calls = parse_lemma_xform_calls(NLA_VARMAP)
    assert len(calls) == 3
    # Lemmas 1 and 3 share a fingerprint; lemma 2 is unique.
    assert calls[0].fingerprint == calls[2].fingerprint
    assert calls[0].fingerprint != calls[1].fingerprint
    # Resolved constraints for lemma 1: R188 >= 1, R195 >= 1, (* R188 R195) >= 1
    assert any('R188 >= 1' in c for c in calls[0].constraints)
    assert any('R195 >= 1' in c for c in calls[0].constraints)
    assert any('(* R188 R195) >= 1' in c for c in calls[0].constraints)


def test_parse_lemma_xform_extracts_R_and_I_vars():
    calls = parse_lemma_xform_calls(NLA_VARMAP)
    # Lemma 2 references R188 (from j16) and I176 (inside (div I176 R188)).
    assert 'R188' in calls[1].variables
    assert 'I176' in calls[1].variables


def test_xform_report_via_varmap():
    calls = parse_lemma_xform_calls(NLA_VARMAP)
    report = build_xform_report(calls, top=10)
    assert report.total == 3
    assert report.unique_fingerprints == 2
    # One repeating fingerprint, count=2.
    assert len(report.repeats) == 1
    count, _ = report.repeats[0]
    assert count == 2


def test_parse_xform_calls_auto_picks_varmap_when_present():
    calls, source = parse_xform_calls(str(NLA_VARMAP), prefer='auto')
    assert source == 'varmap'
    assert len(calls) == 3


def test_parse_xform_calls_auto_falls_back_to_nra():
    # NRA_ONLY has [nra] entries but no ~lemma_builder + varmap pairing.
    calls, source = parse_xform_calls(str(NRA_ONLY), prefer='auto')
    assert source == 'nra'
    assert len(calls) == 4  # synthetic fixture has 4 nra calls


def test_parse_xform_calls_force_nra_path():
    # Forcing the nra path on an nla-only trace yields zero calls (and
    # the caller is expected to handle the empty result).
    calls, source = parse_xform_calls(str(NLA_VARMAP), prefer='nra')
    assert source == 'nra'
    assert calls == []


def test_parse_xform_calls_force_varmap_path():
    calls, source = parse_xform_calls(str(NLA_VARMAP), prefer='varmap')
    assert source == 'varmap'
    assert len(calls) == 3


# --- Coarse fingerprint -----------------------------------------------------


def test_coarse_collapses_standalone_integers():
    assert _coarse_signature('R188 >= 1') == 'R188 >= LIT'
    assert _coarse_signature('R188 + R195 <= 0') == 'R188 + R195 <= LIT'


def test_coarse_collapses_negatives_and_rationals():
    assert _coarse_signature('R7 >= -12') == 'R7 >= LIT'
    assert _coarse_signature('R7 <= 5/4') == 'R7 <= LIT'
    assert _coarse_signature('R7 <= -5 / 4') == 'R7 <= LIT'


def test_coarse_collapses_bignum_underscores():
    # z3's bignums print with underscores; the regex must absorb them.
    assert _coarse_signature(
        '(* R188 R195) <= 115_792_089_237'
    ) == '(* R188 R195) <= LIT'


def test_coarse_preserves_smt_identifier_digits():
    # R/I/x/j var IDs and CANON-style enode names must NOT be normalized
    # — they identify the structural variable, not snapshot data.
    assert _coarse_signature('R188 >= 1') == 'R188 >= LIT'
    assert _coarse_signature('I176 + I99 = 0') == 'I176 + I99 = LIT'
    assert _coarse_signature('x12 * x42 >= 1') == 'x12 * x42 >= LIT'
    assert _coarse_signature(
        '(* CANON123!!8) <= 0'
    ) == '(* CANON123!!8) <= LIT'


def test_coarse_alpha_renames_aux_bool_ids_by_first_appearance():
    # First #-id seen becomes #A0, second becomes #A1, etc.
    out = _coarse_signature('(or #4422 #4427)')
    assert out == '(or #A0 #A1)'
    # Repeated reference reuses the same alias.
    out = _coarse_signature('(or #4422 (and #4427 #4422))')
    assert out == '(or #A0 (and #A1 #A0))'


def test_coarse_state_is_per_signature_not_global():
    # Each call to _coarse_signature starts the rename map at #A0.
    out1 = _coarse_signature('(or #4422 #4427)')
    out2 = _coarse_signature('(or #9999 #1000)')
    assert out1 == out2 == '(or #A0 #A1)'


def test_coarse_collapses_two_threshold_variants_to_one_shape():
    # Two emissions of the "same shape, different threshold" pattern.
    a = _coarse_signature('(* R188 R195) >= 1')
    b = _coarse_signature('(* R188 R195) >= 7')
    assert a == b == '(* R188 R195) >= LIT'


def test_coarse_collapses_aux_id_reorderings_to_one_shape():
    # Two emissions of the "same shape, different aux Bools in the same
    # positions". Crucial for the long-tail conclusions where the lemma
    # builder emits disjunctions over fresh auxiliaries on each call.
    a = _coarse_signature('(or #4422 #4427) ==> (* R188 R195) >= 1')
    b = _coarse_signature('(or #5500 #5503) ==> (* R188 R195) >= 7')
    assert a == b


def test_coarse_does_not_collapse_distinct_monomial_targets():
    # Different R/I bracket → different shape, even after coarsening.
    a = _coarse_signature('(* R188 R195) >= 1')
    b = _coarse_signature('(* R188 R200) >= 1')
    assert a != b


def test_parse_lemma_xform_calls_coarse_buckets_threshold_drift():
    # The basic fixture has 3 lemmas: 1 and 3 are identical (same fp
    # already under fine), 2 differs. Under coarse the same picture
    # should hold for THIS fixture (literals are all `1`, so coarse =
    # fine on the constant). This test is about pipeline correctness:
    # the coarse path runs end-to-end and produces the same kind of
    # NraCall list.
    calls = parse_lemma_xform_calls(NLA_VARMAP, coarse=True)
    assert len(calls) == 3
    assert calls[0].fingerprint == calls[2].fingerprint
    assert calls[0].fingerprint != calls[1].fingerprint
    # Display side reflects coarsening: every numeric literal is LIT.
    assert all('1' not in c or 'LIT' in c
               for c in calls[0].constraints), calls[0].constraints
    assert any('LIT' in c for c in calls[0].constraints)


def test_parse_xform_calls_threads_coarse_through_auto_path():
    fine, _ = parse_xform_calls(str(NLA_VARMAP), prefer='auto', coarse=False)
    coarse, _ = parse_xform_calls(str(NLA_VARMAP), prefer='auto', coarse=True)
    # Same call count, but per-call signatures differ once a literal
    # appears in the resolved string.
    assert len(fine) == len(coarse) == 3
    assert any('LIT' in c for c in coarse[0].constraints)
    assert not any('LIT' in c for c in fine[0].constraints)
