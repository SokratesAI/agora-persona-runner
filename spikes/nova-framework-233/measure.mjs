import { JSDOM } from "jsdom";
const dom = new JSDOM(`<!doctype html><div id="thread"></div>`);
for (const k of ["window", "document", "Node", "HTMLElement", "Element", "Text", "Comment", "DocumentFragment", "MutationObserver", "getComputedStyle", "requestAnimationFrame", "navigator"]) {
  if (!(k in globalThis) || k === "navigator") try { globalThis[k] = k === "window" ? dom.window : dom.window[k]; } catch {}
}
globalThis.requestAnimationFrame ??= (f) => setTimeout(f, 0);
const results = {};
for (const v of ["vanilla", "preact", "svelte"]) {
  const mod = await import(`./dist/${v}.test.mjs`);
  const box = document.createElement("div"); document.body.appendChild(box);
  const store = mod.createStore();
  const flush = () => mod.flushSync ? mod.flushSync() : null;
  const srv = [1, 2, 3].map((i) => ({ id: "s" + i, sender: "Nova", text: "message " + i }));
  mod.mount(box, store); store.poll(srv); flush();
  const first = box.querySelector('[data-id="s1"]');
  // he selects text / a drawer is open on message 1: mark the live node
  first.setAttribute("data-open", "yes");
  let mutations = 0; const mo = new window.MutationObserver((l) => (mutations += l.length));
  mo.observe(box, { childList: true, subtree: true, characterData: true });
  // an ordinary 4 s poll that brings one new message
  store.poll(srv.concat({ id: "s4", sender: "Nova", text: "message 4" })); flush();
  await new Promise((r) => setTimeout(r, 0));
  mo.takeRecords().forEach(() => mutations++);
  const same = box.querySelector('[data-id="s1"]') === first && first.isConnected;
  // he sends; a poll that read the server before his write landed arrives
  const sent = store.send("hello"); flush();
  store.poll(srv.concat({ id: "s4", sender: "Nova", text: "message 4" })); flush();
  const survived = box.textContent.includes("hello");
  // the server echoes it with the client id: exactly one copy, not two
  store.poll(srv.concat({ id: "s4", sender: "Nova", text: "message 4" }, { id: "s5", clientId: sent.clientId, sender: "Edvard", text: "hello" })); flush();
  const copies = box.textContent.split("hello").length - 1;
  results[v] = { "msg 1 node kept across poll": same, "drawer state kept": first.getAttribute("data-open") === "yes" && same, "DOM mutations for +1 message": mutations, "sent msg survives stale poll": survived, "copies after echo": copies };
  mo.disconnect();
}
console.table(results);
