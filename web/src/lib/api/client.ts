/**
 * Typed fetch wrapper for the Pyrite REST API.
 *
 * In development, Vite proxies /api to the FastAPI backend.
 * In production, both are served from the same origin.
 */

import type { AuthConfig, AuthUser } from '$lib/types/auth';
import type { BrandingConfig } from '$lib/types/branding';
import type {
	AIAutoTagResponse,
	AILinkSuggestResponse,
	AIStatusResponse,
	AISummarizeResponse,
	BlockListResponse,
	CollectionEntriesResponse,
	CollectionListResponse,
	CollectionResponse,
	CreateEntryRequest,
	CreateResponse,
	DailyDatesResponse,
	DeleteResponse,
	EntryListResponse,
	EntryResponse,
	EntryTitlesResponse,
	EntryTypesResponse,
	GraphResponse,
	ImportResult,
	KBHealthResponse,
	KBInfo,
	KBListResponse,
	KBOrientResponse,
	KBPermissionsResponse,
	KBReindexResponse,
	PluginDetail,
	PluginListResponse,
	RenderedTemplate,
	ReorderStarredRequest,
	ReorderStarredResponse,
	ResolveBatchResponse,
	ResolveResponse,
	SearchResponse,
	WantedPagesResponse,
	SettingResponse,
	SettingsResponse,
	StarEntryResponse,
	StarredEntryListResponse,
	StatsResponse,
	TagsResponse,
	TagTreeResponse,
	TemplateDetail,
	TemplateListResponse,
	TimelineResponse,
	UnstarEntryResponse,
	ClipRequest,
	ClipResponse,
	GitHubConnectionStatus,
	GitHubRepoInfo,
	PRResult,
	RepoInfo,
	RepoListResponse,
	SyncResult,
	TypeSchemasResponse,
	UpdateEntryRequest,
	UpdateResponse,
	UserListResponse,
	VersionListResponse,
	EmbedStatusResponse,
	IndexJobsResponse,
	PendingChangesResponse,
	PublishResponse
} from './types';

// Paths that legitimately answer 401 without an expired session: signing in
// with the wrong password, and the anonymous-visitor probe `getMe` makes on
// every load to ask "is anyone signed in" (it always swallows the error
// itself -- see getMe below). Neither means an authenticated session just
// expired, so neither should fire the onUnauthorized handler (#420 groom:
// "A 401 from login or getMe during init does not fire the handler").
const UNAUTHORIZED_HOOK_EXEMPT_PATHS = new Set(['/auth/login', '/auth/me']);

class ApiClient {
	private baseUrl: string;
	private unauthorizedHandler: () => void = () => {};

	constructor(baseUrl = '') {
		this.baseUrl = baseUrl;
	}

	/**
	 * Registers the single handler called when an authenticated request comes
	 * back 401 (#420). `auth.svelte.ts` is the only registrant: the client
	 * cannot import the store directly (the store already imports `api`), so
	 * this is the hook that lets the store learn its session expired without
	 * every one of ~90 call sites checking `err.status === 401` by hand.
	 * Registering again replaces the previous handler.
	 */
	onUnauthorized(handler: () => void): void {
		this.unauthorizedHandler = handler;
	}

	private handleErrorResponse(res: Response, path: string, error: { detail?: unknown; message?: string }): never {
		if (res.status === 401 && !UNAUTHORIZED_HOOK_EXEMPT_PATHS.has(path)) {
			this.unauthorizedHandler();
		}
		// detail can be a string or structured {code, message, hint}
		const detail = error.detail;
		let message: string;
		if (typeof detail === 'string') {
			message = detail;
		} else if (detail && typeof detail === 'object') {
			message = (detail as { hint?: string; message?: string }).hint ?? (detail as { message?: string }).message ?? JSON.stringify(detail);
		} else {
			message = error.message ?? res.statusText;
		}
		const structured =
			detail && typeof detail === 'object'
				? (detail as { code?: unknown; candidate_kbs?: unknown })
				: {};
		const code = typeof structured.code === 'string' ? structured.code : undefined;
		const candidateKbs = Array.isArray(structured.candidate_kbs)
			? structured.candidate_kbs.filter((k): k is string => typeof k === 'string')
			: undefined;
		throw new ApiError(res.status, message, code, candidateKbs);
	}

