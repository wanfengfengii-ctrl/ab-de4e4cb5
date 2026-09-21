import React, { useEffect, useState } from 'react';
import { buildRequest, fetchHealth, runAudit } from './api';
import { HAZARD_EXAMPLE, SAFE_EXAMPLE } from './examples';
import { GateEditor, InputEditor, MonitorEditor } from './Editors';
import { ResultPanel } from './ResultPanel';

export default function App() {
  const [doc, setDoc] = useState(HAZARD_EXAMPLE);
  const [result, setResult] = useState(null);
  const [error, setError] = useState(null);
  const [loading, setLoading] = useState(false);
  const [health, setHealth] = useState('checking');

  useEffect(() => {
    let alive = true;
    const ping = () =>
      fetchHealth()
        .then(() => alive && setHealth('up'))
        .catch(() => alive && setHealth('down'));
    ping();
    const id = setInterval(ping, 5000);
    return () => {
      alive = false;
      clearInterval(id);
    };
  }, []);

  const patch = (p) => setDoc((d) => ({ ...d, ...p }));

  const audit = async () => {
    setLoading(true);
    setError(null);
    setResult(null); // clear any stale result before the real API call
    try {
      const body = await runAudit(buildRequest(doc));
      setResult(body);
    } catch (e) {
      // invalid input / cyclic netlist: old result is already cleared.
      setResult(null);
      setError({
        message: e.message,
        code: e.code,
        location: e.location,
      });
    } finally {
      setLoading(false);
    }
  };

  const errorGate = error?.location
    ? (error.location.match(/^[A-Za-z0-9_]+/) || [])[0]
    : null;

  return (
    <div className="app">
      <header className="topbar">
        <div>
          <h1>异步设备联锁审计台</h1>
          <p className="subtitle">
            整数惯性延迟 · 同刻成批求值 · 全延迟与交错穷举（无抽样 / 无连续近似）
          </p>
        </div>
        <span className={`health-dot ${health}`}>
          后端 {health === 'up' ? '在线' : health === 'checking' ? '探测中' : '离线'}
        </span>
      </header>

      <div className="layout">
        <div className="editor-col">
          <div className="example-row">
            <button
              className="btn small"
              onClick={() => {
                setDoc(HAZARD_EXAMPLE);
                setResult(null);
                setError(null);
              }}
            >
              载入冒险示例
            </button>
            <button
              className="btn small"
              onClick={() => {
                setDoc(SAFE_EXAMPLE);
                setResult(null);
                setError(null);
              }}
            >
              载入安全示例
            </button>
            <button
              className="btn small"
              onClick={() => audit()}
              disabled={loading}
            >
              {loading ? '审计中…' : '发起审计'}
            </button>
          </div>

          <InputEditor
            inputs={doc.inputs}
            onChange={(inputs) => patch({ inputs })}
          />
          <GateEditor
            gates={doc.gates}
            onChange={(gates) => patch({ gates })}
            highlightGate={errorGate}
          />
          <MonitorEditor
            monitors={doc.monitors}
            gates={doc.gates}
            onChange={(monitors) => patch({ monitors })}
          />
        </div>

        <div className="result-col">
          <ResultPanel
            result={result}
            error={error}
            onDismissError={() => setError(null)}
          />
          {!result && !error && (
            <section className="panel placeholder">
              <h2>等待审计</h2>
              <p>
                编辑左侧的初始/目标输入、无环门网表、各门整数延迟闭区间与监测输出，
                然后点击「发起审计」。请求会通过真实 HTTP API 提交给后端完整探索。
              </p>
              <ul>
                <li>零时刻所有输入成批切换。</li>
                <li>门采用惯性延迟：未到期事件会被后续求值取消或替换。</li>
                <li>同一时刻事件先整体生效，再对受影响门各求值一次。</li>
                <li>后端枚举区间内每个整数延迟及其全部事件交错，合并相同未来。</li>
              </ul>
            </section>
          )}
        </div>
      </div>
    </div>
  );
}
