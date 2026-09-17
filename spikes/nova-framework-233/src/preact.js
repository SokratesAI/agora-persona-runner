// Preact + htm: no build step, keyed diffing.
import { h, render } from "preact";
import htm from "htm";
const html = htm.bind(h);
const Thread = ({ messages }) => html`${messages.map((m) => html`
  <div key=${m.clientId || m.id} class=${"msg" + (m.pending ? " pending" : "")} data-id=${m.id}>${m.sender}: ${m.text}</div>`)}`;
export function mount(container, store) {
  store.subscribe((messages) => render(html`<${Thread} messages=${messages} />`, container));
}