	private async request<T>(path: string, options: RequestInit = {}): Promise<T> {
		const url = `${this.baseUrl}${path}`;
		const res = await fetch(url, {
			headers: {
				'Content-Type': 'application/json',
				...options.headers
			},
			credentials: 'include',
			...options
		});

		if (!res.ok) {
			const error = await res.json().catch(() => ({ message: res.statusText }));
			this.handleErrorResponse(res, path, error);
		}

		return res.json();
	}

	// Knowledge Bases
	async listKBs(): Promise<KBListResponse> {
		return this.request('/api/kbs');
	}

	async getKB(name: string): Promise<KBInfo> {
		return this.request(`/api/kbs/${encodeURIComponent(name)}`);
	}

	async createKB(req: {
		name: string;
		path: string;
		kb_type?: string;
		description?: string;
	}): Promise<{ created: boolean; name: string; path: string }> {
		return this.request('/api/kbs', {
			method: 'POST',
			body: JSON.stringify(req)
		});
	}

	async updateKB(
		name: string,
		updates: { description?: string; kb_type?: string }
	): Promise<{ updated: boolean }> {
		return this.request(`/api/kbs/${encodeURIComponent(name)}`, {
			method: 'PUT',
			body: JSON.stringify(updates)
		});
	}

	async deleteKB(name: string): Promise<{ deleted: boolean; name: string }> {
		return this.request(`/api/kbs/${encodeURIComponent(name)}`, {
			method: 'DELETE'
		});
	}

	async reindexKB(name: string): Promise<KBReindexResponse> {
		return this.request(`/api/kbs/${encodeURIComponent(name)}/reindex`, {
			method: 'POST'
		});
	}

	async getKBHealth(name: string): Promise<KBHealthResponse> {
		return this.request(`/api/kbs/${encodeURIComponent(name)}/health`);
	}

	async getKBOrient(name: string, recent: number = 10): Promise<KBOrientResponse> {
		return this.request(`/api/kbs/${encodeURIComponent(name)}/orient?recent=${recent}`);
	}

	async getKBPermissions(name: string): Promise<KBPermissionsResponse> {
		return this.request(`/api/kbs/${encodeURIComponent(name)}/permissions`);
	}

	async grantKBPermission(
		name: string,
		userId: number,
		role: string
	): Promise<{ granted: boolean; user_id: number; kb_name: string; role: string }> {
		return this.request(`/api/kbs/${encodeURIComponent(name)}/permissions`, {
			method: 'POST',
			body: JSON.stringify({ user_id: userId, role })
		});
	}

	async revokeKBPermission(
		name: string,
		userId: number
	): Promise<{ revoked: boolean; user_id: number; kb_name: string }> {
		return this.request(`/api/kbs/${encodeURIComponent(name)}/permissions`, {
			method: 'POST',
			body: JSON.stringify({ user_id: userId, revoke: true })
		});
	}

	async updateKBDefaultRole(
		name: string,
		role: string | null
	): Promise<{ ok: boolean; kb_name: string; default_role: string | null }> {
		return this.request(`/api/kbs/${encodeURIComponent(name)}/default-role`, {
			method: 'PUT',
			body: JSON.stringify({ role })
		});
	}

	async listUsers(): Promise<UserListResponse> {
		return this.request('/auth/users');
	}

	async setUserRole(userId: number, role: string): Promise<{ updated: boolean; user_id: number; role: string }> {
		return this.request(`/auth/users/${userId}/role`, {
			method: 'PUT',
			body: JSON.stringify({ role })
		});
	}

	async getUserPermissions(userId: number): Promise<{ user_id: number; permissions: Record<string, string> }> {
		return this.request(`/auth/users/${userId}/permissions`);
	}

