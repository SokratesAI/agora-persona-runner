import * as esbuild from "esbuild";
import { compile } from "svelte/compiler";
import fs from "node:fs"; import zlib from "node:zlib";
const sveltePlugin = { name: "svelte", setup(b) { b.onLoad({ filter: /\.svelte$/ }, async (a) => {
  const r = compile(fs.readFileSync(a.path, "utf8"), { filename: a.path, generate: "client" });
  return { contents: r.js.code, loader: "js" }; }); } };
fs.mkdirSync("dist", { recursive: true });
for (const v of ["vanilla", "preact", "svelte"]) {
  // app entry: what the phone would actually download for this surface
  fs.writeFileSync(`src/entry-${v}.js`, `import { createStore } from "./store.js"; import { mount } from "./${v}.js"; const s = createStore(); mount(document.getElementById("thread"), s); window.__store = s;`);
  await esbuild.build({ entryPoints: [`src/entry-${v}.js`], bundle: true, minify: true, format: "esm", platform: "browser", conditions: ["browser", "production"], define: { "process.env.NODE_ENV": "\"production\"" }, outfile: `dist/${v}.min.js`, plugins: [sveltePlugin], logLevel: "error" });
  // test build: unminified, exports for the jsdom harness
  await esbuild.build({ stdin: { contents: `export { createStore } from "./store.js"; export * from "./${v}.js";`, resolveDir: "src" }, bundle: true, format: "esm", platform: "browser", conditions: ["browser"], outfile: `dist/${v}.test.mjs`, plugins: [sveltePlugin], logLevel: "error" });
  const buf = fs.readFileSync(`dist/${v}.min.js`);
  console.log(v.padEnd(8), "min", buf.length, "bytes, gzip", zlib.gzipSync(buf, { level: 9 }).length, "bytes");
}
