// Today's pattern in nova_public/app.js (renderAskThread): clear the container, rebuild all.
export function mount(container, store) {
  store.subscribe((messages) => {
    container.textContent = "";
    for (const m of messages) {
      const row = document.createElement("div");
      row.className = "msg" + (m.pending ? " pending" : "");
      row.dataset.id = m.id;
      row.textContent = m.sender + ": " + m.text;
      container.appendChild(row);
    }
  });
}