	async listEphemeralKBs(): Promise<{
		ephemeral_kbs: Array<{
			name: string;
			path: string;
			created_at: number | null;
			ttl: number | null;
			expires_at: number | null;
			expired: boolean;
		}>;
		count: number;
	}> {
		return this.request('/api/kbs/ephemeral');
	}

	async forceExpireKB(name: string): Promise<{ expired: boolean; name: string }> {
		return this.request(`/api/kbs/ephemeral/${encodeURIComponent(name)}`, {
			method: 'DELETE'
		});
	}

	async exportKB(
		kbName: string,
		repoUrl: string
	): Promise<{ success: boolean; entries_exported: number; message: string }> {
		return this.request(`/api/kbs/${encodeURIComponent(kbName)}/export`, {
			method: 'POST',
			body: JSON.stringify({ repo_url: repoUrl })
		});
	}

	// Search
	async search(
		query: string,
		options: {
			kb?: string;
			type?: string;
			tags?: string;
			limit?: number;
			mode?: 'keyword' | 'semantic' | 'hybrid';
			date_from?: string;
			date_to?: string;
		} = {}
	): Promise<SearchResponse> {
		const params = new URLSearchParams({ q: query });
		if (options.kb) params.set('kb', options.kb);
		if (options.type) params.set('type', options.type);
		if (options.tags) params.set('tags', options.tags);
		if (options.limit) params.set('limit', String(options.limit));
		if (options.mode) params.set('mode', options.mode);
		if (options.date_from) params.set('date_from', options.date_from);
		if (options.date_to) params.set('date_to', options.date_to);
		return this.request(`/api/search?${params}`);
	}

	// Entries
	async listEntries(options: {
		kb?: string;
		entry_type?: string;
		tag?: string;
		status?: string;
		min_importance?: number;
		sort_by?: string;
		sort_order?: string;
		limit?: number;
		offset?: number;
	} = {}): Promise<EntryListResponse> {
		const params = new URLSearchParams();
		if (options.kb) params.set('kb', options.kb);
		if (options.entry_type) params.set('entry_type', options.entry_type);
		if (options.tag) params.set('tag', options.tag);
		if (options.status) params.set('status', options.status);
		if (options.min_importance) params.set('min_importance', String(options.min_importance));
		if (options.sort_by) params.set('sort_by', options.sort_by);
		if (options.sort_order) params.set('sort_order', options.sort_order);
		if (options.limit) params.set('limit', String(options.limit));
		if (options.offset) params.set('offset', String(options.offset));
		const qs = params.toString();
		return this.request(`/api/entries${qs ? `?${qs}` : ''}`);
	}

	async getEntryTypes(kb?: string): Promise<EntryTypesResponse> {
		const params = kb ? `?kb=${encodeURIComponent(kb)}` : '';
		return this.request(`/api/entries/types${params}`);
	}

	async getTypeSchemas(kb?: string): Promise<TypeSchemasResponse> {
		const params = kb ? `?kb=${encodeURIComponent(kb)}` : '';
		return this.request(`/api/entries/type-schemas${params}`);
	}

	async getEntry(id: string, options: { kb?: string; with_links?: boolean } = {}): Promise<EntryResponse> {
		const params = new URLSearchParams();
		if (options.kb) params.set('kb', options.kb);
		if (options.with_links) params.set('with_links', 'true');
		const qs = params.toString();
		return this.request(`/api/entries/${encodeURIComponent(id)}${qs ? `?${qs}` : ''}`);
	}

	// REST refuses a type the KB's kb.yaml does not declare unless the caller
	// sends allow_undeclared (#378). The form offers only declared types and
	// sets the flag itself only through its explicit override control (#392),
	// so the client never opts in on the caller's behalf.
	async createEntry(req: CreateEntryRequest): Promise<CreateResponse> {
		return this.request('/api/entries', {
			method: 'POST',
			body: JSON.stringify(req)
		});
	}

	async updateEntry(id: string, req: UpdateEntryRequest): Promise<UpdateResponse> {
		return this.request(`/api/entries/${encodeURIComponent(id)}`, {
			method: 'PUT',
			body: JSON.stringify(req)
		});
	}

