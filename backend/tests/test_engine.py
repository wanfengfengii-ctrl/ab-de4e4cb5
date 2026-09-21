"""Unit tests for validation and exhaustive scheduling semantics."""
import pytest

from app.engine import AuditLimit, NetlistError, audit
from app.models import AuditRequest, DelayRange, Gate


def req(initial, target, gates, delays, monitors):
    return AuditRequest(
        initial=initial,
        target=target,
        gates={k: Gate(type=t, inputs=i) for k, (t, i) in gates.items()},
        delays={k: DelayRange(min=a, max=b) for k, (a, b) in delays.items()},
        monitors=monitors,
    )


# ---------------------------------------------------------------- basics

def test_single_buffer_flips_once():
    r = audit(req({"a": 0}, {"a": 1},
                  {"g1": ("BUF", ["a"])}, {"g1": (2, 2)}, ["g1"]))
    assert r["safe"] is True
    assert [l["time"] for l in r["timeline"]] == [0, 2]


def test_every_integer_delay_is_explored_not_just_endpoints():
    # d in [1,3] -> three distinct pending futures after t0
    r = audit(req({"a": 0}, {"a": 1},
                  {"g1": ("BUF", ["a"])}, {"g1": (1, 3)}, ["g1"]))
    assert r["safe"] is True
    assert r["timeline"][0]["reachable_states"] == 3


def test_delay_futures_merge_after_all_fired():
    r = audit(req({"a": 0}, {"a": 1},
                  {"g1": ("BUF", ["a"])}, {"g1": (1, 3)}, ["g1"]))
    assert r["timeline"][-1]["reachable_states"] == 1


def test_no_input_change_is_trivially_safe():
    r = audit(req({"a": 1}, {"a": 1},
                  {"g1": ("BUF", ["a"])}, {"g1": (0, 3)}, ["g1"]))
    assert r["safe"] is True
    assert len(r["timeline"]) == 1
    assert r["timeline"][0]["reachable_states"] == 1


# ---------------------------------------------------------------- hazards

def test_reconvergent_or_glitch_earliest_time():
    # a:1->0, g1=NOT a, g2=OR(a,g1), all d=1.
    # g2 steady 1 before and after, yet 1->0 fires in the t=1 batch.
    r = audit(req({"a": 1}, {"a": 0},
                  {"g1": ("NOT", ["a"]), "g2": ("OR", ["a", "g1"])},
                  {"g1": (1, 1), "g2": (1, 1)}, ["g2"]))
    assert r["safe"] is False
    assert r["violation"]["time"] == 1
    assert r["violation"]["gate"] == "g2"
    assert r["violation"]["monitor"] == "g2"


def test_only_some_delay_choices_hazard_still_unsafe():
    # g1 slow (d=3) lets g2 fall at t=2; g1 fast (d=1) prevents it.
    r = audit(req({"a": 1}, {"a": 0},
                  {"g1": ("NOT", ["a"]), "g2": ("OR", ["a", "g1"])},
                  {"g1": (1, 3), "g2": (2, 2)}, ["g2"]))
    assert r["safe"] is False
    assert r["violation"]["time"] == 2


def test_all_delay_choices_safe():
    # wide uncertainty on a simple buffer must stay safe.
    r = audit(req({"a": 0}, {"a": 1},
                  {"g1": ("BUF", ["a"])}, {"g1": (0, 50)}, ["g1"]))
    assert r["safe"] is True
    assert r["timeline"][0]["reachable_states"] == 51


# --------------------------------------------------------- inertial delay

def test_pending_event_cancelled_before_firing():
    # g1=NOT a (d=0) -> g2=NOT g1 (d=0) -> g3=BUF g2 (d=4).
    # a:0->1: g1 1->0 and g2 0->1 resolve within t=0 micro batches, so by
    # the time g3 is (re)evaluated it sees the final value only; exactly
    # one scheduling per re-evaluation, no stale pulse survives.
    r = audit(req(
        {"a": 0}, {"a": 1},
        {"g1": ("NOT", ["a"]), "g2": ("NOT", ["g1"]),
         "g3": ("BUF", ["g2"])},
        {"g1": (0, 0), "g2": (0, 0), "g3": (4, 4)}, ["g3"]))
    assert r["safe"] is True
    assert r["timeline"][0]["reachable_states"] == 1
    assert r["timeline"][-1]["time"] == 4


