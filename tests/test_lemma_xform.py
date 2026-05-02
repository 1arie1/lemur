"""Tests for varmap-resolved x-form fingerprinting (no -tr:nra needed)."""

from pathlib import Path

import pytest

from lemur.lemma_xform import (
    parse_lemma_xform_calls,
    parse_xform_calls,
    _resolve_jvars,
    _extract_lemma_jform,
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
