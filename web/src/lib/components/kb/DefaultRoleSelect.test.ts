import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, screen, cleanup, fireEvent, waitFor } from '@testing-library/svelte';

vi.mock('$lib/api/client', async () => {
	const actual = await vi.importActual<typeof import('$lib/api/client')>('$lib/api/client');
	return {
		ApiError: actual.ApiError,
		api: { updateKBDefaultRole: vi.fn() }
	};
});

import { api, ApiError } from '$lib/api/client';
import DefaultRoleSelect from './DefaultRoleSelect.svelte';

const mockUpdate = vi.mocked(api.updateKBDefaultRole);

afterEach(() => {
	cleanup();
	vi.clearAllMocks();
});

function select(): HTMLSelectElement {
	return screen.getByRole('combobox') as HTMLSelectElement;
}

describe('DefaultRoleSelect', () => {
	it('shows a refusal as an error and returns to the stored value', async () => {
		mockUpdate.mockRejectedValue(new ApiError(409, 'KB is defined by hand in config.yaml'));
		const onSaved = vi.fn();
		render(DefaultRoleSelect, { kbName: 'kb', value: 'read', onSaved });

		await fireEvent.change(select(), { target: { value: 'none' } });

		await waitFor(() => expect(screen.getByRole('alert')).toBeTruthy());
		expect(screen.getByRole('alert').textContent).toContain('config.yaml');
		expect(screen.getByRole('alert').className).toContain('text-red-600');
		expect(select().value).toBe('read');
		expect(onSaved).not.toHaveBeenCalled();
	});

	it('reports success and keeps the new value', async () => {
		mockUpdate.mockResolvedValue({ ok: true, kb_name: 'kb', default_role: 'none' });
		const onSaved = vi.fn();
		render(DefaultRoleSelect, { kbName: 'kb', value: 'read', onSaved });

		await fireEvent.change(select(), { target: { value: 'none' } });

		await waitFor(() => expect(onSaved).toHaveBeenCalledWith('none'));
		expect(mockUpdate).toHaveBeenCalledWith('kb', 'none');
		expect(screen.getByRole('status').textContent).toContain('updated');
		expect(screen.queryByRole('alert')).toBeNull();
		expect(select().value).toBe('none');
	});

	it('sends null for the global role', async () => {
		mockUpdate.mockResolvedValue({ ok: true, kb_name: 'kb', default_role: null });
		render(DefaultRoleSelect, { kbName: 'kb', value: 'read' });
		await fireEvent.change(select(), { target: { value: '' } });
		await waitFor(() => expect(mockUpdate).toHaveBeenCalledWith('kb', null));
	});

	it('is disabled, with the reason, for a KB not editable here', () => {
		render(DefaultRoleSelect, { kbName: 'kb', value: 'read', editable: false });
		expect(select().disabled).toBe(true);
		expect(screen.getByTestId('role-locked').textContent).toContain('config.yaml');
	});

	it('is enabled for an editable KB', () => {
		render(DefaultRoleSelect, { kbName: 'kb', value: 'read' });
		expect(select().disabled).toBe(false);
		expect(screen.queryByTestId('role-locked')).toBeNull();
	});
});
