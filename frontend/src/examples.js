// Built-in example circuits.

export const HAZARD_EXAMPLE = {
  inputs: [{ name: 'a', initial: '1', target: '0' }],
  gates: [
    { name: 'g1', type: 'NOT', inputs: 'a', dmin: '1', dmax: '3' },
    { name: 'g2', type: 'OR', inputs: 'a g1', dmin: '2', dmax: '2' },
  ],
  monitors: ['g2'],
};

export const SAFE_EXAMPLE = {
  inputs: [
    { name: 'a', initial: '0', target: '1' },
    { name: 'b', initial: '0', target: '1' },
  ],
  gates: [
    { name: 'g1', type: 'BUF', inputs: 'a', dmin: '1', dmax: '2' },
    { name: 'g2', type: 'BUF', inputs: 'b', dmin: '1', dmax: '2' },
    { name: 'g3', type: 'AND', inputs: 'g1 g2', dmin: '1', dmax: '1' },
  ],
  monitors: ['g3'],
};

export function emptyDoc() {
  return { inputs: [], gates: [], monitors: [] };
}
