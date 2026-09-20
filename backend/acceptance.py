"""Acceptance suite for the one-shot `verify` compose service.

Talks to the running API over real HTTP (BASE_URL env, default
http://web:8000) and exits non-zero if any acceptance criterion fails.
Exit code == number of failed checks (capped at 100).
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request

BASE = os.environ.get("BASE_URL", "http://web:8000").rstrip("/")
failures: list[str] = []
passed = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global passed
    if cond:
        passed += 1
        print(f"  PASS  {name}")
    else:
        failures.append(f"{name} {detail}".strip())
        print(f"  FAIL  {name} {detail}")


def post(path: str, payload: dict) -> tuple[int, dict]:
    req = urllib.request.Request(
        BASE + path,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode())


def get(path: str) -> tuple[int, bytes]:
    with urllib.request.urlopen(BASE + path, timeout=30) as resp:
        return resp.status, resp.read()


def wait_for_health(timeout: float = 60.0) -> bool:
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        try:
            status, body = get("/health")
            if status == 200 and json.loads(body)["status"] == "ok":
                return True
        except Exception as e:  # noqa: BLE001
            last = e
        time.sleep(1.0)
    print("health wait failed:", last)
    return False


def audit(payload):
    return post("/api/audit", payload)


HAZARD = {
    "inputs": ["a"],
    "initial": {"a": 0},
    "target": {"a": 1},
    "gates": {
        "g1": {"type": "NOT", "inputs": ["a"], "delay_min": 1, "delay_max": 2},
        "g2": {"type": "AND", "inputs": ["a", "g1"],
               "delay_min": 1, "delay_max": 1},
    },
    "monitors": ["g2"],
}

SAFE = {
    "inputs": ["a"],
    "initial": {"a": 0},
    "target": {"a": 1},
    "gates": {
        "g1": {"type": "NOT", "inputs": ["a"], "delay_min": 1, "delay_max": 3},
    },
    "monitors": ["g1"],
}

BATCHED = {
    "inputs": ["a"],
    "initial": {"a": 0},
    "target": {"a": 1},
    "gates": {
        "g1": {"type": "NOT", "inputs": ["a"], "delay_min": 1, "delay_max": 1},
        "g2": {"type": "NOT", "inputs": ["g1"], "delay_min": 1, "delay_max": 1},
    },
    "monitors": ["g2"],
}

INERTIAL = {
    "inputs": ["a", "b"],
    "initial": {"a": 0, "b": 1},
    "target": {"a": 1, "b": 1},
    "gates": {
        "g1": {"type": "OR", "inputs": ["a", "b"],
               "delay_min": 1, "delay_max": 5},
    },
    "monitors": ["g1"],
}

CYCLIC = {
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

INVALID = {
    "inputs": ["a"],
    "initial": {"a": 2},
    "target": {"a": 1},
    "gates": {"g1": {"type": "AND", "inputs": ["a", "zz"],
                     "delay_min": 3, "delay_max": 1}},
    "monitors": ["g1"],
}


def main() -> int:
    print(f"acceptance target: {BASE}")
    if not wait_for_health():
        print("API never became healthy")
        return 2
    print("\n[1] health & static frontend")
    check("GET /health -> ok", True)
    status, html = get("/")
    check("GET / serves built frontend", status == 200 and b'root' in html)

    print("\n[2] exhaustive safe audit")
    code, data = audit(SAFE)
    check("HTTP 200", code == 200, str(code))
    check("valid envelope", data.get("valid") is True)
    r = data.get("result") or {}
    check("marked safe", r.get("safe") is True)
    reach = r.get("reachable_states", {})
    check("per-time reachable state counts present",
          all(reach.get(str(t)) for t in (0, 1, 2, 3)), str(reach))
    # delays 1,2,3: three pending branches after t=0
    check("explored every integer delay (3 branches)",
          reach.get("0") == 3, str(reach))
    check("monitor has at most the one necessary toggle",
          (r.get("toggle_counts") or [{}])[0].get("toggles") == 1)
    check("playback timeline present", len(r.get("timeline", [])) >= 2)

    print("\n[3] same-time batching semantics (chained inverters)")
    code, data = audit(BATCHED)
    check("safe (post-batch eval avoids phantom events)",
          data.get("valid") and data["result"]["safe"] is True,
          json.dumps(data, ensure_ascii=False))
    flat = [(s["time"], e["gate"])
            for s in data["result"]["timeline"] for e in s["batch"]]
    check("g1 toggles at t=1 then g2 exactly once at t=2",
          flat == [(1, "g1"), (2, "g2")], str(flat))

    print("\n[4] inertial delay cancellation / stability")
    code, data = audit(INERTIAL)
    check("valid", data.get("valid") is True)
    check("OR stays stable; no gate events scheduled",
          data["result"]["safe"] is True
          and all(not s["batch"]
                  for s in data["result"]["timeline"][2:]))

    print("\n[5] exhaustive interleaving finds a hazard")
    code, data = audit(HAZARD)
    check("HTTP 200", code == 200)
    check("valid envelope", data.get("valid") is True)
    r = data["result"]
    check("hazard reported", r.get("safe") is False)
    check("earliest violation time == 1",
          r.get("earliest_violation_time") == 1,
          str(r.get("earliest_violation_time")))
    w = (r.get("witnesses") or [{}])[0]
    check("witness names offending monitor g2", w.get("monitor") == "g2")
    check("witness reports toggle count beyond necessary",
          w.get("toggles", 0) >= 1 and w.get("toggles") > w.get("necessary", 0))
    first_bad = [s for s in w.get("steps", [])
                 if s["time"] == 1 and s["batch"]]
    check("canonical witness shows batch+chosen delays at t=1",
          any(e["gate"] == "g2" and e["delay"] == 1
              for s in first_bad for e in s["batch"]),
          json.dumps(first_bad, ensure_ascii=False))
    check("hazard timeline is replayable", len(r.get("timeline", [])) >= 2)

    print("\n[6] invalid input: no result, located reasons, old cleared")
    code, data = audit(INVALID)
    check("HTTP 200 envelope (not opaque 422)", code == 200)
    check("valid=false", data.get("valid") is False)
    check("result cleared (null)", data.get("result") is None)
    joined = " | ".join(data.get("errors", []))
    check("bad bit located at initial.a", "initial.a" in joined, joined)
    check("dangling wire located", "zz" in joined, joined)
    check("inverted delay interval located", "delay_min(3)" in joined, joined)

    print("\n[7] cyclic netlist rejected with cycle path")
    code, data = audit(CYCLIC)
    check("valid=false", data.get("valid") is False)
    joined = " | ".join(data.get("errors", []))
    check("cycle reported in Chinese with localized path",
          "成环" in joined and "g1" in joined and "g3" in joined, joined)
    check("no result on cycle", data.get("result") is None)

    print("\n[8] malformed JSON body handled gracefully")
    req = urllib.request.Request(
        BASE + "/api/audit",
        data=b"{not json",
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            body = json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        body = json.loads(e.read().decode())
    check("malformed body -> valid=false envelope",
          body.get("valid") is False and body.get("errors"))

    print(f"\n==== {passed} passed, {len(failures)} failed ====")
    if failures:
        for f in failures:
            print(" -", f)
        return min(100, len(failures))
    print("ACCEPTANCE OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
