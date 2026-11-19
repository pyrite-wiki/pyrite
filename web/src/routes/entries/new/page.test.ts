/** Page-level coverage for the New-entry form (#392, round-1 cold read).
 *
 * TypeChoice.test.ts already pins the picker's own contract in isolation;
 * these tests pin the PAGE's wiring around it -- the parts a reverted page
 * (even with a correct TypeChoice) could still get wrong: preselecting a
 * declared type itself, defaulting a template's undeclared type into
 * allow_undeclared, and submitting before schemas load or after they fail.
 */
import { describe, it, expect, vi, afterEach, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor, cleanup } from '@testing-library/svelte';

vi.mock('$app/navigation', () => ({ goto: vi.fn() }));

const mockKBStore = vi.hoisted(() => ({
	activeKB: 'sw' as string | null,
	load: vi.fn(async () => {})
}));
vi.mock('$lib/stores/kbs.svelte', () => ({ kbStore: mockKBStore }));

const mockUiStore = vi.hoisted(() => ({ toast: vi.fn() }));
vi.mock('$lib/stores/ui.svelte', () => ({ uiStore: mockUiStore }));

type TemplateSummary = { name: string; description: string; entry_type: string };

const mockApi = vi.hoisted(() => ({
	getTemplates: vi.fn(async () => ({ templates: [] as TemplateSummary[] })),
	getTypeSchemas: vi.fn(),
	renderTemplate: vi.fn(),
	createEntry: vi.fn()
}));
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
vi.mock('$lib/api/client', () => ({ api: mockApi, ApiError: MockApiError }));

import NewEntryPage from './+page.svelte';

const declaredSchemas = {
	adr: { description: 'Architecture decision', fields: {} },
	backlog_item: { description: 'Backlog item', fields: {} },
	component: { description: '', fields: {} },
	standard: { description: '', fields: {} },
	note: { description: '', fields: {} }
};
const declared = ['adr', 'backlog_item', 'component', 'standard'];

beforeEach(() => {
	vi.clearAllMocks();
	mockKBStore.activeKB = 'sw';
	mockApi.getTemplates.mockResolvedValue({ templates: [] });
});

afterEach(() => {
	cleanup();
});

