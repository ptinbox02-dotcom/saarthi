"""Pulling ops out of a half-arrived JSON response.

The board is written from a streamed completion, so ops have to be recognised before
the response is valid JSON. Everything here is a way that scan could go wrong: a brace
inside a string, an escaped quote, a number split across chunks, an object that is only
half delivered.

    python3 -m pytest classroom/tests/test_stream.py -q
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))
import app as srv  # noqa: E402

# Built with json.dumps so the fixture is provably valid JSON rather than a string
# whose escaping I have to get right by hand — the first version of this file failed
# because the *test* was malformed, not the scanner.
import json  # noqa: E402

ANSWER = {
    "say": "s is round {mostly}",
    "ops": [
        {"op": "write", "role": "term", "text": "Orbital"},
        {"op": "write", "role": "explain", "text": '90% "likely" region'},
        {"op": "draw", "shape": "sphere3d"},
    ],
    "jump_beat": 3,
}
FULL = json.dumps(ANSWER)


def test_a_complete_response_yields_every_op():
    ops, n = srv.ops_so_far(FULL, 0)
    assert n == 3 and len(ops) == 3
    assert [o["op"] for o in ops] == ["write", "write", "draw"]


def test_ops_appear_one_at_a_time_as_bytes_arrive():
    """The whole point: an op is usable the moment its closing brace lands."""
    seen, taken = [], 0
    for i in range(1, len(FULL) + 1):
        fresh, taken = srv.ops_so_far(FULL[:i], taken)
        seen.extend(fresh)
    assert len(seen) == 3, f"saw {len(seen)}"
    assert seen[0]["text"] == "Orbital"
    assert seen[2]["shape"] == "sphere3d"


def test_a_brace_inside_a_string_does_not_open_an_object():
    """`{mostly}` sits in the `say` field, before the ops array even starts."""
    ops, n = srv.ops_so_far(FULL, 0)
    assert all(o.get("op") for o in ops), ops


def test_an_escaped_quote_does_not_end_the_string():
    ops, _ = srv.ops_so_far(FULL, 0)
    assert '"likely"' in ops[1]["text"], ops[1]["text"]


def test_a_half_delivered_object_is_not_emitted():
    partial = FULL[:FULL.index('"role": "explain"')]
    ops, n = srv.ops_so_far(partial, 0)
    assert n == 1 and len(ops) == 1, f"emitted {n} from a partial second op"


def test_nothing_before_the_ops_array_arrives():
    assert srv.ops_so_far('{"say": "still writ', 0) == ([], 0)
    assert srv.ops_so_far('{"say": "done", "ops"', 0) == ([], 0)


def test_already_taken_ops_are_not_repeated():
    _, n = srv.ops_so_far(FULL, 0)
    again, n2 = srv.ops_so_far(FULL, n)
    assert again == [] and n2 == n


def test_it_survives_text_that_is_not_json_at_all():
    assert srv.ops_so_far("I'm sorry, I can't help with that.", 0) == ([], 0)


def test_the_scanner_agrees_with_a_full_parse():
    import json
    ops, _ = srv.ops_so_far(FULL, 0)
    assert ops == json.loads(FULL)["ops"]
