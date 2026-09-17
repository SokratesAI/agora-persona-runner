// One store for all three renderers, so the comparison is rendering only.
// A poll replaces what the server said; a message he sent that the server has
// not echoed yet stays pending and is drawn after it. That merge is what stops
// the "message vanishes after send" race -- no framework does it for you.
export function createStore() {
  let server = [], pending = [], subs = [];
  const emit = () => subs.forEach((f) => f(view()));
  const view = () => {
    const seen = new Set(server.map((m) => m.clientId).filter(Boolean));
    return server.concat(pending.filter((p) => !seen.has(p.clientId)));
  };
  return {
    subscribe(f) { subs.push(f); f(view()); },
    poll(messages) { server = messages; emit(); },
    send(text) {
      const m = { id: "local-" + Date.now() + Math.random(), clientId: "c" + Math.random(), sender: "Edvard", text, pending: true };
      pending.push(m); emit(); return m;
    },
  };
}
