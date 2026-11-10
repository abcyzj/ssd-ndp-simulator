import cytoscape from "cytoscape";
import ELK from "elkjs/lib/elk.bundled.js";
import { graphElements } from "./graph.js";

const byId = id => document.getElementById(id);
const data = JSON.parse(byId("graph-data").textContent);
const nodes = new Map(data.nodes.map(node => [node.id, node]));
const expanded = new Set();
let selected = null;
let layoutVersion = 0;

const elk = new ELK();
const cy = cytoscape({
  container: byId("graph"),
  minZoom: 0.02,
  maxZoom: 3,
  style: [
    { selector: "node", style: {
      label: "data(label)", shape: "round-rectangle", width: 180, height: 44,
      "background-color": "#d9e7fa", "border-width": 1, "border-color": "#5f80a9",
      "text-valign": "center", "text-halign": "center", "text-wrap": "ellipsis",
      "text-max-width": 169, color: "#173659", "font-size": 12,
    } },
    { selector: "node[group]", style: { "background-color": "#e4f3ee", "border-color": "#538c7a" } },
    { selector: "edge", style: {
      width: 2, "curve-style": "bezier", "line-color": "#7b8da4",
      "target-arrow-shape": "triangle", "target-arrow-color": "#7b8da4",
    } },
    { selector: ".upstream", style: {
      "border-width": 3, "border-color": "#996119", "line-color": "#996119",
      "target-arrow-color": "#996119",
    } },
    { selector: ".downstream", style: {
      "border-width": 3, "border-color": "#007661", "line-color": "#007661",
      "target-arrow-color": "#007661",
    } },
    { selector: ".chosen", style: {
      "border-width": 4, "border-color": "#194caf", "line-color": "#194caf",
      "target-arrow-color": "#194caf", width: ele => ele.isNode() ? 180 : 4,
    } },
  ],
});

function showNode(id, center = false) {
  const element = cy.getElementById(id);
  if (element.empty()) return;
  const node = nodes.get(id);
  selected = id;
  cy.elements().removeClass("chosen upstream downstream");
  element.predecessors().addClass("upstream");
  element.successors().addClass("downstream");
  element.addClass("chosen");
  if (center) {
    cy.zoom({ level: Math.max(cy.zoom(), 0.75), renderedPosition: element.renderedPosition() });
    cy.center(element);
  }
  byId("selection").textContent = node.label;
  byId("toggle").hidden = !node.stageCount && !node.group;
  byId("toggle").textContent = node.group ? "折叠算子" : "展开阶段";
  byId("key-label").hidden = !node.key;
  byId("key").value = node.key ? JSON.stringify(node.key) : "";
  const links = byId("edges");
  links.replaceChildren(new Option("查看原始依赖", ""));
  element.connectedEdges().forEach(edge => {
    const source = nodes.get(edge.data("source")).label;
    const target = nodes.get(edge.data("target")).label;
    links.add(new Option(`${source} → ${target}`, edge.id()));
  });
  byId("edge-label").hidden = links.options.length === 1;
  byId("details").textContent = JSON.stringify({
    operator: node.operator,
    ...(node.kind ? { kind: node.kind } : { stages: node.stageCount }),
    work: node.work,
    ...(node.features ? { features: node.features } : {}),
    timing: node.timings?.length ? node.timings : "未提供",
    dependency_ready_wait: "Unsupported",
  }, null, 2);
}

function showEdge(edge) {
  selected = null;
  cy.elements().removeClass("chosen upstream downstream");
  edge.addClass("chosen");
  byId("selection").textContent = "原始阶段依赖";
  byId("toggle").hidden = true;
  byId("key-label").hidden = true;
  byId("edge-label").hidden = true;
  byId("details").textContent = JSON.stringify(edge.data("dependencies").map(dep => ({
    kind: dep.kind,
    source: nodes.get(dep.source).key,
    source_port: dep.sourcePort,
    target: nodes.get(dep.target).key,
    target_port: dep.targetPort,
  })), null, 2);
}

