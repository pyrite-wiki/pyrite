import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, screen, fireEvent, cleanup } from '@testing-library/svelte';
import DeleteEntryButton from './DeleteEntryButton.svelte';

vi.mock('$lib/api/client', () => ({
	api: {
		deleteEntry: vi.fn()
	}
}));

vi.mock('$lib/stores/ui.svelte', () => ({
	uiStore: {
		toast: vi.fn()
	}
}));

import { api } from '$lib/api/client';
import { uiStore } from '$lib/stores/ui.svelte';

const mockDeleteEntry = vi.mocked(api.deleteEntry);
const mockToast = vi.mocked(uiStore.toast);

afterEach(() => {
	cleanup();
	vi.clearAllMocks();
});

function setup() {
	const onDeleted = vi.fn();
	render(DeleteEntryButton, { props: { entryId: 'hub', kbName: 'demo', onDeleted } });
	return { onDeleted };
}

describe('DeleteEntryButton', () => {
	it('renders a Delete button with an accessible name', () => {
		setup();
		expect(screen.getByRole('button', { name: 'Delete entry' })).toBeInTheDocument();
	});

	it('asks for confirmation in place instead of deleting immediately', async () => {
		setup();
		await fireEvent.click(screen.getByRole('button', { name: 'Delete entry' }));
		expect(screen.getByRole('button', { name: 'Confirm delete' })).toBeInTheDocument();
		expect(screen.getByRole('button', { name: 'Cancel delete' })).toBeInTheDocument();
		expect(mockDeleteEntry).not.toHaveBeenCalled();
	});

	it('cancel sends no request and restores the Delete button', async () => {
		setup();
		await fireEvent.click(screen.getByRole('button', { name: 'Delete entry' }));
		await fireEvent.click(screen.getByRole('button', { name: 'Cancel delete' }));
		expect(mockDeleteEntry).not.toHaveBeenCalled();
		expect(screen.getByRole('button', { name: 'Delete entry' })).toBeInTheDocument();
	});

	it('confirm calls deleteEntry with the entry id and kb, then calls onDeleted', async () => {
		const { onDeleted } = setup();
		mockDeleteEntry.mockResolvedValue({ deleted: true, id: 'hub' });
		await fireEvent.click(screen.getByRole('button', { name: 'Delete entry' }));
		await fireEvent.click(screen.getByRole('button', { name: 'Confirm delete' }));
		expect(mockDeleteEntry).toHaveBeenCalledWith('hub', 'demo');
		expect(onDeleted).toHaveBeenCalledTimes(1);
	});

	it('shows an error toast and stays on the entry when the API fails', async () => {
		const { onDeleted } = setup();
		mockDeleteEntry.mockRejectedValue(new Error('forbidden'));
		await fireEvent.click(screen.getByRole('button', { name: 'Delete entry' }));
		await fireEvent.click(screen.getByRole('button', { name: 'Confirm delete' }));
		expect(onDeleted).not.toHaveBeenCalled();
		expect(mockToast).toHaveBeenCalledWith('Could not delete entry', 'error');
		expect(screen.getByRole('button', { name: 'Delete entry' })).toBeInTheDocument();
	});
});
