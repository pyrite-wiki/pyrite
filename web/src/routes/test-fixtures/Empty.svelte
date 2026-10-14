<script lang="ts">
  let { children } = $props();
</script>
{#if children}{@render children()}{/if}
