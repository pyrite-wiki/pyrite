import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, screen, fireEvent, cleanup, waitFor } from '@testing-library/svelte';
import DeleteEntryButton from './DeleteEntryButton.svelte';

const MockApiError = vi.hoisted(() => {
	return class MockApiError extends Error {
		status: number;
		detail: string;
		constructor(status: number, detail: string) {
			super(`API Error ${status}: ${detail}`);
			this.status = status;
			this.detail = detail;
		}
	};
});
vi.mock('$lib/api/client', () => ({
	api: {
		deleteEntry: vi.fn()
	},
	ApiError: MockApiError
}));

vi.mock('$lib/stores/ui.svelte', () => ({
	uiStore: {
		toast: vi.fn()
	}
}));

import { api, ApiError } from '$lib/api/client';
import { uiStore } from '$lib/stores/ui.svelte';

const mockDeleteEntry = vi.mocked(api.deleteEntry);
const mockToast = vi.mocked(uiStore.toast);

afterEach(() => {
	cleanup();
	vi.clearAllMocks();
});

function setup(overrides: { kbName?: string } = {}) {
	const onDeleted = vi.fn();
	render(DeleteEntryButton, {
		props: {
			entryId: 'hub',
			kbName: 'demo',
			entryTitle: 'Hub Design',
			onDeleted,
			...overrides
		}
	});
	return { onDeleted };
}

describe('DeleteEntryButton', () => {
	it('renders a Delete button with an accessible name', () => {
		setup();
		expect(screen.getByRole('button', { name: 'Delete' })).toBeInTheDocument();
	});

	it('keeps Delete in place, disabled, beside the confirm step', async () => {
		setup();
		const del = screen.getByRole('button', { name: 'Delete' });
		await fireEvent.click(del);
		const kept = screen.getByRole('button', { name: 'Delete' });
		expect(kept).toBe(del);
		expect(kept).toBeDisabled();
		expect(screen.getByRole('button', { name: 'Confirm delete' })).toBeInTheDocument();
		expect(screen.getByRole('button', { name: 'Cancel' })).toBeInTheDocument();
		expect(mockDeleteEntry).not.toHaveBeenCalled();
	});

	it('a double-click on Delete cannot confirm: the second click lands on a disabled button', async () => {
		setup();
		const del = screen.getByRole('button', { name: 'Delete' });
		await fireEvent.click(del);
		await fireEvent.click(del);
		await fireEvent.click(del);
		expect(mockDeleteEntry).not.toHaveBeenCalled();
	});

	it('names the entry and KB being deleted in the confirm step', async () => {
		setup();
		await fireEvent.click(screen.getByRole('button', { name: 'Delete' }));
		expect(screen.getByText(/Hub Design/)).toBeInTheDocument();
		expect(screen.getByText(/demo/)).toBeInTheDocument();
	});

	it('keeps the KB readable and carries the full value in title', async () => {
		setup({ kbName: 'a-very-long-knowledge-base-name-that-would-be-cut-off' });
		await fireEvent.click(screen.getByRole('button', { name: 'Delete' }));
		const kb = screen.getByText('a-very-long-knowledge-base-name-that-would-be-cut-off');
		// The KB is named outside the truncating element, so a long one is
		// still announced in full rather than cut to an ellipsis.
		expect(kb).toHaveAttribute('title', 'a-very-long-knowledge-base-name-that-would-be-cut-off');
		expect(kb.className).not.toContain('truncate');
	});

	it('opens the step with focus on the prompt, never on Confirm', async () => {
		setup();
		const del = screen.getByRole('button', { name: 'Delete' });
		await fireEvent.click(del);
		// Enter-Enter is the hazard: if focus landed on Confirm, the reader's
		// second Enter would delete the entry without reading the prompt. The
		// step is not actionable, so Enter there does nothing.
		expect(document.activeElement).not.toBe(
			screen.getByRole('button', { name: 'Confirm delete' })
		);
		expect(document.activeElement).toHaveAccessibleName('Confirm deleting this entry');
	});

	it('keeps the confirm step out of the toolbar flow', async () => {
		setup();
		await fireEvent.click(screen.getByRole('button', { name: 'Delete' }));
		const step = screen.getByRole('group', { name: 'Confirm deleting this entry' });
		// Absolutely positioned, so opening the step cannot reflow the row the
		// reader is on. The Delete button's own box is unchanged.
		expect(step.className).toContain('absolute');
	});

	it('confirm calls deleteEntry with the entry id and kb, then hands that kb back', async () => {
		const { onDeleted } = setup();
		mockDeleteEntry.mockResolvedValue({ deleted: true, id: 'hub' });
		await fireEvent.click(screen.getByRole('button', { name: 'Delete' }));
		await fireEvent.click(screen.getByRole('button', { name: 'Confirm delete' }));
		await waitFor(() => expect(onDeleted).toHaveBeenCalledTimes(1));
		expect(mockDeleteEntry).toHaveBeenCalledWith('hub', 'demo');
		// The KB the request named, so the caller navigates to the KB the entry
		// was actually removed from.
		expect(onDeleted).toHaveBeenCalledWith('demo');
	});

	it('cancel sends no request, restores Delete, and returns focus to it', async () => {
		setup();
		await fireEvent.click(screen.getByRole('button', { name: 'Delete' }));
		await fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));
		expect(mockDeleteEntry).not.toHaveBeenCalled();
		const del = screen.getByRole('button', { name: 'Delete' });
		expect(del).toBeEnabled();
		expect(document.activeElement).toBe(del);
	});

	it('a second click while deleting cannot fire a second request', async () => {
		const { onDeleted } = setup();
		let release!: (v: { deleted: boolean; id: string }) => void;
		mockDeleteEntry.mockImplementation(
			() => new Promise((resolve) => (release = resolve))
		);
		await fireEvent.click(screen.getByRole('button', { name: 'Delete' }));
		const confirm = screen.getByRole('button', { name: 'Confirm delete' });
		await fireEvent.click(confirm);
		await fireEvent.click(confirm);
		await fireEvent.click(confirm);
		expect(mockDeleteEntry).toHaveBeenCalledTimes(1);
		release({ deleted: true, id: 'hub' });
		await waitFor(() => expect(onDeleted).toHaveBeenCalledTimes(1));
	});

	it('shows the server reason on refusal and keeps the reader on the entry', async () => {
		const { onDeleted } = setup();
		mockDeleteEntry.mockRejectedValue(new ApiError(403, 'not yours to delete'));
		await fireEvent.click(screen.getByRole('button', { name: 'Delete' }));
		await fireEvent.click(screen.getByRole('button', { name: 'Confirm delete' }));
		await waitFor(() =>
			expect(mockToast).toHaveBeenCalledWith('Could not delete: not yours to delete', 'error')
		);
		expect(onDeleted).not.toHaveBeenCalled();
		expect(screen.getByRole('button', { name: 'Delete' })).toBeEnabled();
	});

	it('treats a 404 as already deleted: toast, then leave the entry', async () => {
		const { onDeleted } = setup();
		mockDeleteEntry.mockRejectedValue(new ApiError(404, 'gone'));
		await fireEvent.click(screen.getByRole('button', { name: 'Delete' }));
		await fireEvent.click(screen.getByRole('button', { name: 'Confirm delete' }));
		await waitFor(() => expect(onDeleted).toHaveBeenCalledTimes(1));
		expect(mockToast).toHaveBeenCalledWith('Entry was already deleted', 'info');
	});
});