	async deleteEntry(id: string, kb: string): Promise<DeleteResponse> {
		return this.request(`/api/entries/${encodeURIComponent(id)}?kb=${encodeURIComponent(kb)}`, {
			method: 'DELETE'
		});
	}

	// Wikilinks
	async getEntryTitles(options: { kb?: string; q?: string; limit?: number } = {}): Promise<EntryTitlesResponse> {
		const params = new URLSearchParams();
		if (options.kb) params.set('kb', options.kb);
		if (options.q) params.set('q', options.q);
		if (options.limit) params.set('limit', String(options.limit));
		const qs = params.toString();
		return this.request(`/api/entries/titles${qs ? `?${qs}` : ''}`);
	}

	async resolveEntry(target: string, kb?: string): Promise<ResolveResponse> {
		const params = new URLSearchParams({ target });
		if (kb) params.set('kb', kb);
		return this.request(`/api/entries/resolve?${params}`);
	}

	async resolveBatch(targets: string[], kb?: string): Promise<ResolveBatchResponse> {
		return this.request('/api/entries/resolve-batch', {
			method: 'POST',
			headers: { 'Content-Type': 'application/json' },
			body: JSON.stringify({ targets, kb: kb ?? null })
		});
	}

	async getWantedPages(kb?: string, limit?: number): Promise<WantedPagesResponse> {
		const params = new URLSearchParams();
		if (kb) params.set('kb', kb);
		if (limit) params.set('limit', String(limit));
		const qs = params.toString();
		return this.request(`/api/entries/wanted${qs ? `?${qs}` : ''}`);
	}

	// Tags
	async getTags(kb?: string, prefix?: string): Promise<TagsResponse> {
		const params = new URLSearchParams();
		if (kb) params.set('kb', kb);
		if (prefix) params.set('prefix', prefix);
		const qs = params.toString();
		return this.request(`/api/tags${qs ? `?${qs}` : ''}`);
	}

	async getTagTree(kb?: string): Promise<TagTreeResponse> {
		const params = kb ? `?kb=${encodeURIComponent(kb)}` : '';
		return this.request(`/api/tags/tree${params}`);
	}

	// Timeline
	async getTimeline(options: {
		date_from?: string;
		date_to?: string;
		min_importance?: number;
		limit?: number;
	} = {}): Promise<TimelineResponse> {
		const params = new URLSearchParams();
		if (options.date_from) params.set('date_from', options.date_from);
		if (options.date_to) params.set('date_to', options.date_to);
		if (options.min_importance) params.set('min_importance', String(options.min_importance));
		if (options.limit) params.set('limit', String(options.limit));
		const qs = params.toString();
		return this.request(`/api/timeline${qs ? `?${qs}` : ''}`);
	}

	// Stats
	async getStats(): Promise<StatsResponse> {
		return this.request('/api/stats');
	}

	// Index sync
	async syncIndex(): Promise<{ synced: boolean; added: number; updated: number; removed: number }> {
		return this.request('/api/index/sync', { method: 'POST' });
	}

	// Pending Changes & Publish
	async getPendingChanges(kb: string): Promise<PendingChangesResponse> {
		return this.request(`/api/kbs/${encodeURIComponent(kb)}/changes`);
	}

	async publishChanges(kb: string, summary?: string): Promise<PublishResponse> {
		return this.request(`/api/kbs/${encodeURIComponent(kb)}/publish`, {
			method: 'POST',
			body: JSON.stringify({ summary: summary || null })
		});
	}

	async getEmbedStatus(): Promise<EmbedStatusResponse> {
		return this.request('/api/index/embed-status');
	}

	async getIndexJobs(): Promise<IndexJobsResponse> {
		return this.request('/api/index/jobs');
	}

	async renderSiteCache(): Promise<{ rendered: boolean; kbs: number; entries: number; errors: number }> {
		return this.request('/api/site/render', { method: 'POST' });
	}

