"""Tests for the streaming round-data parser used by `lemur n-over-time`."""

import io
import json
import subprocess
import sys

import pytest

from lemur.round import parse_round_data, quartiles


def _block(tag: str, body: str) -> str:
    return (f"-------- [{tag}] fn /tmp/x.cpp:1 ---------\n"
            f"{body}\n"
            "------------------------------------------------\n")


# Three rounds, the third unclosed at trace end.
#
# Walk:
#   DEC 5,10                              current_level climbs to 10
#   NLA #1            → starts round 0 at (idx=1, N=10)
#   DEC 15            in-round; round 0 max → 15
#   NLA #2            in-round
#   AC  @18 conflict  in-round; counts toward round_length
#   DEC 20            in-round; round 0 max → 20
#   POP target=7      target<N(=10) → round-ending; climb=10, end_drop=3
#   DEC 12            current_level=12 (between rounds)
#   NLA #3            → starts round 1 at (idx=3, N=12); pop_marks[0]=(3,10,7)
#   DEC 15            in-round; round 1 max → 15
#   POP target=13     target≥N(=12) → internal pop; drop=5
#   POP target=8      target<N(=12) → round-ending; climb=3, end_drop=4
#   NLA #4            → starts round 2 at (idx=4, N=8);  pop_marks[1]=(4,12,8)
#   <trace ends, round 2 unclosed>
SYNTHETIC_TRACE = "".join([
    _block("decide",        "splitting, lvl: 5"),
    _block("decide",        "splitting, lvl: 10"),
    _block("nla_solver",    "~lemma_builder"),
    _block("decide",        "splitting, lvl: 15"),
    _block("nla_solver",    "~lemma_builder"),
    _block("arith_conflict", "@18 conflict"),
    _block("decide",        "splitting, lvl: 20"),
    _block("pop_scope",     "backtracking: 13, new_lvl: 7"),
    _block("decide",        "splitting, lvl: 12"),
    _block("nla_solver",    "~lemma_builder"),
    _block("decide",        "splitting, lvl: 15"),
    _block("pop_scope",     "backtracking: 5, new_lvl: 13"),
    _block("pop_scope",     "backtracking: 10, new_lvl: 8"),
    _block("nla_solver",    "~lemma_builder"),
])


def _write_trace(tmp_path, name, content):
    p = tmp_path / name
    p.write_text(content)
    return p


def test_parse_three_round_trace(tmp_path):
    p = _write_trace(tmp_path, "synthetic.trace", SYNTHETIC_TRACE)
    rd = parse_round_data(p)

    assert rd.label == "synthetic"
    assert rd.n_series == [10, 15, 12, 8]

    # round_starts has 3 entries (third round didn't close before trace end).
    assert rd.round_starts == [(1, 10), (3, 12), (4, 8)]
    # pop_marks has 2 entries — one per closed round.
    assert rd.pop_marks == [(3, 10, 7), (4, 12, 8)]

    # Pairing invariant: pop_marks[i] closes round_starts[i].
    for (start_idx, _start_N), (pop_idx, prev_N, _post) in zip(
        rd.round_starts, rd.pop_marks
    ):
        assert start_idx <= pop_idx
        assert prev_N == _start_N  # prev_N in pop_marks must match the round's anchor N

    assert rd.round_climbs == [10, 3]
    assert rd.internal_pop_drops == [5]
    assert rd.end_drops == [3, 4]
    assert rd.round_lengths == [4, 2]

    assert rd.totals == {
        "rounds": 3,
        "closed_rounds": 2,
        "nla_emissions": 4,
        "decisions": 6,
        "pops": 3,
        "arith_conflicts": 1,
    }
    assert rd.max_decision_level == 20


def test_empty_trace_warns(tmp_path, capsys):
    p = _write_trace(tmp_path, "empty.trace", "no trace blocks here\n")
    rd = parse_round_data(p)
    assert rd.n_series == []
    assert rd.round_starts == []
    assert rd.pop_marks == []
    captured = capsys.readouterr()
    assert "no NLA emissions" in captured.err


def test_false_case_of_check_nla_also_emits(tmp_path):
    """The other NLA-emission marker variant must also be recognized."""
    trace = "".join([
        _block("decide", "splitting, lvl: 3"),
        _block("nla_solver", "false_case_of_check_nla on monomial 5"),
        _block("decide", "splitting, lvl: 7"),
        _block("nla_solver", "~lemma_builder"),
        _block("pop_scope", "backtracking: 4, new_lvl: 1"),
    ])
    p = _write_trace(tmp_path, "alt.trace", trace)
    rd = parse_round_data(p)
    assert rd.n_series == [3, 7]
    assert rd.totals["nla_emissions"] == 2


def test_pop_below_zero_round_anchor(tmp_path):
    """Round anchored at N=0 must still be detected; only target<N triggers
    round-end. target=0 with N=0 is a no-op (target>=N)."""
    trace = "".join([
        _block("nla_solver", "~lemma_builder"),       # round 0 starts at N=0
        _block("decide", "splitting, lvl: 5"),
        _block("pop_scope", "backtracking: 5, new_lvl: 0"),  # target=N → internal
    ])
    p = _write_trace(tmp_path, "z.trace", trace)
    rd = parse_round_data(p)
    assert rd.round_starts == [(1, 0)]
    assert rd.pop_marks == []   # no round-ending pop (target 0 not < N 0)
    assert rd.internal_pop_drops == [5]


def test_quartiles_empty_and_simple():
    assert quartiles([])["n"] == 0
    q = quartiles([1, 2, 3, 4, 5, 6, 7, 8, 9, 10])
    assert q["n"] == 10
    assert q["min"] == 1
    assert q["max"] == 10
    assert q["mean"] == 5.5


def test_cli_json_format(tmp_path):
    """Smoke test: invoke `lemur n-over-time --format json` end-to-end."""
    p = _write_trace(tmp_path, "cli.trace", SYNTHETIC_TRACE)
    result = subprocess.run(
        [sys.executable, "-m", "lemur.cli.main",
         "n-over-time", str(p), "--format", "json"],
        capture_output=True, text=True, check=True,
    )
    payload = json.loads(result.stdout)
    assert "cli" in payload
    entry = payload["cli"]
    assert entry["totals"]["rounds"] == 3
    assert entry["totals"]["closed_rounds"] == 2
    assert entry["n_series"] == [10, 15, 12, 8]
    assert entry["round_starts"] == [[1, 10], [3, 12], [4, 8]]
    assert entry["pop_marks"] == [[3, 10, 7], [4, 12, 8]]


def test_cli_table_csv_to_file(tmp_path):
    """With --out PATH and --format table, CSV is written to file."""
    p = _write_trace(tmp_path, "csv.trace", SYNTHETIC_TRACE)
    out = tmp_path / "out.csv"
    subprocess.run(
        [sys.executable, "-m", "lemur.cli.main",
         "n-over-time", str(p), "--format", "table", "--out", str(out)],
        check=True,
    )
    text = out.read_text()
    assert "csv,rounds,3" in text
    assert "csv,nla_emissions,4" in text
    assert "round climb (max-N)" in text