def test_zero_delay_batch_drains_without_advancing_time():
    r = audit(req({"a": 0}, {"a": 1},
                  {"g1": ("NOT", ["a"]), "g2": ("NOT", ["g1"])},
                  {"g1": (0, 0), "g2": (0, 0)}, ["g2"]))
    assert r["safe"] is True
    assert [l["time"] for l in r["timeline"]] == [0]


def test_same_time_events_apply_as_one_batch():
    # g1,g2 rise together at t=1; g3 (OR) must be evaluated exactly once.
    r = audit(req(
        {"a": 0, "b": 0}, {"a": 1, "b": 1},
        {"g1": ("BUF", ["a"]), "g2": ("BUF", ["b"]),
         "g3": ("OR", ["g1", "g2"])},
        {"g1": (1, 1), "g2": (1, 1), "g3": (1, 1)}, ["g3"]))
    assert r["safe"] is True
    assert [l["reachable_states"] for l in r["timeline"]] == [1, 1, 1]


def test_inertial_delay_cancels_pending_event_on_short_pulse():
    # g0=NOT a d=3 feeds XOR(a,g0) d=0: g1 emits a width-3 pulse (1->0 at
    # t0, restored at t3). g2=BUF g1 d=5 must NOT glitch: its pending
    # 1->0 event due at t5 is cancelled when g1 restores at t3 < t5.
    r = audit(req(
        {"a": 0}, {"a": 1},
        {"g0": ("NOT", ["a"]), "g1": ("XOR", ["a", "g0"]),
         "g2": ("BUF", ["g1"])},
        {"g0": (3, 3), "g1": (0, 0), "g2": (5, 5)}, ["g2"]))
    assert r["safe"] is True
    # g2 never visibly changes; only layers at t=0 and t=3 exist
    assert [l["time"] for l in r["timeline"]] == [0, 3]
    for step in r["witness"]["transitions"]:
        assert not any(e.startswith("g2") for e in step["events"])


def test_transport_style_short_pulse_does_glitch_with_small_delay():
    # Same circuit, but g2 delay 1 (< pulse width 3): the pulse reaches g2,
    # producing 1->0 at t1 then 0->1 at t4 -- a hazard (needed zero).
    r = audit(req(
        {"a": 0}, {"a": 1},
        {"g0": ("NOT", ["a"]), "g1": ("XOR", ["a", "g0"]),
         "g2": ("BUF", ["g1"])},
        {"g0": (3, 3), "g1": (0, 0), "g2": (1, 1)}, ["g2"]))
    assert r["safe"] is False
    assert r["violation"]["time"] == 1
    assert r["violation"]["gate"] == "g2"


# ------------------------------------------------- witness canonicalization

def test_witness_is_canonical_and_complete():
    r = audit(req({"a": 1}, {"a": 0},
                  {"g1": ("NOT", ["a"]), "g2": ("OR", ["a", "g1"])},
                  {"g1": (1, 3), "g2": (2, 2)}, ["g2"]))
    assert r["safe"] is False
    tr = r["witness"]["transitions"]
    assert tr[0]["time"] == 0
    # canonical witness takes the slow g1 (d=3): no gate batch at t=1
    assert all(s["time"] != 1 for s in tr)
    # every step carries a full state vector and pending event list
    for step in tr:
        assert "a" in step["state"] and "g1" in step["state"]
        assert isinstance(step["active_events"], list)


def test_witness_records_delay_chosen_per_event():
    r = audit(req({"a": 0}, {"a": 1},
                  {"g1": ("BUF", ["a"])}, {"g1": (2, 2)}, ["g1"]))
    last = r["witness"]["transitions"][-1]
    assert "d=2" in last["events"][0]


# ------------------------------------------------------------- validation

