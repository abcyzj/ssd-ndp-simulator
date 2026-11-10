// Aggregate only the displayed endpoints; retain every explicit stage edge.
export function graphElements(data, expanded) {
  const representative = new Map();
  const visible = [];
  for (const node of data.nodes) {
    if (!node.group) {
      if (!expanded.has(node.id)) visible.push(node);
    } else if (expanded.has(node.group)) {
      visible.push(node);
      representative.set(node.id, node.id);
    } else {
      representative.set(node.id, node.group);
    }
  }
  const groups = new Map();
  for (const edge of data.edges) {
    const source = representative.get(edge.source);
    const target = representative.get(edge.target);
    if (source === target) continue;
    const pair = JSON.stringify([source, target]);
    if (!groups.has(pair)) {
      groups.set(pair, { id: `edge-${groups.size}`, source, target, dependencies: [] });
    }
    groups.get(pair).dependencies.push(edge);
  }
  return { visible, edges: [...groups.values()] };
}
