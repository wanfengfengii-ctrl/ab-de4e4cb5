"""Exact hazard audit engine.

Semantics
---------
* At time 0 all primary inputs switch simultaneously from ``initial`` to
  ``target``.
* Every gate has an *inertial* integer delay in ``[min, max]``.  When one of
  its input nets changes (evaluation happens only after the whole
  simultaneous batch is visible), the gate is re-evaluated exactly once:
    - output unchanged -> its pending (not-yet-fired) event is cancelled;
    - output changes   -> the pending event is replaced by an event at
      ``t + d`` for *every* integer ``d in [min, max]`` (each delay is a
      distinct execution).
* Events sharing a time stamp fire as one batch and only then does
  evaluation run.  Zero-delay follow-ups form further batches at the same
  time, drained before time advances.
* The engine enumerates **every** delay choice (and the event interleavings
  they induce).  Identical futures are merged, so the reported number is the
  count of *distinct reachable states* per time -- no sampling, no fixed
  delay, no continuous-time approximation.
* A monitored output must complete exactly its *necessary* transition (one
  when its steady value changes, zero otherwise) on every execution.  The
  first extra transition is a hazard.
"""
from __future__ import annotations

import itertools
from dataclasses import dataclass, field
from typing import Dict, FrozenSet, List, Optional, Sequence, Tuple

from .models import AuditRequest

# One pending inertial event: (gate index, fire time, new value, scheduled at)
PendingEvent = Tuple[int, int, int, int]
# A concrete future: gate values + pending events + monitor transition counts
StateId = Tuple[Tuple[int, ...], Tuple[PendingEvent, ...], Tuple[int, ...]]


class AuditLimit(RuntimeError):
    """Exhaustive exploration exceeded the configured state cap."""


class NetlistError(ValueError):
    """Validation failure carrying a machine readable code and location."""

    def __init__(self, message: str, code: str = "invalid", location: Optional[str] = None):
        super().__init__(message)
        self.message = message
        self.code = code
        self.location = location


@dataclass(frozen=True)
class BatchEvent:
    kind: str  # "input" | "gate"
    gate: str
    from_value: int
    to_value: int
    delay: Optional[int] = None  # gate events only


@dataclass(frozen=True)
class Batch:
    time: int
    events: Tuple[BatchEvent, ...]
    values: Tuple[int, ...]            # gate values after the batch
    active: Tuple[PendingEvent, ...]  # pending events after the batch


@dataclass
class Node:
    parent: Optional["Node"]
    batches: List[Batch]  # batches leading from the parent rest state here
    key: Tuple            # canonical, lexicographically ordered witness key
    time: int


def _eval_gate(kind: str, xs: Sequence[int]) -> int:
    if kind == "AND":
        return int(all(xs))
    if kind == "OR":
        return int(any(xs))
    if kind == "NAND":
        return int(not all(xs))
    if kind == "NOR":
        return int(not any(xs))
    if kind == "XOR":
        return int(sum(xs) % 2 == 1)
    if kind == "XNOR":
        return int(sum(xs) % 2 == 0)
    if kind == "NOT":
        return 1 - xs[0]
    if kind == "BUF":
        return xs[0]
    raise NetlistError(f"未知门类型 {kind!r}", "unknown_gate_type")


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def validate(req: AuditRequest) -> AuditRequest:
    req = req.normalized()

    if set(req.initial) != set(req.target):
        diff = sorted(set(req.initial) ^ set(req.target))
        raise NetlistError(
            "initial 与 target 必须声明完全相同的输入集合",
            "input_mismatch", ", ".join(diff),
        )
    if not req.initial:
        raise NetlistError("至少需要一个主输入", "empty_inputs")
    if not req.monitors:
        raise NetlistError("至少需要一个监测输出", "empty_monitors")

    gate_ids = set(req.gates)
    primary = set(req.initial)
    overlap = sorted(primary & gate_ids)
    if overlap:
        raise NetlistError(
            "名称同时被声明为主输入和门输出", "name_collision", ", ".join(overlap)
        )

    known = primary | gate_ids
    fanin: Dict[str, List[str]] = {}
    for gid, gate in req.gates.items():
        if gate.type in ("NOT", "BUF"):
            if len(gate.inputs) != 1:
                raise NetlistError(
                    f"门 {gid} ({gate.type}) 必须恰好有 1 个输入，当前 "
                    f"{len(gate.inputs)} 个", "bad_fanin", gid,
                )
        elif not gate.inputs:
            raise NetlistError(
                f"门 {gid} ({gate.type}) 至少需要 1 个输入", "bad_fanin", gid,
            )
        for i, net in enumerate(gate.inputs):
            if net not in known:
                raise NetlistError(
                    f"门 {gid} 的第 {i + 1} 个输入 {net!r} 未定义（既不是输入"
                    "也不是门输出）", "unknown_net", f"{gid}.inputs[{i}]",
                )
        fanin[gid] = list(gate.inputs)

    for gid in req.gates:
        if gid not in req.delays:
            raise NetlistError(f"门 {gid} 缺少延迟区间", "missing_delay", gid)
    extra = sorted(set(req.delays) - gate_ids)
    if extra:
        raise NetlistError(
            "为不存在的门声明了延迟", "extra_delay", ", ".join(extra)
        )
    for gid, d in req.delays.items():
        if d.min > d.max:
            raise NetlistError(
                f"门 {gid} 的延迟下界 {d.min} 大于上界 {d.max}",
                "bad_delay", gid,
            )

    for m in dict.fromkeys(req.monitors):
        if m not in gate_ids:
            raise NetlistError(
                f"监测点 {m!r} 必须是某个门的输出", "unknown_monitor", m
            )

    _detect_cycle(fanin)
    return req


