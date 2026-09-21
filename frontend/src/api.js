// Thin client for the FastAPI audit service. All audit runs go through the
// real HTTP API — there is no client-side simulation fallback.

export async function fetchHealth() {
  const r = await fetch('/health');
  if (!r.ok) throw new Error(`health ${r.status}`);
  return r.json();
}

export async function runAudit(payload) {
  const r = await fetch('/api/audit', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  const body = await r.json().catch(() => ({}));
  if (!r.ok) {
    const err = new Error(body.error || `审计请求失败 (HTTP ${r.status})`);
    err.code = body.code || 'http_error';
    err.location = body.location || null;
    err.status = r.status;
    throw err;
  }
  return body;
}

// Convert the editable document into the API request shape.
export function buildRequest(doc) {
  const gates = {};
  const delays = {};
  for (const g of doc.gates) {
    gates[g.name] = {
      type: g.type,
      inputs: g.inputs.split(/[\s,]+/).filter(Boolean),
    };
    delays[g.name] = { min: Number(g.dmin), max: Number(g.dmax) };
  }
  const initial = {};
  const target = {};
  for (const i of doc.inputs) {
    initial[i.name] = Number(i.initial) ? 1 : 0;
    target[i.name] = Number(i.target) ? 1 : 0;
  }
  return {
    initial,
    target,
    gates,
    delays,
    monitors: doc.monitors.filter(Boolean),
  };
}
