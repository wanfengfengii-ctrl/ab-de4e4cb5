import React, { useEffect, useMemo, useRef, useState } from "react";
import { EXAMPLE_HAZARD, EXAMPLE_SAFE, runAudit } from "./api.js";

const GATE_TYPES = ["AND", "OR", "NOT", "BUF", "NAND", "NOR", "XOR", "XNOR"];
const UNARY = new Set(["NOT", "BUF"]);

function clone(obj) {
  return JSON.parse(JSON.stringify(obj));
}

function freshDraft() {
  return clone(EXAMPLE_HAZARD);
}

/* ----------------------------------------------------------------- editors */

function InputsEditor({ draft, setDraft }) {
  const setName = (i, v) => {
    const old = draft.inputs[i];
    draft.inputs[i] = v;
    for (const tbl of [draft.initial, draft.target]) {
      if (old in tbl) {
        tbl[v] = tbl[old];
        delete tbl[old];
      }
    }
    setDraft(clone(draft));
  };
  const setBit = (tbl, i, bit) => {
    tbl[draft.inputs[i]] = bit;
    setDraft(clone(draft));
  };
  const add = () => {
    let n = 1;
    while (draft.inputs.includes(`x${n}`)) n += 1;
    draft.inputs.push(`x${n}`);
    draft.initial[`x${n}`] = 0;
    draft.target[`x${n}`] = 0;
    setDraft(clone(draft));
  };
  const remove = (i) => {
    const name = draft.inputs[i];
    draft.inputs.splice(i, 1);
    delete draft.initial[name];
    delete draft.target[name];
    setDraft(clone(draft));
  };

  return (
    <section className="card">
      <h2>① 外部输入（零时刻成批切换）</h2>
      <table className="grid">
        <thead>
          <tr>
            <th>输入名</th>
            <th>初始电平</th>
            <th>目标电平</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          {draft.inputs.map((name, i) => (
            <tr key={i}>
              <td>
                <input
                  value={name}
                  onChange={(e) => setName(i, e.target.value)}
                />
              </td>
              <td>
                <BitToggle
                  value={draft.initial[name]}
                  onChange={(b) => setBit(draft.initial, i, b)}
                />
              </td>
              <td>
                <BitToggle
                  value={draft.target[name]}
                  onChange={(b) => setBit(draft.target, i, b)}
                />
              </td>
              <td>
                <button className="mini danger" onClick={() => remove(i)}>
                  删除
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <button className="mini" onClick={add}>
        + 添加输入
      </button>
    </section>
  );
}

function BitToggle({ value, onChange }) {
  return (
    <div className="bit-toggle">
      {[0, 1].map((b) => (
        <button
          key={b}
          className={value === b ? `bit b${b} active` : "bit"}
          onClick={() => onChange(b)}
        >
          {b}
        </button>
      ))}
    </div>
  );
}

function GatesEditor({ draft, setDraft }) {
  const names = Object.keys(draft.gates);
  const update = (id, patch) => {
    Object.assign(draft.gates[id], patch);
    setDraft(clone(draft));
  };
  const rename = (oldId, newId) => {
    const g = draft.gates[oldId];
    delete draft.gates[oldId];
    draft.gates[newId] = g;
    for (const other of Object.values(draft.gates)) {
      other.inputs = other.inputs.map((x) => (x === oldId ? newId : x));
    }
    draft.monitors = draft.monitors.map((x) =>
      x === oldId ? newId : x
    );
    setDraft(clone(draft));
  };
  const add = () => {
    let n = 1;
    while (draft.gates[`g${n}`]) n += 1;
    draft.gates[`g${n}`] = {
      type: "AND",
      inputs: [],
      delay_min: 1,
      delay_max: 1,
    };
    setDraft(clone(draft));
  };
  const remove = (id) => {
    delete draft.gates[id];
    for (const other of Object.values(draft.gates)) {
      other.inputs = other.inputs.filter((x) => x !== id);
    }
    draft.monitors = draft.monitors.filter((x) => x !== id);
    setDraft(clone(draft));
  };

  return (
    <section className="card">
      <h2>② 无环逻辑门网表与整数延迟区间</h2>
      <table className="grid gates">
        <thead>
          <tr>
            <th>门编号</th>
            <th>类型</th>
            <th>输入（逗号分隔）</th>
            <th>d_min</th>
            <th>d_max</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          {names.map((id) => {
            const g = draft.gates[id];
            return (
              <tr key={id}>
                <td>
                  <input
                    value={id}
                    onChange={(e) => rename(id, e.target.value)}
                  />
                </td>
                <td>
                  <select
                    value={g.type}
                    onChange={(e) => update(id, { type: e.target.value })}
                  >
                    {GATE_TYPES.map((t) => (
                      <option key={t}>{t}</option>
                    ))}
                  </select>
                </td>
                <td className="wire-cell">
                  <input
                    value={g.inputs.join(", ")}
                    placeholder={UNARY.has(g.type) ? "1 个输入" : "≥2 个输入"}
                    onChange={(e) =>
                      update(id, {
                        inputs: e.target.value
                          .split(/[,\s]+/)
                          .map((s) => s.trim())
                          .filter(Boolean),
                      })
                    }
                  />
                </td>
                <td>
                  <input
                    type="number"
                    min="0"
                    value={g.delay_min}
                    onChange={(e) =>
                      update(id, { delay_min: Number(e.target.value) })
                    }
                  />
                </td>
                <td>
                  <input
                    type="number"
                    min="0"
                    value={g.delay_max}
                    onChange={(e) =>
                      update(id, { delay_max: Number(e.target.value) })
                    }
                  />
                </td>
                <td>
                  <button className="mini danger" onClick={() => remove(id)}>
                    删除
                  </button>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
      <button className="mini" onClick={add}>
        + 添加门
      </button>
    </section>
  );
}

function MonitorsEditor({ draft, setDraft }) {
  const all = [...draft.inputs, ...Object.keys(draft.gates)];
  const toggle = (name) => {
    if (draft.monitors.includes(name)) {
      draft.monitors = draft.monitors.filter((x) => x !== name);
    } else {
      draft.monitors.push(name);
    }
    setDraft(clone(draft));
  };
  return (
    <section className="card">
      <h2>③ 监测输出</h2>
      <div className="chips">
        {all.map((name) => (
          <button
            key={name}
            className={
              draft.monitors.includes(name) ? "chip active" : "chip"
            }
            onClick={() => toggle(name)}
          >
            {name}
          </button>
        ))}
        {all.length === 0 && <span className="muted">（先添加输入或门）</span>}
      </div>
    </section>
  );
}

/* ------------------------------------------------------------- json editor */

function JsonEditor({ draft, setDraft }) {
  const [text, setText] = useState(() => JSON.stringify(draft, null, 2));
  const [parseError, setParseError] = useState(null);
  const [open, setOpen] = useState(false);

  useEffect(() => {
    setText(JSON.stringify(draft, null, 2));
    setParseError(null);
  }, [draft]);

  const apply = () => {
    try {
      const obj = JSON.parse(text);
      setDraft(obj);
      setParseError(null);
    } catch (err) {
      setParseError("JSON 解析失败：" + err.message);
    }
  };

  return (
    <section className="card">
      <h2 onClick={() => setOpen(!open)} className="collapsible">
        {open ? "▼" : "▶"} 高级：直接编辑审计 JSON
      </h2>
      {open && (
        <>
          <textarea
            className="json"
            rows={14}
            value={text}
            onChange={(e) => setText(e.target.value)}
            spellCheck="false"
          />
          {parseError && <div className="error">{parseError}</div>}
          <button className="mini" onClick={apply}>
            应用 JSON
          </button>
        </>
      )}
    </section>
  );
}

/* ----------------------------------------------------------- result panels */

function ReachableChart({ reachable }) {
  const entries = Object.entries(reachable)
    .map(([t, n]) => [Number(t), n])
    .sort((a, b) => a[0] - b[0]);
  const max = Math.max(1, ...entries.map(([, n]) => n));
  return (
    <div className="bars">
      {entries.map(([t, n]) => (
        <div key={t} className="bar-row">
          <span className="bar-time">t={t}</span>
          <div className="bar-track">
            <div className="bar-fill" style={{ width: `${(n / max) * 100}%` }} />
          </div>
          <span className="bar-count">{n}</span>
        </div>
      ))}
    </div>
  );
}

function Timeline({ steps, hazardStepIndex }) {
  const [idx, setIdx] = useState(0);
  const [playing, setPlaying] = useState(false);
  const timer = useRef(null);

  useEffect(() => {
    setIdx(0);
    setPlaying(false);
  }, [steps]);

  useEffect(() => {
    if (!playing) return;
    if (idx >= steps.length - 1) {
      setPlaying(false);
      return;
    }
    timer.current = setTimeout(() => setIdx((i) => i + 1), 900);
    return () => clearTimeout(timer.current);
  }, [playing, idx, steps.length]);

  if (!steps || steps.length === 0) return null;
  const step = steps[idx];
  const isViolation =
    hazardStepIndex != null && idx === hazardStepIndex;

  return (
    <div className="timeline">
      <div className="tl-controls">
        <button
          className="mini"
          onClick={() => {
            setPlaying(false);
            setIdx((i) => Math.max(0, i - 1));
          }}
        >
          ⏮ 上一时刻
        </button>
        <button
          className="mini primary"
          onClick={() => {
            if (idx >= steps.length - 1) setIdx(0);
            setPlaying((p) => !p);
          }}
        >
          {playing ? "⏸ 暂停" : "▶ 回放"}
        </button>
        <button
          className="mini"
          onClick={() => {
            setPlaying(false);
            setIdx((i) => Math.min(steps.length - 1, i + 1));
          }}
        >
          下一时刻 ⏭
        </button>
        <input
          type="range"
          min="0"
          max={steps.length - 1}
          value={idx}
          onChange={(e) => {
            setPlaying(false);
            setIdx(Number(e.target.value));
          }}
        />
        <span className="muted">
          {idx + 1} / {steps.length}
        </span>
      </div>
      <div className={isViolation ? "tl-step hazard" : "tl-step"}>
        <div className="tl-head">
          <strong>时刻 t = {step.time}</strong>
          <span className="note">{step.note}</span>
          {isViolation && <span className="badge danger">最早违规时刻</span>}
        </div>
        {step.batch && step.batch.length > 0 ? (
          <div>
            <div className="muted">本批成批生效的事件：</div>
            <ul className="events">
              {step.batch.map((e, i) => (
                <li key={i}>
                  <span className="mono">{e.gate}</span> →{" "}
                  <span className={`val b${e.value}`}>{e.value}</span>
                  <span className="muted">
                    （该次调度选用延迟 d = {e.delay}）
                  </span>
                </li>
              ))}
            </ul>
          </div>
        ) : (
          <div className="muted">（无门事件，仅输入/监测值）</div>
        )}
        <div className="muted">批生效后的监测输出：</div>
        <div className="chips">
          {(step.outputs || []).map((o, i) => (
            <span key={i} className="chip static">
              {o.monitor} = <span className={`val b${o.value}`}>{o.value}</span>
            </span>
          ))}
        </div>
      </div>
    </div>
  );
}

function ResultPanel({ result }) {
  if (!result) return null;

  if (!result.safe) {
    const w = result.witnesses[0];
    // index of the first violating step in the timeline
    const hazardIdx = w.steps.findIndex(
      (s) => s.time === w.first_violation_time && s.batch.length > 0
    );
    return (
      <section className="card result hazard-card">
        <h2 className="hazard">⚠ 发现静态/动态冒险</h2>
        <p>
          最早违规时刻：<strong className="big">t = {w.first_violation_time}</strong>
        </p>
        <p>
          违规监测点：<code>{w.monitor}</code>；该执行中翻转{" "}
          <strong>{w.toggles}</strong> 次，而必要翻转最多{" "}
          <strong>{w.necessary}</strong> 次。
        </p>
        <p className="muted">
          见证按门编号与所选延迟字典序决胜（规范见证），可逐步回放：
        </p>
        <Timeline steps={result.timeline} hazardStepIndex={hazardIdx} />
        <p className="muted small">
          已穷举 {result.explored_schedules} 个延迟/交错组合，访问{" "}
          {result.state_count_total} 个去重可达状态，无抽样、无固定延迟近似。
        </p>
      </section>
    );
  }

  return (
    <section className="card result safe-card">
      <h2 className="safe">✓ 审计通过：所有执行至多完成一次必要翻转</h2>
      <p>
        逐时刻可达状态数（含等待触发的调度分支与已静止执行）：
      </p>
      <ReachableChart reachable={result.reachable_states} />
      <h3>各监测输出翻转统计（所有完全执行中的最大翻转次数）</h3>
      <table className="grid">
        <thead>
          <tr>
            <th>监测点</th>
            <th>必要翻转</th>
            <th>实际最大翻转</th>
            <th>终值</th>
            <th>结论</th>
          </tr>
        </thead>
        <tbody>
          {result.toggle_counts.map((tc) => (
            <tr key={tc.monitor}>
              <td>{tc.monitor}</td>
              <td>{tc.necessary}</td>
              <td>{tc.toggles}</td>
              <td className={`val b${tc.end_value}`}>{tc.end_value}</td>
              <td>
                {tc.toggles <= tc.necessary ? (
                  <span className="ok">合规</span>
                ) : (
                  <span className="bad">冒险</span>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <h3>规范执行时间线回放</h3>
      <Timeline steps={result.timeline} />
      <p className="muted small">
        穷举调度组合 {result.explored_schedules} 个，去重可达状态共{" "}
        {result.state_count_total} 个，时间视界 t = {result.horizon}。
      </p>
    </section>
  );
}

/* --------------------------------------------------------------------- app */

export default function App() {
  const [draft, setDraft] = useState(freshDraft);
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState(null);
  const [errors, setErrors] = useState([]);
  const [networkError, setNetworkError] = useState(null);

  const run = async () => {
    setLoading(true);
    setNetworkError(null);
    // invalid input / cyclic netlist clears any previous result
    setResult(null);
    setErrors([]);
    try {
      const data = await runAudit(draft);
      if (data.valid) {
        setResult(data.result);
        setErrors([]);
      } else {
        setResult(null);
        setErrors(data.errors);
      }
    } catch (err) {
      setResult(null);
      setNetworkError("无法连接审计 API：" + err.message);
    } finally {
      setLoading(false);
    }
  };

  const errLocations = useMemo(() => errors, [errors]);

  return (
    <div className="layout">
      <header>
        <h1>异步设备联锁板 · 门延迟冒险审计台</h1>
        <p className="muted">
          惯性延迟 · 同刻成批生效 · 延迟区间逐值穷举 · 事件交错完整探索
        </p>
      </header>

      <div className="toolbar">
        <button className="primary big-btn" onClick={run} disabled={loading}>
          {loading ? "审计中…" : "发起审计（真实 API）"}
        </button>
        <button className="mini" onClick={() => setDraft(clone(EXAMPLE_HAZARD))}>
          载入冒险示例
        </button>
        <button className="mini" onClick={() => setDraft(clone(EXAMPLE_SAFE))}>
          载入安全示例
        </button>
      </div>

      {networkError && <div className="card error-banner">{networkError}</div>}
      {errLocations.length > 0 && (
        <section className="card error-banner">
          <h2>输入无效或网表成环 —— 已清除旧结果</h2>
          <ul>
            {errLocations.map((e, i) => (
              <li key={i} className="error-text">{e}</li>
            ))}
          </ul>
        </section>
      )}

      {result && <ResultPanel result={result} />}

      <div className="editors">
        <InputsEditor draft={draft} setDraft={setDraft} />
        <GatesEditor draft={draft} setDraft={setDraft} />
        <MonitorsEditor draft={draft} setDraft={setDraft} />
        <JsonEditor draft={draft} setDraft={setDraft} />
      </div>
    </div>
  );
}
