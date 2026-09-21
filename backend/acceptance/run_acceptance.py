#!/usr/bin/env python3
"""End-to-end acceptance checks for the audit service.

Runs entirely over HTTP against the deployed API, prints a line per check,
and exits 0 only when every check passes. Used by the ``verify`` compose
service; usable locally with API_BASE=http://localhost:8000.
"""
from __future__ import annotations

import os
import sys
import time

import httpx

BASE = os.environ.get("API_BASE", "http://localhost:8000").rstrip("/")
TIMEOUT = float(os.environ.get("ACCEPTANCE_TIMEOUT", "30"))

failures: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    mark = "PASS" if cond else "FAIL"
    print(f"[{mark}] {name}" + (f" -- {detail}" if detail and not cond else ""))
    if not cond:
        failures.append(name)


def wait_for_service() -> bool:
    deadline = time.time() + TIMEOUT
    last = ""
    while time.time() < deadline:
        try:
            r = httpx.get(f"{BASE}/health", timeout=2)
            if r.status_code == 200 and r.json().get("status") == "ok":
                return True
            last = f"status={r.status_code}"
        except Exception as exc:  # noqa: BLE001
            last = str(exc)
        time.sleep(0.5)
    print(f"service never became healthy: {last}", file=sys.stderr)
    return False


def gate(t: str, xs: list[str]) -> dict:
    return {"type": t, "inputs": xs}


