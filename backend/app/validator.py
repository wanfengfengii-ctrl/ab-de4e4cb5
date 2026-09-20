"""Validation, cycle localization and normalization of an audit request.

The API accepts a plain JSON object so that *every* invalid input (including
type errors) is reported with a location instead of producing an opaque 422.
"""
from __future__ import annotations

from dataclasses import dataclass, field

VALID_OPS = {"AND", "OR", "NOT", "BUF", "NAND", "NOR", "XOR", "XNOR"}
UNARY_OPS = {"NOT", "BUF"}


@dataclass
class GateSpec:
    idx: int
    name: str
    op: str
    fanin: list[tuple[int, int]]  # (wire kind: 0=input 1=gate, index)
    delay_min: int
    delay_max: int


@dataclass
class MonitorSpec:
    name: str
    kind: int  # 0=input 1=gate
    idx: int


@dataclass
class Spec:
    input_names: list[str]
    gate_names: list[str]
    gates: list[GateSpec]
    monitors: list[MonitorSpec]
    initial_bits: tuple[int, ...]
    target_bits: tuple[int, ...]
    fanout: list[list[int]] = field(default_factory=list)
    topo: list[int] = field(default_factory=list)


def _is_int(v: object) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def validate_and_build(payload: object) -> tuple[Spec | None, list[str]]:
    errors: list[str] = []

    if not isinstance(payload, dict):
        return None, ["请求体必须是 JSON 对象"]

    def get(key: str, expected_type: type | tuple[type, ...]) -> object:
        if key not in payload:
            errors.append(f"缺少字段 '{key}'")
            return None
        if not isinstance(payload[key], expected_type):
            expect = "列表" if expected_type is list else "对象" if expected_type is dict else "值"
            errors.append(f"字段 '{key}' 必须是{expect}")
            return None
        return payload[key]

    raw_inputs = get("inputs", list)
    raw_initial = get("initial", dict)
    raw_target = get("target", dict)
    raw_gates = get("gates", dict)
    raw_monitors = get("monitors", list)

    if errors:
        return None, errors

    # ---- inputs -----------------------------------------------------------
    input_names: list[str] = []
    seen: set[str] = set()
    for i, item in enumerate(raw_inputs):
        if not isinstance(item, str) or not item.strip():
            errors.append(f"inputs[{i}]: 输入名必须是非空字符串")
            continue
        name = item.strip()
        if name in seen:
            errors.append(f"inputs[{i}]: 输入 '{name}' 重复")
            continue
        seen.add(name)
        input_names.append(name)
    if not input_names and isinstance(raw_inputs, list) and len(raw_inputs) == 0:
        errors.append("inputs: 至少需要一个外部输入")
    input_index = {n: i for i, n in enumerate(input_names)}

    # ---- initial / target bit vectors ------------------------------------
    def parse_bits(raw: dict, label: str) -> tuple[int, ...] | None:
        bits = [0] * len(input_names)
        ok = True
        for name in raw:
            if name not in input_index:
                errors.append(f"{label}.{name}: 未在 inputs 中声明")
                ok = False
        for name, i in input_index.items():
            if name not in raw:
                errors.append(f"{label}.{name}: 缺少该输入的初始/目标电平")
                ok = False
                continue
            v = raw[name]
            if not _is_int(v) or v not in (0, 1):
                errors.append(f"{label}.{name}: 电平必须是 0 或 1")
                ok = False
            else:
                bits[i] = v
        return tuple(bits) if ok else None

    initial_bits = parse_bits(raw_initial, "initial")
    target_bits = parse_bits(raw_target, "target")

    # ---- gates ------------------------------------------------------------
    gate_names: list[str] = []
    gate_seen: set[str] = set()
    parsed_gates: dict[str, tuple[str, list[str], int, int]] = {}
    assert isinstance(raw_gates, dict)
    for gid, g in raw_gates.items():
        if not isinstance(gid, str) or not gid.strip():
            errors.append(f"gates: 门编号必须是非空字符串 (收到 {gid!r})")
            continue
        name = gid.strip()
        if name in gate_seen:
            errors.append(f"gates.{name}: 门编号重复")
            continue
        if name in input_index:
            errors.append(f"gates.{name}: 门编号与外部输入同名")
            continue
        if not isinstance(g, dict):
            errors.append(f"gates.{name}: 门定义必须是对象")
            continue
        gate_seen.add(name)
        gate_names.append(name)

        op = g.get("type")
        if not isinstance(op, str) or op.upper() not in VALID_OPS:
            errors.append(
                f"gates.{name}.type: 必须是 {sorted(VALID_OPS)} 之一"
            )
            op = None
        else:
            op = op.upper()

        gin = g.get("inputs", [])
        if not isinstance(gin, list):
            errors.append(f"gates.{name}.inputs: 必须是编号列表")
            gin = []
        for j, ref in enumerate(gin):
            if not isinstance(ref, str) or not ref.strip():
                errors.append(f"gates.{name}.inputs[{j}]: 连线编号必须是非空字符串")
        gin = [r.strip() for r in gin if isinstance(r, str) and r.strip()]

        if op is not None:
            if op in UNARY_OPS and len(gin) != 1:
                errors.append(f"gates.{name}.inputs: {op} 门必须恰好有 1 个输入")
            elif op not in UNARY_OPS and len(gin) < 2:
                errors.append(f"gates.{name}.inputs: {op} 门至少需要 2 个输入")

        dmin = g.get("delay_min")
        dmax = g.get("delay_max")
        if not _is_int(dmin):
            errors.append(f"gates.{name}.delay_min: 必须是非负整数")
            dmin = -1
        elif dmin < 0:
            errors.append(f"gates.{name}.delay_min: 必须是非负整数")
        if not _is_int(dmax):
            errors.append(f"gates.{name}.delay_max: 必须是非负整数")
            dmax = -1
        elif dmax < 0:
            errors.append(f"gates.{name}.delay_max: 必须是非负整数")
        if _is_int(dmin) and _is_int(dmax) and 0 <= dmin <= dmax:
            pass
        elif _is_int(dmin) and _is_int(dmax) and 0 <= dmin and 0 <= dmax:
            errors.append(
                f"gates.{name}: delay_min({dmin}) 不能大于 delay_max({dmax})"
            )

        parsed_gates[name] = (op, gin, dmin if _is_int(dmin) else 0,
                              dmax if _is_int(dmax) else 0)

    gate_index = {n: i for i, n in enumerate(gate_names)}

    # ---- monitors ---------------------------------------------------------
    monitor_names: list[str] = []
    mon_seen: set[str] = set()
    for i, m in enumerate(raw_monitors):
        if not isinstance(m, str) or not m.strip():
            errors.append(f"monitors[{i}]: 监测点必须是非空字符串")
            continue
        name = m.strip()
        if name in mon_seen:
            errors.append(f"monitors[{i}]: 监测点 '{name}' 重复")
            continue
        if name not in input_index and name not in gate_index:
            errors.append(f"monitors[{i}]: '{name}' 既不是输入也不是门")
            continue
        if not gate_names and name not in input_index:
            continue
        mon_seen.add(name)
        monitor_names.append(name)
    if not monitor_names and isinstance(raw_monitors, list) and len(raw_monitors) == 0:
        errors.append("monitors: 至少需要一个监测输出")

    # ---- wire references (dangling wires reported with all other errors) --
    for name in gate_names:
        _op, gin, _d0, _d1 = parsed_gates[name]
        for j, ref in enumerate(gin):
            if ref not in input_index and ref not in gate_index:
                errors.append(
                    f"gates.{name}.inputs[{j}]: 连线 '{ref}' 未声明"
                    "（既不是输入也不是门）"
                )

    if errors:
        return None, errors

    # ---- resolve fanin ----------------------------------------------------
    gates: list[GateSpec] = []
    for name in gate_names:
        op, gin, dmin, dmax = parsed_gates[name]
        fanin: list[tuple[int, int]] = []
        for ref in gin:
            if ref in input_index:
                fanin.append((0, input_index[ref]))
            else:
                fanin.append((1, gate_index[ref]))
        gates.append(GateSpec(gate_index[name], name, op, fanin, dmin, dmax))

    # ---- cycle detection with an explicit cycle path ---------------------
    adj = [[idx for (k, idx) in g.fanin if k == 1] for g in gates]
    color = [0] * len(gates)  # 0 white 1 gray 2 black
    stack: list[int] = []
    cycle_path: list[str] | None = None

    def dfs(u: int) -> bool:
        nonlocal cycle_path
        color[u] = 1
        stack.append(u)
        for v in adj[u]:
            if color[v] == 1:
                start = stack.index(v)
                cycle_path = [gate_names[x] for x in stack[start:]] + [gate_names[v]]
                return True
            if color[v] == 0 and dfs(v):
                return True
        stack.pop()
        color[u] = 2
        return False

    for i in range(len(gates)):
        if color[i] == 0 and dfs(i):
            break
    if cycle_path is not None:
        errors.append("网表成环: " + " -> ".join(cycle_path))
        return None, errors

    # ---- topological order (Kahn) ----------------------------------------
    indeg = [len(adj[i]) for i in range(len(gates))]
    ready = sorted([i for i, d in enumerate(indeg) if d == 0])
    topo: list[int] = []
    while ready:
        u = ready.pop(0)
        topo.append(u)
        for j in range(len(gates)):
            if u in adj[j]:
                indeg[j] -= 1
                if indeg[j] == 0:
                    ready.append(j)
        ready.sort()

    monitors: list[MonitorSpec] = []
    for name in monitor_names:
        if name in input_index:
            monitors.append(MonitorSpec(name, 0, input_index[name]))
        else:
            monitors.append(MonitorSpec(name, 1, gate_index[name]))

    spec = Spec(
        input_names=input_names,
        gate_names=gate_names,
        gates=gates,
        monitors=monitors,
        initial_bits=initial_bits,  # type: ignore[arg-type]
        target_bits=target_bits,  # type: ignore[arg-type]
        topo=topo,
    )

    fanout = [[] for _ in gates]
    for g in gates:
        for kind, idx in g.fanin:
            if kind == 1:
                fanout[idx].append(g.idx)
    spec.fanout = fanout
    return spec, []
