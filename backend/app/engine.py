"""Exhaustive asynchronous scheduling engine.

Implemented semantics (no sampling, fixed/averaged delays, or continuous
time approximation):

* All external inputs switch simultaneously at time 0.
* Each gate owns an integer delay interval [dmin, dmax].  Whenever a gate
  output must change, **every** integer delay in the interval is explored as
  a distinct scheduling branch.
* Inertial delay: an event that has not yet fired is cancelled (or replaced)
  when re-evaluation makes the gate settle on another value before firing.
* Events sharing a timestamp fire as one batch; the network is evaluated
  only after the whole batch is applied.  Delay-0 consequences are resolved
  in further micro-batches at the same timestamp (the netlist is acyclic,
  so micro-batch rounds always terminate).
* Reachable states (gate values + pending events + toggle counts) are
  deduplicated; the traversal is therefore a complete finite exploration of
  every feasible delay choice and event interleaving.

A monitored output is correct for one execution iff it toggles at most once
*and* its final value equals the value entailed by the target input vector.
A toggle that ends back at the original value, settling to the wrong value,
or two-or more toggles each constitute a hazard.
"""
from __future__ import annotations

import itertools
from dataclasses import dataclass
from typing import Optional

from .validator import Spec


def _eval_op(op: str, values: list[int]) -> int:
    if op == "AND":
        return 1 if all(values) else 0
    if op == "OR":
        return 1 if any(values) else 0
    if op == "NAND":
        return 0 if all(values) else 1
    if op == "NOR":
        return 1 if not any(values) else 0
    x = 0
    for v in values:
        x ^= v
    if op == "XOR":
        return x
    if op == "XNOR":
        return 1 - x
    if op == "NOT":
        return 1 - values[0]
    if op == "BUF":
        return values[0]
    raise ValueError(op)


@dataclass(frozen=True)
class Step:
    """One applied (micro-)batch and the monitor values right after it."""

    time: int
    batch: tuple[tuple[str, int, int], ...]  # (gate id, new value, delay used)
    outputs: tuple[tuple[str, int], ...]
    note: str = ""


@dataclass
class _Rec:
    parent_time: int
    parent_key: tuple
    steps: list  # steps produced within the parent timestamp resolution
    gv: tuple
    toggles: tuple


class _Node:
    __slots__ = ("gv", "pend", "toggles")

    def __init__(self, gv, pend, toggles):
        self.gv = gv          # gate value vector
        self.pend = pend      # per gate: None or (fire_time, value, delay)
        self.toggles = toggles  # per monitor: fired-toggle count


