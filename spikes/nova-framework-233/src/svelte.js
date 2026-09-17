import { mount as svMount, flushSync } from "svelte";
import Thread from "./Thread.svelte";
export function mount(container, store) { svMount(Thread, { target: container, props: { store } }); }
export { flushSync };