	// Starred Entries
	async getStarredEntries(kb?: string): Promise<StarredEntryListResponse> {
		const params = kb ? `?kb=${encodeURIComponent(kb)}` : '';
		return this.request(`/api/starred${params}`);
	}

	async starEntry(entryId: string, kbName: string): Promise<StarEntryResponse> {
		return this.request('/api/starred', {
			method: 'POST',
			body: JSON.stringify({ entry_id: entryId, kb_name: kbName })
		});
	}

	async unstarEntry(entryId: string, kb?: string): Promise<UnstarEntryResponse> {
		const params = kb ? `?kb=${encodeURIComponent(kb)}` : '';
		return this.request(`/api/starred/${encodeURIComponent(entryId)}${params}`, {
			method: 'DELETE'
		});
	}

	async reorderStarred(entries: ReorderStarredRequest['entries']): Promise<ReorderStarredResponse> {
		return this.request('/api/starred/reorder', {
			method: 'PUT',
			body: JSON.stringify({ entries })
		});
	}

	// Templates
	async getTemplates(kbName: string): Promise<TemplateListResponse> {
		return this.request(`/api/kbs/${encodeURIComponent(kbName)}/templates`);
	}

	async getTemplate(kbName: string, templateName: string): Promise<TemplateDetail> {
		return this.request(
			`/api/kbs/${encodeURIComponent(kbName)}/templates/${encodeURIComponent(templateName)}`
		);
	}

	async renderTemplate(
		kbName: string,
		templateName: string,
		variables: Record<string, string> = {}
	): Promise<RenderedTemplate> {
		return this.request(
			`/api/kbs/${encodeURIComponent(kbName)}/templates/${encodeURIComponent(templateName)}/render`,
			{
				method: 'POST',
				body: JSON.stringify({ variables })
			}
		);
	}

	// Daily Notes
	async getDailyNote(date: string, kb: string): Promise<EntryResponse> {
		return this.request(`/api/daily/${date}?kb=${encodeURIComponent(kb)}`);
	}

	async createDailyNote(date: string, kb: string): Promise<EntryResponse> {
		return this.request(`/api/daily/${date}?kb=${encodeURIComponent(kb)}`, {
			method: 'POST'
		});
	}

	async getDailyDates(kb: string, month?: string): Promise<DailyDatesResponse> {
		const params = new URLSearchParams({ kb });
		if (month) params.set('month', month);
		return this.request(`/api/daily/dates?${params}`);
	}

	// Settings
	async getSettings(): Promise<SettingsResponse> {
		return this.request('/api/settings');
	}

	async setSetting(key: string, value: string): Promise<SettingResponse> {
		return this.request(`/api/settings/${encodeURIComponent(key)}`, {
			method: 'PUT',
			body: JSON.stringify({ value })
		});
	}

	async bulkUpdateSettings(settings: Record<string, string>): Promise<SettingsResponse> {
		return this.request('/api/settings', {
			method: 'PUT',
			body: JSON.stringify({ settings })
		});
	}

	// AI
	async getAIStatus(): Promise<AIStatusResponse> {
		return this.request('/api/ai/status');
	}

	async testAIConnection(): Promise<{ ok: boolean; message: string }> {
		return this.request('/api/ai/test', { method: 'POST' });
	}

	// MCP connection info
	async getMcpInfo(): Promise<{ endpoint: string; transport: string; auth: string; tools_count: number; tier: string }> {
		return this.request('/mcp/info');
	}

	// User API Keys (BYOK)
	async listUserApiKeys(): Promise<{ keys: Array<{ provider: string; model: string; created_at: string }> }> {
		return this.request('/auth/api-keys');
	}

	async storeUserApiKey(provider: string, apiKey: string, model: string = ''): Promise<{ provider: string; model: string; stored: boolean }> {
		return this.request('/auth/api-keys', {
			method: 'POST',
			body: JSON.stringify({ provider, api_key: apiKey, model })
		});
	}

	async deleteUserApiKey(provider: string): Promise<{ ok: boolean; provider: string }> {
		return this.request(`/auth/api-keys/${encodeURIComponent(provider)}`, {
			method: 'DELETE'
		});
	}

