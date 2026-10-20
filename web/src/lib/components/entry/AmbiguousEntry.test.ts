import { afterEach, expect, it } from 'vitest';
import { cleanup, render, screen } from '@testing-library/svelte';
import AmbiguousEntry from './AmbiguousEntry.svelte';

afterEach(cleanup);

it('links to the entry in each candidate KB', () => {
	render(AmbiguousEntry, { entryId: 'dup id', kbs: ['kb-a', 'kb b'] });
	expect(screen.getByTestId('ambiguous-entry')).toBeInTheDocument();
	expect(screen.getByRole('link', { name: 'kb-a' })).toHaveAttribute('href', '/entries/dup%20id?kb=kb-a');
	expect(screen.getByRole('link', { name: 'kb b' })).toHaveAttribute('href', '/entries/dup%20id?kb=kb%20b');
});