def main() -> int:
    print(f"acceptance target: {BASE}")
    if not wait_for_service():
        return 2

    # 1. health ----------------------------------------------------------------
    check("health endpoint reports ok", True)

    # 2. classic reconvergent hazard, fixed delays -----------------------------
    hazard = {
        "initial": {"a": 1},
        "target": {"a": 0},
        "gates": {
            "g1": gate("NOT", ["a"]),
            "g2": gate("OR", ["a", "g1"]),
        },
        "delays": {"g1": {"min": 1, "max": 1},
                   "g2": {"min": 1, "max": 1}},
        "monitors": ["g2"],
    }
    r = httpx.post(f"{BASE}/api/audit", json=hazard, timeout=10)
    check("hazard case returns 200", r.status_code == 200, str(r.text))
    body = r.json()
    check("hazard case is unsafe", body.get("safe") is False)
    v = body.get("violation") or {}
    check("earliest violation at t=1", v.get("time") == 1, str(v))
    check("violation gate g2", v.get("gate") == "g2", str(v))
    tr = (body.get("witness") or {}).get("transitions") or []
    check("witness starts at t=0", bool(tr) and tr[0]["time"] == 0, str(tr))

    # 3. hazard only present for some integer delays in the range --------------
    hazard_range = {
        **hazard,
        "delays": {"g1": {"min": 1, "max": 3},
                   "g2": {"min": 2, "max": 2}},
    }
    r = httpx.post(f"{BASE}/api/audit", json=hazard_range, timeout=10)
    body = r.json()
    check("delay-range case is unsafe", r.status_code == 200 and
          body.get("safe") is False, str(body)[:300])
    check("delay-range earliest t=2",
          (body.get("violation") or {}).get("time") == 2)

    # 4. exhaustive counts: one buffer with d in [1,3] -> 3 futures at t0 -----
    safe1 = {
        "initial": {"a": 0},
        "target": {"a": 1},
        "gates": {"g1": gate("BUF", ["a"])},
        "delays": {"g1": {"min": 1, "max": 3}},
        "monitors": ["g1"],
    }
    r = httpx.post(f"{BASE}/api/audit", json=safe1, timeout=10)
    body = r.json()
    check("single buffer safe", r.status_code == 200 and body.get("safe"),
          str(body)[:300])
    tl = body.get("timeline") or []
    check("three integer delays enumerated (t0 states=3)",
          tl and tl[0]["reachable_states"] == 3, str(tl))
    check("futures merge after firing (last states=1)",
          tl and tl[-1]["reachable_states"] == 1, str(tl))

    # 5. two independent buffers d in {1,2}: exact layer counts 4 / 4 / 1 ------
    safe2 = {
        "initial": {"a": 0, "b": 0},
        "target": {"a": 1, "b": 1},
        "gates": {"g1": gate("BUF", ["a"]), "g2": gate("BUF", ["b"])},
        "delays": {"g1": {"min": 1, "max": 2},
                   "g2": {"min": 1, "max": 2}},
        "monitors": ["g1", "g2"],
    }
    r = httpx.post(f"{BASE}/api/audit", json=safe2, timeout=10)
    body = r.json()
    counts = {l["time"]: l["reachable_states"] for l in
              (body.get("timeline") or [])}
    check("two-buffer exact counts {0:4,1:4,2:1}",
          counts == {0: 4, 1: 4, 2: 1}, str(counts))

    # 6. cyclic netlist rejected with 422 + location ---------------------------
    cyc = {
        "initial": {"a": 0},
        "target": {"a": 1},
        "gates": {
            "g1": gate("AND", ["a", "g2"]),
            "g2": gate("OR", ["g1"]),
        },
        "delays": {"g1": {"min": 1, "max": 1},
                   "g2": {"min": 1, "max": 1}},
        "monitors": ["g1"],
    }
    r = httpx.post(f"{BASE}/api/audit", json=cyc, timeout=10)
    err = r.json() if r.headers.get("content-type", "").startswith(
        "application/json") else {}
    check("cycle rejected 422", r.status_code == 422, str(r.status_code))
    check("cycle code cyclic", err.get("code") == "cyclic", str(err))
    check("cycle location names the loop",
          "g1" in (err.get("location") or "") and
          "g2" in (err.get("location") or ""), str(err))

    # 7. unknown net located precisely -----------------------------------------
    bad_net = {
        "initial": {"a": 0},
        "target": {"a": 1},
        "gates": {"g1": gate("BUF", ["x"])},
        "delays": {"g1": {"min": 1, "max": 1}},
        "monitors": ["g1"],
    }
    r = httpx.post(f"{BASE}/api/audit", json=bad_net, timeout=10)
    err = r.json()
    check("unknown net 422", r.status_code == 422)
    check("unknown net code+location",
          err.get("code") == "unknown_net" and
          err.get("location") == "g1.inputs[0]", str(err))

    # 8. input set mismatch ----------------------------------------------------
    mismatch = {
        "initial": {"a": 0},
        "target": {"b": 1},
        "gates": {},
        "delays": {},
        "monitors": [],
    }
    r = httpx.post(f"{BASE}/api/audit", json=mismatch, timeout=10)
    err = r.json()
    check("input mismatch 422", r.status_code == 422 and
          err.get("code") == "input_mismatch", str(err))

    # 9. malformed JSON shape uses same error envelope -------------------------
    r = httpx.post(f"{BASE}/api/audit", json={"initial": {"a": 0}},
                   timeout=10)
    err = r.json()
    check("schema error envelope", r.status_code == 422 and
          err.get("code") == "schema_invalid" and "error" in err, str(err))

    # 10. zero-delay same-time micro batches drain at t=0 ----------------------
    zero = {
        "initial": {"a": 0},
        "target": {"a": 1},
        "gates": {"g1": gate("NOT", ["a"]), "g2": gate("NOT", ["g1"])},
        "delays": {"g1": {"min": 0, "max": 0},
                   "g2": {"min": 0, "max": 0}},
        "monitors": ["g2"],
    }
    r = httpx.post(f"{BASE}/api/audit", json=zero, timeout=10)
    body = r.json()
    times = [l["time"] for l in (body.get("timeline") or [])]
    check("zero-delay safe and single t=0 layer",
          body.get("safe") and times == [0], str(body)[:300])

    print()
    if failures:
        print(f"ACCEPTANCE FAILED: {len(failures)} check(s): "
              + ", ".join(failures))
        return 1
    print("ACCEPTANCE PASSED: all checks succeeded")
    return 0


if __name__ == "__main__":
    sys.exit(main())
