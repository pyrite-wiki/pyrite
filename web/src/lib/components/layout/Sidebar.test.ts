/**
 * The sidebar's "Log out" goes through the auth store (#336).
 *
 * The root layout reopens the live-update socket when
 * `authStore.socketIdentity` changes. A logout that called `api.logout()`
 * directly left `authStore.user` holding the old user, so the socket -- and
 * the old user's scope -- survived the logout.
 */
import { describe, it, expect, vi, afterEach, beforeEach } from 'vitest';
import { render, screen, cleanup, fireEvent, waitFor } from '@testing-library/svelte';

vi.mock('$app/navigation', () => ({ goto: vi.fn() }));

vi.mock('$app/state', async () => {
	const { createPageState } = await import('$lib/test-utils/page-state.svelte');
	return {
		page: createPageState({ url: new URL('http://localhost/entries'), params: {}, route: { id: '/entries' } })
	};
});

vi.mock('$lib/api/client', () => ({
	api: {
		getAuthConfig: vi.fn(),
		getMe: vi.fn(),
		logout: vi.fn(),
		listKBs: vi.fn().mockResolvedValue({ kbs: [], total: 0 }),
		getStarred: vi.fn().mockResolvedValue({ starred: [], count: 0 }),
		getBranding: vi.fn().mockResolvedValue({}),
		// auth.svelte.ts registers a handler on import (#420); a mock missing
		// this throws as soon as the store module loads.
		onUnauthorized: vi.fn()
	}
}));

import { goto } from '$app/navigation';
import { api } from '$lib/api/client';
import { authStore } from '$lib/stores/auth.svelte';
import Sidebar from './Sidebar.svelte';

const alice = {
	id: 1,
	username: 'alice',
	display_name: 'Alice',
	role: 'read' as const,
	auth_provider: 'local',
	avatar_url: null,
	kb_permissions: {}
};

beforeEach(() => {
	vi.mocked(api.getAuthConfig).mockResolvedValue({
		enabled: true,
		allow_registration: false,
		require_invite_code: false,
		providers: [],
		anonymous_tier: 'none'
	});
	vi.mocked(api.getMe).mockResolvedValue(alice);
	vi.mocked(api.logout).mockResolvedValue(undefined as never);
	authStore.loading = false;
	authStore.authConfig = {
		enabled: true,
		allow_registration: false,
		require_invite_code: false,
		providers: [],
		anonymous_tier: 'none'
	};
	authStore.user = alice;
});

afterEach(() => {
	cleanup();
	vi.clearAllMocks();
	authStore.user = null;
});

describe('Sidebar logout', () => {
	it('clears authStore.user, so the socket identity becomes null', async () => {
		render(Sidebar);
		const button = await screen.findByRole('button', { name: 'Log out' });

		await fireEvent.click(button);

		await waitFor(() => expect(goto).toHaveBeenCalledWith('/login'));
		expect(api.logout).toHaveBeenCalledTimes(1);
		expect(authStore.user).toBeNull();
		expect(authStore.socketIdentity).toBeNull();
	});
});