@pytest.mark.parametrize("bad,code", [
    (dict(initial={"a": 0}, target={"b": 1}, gates={}, delays={},
          monitors=[]), "input_mismatch"),
])
def test_input_set_mismatch(bad, code):
    with pytest.raises(NetlistError) as ei:
        audit(AuditRequest(**bad))
    assert ei.value.code == code


def test_cyclic_netlist_reports_cycle_path():
    with pytest.raises(NetlistError) as ei:
        audit(req({"a": 0}, {"a": 1},
                  {"g1": ("AND", ["a", "g2"]), "g2": ("OR", ["g1"])},
                  {"g1": (1, 1), "g2": (1, 1)}, ["g1"]))
    assert ei.value.code == "cyclic"
    assert "g1" in ei.value.location and "g2" in ei.value.location


def test_self_loop_rejected():
    with pytest.raises(NetlistError) as ei:
        audit(req({"a": 0}, {"a": 1},
                  {"g1": ("AND", ["a", "g1"])}, {"g1": (1, 1)}, ["g1"]))
    assert ei.value.code == "cyclic"


def test_unknown_net_points_at_input_index():
    with pytest.raises(NetlistError) as ei:
        audit(req({"a": 0}, {"a": 1}, {"g1": ("BUF", ["x"])},
                  {"g1": (1, 1)}, ["g1"]))
    assert ei.value.code == "unknown_net"
    assert ei.value.location == "g1.inputs[0]"


def test_missing_delay_located_at_gate():
    with pytest.raises(NetlistError) as ei:
        audit(req({"a": 0}, {"a": 1}, {"g1": ("BUF", ["a"])},
                  {}, ["g1"]))
    assert ei.value.code == "missing_delay"
    assert ei.value.location == "g1"


def test_inverted_delay_range_rejected():
    with pytest.raises(NetlistError) as ei:
        audit(req({"a": 0}, {"a": 1}, {"g1": ("BUF", ["a"])},
                  {"g1": (3, 1)}, ["g1"]))
    assert ei.value.code == "bad_delay"


def test_monitor_must_be_gate_output():
    with pytest.raises(NetlistError) as ei:
        audit(req({"a": 0}, {"a": 1}, {"g1": ("BUF", ["a"])},
                  {"g1": (1, 1)}, ["a"]))
    assert ei.value.code == "unknown_monitor"


def test_input_gate_name_collision_rejected():
    with pytest.raises(NetlistError) as ei:
        audit(req({"a": 0}, {"a": 1}, {"a": ("BUF", ["a"])},  # type: ignore
                  {"a": (1, 1)}, ["a"]))
    assert ei.value.code == "name_collision"


def test_not_requires_single_input():
    with pytest.raises(NetlistError) as ei:
        audit(req({"a": 0, "b": 0}, {"a": 1, "b": 1},
                  {"g1": ("NOT", ["a", "b"])}, {"g1": (1, 1)}, ["g1"]))
    assert ei.value.code == "bad_fanin"


def test_state_cap_enforced():
    # 20 independent buffers each with 2 delays -> 2**20 futures at t0.
    gates = {f"g{i}": ("BUF", [f"a{i}"]) for i in range(20)}
    delays = {f"g{i}": (1, 2) for i in range(20)}
    with pytest.raises(AuditLimit):
        audit(req({f"a{i}": 0 for i in range(20)},
                  {f"a{i}": 1 for i in range(20)},
                  gates, delays, ["g0"]), state_cap=100_000)


# -------------------------------------------------- larger exact-count case

def test_independent_buffers_exact_layer_counts():
    # two buffers, d in {1,2}: at t0 4 futures; both fire by t2 -> 1 state.
    r = audit(req(
        {"a": 0, "b": 0}, {"a": 1, "b": 1},
        {"g1": ("BUF", ["a"]), "g2": ("BUF", ["b"])},
        {"g1": (1, 2), "g2": (1, 2)}, ["g1", "g2"]))
    assert r["safe"] is True
    counts = {l["time"]: l["reachable_states"] for l in r["timeline"]}
    # t0: 4 delay combos; t1: 3 post-batch configs + the (t2,t2) carry = 4;
    # t2: all have fired and merged into one.
    assert counts == {0: 4, 1: 4, 2: 1}