describe('New-entry page (#392 round 1)', () => {
	it('does not preselect an arbitrary declared type: Create stays disabled until one is chosen', async () => {
		mockApi.getTypeSchemas.mockResolvedValue({ types: declaredSchemas, declared });
		render(NewEntryPage);

		await waitFor(() => {
			expect(screen.getByTestId('entry-type-select')).toBeInTheDocument();
		});

		// Move to the edit step via the blank-entry template option.
		const titleInput = screen.getByPlaceholderText('Entry title...');
		await fireEvent.input(titleInput, { target: { value: 'My Entry' } });
		const blank = screen.getAllByTestId('template-blank')[0];
		await fireEvent.click(blank);

		await waitFor(() => {
			expect(screen.getByText('Create')).toBeInTheDocument();
		});
		const createButton = screen.getByText('Create').closest('button') as HTMLButtonElement;
		expect(createButton.disabled).toBe(true);
	});

	it('enables Create once a declared type is explicitly chosen, and sends it with no allow_undeclared', async () => {
		mockApi.getTypeSchemas.mockResolvedValue({ types: declaredSchemas, declared });
		mockApi.createEntry.mockResolvedValue({ id: 'new-1', kb_name: 'sw', file_path: '' });
		render(NewEntryPage);

		await waitFor(() => expect(screen.getByTestId('entry-type-select')).toBeInTheDocument());

		const titleInput = screen.getByPlaceholderText('Entry title...');
		await fireEvent.input(titleInput, { target: { value: 'My Entry' } });
		await fireEvent.click(screen.getAllByTestId('template-blank')[0]);

		const select = screen.getByTestId('entry-type-select') as HTMLSelectElement;
		await fireEvent.change(select, { target: { value: 'adr' } });

		const createButton = screen.getByText('Create').closest('button') as HTMLButtonElement;
		expect(createButton.disabled).toBe(false);

		await fireEvent.click(createButton);

		await waitFor(() => expect(mockApi.createEntry).toHaveBeenCalled());
		const sent = mockApi.createEntry.mock.calls[0][0];
		expect(sent.entry_type).toBe('adr');
		expect(sent.allow_undeclared).toBeUndefined();
	});

	it('sends the Status input inside metadata, never as a top-level key the REST body refuses (#1090)', async () => {
		mockApi.getTypeSchemas.mockResolvedValue({ types: declaredSchemas, declared });
		mockApi.createEntry.mockResolvedValue({ id: 'new-1', kb_name: 'sw', file_path: '' });
		render(NewEntryPage);

		await waitFor(() => expect(screen.getByTestId('entry-type-select')).toBeInTheDocument());

		await fireEvent.input(screen.getByPlaceholderText('Entry title...'), {
			target: { value: 'My Entry' }
		});
		await fireEvent.click(screen.getAllByTestId('template-blank')[0]);
		await fireEvent.change(screen.getByTestId('entry-type-select') as HTMLSelectElement, {
			target: { value: 'adr' }
		});
		await fireEvent.input(screen.getByLabelText('Status'), { target: { value: ' accepted ' } });

		await fireEvent.click(screen.getByText('Create').closest('button') as HTMLButtonElement);

		await waitFor(() => expect(mockApi.createEntry).toHaveBeenCalled());
		const sent = mockApi.createEntry.mock.calls[0][0];
		expect(sent.metadata).toEqual({ status: 'accepted' });
		expect(Object.keys(sent).sort()).toEqual(['body', 'entry_type', 'kb', 'metadata', 'title']);
	});

	it('a template naming an undeclared type does not set allow_undeclared; submitting unmodified surfaces the server refusal', async () => {
		mockApi.getTypeSchemas.mockResolvedValue({ types: declaredSchemas, declared });
		mockApi.renderTemplate.mockResolvedValue({ body: 'templated body', entry_type: 'note' });
		mockApi.createEntry.mockRejectedValue(
			new MockApiError(400, "type 'note' is not declared in KB 'sw'.")
		);
		render(NewEntryPage);

		await waitFor(() => expect(screen.getByTestId('entry-type-select')).toBeInTheDocument());

		const titleInput = screen.getByPlaceholderText('Entry title...');
		await fireEvent.input(titleInput, { target: { value: 'Meeting notes' } });

		// No custom template fixtures are wired here; simulate the picker step
		// having already resolved a template by driving onTemplateSelect's
		// effect through the api mock directly is not exposed, so instead we
		// exercise the equivalent path via TemplatePicker's "template-option"
		// button when templates exist.
		mockApi.getTemplates.mockResolvedValue({
			templates: [{ name: 'Meeting', description: '', entry_type: 'note' }]
		});
		// Re-render with templates available.
		cleanup();
		render(NewEntryPage);
		await waitFor(() => expect(screen.getByTestId('entry-type-select')).toBeInTheDocument());
		await fireEvent.input(screen.getByPlaceholderText('Entry title...'), {
			target: { value: 'Meeting notes' }
		});
		await waitFor(() => expect(screen.getAllByTestId('template-option').length).toBeGreaterThan(0));
		await fireEvent.click(screen.getAllByTestId('template-option')[0]);

		await waitFor(() => expect(screen.getByText(/Type:/)).toBeInTheDocument());
		expect(screen.getByText('Type: note')).toBeInTheDocument();
		// The undeclared-type override must be visible (engaged) so the user
		// can see why, but must NOT have set allow_undeclared for them.
		const toggle = screen.getByTestId('allow-undeclared-toggle');
		expect(toggle).toHaveAttribute('aria-pressed', 'true');

		const createButton = screen.getByText('Create').closest('button') as HTMLButtonElement;
		// Not blocked by typeChoicePending (a template DID choose a type) --
		// submitting must go to the server, which refuses it.
		await fireEvent.click(createButton);

		await waitFor(() => expect(mockApi.createEntry).toHaveBeenCalled());
		const sent = mockApi.createEntry.mock.calls[0][0];
		expect(sent.allow_undeclared).toBeUndefined();
		await waitFor(() =>
			expect(mockUiStore.toast).toHaveBeenCalledWith(
				expect.stringContaining('not declared'),
				'error'
			)
		);
	});

	it('disables Create while type schemas are still loading', async () => {
		let resolveSchemas: (v: unknown) => void = () => {};
		mockApi.getTypeSchemas.mockReturnValue(
			new Promise((resolve) => {
				resolveSchemas = resolve;
			})
		);
		render(NewEntryPage);

		const titleInput = screen.getByPlaceholderText('Entry title...');
		await fireEvent.input(titleInput, { target: { value: 'My Entry' } });
		await fireEvent.click(screen.getAllByTestId('template-blank')[0]);

		const createButton = screen.getByText('Create').closest('button') as HTMLButtonElement;
		expect(createButton.disabled).toBe(true);

		resolveSchemas({ types: declaredSchemas, declared });
		await waitFor(() => expect(screen.getByTestId('entry-type-select')).toBeInTheDocument());
	});

	it('shows a retry control instead of a silent note default when schemas fail to load, and Create stays disabled meanwhile', async () => {
		mockApi.getTypeSchemas.mockRejectedValue(new Error('network down'));
		render(NewEntryPage);

		await waitFor(() => expect(screen.getByTestId('type-schemas-error')).toBeInTheDocument());
		expect(screen.queryByTestId('entry-type-select')).not.toBeInTheDocument();

		const titleInput = screen.getByPlaceholderText('Entry title...');
		await fireEvent.input(titleInput, { target: { value: 'My Entry' } });
		await fireEvent.click(screen.getAllByTestId('template-blank')[0]);

		const createButton = screen.getByText('Create').closest('button') as HTMLButtonElement;
		expect(createButton.disabled).toBe(true);

		mockApi.getTypeSchemas.mockResolvedValue({ types: declaredSchemas, declared });
		await fireEvent.click(screen.getByTestId('type-schemas-retry'));

		await waitFor(() => expect(screen.getByTestId('entry-type-select')).toBeInTheDocument());
		// Still disabled: schemas loaded, but no type has been chosen yet.
		expect(createButton.disabled).toBe(true);
	});
});
