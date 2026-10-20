<script lang="ts">
	import '../app.css';
	import Sidebar from '$lib/components/layout/Sidebar.svelte';
	import Toast from '$lib/components/common/Toast.svelte';
	import FeedbackButton from '$lib/components/common/FeedbackButton.svelte';
	import QuickSwitcher from '$lib/components/QuickSwitcher.svelte';
	import CommandPalette from '$lib/components/CommandPalette.svelte';
	import ChatSidebar from '$lib/components/ai/ChatSidebar.svelte';
	import KeyboardShortcutsModal from '$lib/components/common/KeyboardShortcutsModal.svelte';
	import ConnectionStatus from '$lib/components/common/ConnectionStatus.svelte';
	import { kbStore } from '$lib/stores/kbs.svelte';
	import { uiStore } from '$lib/stores/ui.svelte';
	import { wsClient } from '$lib/api/websocket';
	import { entryStore } from '$lib/stores/entries.svelte';
	import { authStore } from '$lib/stores/auth.svelte';
	import { brandStore } from '$lib/stores/brand.svelte';
	import { useStarred } from '$lib/stores/starred.svelte';
	import { registerShortcut } from '$lib/utils/keyboard';
	import { goto } from '$app/navigation';
	import { page } from '$app/stores';
	import { onMount, untrack } from 'svelte';
	import { fade } from 'svelte/transition';
	import { UNTITLED_ROUTES } from './brand-title-routes';

	let { children } = $props();

	let shortcutsOpen = $state(false);

	const starredStore = useStarred();

	const AUTH_ROUTES = ['/login', '/register'];

	onMount(() => {
		// Load branding early — non-blocking; display-only fallback is fine.
		brandStore.init();
		// Initialize auth before loading KBs — session cookie must be
		// available before API calls or they'll 401 when anonymous_tier=none
		authStore.init().then(() => {
			const path = $page.url.pathname;
			const isAuthRoute = AUTH_ROUTES.includes(path);

			if (authStore.authConfig.enabled && !authStore.isAuthenticated && !authStore.allowsAnonymous && !isAuthRoute) {
				goto('/login');
				return;
			}
			if (authStore.authConfig.enabled && authStore.isAuthenticated && isAuthRoute) {
				goto('/');
				return;
			}
		});

		// WebSocket for multi-tab awareness. Opened (and reopened) by the
		// $effect below, which follows the signed-in user.
		const unregisterWS = wsClient.onEvent((event) => {
			if (event.type === 'entry_updated' || event.type === 'entry_deleted') {
				// If viewing the changed entry, auto-reload
				if (
					entryStore.current &&
					entryStore.current.id === event.entry_id &&
					(!event.kb_name || entryStore.current.kb_name === event.kb_name)
				) {
					if (event.type === 'entry_updated') {
						entryStore.loadEntry(event.entry_id, event.kb_name);
						uiStore.toast(`"${event.entry_id}" was updated in another tab`, 'info');
					} else {
						uiStore.toast(`"${event.entry_id}" was deleted in another tab`, 'info');
					}
				}
			} else if (event.type === 'entry_created') {
				uiStore.toast(`New entry created: ${event.entry_id}`, 'info');
			} else if (event.type === 'kb_synced') {
				uiStore.toast('Knowledge base synced', 'info');
			}
		});

		// Global: Cmd+D / Ctrl+D navigates to today's daily note
		const unregisterDaily = registerShortcut('d', ['mod'], () => {
			goto('/daily');
		});

		// Global: Cmd+Shift+K toggles AI chat sidebar
		const unregisterChat = registerShortcut('k', ['mod', 'shift'], () => {
			uiStore.toggleChatPanel();
		});

		// Global: Cmd+/ toggles sidebar
		const unregisterSidebar = registerShortcut('/', ['mod'], () => {
			uiStore.toggleSidebar();
		});

		// Global: ? opens keyboard shortcuts overlay
		function handleQuestionMark(e: KeyboardEvent) {
			if (e.key !== '?') return;
			const target = e.target as HTMLElement;
			if (
				target.tagName === 'INPUT' ||
				target.tagName === 'TEXTAREA' ||
				target.isContentEditable
			) {
				return;
			}
			shortcutsOpen = !shortcutsOpen;
		}
		window.addEventListener('keydown', handleQuestionMark);

		return () => {
			wsClient.disconnect();
			unregisterWS();
			unregisterDaily();
			unregisterChat();
			unregisterSidebar();
			window.removeEventListener('keydown', handleQuestionMark);
		};
	});

	// The live-update socket follows the signed-in user (#336). The server
	// fixes a socket's readable KBs at handshake, so a login, logout or change
	// of user must close the socket and open a new one; `follow` is a no-op
	// while the identity is unchanged. Null (loading, or signed out where no
	// anonymous reader is admitted) means no socket at all.
	$effect(() => {
		wsClient.follow(authStore.socketIdentity);
	});

	// A client-side login does not remount this layout. Load the readable
	// session's data when auth is ready, and once again if its identity changes.
	// Route changes and reactive reads inside load() must not trigger reloads.
	let loadedIdentity: string | null = null;
	$effect(() => {
		const identity = authStore.socketIdentity;
		if (identity === loadedIdentity) return;
		loadedIdentity = identity;
		if (identity === null) return;
		untrack(() => {
			void kbStore.load();
			void starredStore.load();
		});
	});

	// Push the brand color into the DOM as it loads.
	$effect(() => {
		if (typeof document === 'undefined') return;
		document.documentElement.style.setProperty('--brand-primary', brandStore.primary_color);
	});