	async aiSummarize(entryId: string, kbName: string): Promise<AISummarizeResponse> {
		return this.request('/api/ai/summarize', {
			method: 'POST',
			body: JSON.stringify({ entry_id: entryId, kb_name: kbName })
		});
	}

	async aiAutoTag(entryId: string, kbName: string): Promise<AIAutoTagResponse> {
		return this.request('/api/ai/auto-tag', {
			method: 'POST',
			body: JSON.stringify({ entry_id: entryId, kb_name: kbName })
		});
	}

	// Graph
	async getGraph(options: {
		center?: string;
		center_kb?: string;
		kb?: string;
		type?: string;
		depth?: number;
		limit?: number;
		include_centrality?: boolean;
	} = {}): Promise<GraphResponse> {
		const params = new URLSearchParams();
		if (options.center) params.set('center', options.center);
		if (options.center_kb) params.set('center_kb', options.center_kb);
		if (options.kb) params.set('kb', options.kb);
		if (options.type) params.set('type', options.type);
		if (options.depth) params.set('depth', String(options.depth));
		if (options.limit) params.set('limit', String(options.limit));
		if (options.include_centrality) params.set('include_centrality', 'true');
		const qs = params.toString();
		return this.request(`/api/graph${qs ? `?${qs}` : ''}`);
	}

	async aiSuggestLinks(entryId: string, kbName: string): Promise<AILinkSuggestResponse> {
		return this.request('/api/ai/suggest-links', {
			method: 'POST',
			body: JSON.stringify({ entry_id: entryId, kb_name: kbName })
		});
	}

	// Collections
	async listCollections(kb?: string): Promise<CollectionListResponse> {
		const params = kb ? `?kb=${encodeURIComponent(kb)}` : '';
		return this.request(`/api/collections${params}`);
	}

	async getCollection(id: string, kb: string): Promise<CollectionResponse> {
		return this.request(`/api/collections/${encodeURIComponent(id)}?kb=${encodeURIComponent(kb)}`);
	}

	async previewCollectionQuery(
		query: string,
		kb?: string,
		limit?: number
	): Promise<{ entries: EntryResponse[]; total: number; query_parsed: Record<string, unknown> }> {
		return this.request('/api/collections/query-preview', {
			method: 'POST',
			body: JSON.stringify({ query, kb, limit: limit ?? 20 })
		});
	}

	async createCollection(req: {
		kb: string;
		title: string;
		query: string;
		description?: string;
		icon?: string;
		view_config?: Record<string, unknown>;
		collection_type?: string;
	}): Promise<CollectionResponse> {
		return this.request('/api/collections', {
			method: 'POST',
			body: JSON.stringify(req)
		});
	}

	async getCollectionEntries(
		id: string,
		kb: string,
		options: { sort_by?: string; sort_order?: string; limit?: number; offset?: number } = {}
	): Promise<CollectionEntriesResponse> {
		const params = new URLSearchParams({ kb });
		if (options.sort_by) params.set('sort_by', options.sort_by);
		if (options.sort_order) params.set('sort_order', options.sort_order);
		if (options.limit) params.set('limit', String(options.limit));
		if (options.offset) params.set('offset', String(options.offset));
		return this.request(`/api/collections/${encodeURIComponent(id)}/entries?${params}`);
	}

	// Blocks
	async getEntryBlocks(entryId: string, kb: string, options?: { heading?: string; block_type?: string; block_id?: string }): Promise<BlockListResponse> {
		const params = new URLSearchParams({ kb });
		if (options?.heading) params.set('heading', options.heading);
		if (options?.block_type) params.set('block_type', options.block_type);
		if (options?.block_id) params.set('block_id', options.block_id);
		return this.request(`/api/entries/${encodeURIComponent(entryId)}/blocks?${params}`);
	}

	// Versions
	async getEntryVersions(entryId: string, kb: string, limit?: number): Promise<VersionListResponse> {
		const params = new URLSearchParams({ kb });
		if (limit) params.set('limit', String(limit));
		return this.request(`/api/entries/${encodeURIComponent(entryId)}/versions?${params}`);
	}

