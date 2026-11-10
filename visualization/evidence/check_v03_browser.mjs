import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { resolve } from "node:path";
import { pathToFileURL } from "node:url";
const root = resolve(process.argv[2]);
const [port] = (await readFile(root + "/chrome/DevToolsActivePort", "utf8")).split("\n");
const pages = await (await fetch(`http://127.0.0.1:${port}/json/list`)).json();
const socket = new WebSocket(pages.find(page => page.type === "page").webSocketDebuggerUrl);
await new Promise((resolve, reject) => { socket.onopen = resolve; socket.onerror = reject; });
const pending = new Map();
const exceptions = [];
const requests = [];
let next = 0;
socket.onmessage = ({ data }) => {
  const message = JSON.parse(data);
  if (message.method === "Runtime.exceptionThrown") exceptions.push(message.params);
  if (message.method === "Network.requestWillBeSent") requests.push(message.params.request.url);
  if (!message.id) return;
  const { resolve, reject } = pending.get(message.id);
  pending.delete(message.id);
  if (message.error) reject(new Error(JSON.stringify(message.error)));
  else resolve(message.result);
};
const call = (method, params = {}) => new Promise((resolve, reject) => {
  const id = ++next;
  pending.set(id, { resolve, reject });
  socket.send(JSON.stringify({ id, method, params }));
});
const evaluate = async expression => {
  const response = await call("Runtime.evaluate", { expression, returnByValue: true, awaitPromise: true });
  if (response.exceptionDetails) throw new Error(JSON.stringify(response.exceptionDetails));
  return response.result.value;
};
const wait = async (expression) => {
  for (let count = 0; count < 100; count++) {
    if (await evaluate(expression)) return;
    await new Promise(resolve => setTimeout(resolve, 200));
  }
  throw Error("Timed out: " + expression + ": " + await evaluate("document.querySelector('#status')?.textContent"));
};
const query = id => `document.getElementById('${id}')`;
async function search(text, option) {
  await evaluate(`(() => {
    const input = ${query("search")};
    input.value = ${JSON.stringify(text)};
    input.dispatchEvent(new Event("input"));
    const matches = ${query("matches")};
    matches.value = ${JSON.stringify(option)};
    if (!matches.value) throw Error("No matching option");
    matches.dispatchEvent(new Event("change"));
  })()`);
}
try {
  await call("Page.enable");
  await call("Runtime.enable");
  await call("Network.enable");
  await call("Network.setBlockedURLs", { urls: ["http://*", "https://*"] });
  await call("Emulation.setDeviceMetricsOverride", { width: 1280, height: 800, deviceScaleFactor: 1, mobile: false });
  await call("Page.navigate", { url: pathToFileURL(root + "/teaching.html").href });
  await wait(`${query("status")}?.textContent.includes("3 个节点 · 2 条可见边")`);
  await search("join", "op-2");
  assert.match(await evaluate(`${query("selection")}.textContent`), /join/);
  const edgeOptions = await evaluate(`${query("edges")}.options.length`);
  assert.equal(edgeOptions, 3);
  await evaluate(`(() => {
    const edges = ${query("edges")};
    edges.selectedIndex = 1;
    edges.dispatchEvent(new Event("change"));
  })()`);
  const deps = JSON.parse(await evaluate(`${query("details")}.textContent`));
  assert.equal(deps[0].kind, "DATA");
  assert.ok(["left", "right"].includes(deps[0].source.op));
  assert.deepEqual(deps[0].target, { query: "0", op: "join", stage: "sum" });
  await search("join", "op-2");
  await evaluate(`${query("toggle")}.click()`);
  await wait(`${query("key")}.value.includes('"stage":"sum"')`);
  assert.equal(await evaluate(`${query("key")}.value`), '{"query":"0","op":"join","stage":"sum"}');
  assert.equal(JSON.parse(await evaluate(`${query("details")}.textContent`)).timing, "未提供");
  await evaluate(`${query("collapse-all")}.click()`);
  await wait(`${query("key-label")}.hidden`);
  await call("Page.navigate", { url: pathToFileURL(root + "/formal.html").href });
  await wait(`${query("title")}?.textContent.includes("2579") && ${query("status")}?.textContent.includes("条可见边")`);
  const target = await evaluate(`JSON.parse(${query("graph-data")}.textContent).nodes
    .filter(node => node.op === "op2")`);
  assert.equal(target[0].operator, "graph.SCORE_L2");
  await search("SCORE_L2", target[0].id);
  await evaluate(`${query("toggle")}.click()`);
  await wait(`${query("key")}.value.includes('"op":"op2"')`);
  const kinds = new Set();
  for (const stage of target.slice(1)) {
    await search("op2", stage.id);
    const row = JSON.parse(await evaluate(`${query("details")}.textContent`));
    kinds.add(row.kind);
    assert.deepEqual(row.timing.map(item => item.run), ["B01", "C01"]);
    assert.deepEqual(row.timing.map(item => item.status), ["completed", "completed"]);
  }
  assert.deepEqual([...kinds].sort(), ["merge_append", "score_l2", "score_prepare"]);
  assert.deepEqual(exceptions, []);
  assert.ok(!requests.some(url => /^https?:/.test(url)));
  console.log(JSON.stringify({ chrome: await call("Browser.getVersion"), kinds: [...kinds], requests, exceptions }, null, 2));
} finally {
  socket.close();
}