async function render(focus) {
  const version = ++layoutVersion;
  const { visible, edges } = graphElements(data, expanded);
  byId("status").textContent = "正在布局…";
  try {
    const layout = await elk.layout({
      id: "dag",
      layoutOptions: {
        "elk.algorithm": "layered",
        "elk.direction": "RIGHT",
        "elk.spacing.nodeNode": "22",
        "elk.layered.spacing.nodeNodeBetweenLayers": "44",
      },
      children: visible.map(node => ({ id: node.id, width: 180, height: 44 })),
      edges: edges.map(edge => ({ id: edge.id, sources: [edge.source], targets: [edge.target] })),
    });
    if (version !== layoutVersion) return;
    const positions = new Map(layout.children.map(node => [
      node.id, { x: node.x + node.width / 2, y: node.y + node.height / 2 },
    ]));
    cy.batch(() => {
      cy.elements().remove();
      cy.add(visible.map(node => ({ group: "nodes", data: node, position: positions.get(node.id) })));
      cy.add(edges.map(edge => ({ group: "edges", data: edge })));
    });
    cy.fit(undefined, 24);
    byId("status").textContent = `${visible.length} 个节点 · ${edges.length} 条可见边 · 橙色为前驱，绿色为后继`;
    if (focus) showNode(focus, true);
    else {
      selected = null;
      byId("selection").textContent = "选择节点或边";
      byId("toggle").hidden = true;
      byId("key-label").hidden = true;
      byId("edge-label").hidden = true;
      byId("details").textContent = "点击节点查看 Work 和时序。点击边查看原始阶段依赖。";
    }
  } catch (error) {
    if (version === layoutVersion) byId("status").textContent = `布局失败：${error.message}`;
  }
}

function updateMatches() {
  const search = byId("search").value.trim().toLowerCase();
  const matches = byId("matches");
  matches.replaceChildren(new Option("选择匹配节点", ""));
  if (search) {
    for (const node of nodes.values()) {
      const text = `${node.label} ${node.operator} ${node.kind ?? ""} ${JSON.stringify(node.key ?? "")}`;
      if (text.toLowerCase().includes(search)) matches.add(new Option(node.label, node.id));
    }
  }
  matches.disabled = matches.options.length === 1;
}

function chooseMatch(id) {
  if (!id) return;
  const node = nodes.get(id);
  if (node.group && !expanded.has(node.group)) {
    expanded.add(node.group);
    render(id);
  } else if (node.stageCount && expanded.has(id)) {
    expanded.delete(id);
    render(id);
  } else {
    showNode(id, true);
  }
}

cy.on("tap", "node", event => showNode(event.target.id()));
cy.on("tap", "edge", event => showEdge(event.target));
byId("search").addEventListener("input", updateMatches);
byId("search").addEventListener("keydown", event => {
  if (event.key === "Enter") chooseMatch(byId("matches").options[1]?.value);
});
byId("matches").addEventListener("change", event => chooseMatch(event.target.value));
byId("edges").addEventListener("change", event => {
  if (event.target.value) showEdge(cy.getElementById(event.target.value));
});
byId("fit").addEventListener("click", () => cy.fit(undefined, 24));
byId("collapse-all").addEventListener("click", () => {
  expanded.clear();
  render(selected ? nodes.get(selected).group ?? selected : null);
});
byId("toggle").addEventListener("click", () => {
  if (!selected) return;
  const node = nodes.get(selected);
  if (node.group) {
    expanded.delete(node.group);
    render(node.group);
  } else {
    expanded.add(node.id);
    render(data.nodes.find(stage => stage.group === node.id).id);
  }
});
byId("copy").addEventListener("click", async () => {
  const field = byId("key");
  field.select();
  try {
    await navigator.clipboard.writeText(field.value);
  } catch {
    document.execCommand("copy");
  }
});

byId("title").textContent += ` · query ${data.query}`;
updateMatches();
render();