	// Web Clipper
	async clipUrl(req: ClipRequest): Promise<ClipResponse> {
		return this.request('/api/clip', {
			method: 'POST',
			body: JSON.stringify(req)
		});
	}

	// Plugins
	async getPlugins(): Promise<PluginListResponse> {
		return this.request('/api/plugins');
	}

	async getPlugin(name: string): Promise<PluginDetail> {
		return this.request(`/api/plugins/${encodeURIComponent(name)}`);
	}

	// GitHub Connection
	async getGitHubConnectionStatus(): Promise<GitHubConnectionStatus> {
		return this.request('/auth/github/status');
	}

	async disconnectGitHub(): Promise<{ ok: boolean; message: string }> {
		return this.request('/auth/github/connect', { method: 'DELETE' });
	}

	// Repos
	async listRepos(): Promise<RepoListResponse> {
		return this.request('/api/repos');
	}

	async getRepo(name: string): Promise<RepoInfo> {
		return this.request(`/api/repos/${encodeURIComponent(name)}`);
	}

	async subscribeToRepo(remoteUrl: string, name?: string, branch?: string): Promise<{
		success: boolean;
		repo?: string;
		kbs?: string[];
		error?: string;
	}> {
		return this.request('/api/repos/subscribe', {
			method: 'POST',
			body: JSON.stringify({ remote_url: remoteUrl, name, branch })
		});
	}

	async forkRepo(remoteUrl: string): Promise<{
		success: boolean;
		repo?: string;
		error?: string;
	}> {
		return this.request('/api/repos/fork', {
			method: 'POST',
			body: JSON.stringify({ remote_url: remoteUrl })
		});
	}

	async syncRepo(name: string): Promise<SyncResult> {
		return this.request(`/api/repos/${encodeURIComponent(name)}/sync`, {
			method: 'POST'
		});
	}

	async unsubscribeRepo(name: string, deleteFiles?: boolean): Promise<{
		success: boolean;
		error?: string;
	}> {
		const params = deleteFiles ? '?delete_files=true' : '';
		return this.request(`/api/repos/${encodeURIComponent(name)}${params}`, {
			method: 'DELETE'
		});
	}

	async createPR(repoName: string, title: string, body: string, branch?: string): Promise<PRResult> {
		return this.request(`/api/repos/${encodeURIComponent(repoName)}/pr`, {
			method: 'POST',
			body: JSON.stringify({ title, body, branch })
		});
	}

	async listGitHubRepos(): Promise<{ repos: GitHubRepoInfo[] }> {
		return this.request('/api/github/repos');
	}

	// Auth
	async login(username: string, password: string): Promise<AuthUser> {
		return this.request('/auth/login', {
			method: 'POST',
			body: JSON.stringify({ username, password })
		});
	}

	async register(username: string, password: string, display_name?: string, invite_code?: string): Promise<AuthUser> {
		return this.request('/auth/register', {
			method: 'POST',
			body: JSON.stringify({ username, password, display_name, invite_code })
		});
	}

	async logout(): Promise<void> {
		await this.request('/auth/logout', { method: 'POST' });
	}

	async getMe(): Promise<AuthUser | null> {
		try {
			return await this.request('/auth/me');
		} catch {
			return null;
		}
	}

	async getAuthConfig(): Promise<AuthConfig> {
		return this.request('/auth/config');
	}

	async getBrandingConfig(): Promise<BrandingConfig> {
		return this.request('/config/branding');
	}

	// Import/Export
	async importEntries(
		file: File,
		kb: string,
		format?: string,
		allowUndeclared?: boolean
	): Promise<ImportResult> {
		const formData = new FormData();
		formData.append('file', file);
		const params = new URLSearchParams({ kb });
		if (format) params.set('format', format);
		if (allowUndeclared) params.set('allow_undeclared', 'true');
		const url = `${this.baseUrl}/api/entries/import?${params}`;
		const res = await fetch(url, {
			method: 'POST',
			credentials: 'include',
			body: formData
		});
		if (!res.ok) {
			const error = await res.json().catch(() => ({ message: res.statusText }));
			this.handleErrorResponse(res, '/api/entries/import', error);
		}
		return res.json();
	}

