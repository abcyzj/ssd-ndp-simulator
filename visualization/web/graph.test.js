import assert from "node:assert/strict";
import test from "node:test";
import { graphElements } from "./graph.js";

const nodes = [
  { id: "left" }, { id: "left-a", group: "left" }, { id: "left-b", group: "left" },
  { id: "right" }, { id: "right-a", group: "right" },
  { id: "join" }, { id: "join-a", group: "join" }, { id: "join-b", group: "join" },
];
const first = { source: "left-a", target: "join-a", kind: "DATA", sourcePort: "out", targetPort: "in" };
const second = { source: "left-b", target: "join-b", kind: "ORDER" };
const right = { source: "right-a", target: "join-a", kind: "CONTROL" };
const internal = { source: "join-a", target: "join-b", kind: "ADDRESS" };
const data = { nodes, edges: [first, second, right, internal] };

test("default folds fork/join and preserves every dependency for an edge", () => {
  const { visible, edges } = graphElements(data, new Set());
  assert.deepEqual(visible.map(node => node.id), ["left", "right", "join"]);
  assert.deepEqual(edges.map(edge => [edge.source, edge.target, edge.dependencies]), [
    ["left", "join", [first, second]], ["right", "join", [right]],
  ]);
});

test("expanded operators show stages and internal dependencies", () => {
  const { visible, edges } = graphElements(data, new Set(["join"]));
  assert.deepEqual(visible.map(node => node.id), ["left", "right", "join-a", "join-b"]);
  assert.deepEqual(edges.map(edge => [edge.source, edge.target, edge.dependencies]), [
    ["left", "join-a", [first]], ["left", "join-b", [second]],
    ["right", "join-a", [right]], ["join-a", "join-b", [internal]],
  ]);
});
