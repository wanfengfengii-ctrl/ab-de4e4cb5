export async function runAudit(payload) {
  const resp = await fetch("/api/audit", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  // the API always responds with a well-formed envelope
  const data = await resp.json();
  return data;
}

export const EXAMPLE_HAZARD = {
  inputs: ["a"],
  initial: { a: 0 },
  target: { a: 1 },
  gates: {
    g1: { type: "NOT", inputs: ["a"], delay_min: 1, delay_max: 2 },
    g2: { type: "AND", inputs: ["a", "g1"], delay_min: 1, delay_max: 1 },
  },
  monitors: ["g2"],
};

export const EXAMPLE_SAFE = {
  inputs: ["a", "b"],
  initial: { a: 0, b: 0 },
  target: { a: 1, b: 1 },
  gates: {
    n1: { type: "NOT", inputs: ["a"], delay_min: 1, delay_max: 3 },
    n2: { type: "NOT", inputs: ["b"], delay_min: 1, delay_max: 3 },
    o: { type: "AND", inputs: ["n1", "n2"], delay_min: 1, delay_max: 2 },
  },
  monitors: ["o"],
};
