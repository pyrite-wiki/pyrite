import { beforeEach, afterEach, describe, it, expect, vi } from 'vitest';
import { render, cleanup, waitFor } from '@testing-library/svelte';
import { writable } from 'svelte/store';
vi.mock('$app/navigation', () => ({ goto: vi.fn() }));
// jsdom has no Web Animations API; transition timing is outside this regression.
vi.mock('svelte/transition', () => ({ fade: () => ({ duration: 0 }) }));
vi.mock('$app/stores', async () => {
  const { writable } = await import('svelte/store');
  return { page: writable({ url: new URL('http://localhost/entries'), route: { id: '/entries' }, params: {} }) };
});
vi.mock('$lib/api/client', () => ({ api: {
  getAuthConfig: vi.fn(), getMe: vi.fn(), login: vi.fn(), onUnauthorized: vi.fn(),
  listKBs: vi.fn().mockResolvedValue({ kbs: [{ name: 'private' }], total: 1 }),
  getStarredEntries: vi.fn().mockResolvedValue({ starred: [] })
} }));
vi.mock('$lib/api/websocket', () => ({ wsClient: { follow: vi.fn(), onEvent: vi.fn(() => vi.fn()), disconnect: vi.fn() } }));
vi.mock('$lib/utils/keyboard', () => ({ registerShortcut: vi.fn(() => vi.fn()) }));
vi.mock('$lib/stores/brand.svelte', () => ({ brandStore: { init: vi.fn(), primary_color: '#000', name: 'Pyrite' } }));
vi.mock('$lib/components/layout/Sidebar.svelte', async () => ({ default: (await import('./test-fixtures/Empty.svelte')).default }));
vi.mock('$lib/components/common/Toast.svelte', async () => ({ default: (await import('./test-fixtures/Empty.svelte')).default }));
vi.mock('$lib/components/common/FeedbackButton.svelte', async () => ({ default: (await import('./test-fixtures/Empty.svelte')).default }));
vi.mock('$lib/components/QuickSwitcher.svelte', async () => ({ default: (await import('./test-fixtures/Empty.svelte')).default }));
vi.mock('$lib/components/CommandPalette.svelte', async () => ({ default: (await import('./test-fixtures/Empty.svelte')).default }));
vi.mock('$lib/components/ai/ChatSidebar.svelte', async () => ({ default: (await import('./test-fixtures/Empty.svelte')).default }));
vi.mock('$lib/components/common/KeyboardShortcutsModal.svelte', async () => ({ default: (await import('./test-fixtures/Empty.svelte')).default }));
vi.mock('$lib/components/common/ConnectionStatus.svelte', async () => ({ default: (await import('./test-fixtures/Empty.svelte')).default }));
import { api } from '$lib/api/client';
import { authStore } from '$lib/stores/auth.svelte';
import { kbStore } from '$lib/stores/kbs.svelte';
import { page } from '$app/stores';
import { goto } from '$app/navigation';
import LayoutHarness from './test-fixtures/LayoutHarness.svelte';
const alice = { id: 1, username: 'alice', display_name: 'Alice', role: 'read' as const, auth_provider: 'local', avatar_url: null, kb_permissions: {} };
const config = { enabled: true, allow_registration: false, require_invite_code: false, providers: [], anonymous_tier: 'none' as const };
function route(path: string) { (page as unknown as ReturnType<typeof writable>).set({ url: new URL(`http://localhost${path}`), route: { id: path }, params: {} }); }
beforeEach(() => {
  vi.clearAllMocks(); authStore.user = null; authStore.loading = true;
  authStore.authConfig = { ...config }; kbStore.kbs = []; kbStore.activeKB = null; kbStore.initialized = false;
  vi.mocked(api.getAuthConfig).mockResolvedValue({ ...config });
  vi.mocked(api.getMe).mockRejectedValue(new Error('signed out'));
  vi.mocked(api.login).mockResolvedValue(alice); route('/entries');
});
afterEach(cleanup);
describe('layout KB loading follows the readable session', () => {
  it('loads the real KB store after sign-in without remounting', async () => {
    render(LayoutHarness); await waitFor(() => expect(goto).toHaveBeenCalledWith('/login'));
    expect(api.listKBs).not.toHaveBeenCalled(); route('/login');
    await authStore.login('alice', 'password'); route('/entries');
    await waitFor(() => expect(kbStore.initialized).toBe(true));
    expect(kbStore.kbs[0].name).toBe('private'); expect(api.getStarredEntries).toHaveBeenCalledTimes(1);
    route('/graph'); await Promise.resolve(); expect(api.listKBs).toHaveBeenCalledTimes(1);
  });
  it('loads on an authenticated initial login route even though it redirects', async () => {
    route('/login'); vi.mocked(api.getMe).mockResolvedValue(alice); render(LayoutHarness);
    await waitFor(() => expect(goto).toHaveBeenCalledWith('/'));
    await waitFor(() => expect(api.listKBs).toHaveBeenCalledTimes(1));
  });
  it('reloads when the signed-in user changes', async () => {
    vi.mocked(api.getMe).mockResolvedValue(alice); render(LayoutHarness);
    await waitFor(() => expect(api.listKBs).toHaveBeenCalledTimes(1));
    authStore.user = { ...alice, id: 2 };
    await waitFor(() => expect(api.listKBs).toHaveBeenCalledTimes(2));
  });
  it('does not load protected KBs after a failed login', async () => {
    render(LayoutHarness); await waitFor(() => expect(authStore.loading).toBe(false));
    vi.mocked(api.login).mockRejectedValue(new Error('bad credentials'));
    await expect(authStore.login('alice','bad')).rejects.toThrow('bad credentials');
    expect(api.listKBs).not.toHaveBeenCalled();
  });
  it.each([false,true])('loads when anonymous access is allowed (auth enabled=%s)', async enabled => {
    vi.mocked(api.getAuthConfig).mockResolvedValue({ ...config, enabled, anonymous_tier: 'read' });
    render(LayoutHarness); await waitFor(() => expect(api.listKBs).toHaveBeenCalledTimes(1));
  });
});