	async exportEntries(
		kb: string,
		format: string = 'json',
		options: { entry_type?: string; tag?: string } = {}
	): Promise<Blob> {
		const params = new URLSearchParams({ kb, format });
		if (options.entry_type) params.set('entry_type', options.entry_type);
		if (options.tag) params.set('tag', options.tag);
		const url = `${this.baseUrl}/api/entries/export?${params}`;
		const res = await fetch(url, { credentials: 'include' });
		if (!res.ok) {
			const error = await res.json().catch(() => ({ message: res.statusText }));
			this.handleErrorResponse(res, '/api/entries/export', error);
		}
		return res.blob();
	}

	// ── Worktree collaboration ──────────────────────────────────────

	async getWorktreeStatus(kb: string): Promise<{
		has_worktree: boolean;
		status: string;
		changes_count: number;
		submitted_at: string | null;
		feedback: string | null;
		branch: string | null;
	}> {
		return this.request(`/api/worktree/status?kb=${encodeURIComponent(kb)}`);
	}

	async submitWorktree(kb: string): Promise<{
		submitted: boolean;
		kb_name: string;
		branch: string;
		submitted_at: string | null;
	}> {
		return this.request('/api/worktree/submit', {
			method: 'POST',
			body: JSON.stringify({ kb })
		});
	}

	async getWorktreeChanges(kb: string): Promise<{
		changes: string[];
		count: number;
		diff: string;
		branch: string;
	}> {
		return this.request(`/api/worktree/changes?kb=${encodeURIComponent(kb)}`);
	}

	async resetWorktree(kb: string): Promise<{ reset: boolean; status: string }> {
		return this.request('/api/worktree/reset', {
			method: 'POST',
			body: JSON.stringify({ kb })
		});
	}

	async getMergeQueue(kb?: string): Promise<{
		submissions: Array<{
			username: string;
			kb_name: string;
			branch: string;
			status: string;
			submitted_at: string | null;
			changes_count: number;
		}>;
		total: number;
	}> {
		const params = kb ? `?kb=${encodeURIComponent(kb)}` : '';
		return this.request(`/api/admin/merge-queue${params}`);
	}

	async getMergeQueueDiff(username: string, kb: string): Promise<{
		username: string;
		kb_name: string;
		branch: string;
		status: string;
		diff: string;
		stat: string;
		submitted_at: string | null;
	}> {
		return this.request(
			`/api/admin/merge-queue/${encodeURIComponent(username)}/diff?kb=${encodeURIComponent(kb)}`
		);
	}

	async mergeWorktree(
		username: string,
		kb: string
	): Promise<{ merged: boolean; message: string }> {
		return this.request(`/api/admin/merge-queue/${encodeURIComponent(username)}/merge`, {
			method: 'POST',
			body: JSON.stringify({ kb })
		});
	}

	async rejectWorktree(
		username: string,
		kb: string,
		feedback: string
	): Promise<{ rejected: boolean; status: string; feedback: string }> {
		return this.request(`/api/admin/merge-queue/${encodeURIComponent(username)}/reject`, {
			method: 'POST',
			body: JSON.stringify({ kb, feedback })
		});
	}
}

export class ApiError extends Error {
	status: number;
	detail: string;
	/** The refusal's machine code (`AMBIGUOUS`, `NOT_FOUND`, ...), when the server sent one. */
	code?: string;
	/** For `AMBIGUOUS`: every readable KB that holds the id (#528). */
	candidateKbs?: string[];

	constructor(status: number, detail: string, code?: string, candidateKbs?: string[]) {
		super(`API Error ${status}: ${detail}`);
		this.status = status;
		this.detail = detail;
		this.code = code;
		this.candidateKbs = candidateKbs;
	}
}

export const api = new ApiClient();