def _detect_cycle(fanin: Dict[str, List[str]]) -> None:
    WHITE, GRAY, BLACK = 0, 1, 2
    color = {g: WHITE for g in fanin}
    stack: List[str] = []

    def dfs(u: str) -> None:
        color[u] = GRAY
        stack.append(u)
        for v in fanin[u]:
            if v not in color:  # primary input
                continue
            if color[v] == GRAY:
                start = stack.index(v)
                cyc = stack[start:] + [v]
                raise NetlistError(
                    "网表存在环: " + " -> ".join(cyc),
                    "cyclic", " -> ".join(cyc),
                )
            if color[v] == WHITE:
                dfs(v)
        stack.pop()
        color[u] = BLACK

    for g in sorted(fanin):
        if color[g] == WHITE:
            dfs(g)


# ---------------------------------------------------------------------------
# Exploration
# ---------------------------------------------------------------------------

@dataclass
class _Deposited:
    parent: Node
    key: Tuple
    batches: List[Batch] = field(default_factory=list)


def _product_size(lists: Sequence[Sequence[int]]) -> int:
    n = 1
    for lst in lists:
        n *= len(lst)
    return n


def audit(req: AuditRequest, state_cap: int = 2_000_000) -> dict:
    req = validate(req)

    gate_ids = sorted(req.gates)
    gidx = {g: i for i, g in enumerate(gate_ids)}
    G = len(gate_ids)
    gates = [req.gates[g] for g in gate_ids]
    delay_ranges = [(req.delays[g].min, req.delays[g].max) for g in gate_ids]
    monitor_names = list(dict.fromkeys(req.monitors))
    monitors = [gidx[m] for m in monitor_names]
    monitor_pos = {gi: k for k, gi in enumerate(monitors)}
    M = len(monitors)

    def net_value(net: str, vals: Sequence[int], inputs: Dict[str, int]) -> int:
        return vals[gidx[net]] if net in gidx else inputs[net]

    def eval_gate(i: int, vals: Sequence[int], inputs: Dict[str, int]) -> int:
        xs = [net_value(n, vals, inputs) for n in gates[i].inputs]
        return _eval_gate(gates[i].type, xs)

    fanin = {g: gates[gidx[g]].inputs for g in gate_ids}
    topo = [gidx[g] for g in _topo_order(gate_ids, fanin)]
    downstream: List[FrozenSet[int]] = [frozenset() for _ in range(G)]
    for i, gate in enumerate(gates):
        for net in gate.inputs:
            if net in gidx:
                j = gidx[net]
                downstream[j] = downstream[j] | {i}

    def settled(inputs: Dict[str, int]) -> Tuple[int, ...]:
        vals = [0] * G
        for i in topo:
            vals[i] = eval_gate(i, vals, inputs)
        return tuple(vals)

    initial_vals = settled(req.initial)
    final_vals = settled(req.target)
    needed = tuple(
        int(initial_vals[gi] != final_vals[gi]) for gi in monitors
    )

    def batch_key(time: int, events: Sequence[BatchEvent]) -> Tuple:
        return (
            time,
            tuple(sorted(
                (gidx[e.gate],
                 -1 if e.delay is None else e.delay,
                 e.from_value, e.to_value)
                for e in events if e.kind == "gate"
            )),
            tuple(sorted(
                (e.gate, e.from_value, e.to_value)
                for e in events if e.kind == "input"
            )),
        )

    violations: List[Tuple[int, int, Tuple, Node, List[Batch]]] = []

    def drain(
        vals: Tuple[int, ...],
        pending: Dict[int, PendingEvent],
        time: int,
        firing: List[PendingEvent],
        counters: Tuple[int, ...],
        parent: Node,
        key: Tuple,
        batches: List[Batch],
        out: Dict[StateId, _Deposited],
    ) -> None:
        """Apply all events due at ``time`` as one batch, then zero-delay."""
        vals_l = list(vals)
        events: List[BatchEvent] = []
        changed: List[int] = []
        counters_l = list(counters)
        for gi, _ft, nv, trig in sorted(firing, key=lambda e: e[0]):
            pending.pop(gi, None)
            old = vals_l[gi]
            if old == nv:
                continue  # was superseded before it could fire
            events.append(BatchEvent(
                "gate", gate_ids[gi], old, nv, delay=_ft - trig
            ))
            vals_l[gi] = nv
            changed.append(gi)
            if gi in monitor_pos:
                counters_l[monitor_pos[gi]] += 1

        vals2 = tuple(vals_l)
        counters2 = tuple(counters_l)
        key2 = key + ((batch_key(time, events),) if events else ())

        def record(pending_map: Dict[int, PendingEvent]) -> List[Batch]:
            """Attach this delay choice's pending set to the visible batch."""
            if not events:
                return batches
            snap = tuple(sorted(
                pending_map.values(), key=lambda e: (e[1], e[0])
            ))
            return batches + [Batch(time, tuple(events), vals2, snap)]

        violated: List[int] = []
        for ev in events:
            gi = gidx[ev.gate]
            if gi in monitor_pos:
                k = monitor_pos[gi]
                if counters2[k] > needed[k]:
                    violated.append(gi)

        def note_violation(chosen_batches: List[Batch], combo_key: Tuple) -> None:
            for gi in violated:
                violations.append(
                    (time, gi, key2 + combo_key, parent, chosen_batches)
                )

        affected = sorted({d for ch in changed for d in downstream[ch]})
        choices: List[Tuple[int, List[int], int]] = []
        for gi in affected:
            nv = eval_gate(gi, vals2, req.target)
            if nv == vals2[gi]:
                pending.pop(gi, None)  # inertial cancellation
            else:
                lo, hi = delay_ranges[gi]
                choices.append((gi, list(range(lo, hi + 1)), nv))

        if not choices:
            pend_t = tuple(sorted(pending.values(), key=lambda e: (e[1], e[0])))
            ident = (vals2, pend_t, counters2)
            chosen_batches = record(pending)
            note_violation(chosen_batches, ())
            dep = out.get(ident)
            if dep is None or key2 < dep.key:
                out[ident] = _Deposited(parent, key2, chosen_batches)
            return

        if _product_size([c[1] for c in choices]) > state_cap:
            raise AuditLimit("状态数超过探索上限")

        gis = [c[0] for c in choices]
        for combo in itertools.product(*[c[1] for c in choices]):
            p2 = dict(pending)
            for gi, d, nv in zip(gis, combo, [c[2] for c in choices]):
                p2[gi] = (gi, time + d, nv, time)
            combo_batches = record(p2)
            combo_key = (("sched", tuple(sorted(zip(gis, combo)))),)
            note_violation(combo_batches, combo_key)
            zero = sorted(
                (e for e in p2.values() if e[1] == time),
                key=lambda e: e[0],
            )
            if zero:
                drain(vals2, p2, time, zero, counters2, parent, key2,
                      combo_batches, out)
            else:
                pend_t = tuple(sorted(p2.values(), key=lambda e: (e[1], e[0])))
                ident = (vals2, pend_t, counters2)
                dep = out.get(ident)
                if dep is None or key2 < dep.key:
                    out[ident] = _Deposited(parent, key2, combo_batches)

    # ---- time 0: simultaneous primary-input switch ---------------------
    input_events = tuple(
        BatchEvent("input", name, req.initial[name], req.target[name])
        for name in sorted(req.initial)
        if req.initial[name] != req.target[name]
    )
    zero_counters = tuple(0 for _ in range(M))
    root = Node(parent=None, batches=[], key=(), time=0)

    seed: Dict[StateId, _Deposited] = {}
    seed_key = ((batch_key(0, list(input_events)),) if input_events else ())

    def seed_record(pending_map: Dict[int, PendingEvent]) -> List[Batch]:
        if not input_events:
            return []
        snap = tuple(sorted(
            pending_map.values(), key=lambda e: (e[1], e[0])
        ))
        return [Batch(0, input_events, initial_vals, snap)]

    changed_pi = {e.gate for e in input_events}
    directly = sorted({
        gi for gi, gate in enumerate(gates)
        if changed_pi & set(gate.inputs)
    })
    seed_choices: List[Tuple[int, List[int], int]] = []
    pending0: Dict[int, PendingEvent] = {}
    for gi in directly:
        nv = eval_gate(gi, initial_vals, req.target)
        if nv != initial_vals[gi]:
            lo, hi = delay_ranges[gi]
            seed_choices.append((gi, list(range(lo, hi + 1)), nv))

    if not seed_choices:
        ident = (initial_vals, (), zero_counters)
        seed[ident] = _Deposited(root, seed_key, seed_record(pending0))
    else:
        if _product_size([c[1] for c in seed_choices]) > state_cap:
            raise AuditLimit("状态数超过探索上限")
        gis = [c[0] for c in seed_choices]
        for combo in itertools.product(*[c[1] for c in seed_choices]):
            p2: Dict[int, PendingEvent] = dict(pending0)
            for gi, d, nv in zip(gis, combo, [c[2] for c in seed_choices]):
                p2[gi] = (gi, d, nv, 0)
            combo_batches = seed_record(p2)
            zero = sorted(
                (e for e in p2.values() if e[1] == 0), key=lambda e: e[0]
            )
            if zero:
                drain(initial_vals, p2, 0, zero, zero_counters, root,
                      seed_key, combo_batches, seed)
            else:
                pend_t = tuple(sorted(p2.values(), key=lambda e: (e[1], e[0])))
                ident = (initial_vals, pend_t, zero_counters)
                dep = seed.get(ident)
                if dep is None or seed_key < dep.key:
                    seed[ident] = _Deposited(root, seed_key, combo_batches)

    # ---- BFS over event times ------------------------------------------
    # A layer for time t holds every *distinct* configuration reachable once
    # the t-batch (and any same-time zero-delay follow-ups) has settled:
    # configurations whose events fire later and configurations already at
    # rest are both carried, then merged by identity.
    nodes: Dict[StateId, Node] = {}
    frontier: Dict[StateId, Node] = {}
    timeline: List[dict] = []
    total_states = 0

    def admit(layer: Dict[StateId, _Deposited], time: int) -> Dict[StateId, Node]:
        nonlocal total_states
        admitted: Dict[StateId, Node] = {}
        for ident, dep in layer.items():
            old = nodes.get(ident)
            if old is None or dep.key < old.key:
                node = Node(
                    parent=dep.parent, batches=dep.batches, key=dep.key,
                    time=time,
                )
                nodes[ident] = node
                admitted[ident] = node
            else:
                admitted[ident] = old
        if len(layer) > state_cap:
            raise AuditLimit("状态数超过探索上限")
        total_states += len(layer)
        timeline.append({
            "time": time,
            "reachable_states": len(layer),
        })
        return admitted

    frontier = admit(seed, 0)

    while frontier:
        with_events = {i: n for i, n in frontier.items() if i[1]}
        if not with_events or violations:
            break
        next_time = min(e[1] for i in with_events for e in i[1])
        layer_states: Dict[StateId, _Deposited] = {}
        # carry every configuration that does not participate at next_time
        for ident, node in frontier.items():
            vals, pend, counters = ident
            if not any(e[1] == next_time for e in pend):
                dep = _Deposited(node.parent, node.key, node.batches)
                layer_states[ident] = dep
                continue
            firing = [e for e in pend if e[1] == next_time]
            kept = {e[0]: e for e in pend if e[1] != next_time}
            drain(vals, kept, next_time, firing, counters, node,
                  node.key, [], layer_states)
        # canonicalize merges on the same identity
        canonical: Dict[StateId, _Deposited] = {}
        for ident, dep in layer_states.items():
            cur = canonical.get(ident)
            if cur is None or dep.key < cur.key:
                canonical[ident] = dep
        frontier = admit(canonical, next_time)

    # ---- result ---------------------------------------------------------
    initial_state = {
        **{name: req.initial[name] for name in sorted(req.initial)},
        **{g: initial_vals[gidx[g]] for g in gate_ids},
    }
    if violations:
        time, gi, vkey, parent, vbatches = min(
            violations, key=lambda v: (v[0], v[1], v[2])
        )
        witness = _witness(parent, vbatches, gate_ids, gidx, req,
                           initial_vals)
        return {
            "safe": False,
            "horizon": time,
            "timeline": timeline,
            "total_states": total_states,
            "initial_state": initial_state,
            "target_inputs": dict(sorted(req.target.items())),
            "witness": witness,
            "violation": {
                "time": time,
                "gate": gate_ids[gi],
                "monitor": monitor_names[monitor_pos[gi]],
            },
        }

    terminal = min(frontier.values(), key=lambda n: n.key)
    witness = _witness(terminal, [], gate_ids, gidx, req, initial_vals)
    horizon = max((t["time"] for t in timeline), default=0)
    return {
        "safe": True,
        "horizon": horizon,
        "timeline": timeline,
        "total_states": total_states,
        "initial_state": initial_state,
        "target_inputs": dict(sorted(req.target.items())),
        "witness": witness,
        "violation": None,
    }


