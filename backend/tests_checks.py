"""Ad-hoc engine checks (also exercised by the verify acceptance service)."""
from app.engine import Engine
from app.validator import validate_and_build


def audit(payload):
    spec, errors = validate_and_build(payload)
    assert not errors, errors
    return Engine(spec).run(), spec


# ---------------------------------------------------------------- case 1
# Single inverter with a clean delay interval: at most one toggle.
p = {
    "inputs": ["a"],
    "initial": {"a": 0},
    "target": {"a": 1},
    "gates": {
        "g1": {"type": "NOT", "inputs": ["a"], "delay_min": 1, "delay_max": 3},
    },
    "monitors": ["g1"],
}
r, _ = audit(p)
assert r["hazard"] is None, r["hazard"]
print("case1 safe; reachable:", r["reachable"], "combos:", r["combos"])
# after t=0 resolution: three delay choices pending (fires at 1,2,3);
# then 1 settles, 2 remain pending; then 2 settles; then 3 settles
assert r["reachable"] == {0: 3, 1: 3, 2: 2, 3: 1}, r["reachable"]

# ---------------------------------------------------------------- case 2
# Static-0 hazard: f = a AND NOT(a), NOT delayed 2, AND delayed 1.
# t=1 the AND spuriously goes 0->1 (unnecessary toggle) -> violation t=1.
p = {
    "inputs": ["a"],
    "initial": {"a": 0},
    "target": {"a": 1},
    "gates": {
        "g1": {"type": "NOT", "inputs": ["a"], "delay_min": 2, "delay_max": 2},
        "g2": {"type": "AND", "inputs": ["a", "g1"],
               "delay_min": 1, "delay_max": 1},
    },
    "monitors": ["g2"],
}
r, _ = audit(p)
assert r["hazard"] is not None, "expected static-0 hazard"
mi, vt, steps = r["hazard"]
assert vt == 1, vt
# the witness ends exactly at the first violation instant
flat = [(st.time, e[0], e[1]) for st in steps for e in st.batch]
assert flat == [(1, "g2", 1)], flat
print("case2 hazard at t =", vt, "witness trace:", flat)

# ---------------------------------------------------------------- case 3
# Chained inverters with equal delays: post-batch evaluation means g2 is
# scheduled only after g1 actually changes, so g2 toggles exactly once.
p = {
    "inputs": ["a"],
    "initial": {"a": 0},
    "target": {"a": 1},
    "gates": {
        "g1": {"type": "NOT", "inputs": ["a"], "delay_min": 1, "delay_max": 1},
        "g2": {"type": "NOT", "inputs": ["g1"], "delay_min": 1, "delay_max": 1},
    },
    "monitors": ["g2"],
}
r, _ = audit(p)
assert r["hazard"] is None, r["hazard"]
leaf = r["safe_leaf"][2]
flat = [(st.time, e[0], e[1]) for st in leaf if st.batch for e in st.batch]
assert flat == [(1, "g1", 0), (2, "g2", 1)], flat
print("case3 single necessary toggle; trace:", flat)

# ---------------------------------------------------------------- case 4
# Both gates draw delays from [1,2].  Four interleavings exist; the branch
# d(NOT)=1, d(AND)=2 must be *cancelled inertially* (the AND's pending
# 1-event is obsolete before firing), while other branches glitch.  The
# engine must still report a hazard (it explores all interleavings).
p = {
    "inputs": ["a"],
    "initial": {"a": 0},
    "target": {"a": 1},
    "gates": {
        "g1": {"type": "NOT", "inputs": ["a"], "delay_min": 1, "delay_max": 2},
        "g2": {"type": "AND", "inputs": ["a", "g1"],
               "delay_min": 1, "delay_max": 2},
    },
    "monitors": ["g2"],
}
r, _ = audit(p)
assert r["hazard"] is not None
mi, vt, steps = r["hazard"]
assert vt == 1
# canonical witness = combo (1,1): batch at t=1 contains both events
b1 = [(e[0], e[2]) for st in steps if st.time == 1 for e in st.batch]
assert b1 == [("g1", 1), ("g2", 1)], b1
print("case4 exhaustive interleavings; combos:", r["combos"],
      "registered states:", r["registered_states"])
assert r["combos"] >= 4

# ---------------------------------------------------------------- case 5
# Inertial stability: a OR b with b already 1 never changes when a rises.
p = {
    "inputs": ["a", "b"],
    "initial": {"a": 0, "b": 1},
    "target": {"a": 1, "b": 1},
    "gates": {
        "g1": {"type": "OR", "inputs": ["a", "b"],
               "delay_min": 1, "delay_max": 5},
    },
    "monitors": ["g1"],
}
r, spec = audit(p)
assert r["hazard"] is None
assert all(not st.batch for st in r["safe_leaf"][2])
print("case5 inertially stable, no events scheduled")

