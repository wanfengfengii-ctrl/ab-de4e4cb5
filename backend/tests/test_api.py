"""HTTP-level tests: success, error shape and health probe."""
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def payload(**overrides):
    base = {
        "initial": {"a": 1},
        "target": {"a": 0},
        "gates": {
            "g1": {"type": "NOT", "inputs": ["a"]},
            "g2": {"type": "OR", "inputs": ["a", "g1"]},
        },
        "delays": {"g1": {"min": 1, "max": 1},
                   "g2": {"min": 1, "max": 1}},
        "monitors": ["g2"],
    }
    base.update(overrides)
    return base


def test_health():
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_audit_finds_hazard():
    r = client.post("/api/audit", json=payload())
    assert r.status_code == 200
    body = r.json()
    assert body["safe"] is False
    assert body["violation"] == {"time": 1, "gate": "g2", "monitor": "g2"}
    assert body["witness"]["transitions"][0]["time"] == 0


def test_audit_safe_shape():
    safe = payload(
        initial={"a": 0}, target={"a": 1},
        gates={"g1": {"type": "BUF", "inputs": ["a"]}},
        delays={"g1": {"min": 1, "max": 2}},
        monitors=["g1"],
    )
    r = client.post("/api/audit", json=safe)
    assert r.status_code == 200
    body = r.json()
    assert body["safe"] is True
    assert body["violation"] is None
    assert body["timeline"][0]["reachable_states"] == 2
    assert body["initial_state"] == {"a": 0, "g1": 0}


def test_cycle_returns_422_with_location():
    cyc = payload(
        gates={"g1": {"type": "AND", "inputs": ["a", "g2"]},
               "g2": {"type": "OR", "inputs": ["g1"]}},
        delays={"g1": {"min": 1, "max": 1},
                "g2": {"min": 1, "max": 1}},
        monitors=["g1"],
    )
    r = client.post("/api/audit", json=cyc)
    assert r.status_code == 422
    body = r.json()
    assert body["code"] == "cyclic"
    assert "g1" in body["location"]


def test_unknown_net_error_points_to_cause():
    bad = payload(
        gates={"g1": {"type": "BUF", "inputs": ["x"]}},
        delays={"g1": {"min": 1, "max": 1}},
        monitors=["g1"],
    )
    r = client.post("/api/audit", json=bad)
    assert r.status_code == 422
    body = r.json()
    assert body["code"] == "unknown_net"
    assert body["location"] == "g1.inputs[0]"
    assert body["error"]


def test_schema_error_uses_same_envelope():
    r = client.post("/api/audit", json={"initial": {"a": 0}})
    assert r.status_code == 422
    body = r.json()
    assert body["code"] == "schema_invalid"
    assert "location" in body