</script>

<!--
  #49: a brand-name default document.title, for the routes that declare
  none of their own (UNTITLED_ROUTES, pinned against the routes on disk by
  untitled-routes.test.ts). Svelte compiles a <title> tag to a bare
  document.title = … assignment inside an effect -- there is no shared
  element a later-mounting title "wins" against, so the only way this
  layout can guarantee it never clobbers a route's own title is to render
  NO title effect at all when the route has one. An earlier version of
  this fix instead wrote an unconditional title guarded by comparing
  document.title to a navigation snapshot; a same-route re-render (e.g.
  entries/+page.svelte's goto(url, { replaceState: true }) on every filter
  change) doesn't re-run the destination's static <title> effect, so that
  comparison can't tell "re-claimed the same title" from "claimed
  nothing" and clobbered it. See the conductor review on PR #202.
-->
<svelte:head>
	{#if UNTITLED_ROUTES.includes($page.route.id ?? '')}
		<title>{brandStore.name}</title>
	{/if}
</svelte:head>

{#if authStore.loading}
	<div class="flex h-screen items-center justify-center bg-zinc-900">
		<p class="text-zinc-400">Loading...</p>
	</div>
{:else if authStore.authConfig.enabled && !authStore.isAuthenticated && !authStore.allowsAnonymous && !AUTH_ROUTES.includes($page.url.pathname)}
	<div class="flex h-screen items-center justify-center bg-zinc-900">
		<p class="text-zinc-400">Redirecting to login...</p>
	</div>
{:else if AUTH_ROUTES.includes($page.url.pathname)}
	{@render children()}
{:else}
	<div class="flex h-screen overflow-hidden">
		<Sidebar />
		<main class="flex flex-1 flex-col overflow-hidden">
			{#key $page.url.pathname}
				<div class="flex min-h-0 flex-1 flex-col" in:fade={{ duration: 150, delay: 50 }} out:fade={{ duration: 100 }}>
					{@render children()}
				</div>
			{/key}
		</main>
		{#if uiStore.chatPanelOpen}
			<ChatSidebar />
		{/if}
	</div>
{/if}

<Toast />
<FeedbackButton />
<QuickSwitcher />
<CommandPalette />
<KeyboardShortcutsModal open={shortcutsOpen} onclose={() => (shortcutsOpen = false)} />
<ConnectionStatus />