# ---------------------------------------------------------------- case 6
# Zero delays resolve in micro-batches all at t=0.
p = {
    "inputs": ["a"],
    "initial": {"a": 0},
    "target": {"a": 1},
    "gates": {
        "g1": {"type": "NOT", "inputs": ["a"], "delay_min": 0, "delay_max": 0},
        "g2": {"type": "OR", "inputs": ["a", "g1"],
               "delay_min": 0, "delay_max": 0},
    },
    "monitors": ["g2"],
}
r, _ = audit(p)
assert r["hazard"] is None
times = {st.time for st in r["safe_leaf"][2] if st.batch}
assert times == {0}, times
print("case6 zero-delay micro-batches resolved at t=0")

# ---------------------------------------------------------------- case 7
# Cycle detection localizes the offending loop.
p = {
    "inputs": ["a"],
    "initial": {"a": 0},
    "target": {"a": 1},
    "gates": {
        "g1": {"type": "AND", "inputs": ["a", "g3"],
               "delay_min": 1, "delay_max": 1},
        "g2": {"type": "NOT", "inputs": ["g1"],
               "delay_min": 1, "delay_max": 1},
        "g3": {"type": "BUF", "inputs": ["g2"],
               "delay_min": 1, "delay_max": 1},
    },
    "monitors": ["g1"],
}
spec, errors = validate_and_build(p)
assert spec is None and any("成环" in e and "g1" in e for e in errors), errors
print("case7 cycle localized:", errors)

# ---------------------------------------------------------------- case 8
# Invalid payload: bad bit, dangling wire, min > max all located.
bad = {
    "inputs": ["a"],
    "initial": {"a": 2},
    "target": {"a": 1},
    "gates": {"g1": {"type": "AND", "inputs": ["a", "zz"],
                     "delay_min": 3, "delay_max": 1}},
    "monitors": ["g1"],
}
spec, errors = validate_and_build(bad)
assert spec is None
joined = " | ".join(errors)
print("case8 errors:", joined)
assert "initial.a" in joined and "zz" in joined and "delay_min(3)" in joined

# ---------------------------------------------------------------- case 9
# Two reconvergent gates glitch at the same earliest time; the canonical
# witness is tie-broken by gate id (o1 before o2).
p = {
    "inputs": ["a"],
    "initial": {"a": 0},
    "target": {"a": 1},
    "gates": {
        "n2": {"type": "NOT", "inputs": ["a"], "delay_min": 2, "delay_max": 2},
        "n1": {"type": "NOT", "inputs": ["a"], "delay_min": 2, "delay_max": 2},
        "o2": {"type": "AND", "inputs": ["a", "n2"],
               "delay_min": 1, "delay_max": 1},
        "o1": {"type": "AND", "inputs": ["a", "n1"],
               "delay_min": 1, "delay_max": 1},
    },
    "monitors": ["o1", "o2"],
}
r, spec2 = audit(p)
mi, vt, steps = r["hazard"]
assert vt == 1, vt
assert spec2.monitors[mi].name == "o1", spec2.monitors[mi].name
print("case9 canonical witness monitor = o1 at t =", vt)

# -------------------------------------------------------------- case 10
# Static-1 hazard: f = (a AND b) OR (a AND NOT b), a=1 stays, b 1->0.
# f must remain 1 (necessary toggles = 0) but glitches to 0 at t=1.
p = {
    "inputs": ["a", "b"],
    "initial": {"a": 1, "b": 1},
    "target": {"a": 1, "b": 0},
    "gates": {
        "n":  {"type": "NOT", "inputs": ["b"],
               "delay_min": 2, "delay_max": 2},
        "g1": {"type": "AND", "inputs": ["a", "b"],
               "delay_min": 1, "delay_max": 1},
        "g2": {"type": "AND", "inputs": ["a", "n"],
               "delay_min": 2, "delay_max": 2},
        "f":  {"type": "OR", "inputs": ["g1", "g2"],
               "delay_min": 0, "delay_max": 0},
    },
    "monitors": ["f"],
}
r, spec2 = audit(p)
assert r["hazard"] is not None
mi, vt, steps = r["hazard"]
assert spec2.monitors[mi].name == "f"
assert vt == 1, vt
assert r["necessary"][mi] == 0, r["necessary"]
flat = [(st.time, e[0], e[1]) for st in steps for e in st.batch]
assert flat == [(1, "g1", 0), (1, "f", 0)], flat
print("case10 static-1 glitch on f at t =", vt, "(zero-delay micro-batch)")

print("\nALL ENGINE CHECKS PASSED")
