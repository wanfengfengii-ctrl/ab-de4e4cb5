"""FastAPI application: static frontend + /api/audit exhaustive audit."""
from __future__ import annotations

import os

from fastapi import FastAPI, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .engine import Engine
from .models import AuditResponse
from .validator import validate_and_build

app = FastAPI(title="异步联锁板冒险审计台", version="1.0.0")


def _steps_to_dto(steps):
    out = []
    for st in steps:
        out.append({
            "time": st.time,
            "note": st.note,
            "batch": [
                {"gate": g, "value": v, "delay": d}
                for (g, v, d) in sorted(st.batch, key=lambda e: (e[0], e[2]))
            ],
            "outputs": [
                {"monitor": m, "value": v} for (m, v) in st.outputs
            ],
        })
    return out


def _process(payload):
    spec, errors = validate_and_build(payload)
    if errors or spec is None:
        # Invalid input or cyclic netlist: the client clears its old result
        # when it receives valid=False with the located reasons.
        return {"valid": False, "errors": errors, "result": None}

    engine = Engine(spec)
    r = engine.run()

    if r["overflow"]:
        return JSONResponse(
            status_code=422,
            content={
                "valid": False,
                "errors": [
                    f"可达状态数超过审计上限 ({engine.max_states})，"
                    "为保证穷举完整性拒绝给出结论。请缩小延迟区间或网表规模。"
                ],
                "result": None,
            },
        )

    return _build_result(spec, engine, r)


@app.post("/api/audit", response_model=AuditResponse)
async def audit(request: Request):
    try:
        payload = await request.json()
    except Exception:
        return JSONResponse(
            {"valid": False, "errors": ["请求体不是合法 JSON"], "result": None}
        )
    return await run_in_threadpool(_process, payload)


def _build_result(spec, engine, r):
    mon_names = [m.name for m in spec.monitors]
    toggle_counts: list = []
    witnesses: list = []
    earliest = None
    safe = True
    if r["hazard"] is None:
        worst = {}
        for _key, node in r["frozen"].items():
            for mi, name in enumerate(mon_names):
                if node.toggles[mi] > worst.get(name, (-1,))[0]:
                    mg = engine._mon_gate[mi]
                    end = (
                        spec.target_bits[spec.monitors[mi].idx]
                        if spec.monitors[mi].kind == 0
                        else node.gv[mg]
                    )
                    worst[name] = (node.toggles[mi], end)
        for mi, name in enumerate(mon_names):
            tg, end = worst.get(name, (0, r["mon_final"][mi]))
            toggle_counts.append({
                "monitor": name,
                "toggles": tg,
                "necessary": r["necessary"][mi],
                "end_value": end,
            })
        witnesses = []
        earliest = None
        safe = True
        timeline = _steps_to_dto(r["safe_leaf"][2])
    else:
        best_mi, earliest_t, full_steps = r["hazard"]
        mon_name = mon_names[best_mi]
        # actual monitor value at the first violating instant
        end_value = r["mon_init"][best_mi]
        for st in full_steps:
            for mname, mval in st.outputs:
                if mname == mon_name:
                    end_value = mval
        wtog = sum(
            1
            for st in full_steps
            for (g, _v, _d) in st.batch
            if g == mon_name
        )
        witness = {
            "monitor": mon_name,
            "first_violation_time": earliest_t,
            "end_value": end_value,
            "toggles": wtog,
            "necessary": r["necessary"][best_mi],
            "steps": _steps_to_dto(full_steps),
        }
        witnesses = [witness]
        toggle_counts = []
        safe = False
        earliest = earliest_t
        timeline = witness["steps"]

    horizon = max(
        (int(k) for k in r["reachable"].keys()), default=0
    )

    result = {
        "safe": safe,
        "horizon": horizon,
        "earliest_violation_time": earliest,
        "witnesses": witnesses,
        "toggle_counts": toggle_counts,
        "reachable_states": {str(k): v for k, v in r["reachable"].items()},
        "state_count_total": r["registered_states"],
        "explored_schedules": r["combos"],
        "timeline": timeline,
    }
    return {"valid": True, "errors": [], "result": result}


@app.get("/health")
async def health():
    return {"status": "ok"}


# ---- bundled frontend ------------------------------------------------------
_DIST = os.path.join(os.path.dirname(__file__), "..", "static")
if os.path.isdir(_DIST):
    app.mount(
        "/assets",
        StaticFiles(directory=os.path.join(_DIST, "assets")),
        name="assets",
    )

    @app.get("/")
    async def index():
        return FileResponse(os.path.join(_DIST, "index.html"))

    @app.get("/{full_path:path}")
    async def spa_fallback(full_path: str):
        candidate = os.path.join(_DIST, full_path)
        if full_path and os.path.isfile(candidate):
            return FileResponse(candidate)
        return FileResponse(os.path.join(_DIST, "index.html"))
