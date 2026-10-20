import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { api, ApiError } from './client';

// Mock global fetch
const mockFetch = vi.fn();
vi.stubGlobal('fetch', mockFetch);

function jsonResponse(data: unknown, status = 200) {
	return {
		ok: status >= 200 && status < 300,
		status,
		statusText: status === 200 ? 'OK' : 'Error',
		json: () => Promise.resolve(data)
	};
}

beforeEach(() => {
	mockFetch.mockReset();
});

describe('ApiClient', () => {
	describe('listKBs', () => {
		it('fetches /api/kbs and returns typed response', async () => {
			const payload = { kbs: [{ name: 'test', type: 'generic', path: '/tmp', entries: 5, indexed: true }], total: 1 };
			mockFetch.mockResolvedValueOnce(jsonResponse(payload));

			const result = await api.listKBs();
			expect(result.kbs).toHaveLength(1);
			expect(result.kbs[0].name).toBe('test');
			expect(mockFetch).toHaveBeenCalledWith('/api/kbs', expect.objectContaining({ headers: expect.any(Object) }));
		});
	});

	describe('search', () => {
		it('passes query params correctly', async () => {
			mockFetch.mockResolvedValueOnce(jsonResponse({ query: 'test', count: 0, results: [] }));

			await api.search('test', { kb: 'my-kb', limit: 10 });
			const url = mockFetch.mock.calls[0][0] as string;
			expect(url).toContain('q=test');
			expect(url).toContain('kb=my-kb');
			expect(url).toContain('limit=10');
		});
	});

	describe('listEntries', () => {
		it('builds query string from options', async () => {
			mockFetch.mockResolvedValueOnce(jsonResponse({ entries: [], total: 0, limit: 50, offset: 0 }));

			await api.listEntries({ kb: 'events', entry_type: 'note', limit: 20 });
			const url = mockFetch.mock.calls[0][0] as string;
			expect(url).toContain('/api/entries');
			expect(url).toContain('kb=events');
			expect(url).toContain('entry_type=note');
			expect(url).toContain('limit=20');
		});
	});

	describe('getEntry', () => {
		it('encodes entry ID in URL', async () => {
			const entry = { id: 'test-entry', kb_name: 'kb', entry_type: 'note', title: 'Test', tags: [], participants: [], sources: [], outlinks: [], backlinks: [], file_path: '/tmp/test.md' };
			mockFetch.mockResolvedValueOnce(jsonResponse(entry));

			await api.getEntry('test-entry', { kb: 'kb', with_links: true });
			const url = mockFetch.mock.calls[0][0] as string;
			expect(url).toContain('/api/entries/test-entry');
			expect(url).toContain('with_links=true');
		});
	});

	describe('createEntry', () => {
		it('sends POST with JSON body', async () => {
			mockFetch.mockResolvedValueOnce(jsonResponse({ created: true, id: 'new-entry', kb_name: 'kb', file_path: '/tmp/new.md' }));

			await api.createEntry({ kb: 'kb', title: 'New', body: 'Content', tags: ['test'] });
			expect(mockFetch).toHaveBeenCalledWith('/api/entries', expect.objectContaining({
				method: 'POST',
				body: expect.stringContaining('"title":"New"')
			}));
		});
	});

	describe('allow_undeclared is the caller\'s explicit choice (#392)', () => {
		// REST refuses a type the KB's kb.yaml does not declare unless the
		// caller sends allow_undeclared (#378). The client is a pass-through:
		// it never opts in on the caller's behalf, and only the New-entry
		// form's explicit "use an undeclared type" control decides to pass it.
		it('createEntry does not send allow_undeclared unless the caller passes it', async () => {
			mockFetch.mockResolvedValueOnce(jsonResponse({ created: true, id: 'n', kb_name: 'kb', file_path: '' }));
			await api.createEntry({ kb: 'kb', entry_type: 'note', title: 'From the form', body: '' });
			const body = JSON.parse(mockFetch.mock.calls[0][1].body);
			expect(body).toEqual({ kb: 'kb', entry_type: 'note', title: 'From the form', body: '' });
			expect(body.allow_undeclared).toBeUndefined();
		});

		it('createEntry sends allow_undeclared when the caller passes it', async () => {
			mockFetch.mockResolvedValueOnce(jsonResponse({ created: true, id: 'n', kb_name: 'kb', file_path: '' }));
			await api.createEntry({ kb: 'kb', entry_type: 'note', title: 'Undeclared', body: '', allow_undeclared: true });
			const body = JSON.parse(mockFetch.mock.calls[0][1].body);
			expect(body.allow_undeclared).toBe(true);
		});

		it('clipUrl does not send allow_undeclared unless the caller passes it', async () => {
			mockFetch.mockResolvedValueOnce(jsonResponse({ created: true, id: 'c', kb_name: 'kb', title: 't', source_url: 'u' }));
			await api.clipUrl({ url: 'https://example.org', kb: 'kb' });
			const body = JSON.parse(mockFetch.mock.calls[0][1].body);
			expect(body.allow_undeclared).toBeUndefined();
		});

		it('clipUrl sends allow_undeclared when the caller passes it', async () => {
			mockFetch.mockResolvedValueOnce(jsonResponse({ created: true, id: 'c', kb_name: 'kb', title: 't', source_url: 'u' }));
			await api.clipUrl({ url: 'https://example.org', kb: 'kb', allow_undeclared: true });
			const body = JSON.parse(mockFetch.mock.calls[0][1].body);
			expect(body.allow_undeclared).toBe(true);
		});

		it('importEntries does not send allow_undeclared unless the caller passes it', async () => {
			mockFetch.mockResolvedValueOnce(jsonResponse({ imported: 0, errors: 0, entries: [], error_details: [] }));
			await api.importEntries(new File(['[]'], 'x.json'), 'kb');
			expect(mockFetch.mock.calls[0][0] as string).not.toContain('allow_undeclared');
		});

		it('importEntries sends allow_undeclared=true when the caller passes it', async () => {
			mockFetch.mockResolvedValueOnce(jsonResponse({ imported: 0, errors: 0, entries: [], error_details: [] }));
			await api.importEntries(new File(['[]'], 'x.json'), 'kb', undefined, true);
			expect(mockFetch.mock.calls[0][0] as string).toContain('allow_undeclared=true');
		});
	});

	describe('updateEntry', () => {
		it('sends PUT with JSON body', async () => {
			mockFetch.mockResolvedValueOnce(jsonResponse({ updated: true, id: 'entry-1' }));

			await api.updateEntry('entry-1', { kb: 'kb', body: 'Updated' });
			expect(mockFetch).toHaveBeenCalledWith('/api/entries/entry-1', expect.objectContaining({
				method: 'PUT',
				body: expect.stringContaining('"body":"Updated"')
			}));
		});
	});

	describe('deleteEntry', () => {
		it('sends DELETE with kb query param', async () => {
			mockFetch.mockResolvedValueOnce(jsonResponse({ deleted: true, id: 'entry-1' }));

			await api.deleteEntry('entry-1', 'my-kb');
			const [url, opts] = mockFetch.mock.calls[0];
			expect(url).toContain('/api/entries/entry-1');
			expect(url).toContain('kb=my-kb');
			expect(opts.method).toBe('DELETE');
		});
	});

	describe('error handling', () => {
		it('throws ApiError on non-OK response', async () => {
			mockFetch.mockResolvedValueOnce({
				ok: false,
				status: 404,
				statusText: 'Not Found',
				json: () => Promise.resolve({ detail: { code: 'NOT_FOUND', message: 'Entry not found' } })
			});

			await expect(api.getEntry('missing')).rejects.toThrow(ApiError);
		});

		it('carries an AMBIGUOUS refusal\'s code and candidate KBs', async () => {
			mockFetch.mockResolvedValueOnce({
				ok: false,
				status: 409,
				statusText: 'Conflict',
				json: () =>
					Promise.resolve({
						detail: { code: 'AMBIGUOUS', message: 'ambiguous', candidate_kbs: ['kb-a', 'kb-b'] }
					})
			});

			const err = await api.getEntry('dup').catch((e) => e);
			expect(err).toBeInstanceOf(ApiError);
			expect(err.status).toBe(409);
			expect(err.code).toBe('AMBIGUOUS');
			expect(err.candidateKbs).toEqual(['kb-a', 'kb-b']);
		});

		it('includes status code in ApiError', async () => {
			mockFetch.mockResolvedValueOnce({
				ok: false,
				status: 401,
				statusText: 'Unauthorized',
				json: () => Promise.resolve({ detail: 'Invalid API key' })
			});

			try {
				await api.listKBs();
			} catch (e) {
				expect(e).toBeInstanceOf(ApiError);
				expect((e as ApiError).status).toBe(401);
			}
		});
	});

	describe('getTags', () => {
		it('passes kb filter', async () => {
			mockFetch.mockResolvedValueOnce(jsonResponse({ count: 2, tags: [{ name: 'a', count: 5 }] }));
			await api.getTags('my-kb');
			expect(mockFetch.mock.calls[0][0]).toContain('kb=my-kb');
		});
	});

	describe('syncIndex', () => {
		it('sends POST to /api/index/sync', async () => {
			mockFetch.mockResolvedValueOnce(jsonResponse({ synced: true, added: 1, updated: 0, removed: 0 }));
			const result = await api.syncIndex();
			expect(result.synced).toBe(true);
			expect(mockFetch.mock.calls[0][1].method).toBe('POST');
		});
	});

	describe('getEmbedStatus', () => {
		it('fetches /api/index/embed-status', async () => {
			const payload = { pending: 5, processing: 1, failed: 0, total: 6 };
			mockFetch.mockResolvedValueOnce(jsonResponse(payload));
			const result = await api.getEmbedStatus();
			expect(result.pending).toBe(5);
			expect(result.total).toBe(6);
			expect(mockFetch.mock.calls[0][0]).toContain('/api/index/embed-status');
		});
	});

	describe('getIndexJobs', () => {
		it('fetches /api/index/jobs', async () => {
			const payload = { jobs: [{ id: 'j1', status: 'running', kb: 'test', operation: 'sync' }] };
			mockFetch.mockResolvedValueOnce(jsonResponse(payload));
			const result = await api.getIndexJobs();
			expect(result.jobs).toHaveLength(1);
			expect(result.jobs[0].status).toBe('running');
		});
	});

	describe('onUnauthorized (#420): a 401 on an authenticated request notifies the registered handler', () => {
		afterEach(() => {
			// Every test registers its own handler; never leave one behind for
			// a later test's requests to trip.
			api.onUnauthorized(() => {});
		});

		it('calls the handler on a 401 from a normal request()-backed call', async () => {
			const handler = vi.fn();
			api.onUnauthorized(handler);
			mockFetch.mockResolvedValueOnce({
				ok: false,
				status: 401,
				statusText: 'Unauthorized',
				json: () => Promise.resolve({ detail: 'Session expired' })
			});

			await expect(api.listKBs()).rejects.toThrow(ApiError);
			expect(handler).toHaveBeenCalledTimes(1);
		});

		it('calls the handler on a 401 from the hand-rolled importEntries fetch', async () => {
			const handler = vi.fn();
			api.onUnauthorized(handler);
			mockFetch.mockResolvedValueOnce({
				ok: false,
				status: 401,
				statusText: 'Unauthorized',
				json: () => Promise.resolve({ detail: 'Session expired' })
			});

			await expect(api.importEntries(new File(['[]'], 'x.json'), 'kb')).rejects.toThrow(ApiError);
			expect(handler).toHaveBeenCalledTimes(1);
		});

		it('calls the handler on a 401 from the hand-rolled exportEntries fetch', async () => {
			const handler = vi.fn();
			api.onUnauthorized(handler);
			mockFetch.mockResolvedValueOnce({
				ok: false,
				status: 401,
				statusText: 'Unauthorized',
				json: () => Promise.resolve({ detail: 'Session expired' })
			});

			await expect(api.exportEntries('kb')).rejects.toThrow(ApiError);
			expect(handler).toHaveBeenCalledTimes(1);
		});

		it('does not call the handler for a 401 from login', async () => {
			const handler = vi.fn();
			api.onUnauthorized(handler);
			mockFetch.mockResolvedValueOnce({
				ok: false,
				status: 401,
				statusText: 'Unauthorized',
				json: () => Promise.resolve({ detail: 'Invalid credentials' })
			});

			await expect(api.login('alice', 'wrong')).rejects.toThrow(ApiError);
			expect(handler).not.toHaveBeenCalled();
		});

		it('does not call the handler for a 401 from getMe', async () => {
			const handler = vi.fn();
			api.onUnauthorized(handler);
			mockFetch.mockResolvedValueOnce({
				ok: false,
				status: 401,
				statusText: 'Unauthorized',
				json: () => Promise.resolve({ detail: 'Not authenticated' })
			});

			const result = await api.getMe();
			expect(result).toBeNull();
			expect(handler).not.toHaveBeenCalled();
		});

		it('does not call the handler on a 403', async () => {
			const handler = vi.fn();
			api.onUnauthorized(handler);
			mockFetch.mockResolvedValueOnce({
				ok: false,
				status: 403,
				statusText: 'Forbidden',
				json: () => Promise.resolve({ detail: 'Not permitted' })
			});

			await expect(api.listKBs()).rejects.toThrow(ApiError);
			expect(handler).not.toHaveBeenCalled();
		});
	});
});