def _witness(
    node: Optional[Node],
    tail: List[Batch],
    gate_ids: List[str],
    gidx: Dict[str, int],
    req: AuditRequest,
    initial_vals: Tuple[int, ...],
) -> dict:
    """Flatten the parent chain into an ordered, replayable batch list."""
    pieces: List[List[Batch]] = [list(tail)]
    cur = node
    while cur is not None:
        pieces.append(cur.batches)
        cur = cur.parent
    pieces.reverse()

    transitions: List[dict] = []
    inputs = dict(req.initial)
    for batches in pieces:
        for b in batches:
            if b.events and b.events[0].kind == "input":
                inputs = dict(req.target)
            state = {name: inputs[name] for name in sorted(inputs)}
            state.update({g: b.values[gidx[g]] for g in gate_ids})
            transitions.append({
                "time": b.time,
                "events": [_event_str(e) for e in b.events],
                "state": state,
                "active_events": [
                    f"{gate_ids[e[0]]}→{e[2]}@t={e[1]} (d={e[1] - e[3]})"
                    for e in b.active
                ],
            })

    return {
        "transitions": transitions,
        "explanation": (
            "见证按 (时刻, 门编号, 本批门事件选用延迟) 字典序规范化选取；"
            "同一时刻的事件先整体生效，再对受影响门各求值一次；"
            "未到期事件在门被再次求值时按惯性延迟规则取消或替换。"
        ),
    }


def _event_str(e: BatchEvent) -> str:
    if e.kind == "input":
        return f"输入 {e.gate}: {e.from_value}→{e.to_value}"
    return f"{e.gate}: {e.from_value}→{e.to_value} (d={e.delay})"


def _topo_order(gate_ids: List[str], fanin: Dict[str, List[str]]) -> List[str]:
    order: List[str] = []
    seen = set()

    def visit(u: str) -> None:
        if u in seen:
            return
        seen.add(u)
        for v in fanin[u]:
            if v in fanin:
                visit(v)
        order.append(u)

    for g in gate_ids:
        visit(g)
    return order
