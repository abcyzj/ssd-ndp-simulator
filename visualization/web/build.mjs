import { build } from "esbuild";
import { copyFile, readFile, writeFile } from "node:fs/promises";

const assets = "../src/ndpsim_viz/assets";
const output = await build({
  entryPoints: ["dag.js"],
  bundle: true,
  minify: true,
  legalComments: "eof",
  supported: { "inline-script": false },
  write: false,
});
await writeFile(`${assets}/dag.js`, output.outputFiles[0].contents);
await copyFile("dag.html", `${assets}/dag.html`);
const licenses = await Promise.all([
  ["cytoscape", "LICENSE"],
  ["elkjs", "LICENSE.md"],
].map(async ([name, file]) => `${name}\n\n${await readFile(`node_modules/${name}/${file}`, "utf8")}`));
await writeFile(`${assets}/THIRD-PARTY-LICENSES.txt`, licenses.join("\n\n"));