class Engine:
    def __init__(self, spec: Spec, max_states: int = 500_000):
        self.spec = spec
        self.s = spec
        self.ng = len(spec.gates)
        self.nm = len(spec.monitors)
        self.max_states = max_states
        self.overflow = False

        def make_eval(g_idx: int):
            op = spec.gates[g_idx].op
            refs = spec.gates[g_idx].fanin

            def evaluate(gv, iv):
                vals = [iv[idx] if kind == 0 else gv[idx]
                        for kind, idx in refs]
                return _eval_op(op, vals)

            return evaluate

        self.evals = [make_eval(i) for i in range(self.ng)]

    # ------------------------------------------------------------------ utils
    def stable_values(self, iv: tuple[int, ...]) -> tuple[int, ...]:
        gv = [0] * self.ng
        for gi in self.s.topo:
            gv[gi] = self.evals[gi](gv, iv)
        return tuple(gv)

    def monitor_values(self, gv, iv) -> tuple[int, ...]:
        return tuple(
            iv[m.idx] if m.kind == 0 else gv[m.idx] for m in self.s.monitors
        )

    def _key(self, gv, pend, toggles):
        return (gv, tuple(pend), toggles)

    def _apply_batch(self, t, fire_list, gv, pend, toggles, steps, note):
        ngv = list(gv)
        npend = list(pend)
        entries = []
        tog = list(toggles)
        mon_gate = self._mon_gate
        for gi in fire_list:
            _, val, delay = npend[gi]
            npend[gi] = None
            if ngv[gi] != val:  # an event arriving at the settled value is absorbed
                ngv[gi] = val
                entries.append((self.s.gate_names[gi], val, delay))
                for mi, mg in enumerate(mon_gate):
                    if mg == gi:
                        tog[mi] += 1
        if entries:
            outs = tuple(
                (self.s.monitors[i].name,
                 self._iv_now[self.s.monitors[i].idx]
                 if self.s.monitors[i].kind == 0
                 else ngv[self.s.monitors[i].idx])
                for i in range(self.nm)
            )
            steps = steps + [Step(t, tuple(entries), outs, note)]
        return tuple(ngv), tuple(npend), tuple(tog), steps

    # ------------------------------------------------------------------ main
    def run(self):
        s = self.s
        self._iv_now = s.target_bits
        g0 = self.stable_values(s.initial_bits)
        gf = self.stable_values(s.target_bits)
        mon_init = self.monitor_values(g0, s.initial_bits)
        mon_final = self.monitor_values(gf, s.target_bits)
        necessary = tuple(
            1 if mon_init[i] != mon_final[i] else 0 for i in range(self.nm)
        )
        self._mon_gate = [m.idx if m.kind == 1 else -1 for m in s.monitors]

        changed_inputs = {
            i for i in range(len(s.input_names))
            if s.initial_bits[i] != s.target_bits[i]
        }
        input_affected: set[int] = set()
        for gi, g in enumerate(s.gates):
            if any(kind == 0 and idx in changed_inputs for kind, idx in g.fanin):
                input_affected.add(gi)

        root = _Node(g0, tuple([None] * self.ng), tuple([0] * self.nm))
        root_key = self._key(root.gv, root.pend, root.toggles)

        buckets: dict[int, dict[tuple, _Node]] = {0: {root_key: root}}
        # history groups records by the resolution timestamp at which the
        # state is current; keys are globally unique (pending entries carry
        # absolute fire times).
        history: dict[int, dict[tuple, _Rec]] = {0: {}}
        stored_at: dict[tuple, int] = {}
        known: set[tuple] = set()
        frozen: dict[tuple, _Node] = {}
        frozen_at: dict[tuple, int] = {}
        pending_set: set[tuple] = set()  # states waiting at some future time
        registered = 0

        reachable: dict[int, int] = {}
        earliest_t: Optional[int] = None
        # candidates: (violation time, state key, monitor index)
        candidates: list[tuple[int, tuple, int]] = []
        hazard_keys: set[tuple] = set()
        combos_explored = 0

        def register(t, node, steps, parent_pt, parent_key):
            """Deduplicated insertion of a state current at time t."""
            nonlocal registered
            key = self._key(node.gv, node.pend, node.toggles)
            if key in known:
                return key, False
            if registered >= self.max_states:
                self.overflow = True
                return key, False
            known.add(key)
            registered += 1
            stored_at[key] = t
            history.setdefault(t, {})[key] = _Rec(
                parent_pt, parent_key, list(steps), node.gv, node.toggles
            )
            return key, True

        def violation_of(node):
            pend, gv, toggles = node.pend, node.gv, node.toggles
            for mi in range(self.nm):
                if toggles[mi] > necessary[mi]:
                    return mi
            if not any(p is not None for p in pend):
                for mi in range(self.nm):
                    m = s.monitors[mi]
                    mv = s.target_bits[m.idx] if m.kind == 0 else gv[m.idx]
                    if mv != mon_final[mi]:
                        return mi
            return -1

        def classify(t, key, node):
            """Place a registered state: hazard witness / settled / bucket."""
            nonlocal earliest_t
            mi = violation_of(node)
            if mi >= 0:
                if earliest_t is None or t < earliest_t:
                    earliest_t = t
                    candidates.clear()
                if earliest_t == t:
                    candidates.append((t, key, mi))
                    hazard_keys.add(key)
                return
            fire_times = [p[0] for p in node.pend if p is not None]
            if not fire_times:
                frozen[key] = node
                frozen_at[key] = t
                pending_set.discard(key)
                return
            nt = min(fire_times)
            if earliest_t is not None and nt > earliest_t:
                return
            buckets.setdefault(nt, {})[key] = node
            pending_set.add(key)

        def schedule(t, key, node, own_steps, affected):
            """Re-evaluate after the last batch; enumerate every delay choice
            (Cartesian product) and continue through delay-0 micro-batches,
            all resolved at timestamp t.

            ``own_steps`` are the steps already recorded on ``key``'s own
            history record; child records store only newly produced steps.
            """
            nonlocal combos_explored
            iv = s.target_bits
            gv, pend, toggles = node.gv, node.pend, node.toggles

            option_gates: list[int] = []
            option_lists: list[list[tuple[int, int]]] = []  # (delay, new val)
            cancellations: list[int] = []
            for gi in sorted(affected, key=lambda x: s.gate_names[x]):
                nv = self.evals[gi](gv, iv)
                p = pend[gi]
                if p is not None:
                    if p[1] == nv:
                        continue  # inertial: pending event still wanted
                    if nv == gv[gi]:
                        cancellations.append(gi)  # obsolete event cancelled
                        continue
                elif nv == gv[gi]:
                    continue  # no pending event and output stays put
                g = s.gates[gi]
                option_gates.append(gi)
                option_lists.append([
                    (d, nv) for d in range(g.delay_min, g.delay_max + 1)
                ])

            if not option_gates:
                if cancellations:
                    lp = list(pend)
                    for gi in cancellations:
                        lp[gi] = None
                    child = _Node(gv, tuple(lp), toggles)
                    ck, is_new = register(t, child, [], t, key)
                    if is_new:
                        classify(t, ck, child)
                else:
                    classify(t, key, node)
                return

            for combo in itertools.product(*option_lists):
                if self.overflow:
                    return
                combos_explored += 1
                lp = list(pend)
                for gi in cancellations:
                    lp[gi] = None
                zero = []
                for gi, (d, nv) in zip(option_gates, combo):
                    lp[gi] = (t + d, nv, d)
                    if d == 0:
                        zero.append(gi)
                if not zero:
                    child = _Node(gv, tuple(lp), toggles)
                    ck, is_new = register(t, child, [], t, key)
                    if is_new:
                        classify(t, ck, child)
                    continue
                # all delay-0 events form one micro-batch at time t
                zlist = sorted(zero, key=lambda x: s.gate_names[x])
                ng, np2, nt2, nsteps = self._apply_batch(
                    t, zlist, gv, tuple(lp), toggles, own_steps,
                    "零延迟微批（同一时刻）",
                )
                delta = nsteps[len(own_steps):]
                child = _Node(ng, np2, nt2)
                ck, is_new = register(t, child, delta, t, key)
                if not is_new:
                    continue
                mi = violation_of(child)
                if mi >= 0:
                    classify(t, ck, child)
                    continue
                actually_changed = {gi for gi in zlist if ng[gi] != gv[gi]}
                downstream: set[int] = set()
                for cg in actually_changed:
                    downstream.update(s.fanout[cg])
                schedule(t, ck, child, delta, downstream)

        def resolve_time(t, node, key):
            fired = sorted(
                (gi for gi, p in enumerate(node.pend)
                 if p is not None and p[0] == t),
                key=lambda x: s.gate_names[x],
            )
            if fired:
                gv, pend, toggles, steps = self._apply_batch(
                    t, fired, node.gv, node.pend, node.toggles, [],
                    "同刻事件成批生效后求值",
                )
                changed = {gi for gi in fired if gv[gi] != node.gv[gi]}
                affected: set[int] = set()
                for gi in changed:
                    affected.update(s.fanout[gi])
                cur = _Node(gv, pend, toggles)
                ck, is_new = register(t, cur, steps, stored_at[key], key)
                if is_new:
                    # evaluate the network only after the whole batch is
                    # applied; schedule() classifies the state once re-eval
                    # has produced any follow-up events
                    schedule(t, ck, cur, steps, affected)
            else:
                # t == 0: external inputs have just switched as one batch.
                # Append the switch step to the root's own record (monitored
                # inputs count as one toggle), then evaluate gates against
                # the target vector.
                tog = list(node.toggles)
                for mi, m in enumerate(s.monitors):
                    if m.kind == 0 and (
                        s.initial_bits[m.idx] != s.target_bits[m.idx]
                    ):
                        tog[mi] += 1
                switched_outputs = tuple(
                    (s.monitors[i].name,
                     s.target_bits[s.monitors[i].idx]
                     if s.monitors[i].kind == 0 else node.gv[s.monitors[i].idx])
                    for i in range(self.nm)
                )
                switch_step = Step(
                    0, (), switched_outputs,
                    "外部输入在零时刻成批切换",
                )
                cur0 = _Node(node.gv, node.pend, tuple(tog))
                if self._key(cur0.gv, cur0.pend, cur0.toggles) != root_key:
                    # a monitored external input switched: the post-switch
                    # state is its own reachable configuration at t=0; the
                    # switch step belongs to its record (not the root's)
                    k0, is_new = register(0, cur0, [switch_step], 0, root_key)
                    if is_new:
                        # schedule() decides follow-up events; if no gate
                        # reacts it classifies the genuinely settled state
                        schedule(0, k0, cur0, [switch_step],
                                 set(input_affected))
                else:
                    history[0][root_key].steps.append(switch_step)
                    # schedule() enumerates the first delay choices; only if
                    # no gate reacts does it classify the state as settled
                    schedule(0, root_key, cur0,
                             history[0][root_key].steps,
                             set(input_affected))

        # ------------------------------------------------------------- BFS
        register(
            0, root,
            [Step(0, (), tuple((s.monitors[i].name, mon_init[i])
                               for i in range(self.nm)),
                  "初始稳态（输入零时刻成批切换前）")],
            -1, (),
        )
        # the root is resolved at t=0 by the external input batch
        buckets[0] = {root_key: root}

        while buckets and not self.overflow:
            t = min(buckets)
            current = buckets.pop(t)
            # these states fire (or get resolved) at t: no longer waiting
            for key in current:
                pending_set.discard(key)
            for key in sorted(current.keys(), key=lambda k: repr(k)):
                if self.overflow:
                    break
                resolve_time(t, current[key], key)
            # distinct configurations possible right after instant t has been
            # resolved: states still pending at later times, executions that
            # have already quiesced, and witnesses at this same instant
            reachable[t] = len(
                pending_set
                | {k for k, ft in frozen_at.items() if ft <= t}
                | {k for k in hazard_keys if stored_at[k] == t}
            )
            if earliest_t is not None and t >= earliest_t:
                break

        # --------------------------------------------- witness reconstruction
        def reconstruct(key, at_time):
            out: list = []
            ck, ct = key, at_time
            while ct >= 0:
                rec = history[ct][ck]
                out = list(rec.steps) + out
                ck, ct = rec.parent_key, rec.parent_time
            return out

        def signature(full_steps):
            return tuple(
                (st.time, tuple(sorted(st.batch, key=lambda e: (e[0], e[2]))))
                for st in full_steps if st.batch
            )

        result = {
            "mon_init": mon_init,
            "mon_final": mon_final,
            "necessary": necessary,
            "reachable": dict(sorted(reachable.items())),
            "combos": combos_explored,
            "registered_states": registered,
            "overflow": self.overflow,
        }

        if self.overflow:
            result["hazard"] = None
            return result

        if earliest_t is not None:
            best = None
            best_sig = None
            best_mi = None
            seen_keys = set()
            for vt, vkey, mi in candidates:
                if (vkey, mi) in seen_keys:
                    continue
                seen_keys.add((vkey, mi))
                full = reconstruct(vkey, vt)
                sig = (signature(full), s.monitors[mi].name)
                if best_sig is None or sig < best_sig:
                    best_sig = sig
                    best = (vkey, vt)
                    best_mi = mi
            result["hazard"] = (best_mi, earliest_t,
                                reconstruct(*best) if best else [])
            return result

        # canonical safe representative: lexicographically smallest execution
        best_key = best_at = None
        best_sig = None
        for key in frozen:
            at = frozen_at[key]
            sig = signature(reconstruct(key, at))
            if best_sig is None or sig < best_sig:
                best_sig = sig
                best_key, best_at = key, at
        result["hazard"] = None
        result["safe_leaf"] = (
            best_key, best_at,
            reconstruct(best_key, best_at) if best_key else [],
        )
        result["frozen"] = frozen
        return result
