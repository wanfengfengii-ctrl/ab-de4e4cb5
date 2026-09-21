import React, { useMemo, useState } from 'react';

function Badge({ tone, children }) {
  return <span className={`badge ${tone}`}>{children}</span>;
}

export function ResultPanel({ result, error, onDismissError }) {
  if (error) {
    return (
      <section className="panel result error-panel">
        <header className="panel-head">
          <h2>输入无效，已清除旧结果</h2>
          <button className="btn ghost" onClick={onDismissError}>
            知道了
          </button>
        </header>
        <div className="error-box">
          <div className="error-code">
            <Badge tone="danger">{error.code}</Badge>
            {error.location && (
              <span className="error-loc">定位：{error.location}</span>
            )}
          </div>
          <p className="error-msg">{error.message}</p>
        </div>
      </section>
    );
  }

  if (!result) return null;

  if (result.safe) {
    return <SafeView result={result} />;
  }
  return <HazardView result={result} />;
}

function SafeView({ result }) {
  const maxStates = Math.max(
    1,
    ...result.timeline.map((l) => l.reachable_states)
  );
  return (
    <section className="panel result">
      <header className="panel-head">
        <h2>
          <Badge tone="ok">安全</Badge> 所有执行中监测输出至多完成一次必要翻转
        </h2>
      </header>
      <p className="summary">
        调度地平到 t = {result.horizon}；共枚举并合并出{' '}
        <strong>{result.total_states}</strong> 个逐时刻可达构型（去重后）。
      </p>
      <div className="timeline">
        {result.timeline.map((layer) => (
          <div className="layer" key={layer.time}>
            <span className="layer-time">t={layer.time}</span>
            <div className="bar-track">
              <div
                className="bar safe-bar"
                style={{
                  width: `${(layer.reachable_states / maxStates) * 100}%`,
                }}
              />
            </div>
            <span className="layer-count">{layer.reachable_states} 态</span>
          </div>
        ))}
      </div>
      <TimelineReplay result={result} />
    </section>
  );
}

function HazardView({ result }) {
  const v = result.violation;
  return (
    <section className="panel result">
      <header className="panel-head">
        <h2>
          <Badge tone="danger">发现冒险</Badge> 监测输出发生了非必要翻转
        </h2>
      </header>
      <div className="hazard-card">
        <div className="hazard-metric">
          <span className="metric-label">最早违规时刻</span>
          <span className="metric-value">t = {v.time}</span>
        </div>
        <div className="hazard-metric">
          <span className="metric-label">违规门</span>
          <span className="metric-value">{v.gate}</span>
        </div>
        <div className="hazard-metric">
          <span className="metric-label">监测点</span>
          <span className="metric-value">{v.monitor}</span>
        </div>
      </div>
      <p className="summary">
        探索在首次违规时刻即停止；见证按
        <em> （时刻、门编号、本批门事件选用延迟）</em>
        的字典序规范化决胜，以下时间线即该见证执行。
      </p>
      <TimelineReplay result={result} highlightTime={v.time} />
    </section>
  );
}

function TimelineReplay({ result, highlightTime }) {
  const steps = result.witness.transitions;
  const [cursor, setCursor] = useState(0);
  const [playing, setPlaying] = useState(false);

  React.useEffect(() => {
    setCursor(0);
    setPlaying(false);
  }, [result]);

  React.useEffect(() => {
    if (!playing) return undefined;
    if (cursor >= steps.length - 1) {
      setPlaying(false);
      return undefined;
    }
    const t = setTimeout(() => setCursor((c) => Math.min(c + 1, steps.length - 1)), 900);
    return () => clearTimeout(t);
  }, [playing, cursor, steps.length]);

  const step = steps[cursor];
  const signalOrder = useMemo(
    () => Object.keys(step?.state || {}),
    [step]
  );

  if (!steps.length) return null;

  return (
    <div className="replay">
      <div className="replay-controls">
        <button
          className="btn small"
          onClick={() => {
            setPlaying(false);
            setCursor(0);
          }}
        >
          ⏮ 复位
        </button>
        <button
          className="btn small"
          onClick={() => setPlaying((p) => !p)}
          disabled={cursor >= steps.length - 1}
        >
          {playing ? '⏸ 暂停' : '▶ 回放'}
        </button>
        <button
          className="btn small"
          onClick={() =>
            setCursor((c) => Math.min(c + 1, steps.length - 1))
          }
          disabled={cursor >= steps.length - 1}
        >
          步进 ⏭
        </button>
        <span className="cursor-label">
          批次 {cursor + 1} / {steps.length}
        </span>
      </div>
      <div
        className={`replay-stage ${
          highlightTime === step.time ? 'stage-violation' : ''
        }`}
      >
        <div className="stage-head">
          <span className="stage-time">t = {step.time}</span>
          <div className="event-list">
            {step.events.length ? (
              step.events.map((e, i) => (
                <span className="event-pill" key={i}>
                  {e}
                </span>
              ))
            ) : (
              <span className="muted">无可见事件（批次求值后无变化）</span>
            )}
          </div>
        </div>
        <div className="state-grid">
          {signalOrder.map((name) => (
            <div
              key={name}
              className={`sig ${step.state[name] ? 'sig-1' : 'sig-0'}`}
            >
              <span className="sig-name">{name}</span>
              <span className="sig-val">{step.state[name]}</span>
            </div>
          ))}
        </div>
        <div className="active">
          <span className="active-label">在途事件（惯性延迟，未到期）：</span>
          {step.active_events.length ? (
            step.active_events.map((e, i) => (
              <span className="event-pill pending" key={i}>
                {e}
              </span>
            ))
          ) : (
            <span className="muted">无</span>
          )}
        </div>
      </div>
      <p className="witness-note">{result.witness.explanation}</p>
    </div>
  );
}
