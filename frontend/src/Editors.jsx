import React from 'react';

const GATE_TYPES = ['AND', 'OR', 'NOT', 'NAND', 'NOR', 'XOR', 'XNOR', 'BUF'];

function Section({ title, hint, children, onAdd, addLabel }) {
  return (
    <section className="panel">
      <header className="panel-head">
        <div>
          <h2>{title}</h2>
          {hint && <p className="hint">{hint}</p>}
        </div>
        {onAdd && (
          <button type="button" className="btn small" onClick={onAdd}>
            + {addLabel}
          </button>
        )}
      </header>
      {children}
    </section>
  );
}

export function InputEditor({ inputs, onChange, errorLocation }) {
  const update = (idx, patch) =>
    onChange(inputs.map((it, i) => (i === idx ? { ...it, ...patch } : it)));
  const remove = (idx) => onChange(inputs.filter((_, i) => i !== idx));

  return (
    <Section
      title="主输入（零时刻成批切换）"
      hint="初始值 → 目标值，均为 0/1"
      onAdd={() =>
        onChange([...inputs, { name: '', initial: '0', target: '1' }])
      }
      addLabel="输入"
    >
      {inputs.length === 0 && <p className="empty">尚无输入</p>}
      <div className="rows">
        {inputs.map((it, idx) => (
          <div className="row" key={idx}>
            <input
              className="cell name"
              placeholder="名称 a"
              value={it.name}
              onChange={(e) => update(idx, { name: e.target.value })}
            />
            <select
              className="cell bit"
              value={it.initial}
              onChange={(e) => update(idx, { initial: e.target.value })}
            >
              <option value="0">0</option>
              <option value="1">1</option>
            </select>
            <span className="arrow">→</span>
            <select
              className="cell bit"
              value={it.target}
              onChange={(e) => update(idx, { target: e.target.value })}
            >
              <option value="0">0</option>
              <option value="1">1</option>
            </select>
            <button className="btn ghost" onClick={() => remove(idx)}>
              删除
            </button>
          </div>
        ))}
      </div>
    </Section>
  );
}

export function GateEditor({ gates, onChange, highlightGate }) {
  const update = (idx, patch) =>
    onChange(gates.map((g, i) => (i === idx ? { ...g, ...patch } : g)));
  const remove = (idx) => onChange(gates.filter((_, i) => i !== idx));

  return (
    <Section
      title="无环逻辑门网表"
      hint="输入以空格或逗号分隔；延迟为闭区间整数"
      onAdd={() =>
        onChange([
          ...gates,
          { name: '', type: 'AND', inputs: '', dmin: '1', dmax: '1' },
        ])
      }
      addLabel="门"
    >
      {gates.length === 0 && <p className="empty">尚无门</p>}
      <div className="rows">
        {gates.map((g, idx) => {
          const bad = highlightGate && g.name === highlightGate;
          return (
            <div className={`row gate-row ${bad ? 'row-error' : ''}`} key={idx}>
              <input
                className="cell name"
                placeholder="门编号 g1"
                value={g.name}
                onChange={(e) => update(idx, { name: e.target.value })}
              />
              <select
                className="cell type"
                value={g.type}
                onChange={(e) => update(idx, { type: e.target.value })}
              >
                {GATE_TYPES.map((t) => (
                  <option key={t}>{t}</option>
                ))}
              </select>
              <input
                className="cell inputs"
                placeholder="输入网络（空格分隔）"
                value={g.inputs}
                onChange={(e) => update(idx, { inputs: e.target.value })}
              />
              <label className="delay">
                d ∈ [
                <input
                  className="cell tiny"
                  value={g.dmin}
                  onChange={(e) => update(idx, { dmin: e.target.value })}
                />
                ,
                <input
                  className="cell tiny"
                  value={g.dmax}
                  onChange={(e) => update(idx, { dmax: e.target.value })}
                />
                ]
              </label>
              <button className="btn ghost" onClick={() => remove(idx)}>
                删除
              </button>
            </div>
          );
        })}
      </div>
    </Section>
  );
}

export function MonitorEditor({ monitors, gates, onChange }) {
  const toggle = (name) =>
    onChange(
      monitors.includes(name)
        ? monitors.filter((m) => m !== name)
        : [...monitors, name]
    );
  return (
    <Section title="监测输出" hint="仅可选择门输出网络">
      {gates.length === 0 ? (
        <p className="empty">请先添加门</p>
      ) : (
        <div className="chips">
          {gates.map((g) => (
            <label key={g.name} className="chip">
              <input
                type="checkbox"
                checked={monitors.includes(g.name)}
                disabled={!g.name}
                onChange={() => toggle(g.name)}
              />
              {g.name || '(未命名)'}
            </label>
          ))}
        </div>
      )}
    </Section>
  );
}
