<script>
  let { store } = $props();
  let messages = $state([]);
  store.subscribe((m) => { messages = m; });
</script>
{#each messages as m (m.clientId || m.id)}
  <div class={"msg" + (m.pending ? " pending" : "")} data-id={m.id}>{m.sender}: {m.text}</div>
{/each}
