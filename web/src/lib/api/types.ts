/** TypeScript types matching Pyrite Pydantic schemas */

export interface KBInfo {
	name: string;
	type: string;
	path: string;
	entries: number;
	indexed: boolean;
	source: string;
	description: string;
	read_only: boolean;
	last_indexed: string | null;
	shortname: string | null;
	default_role: string | null;
	/** False when an operator set this KB's default role by hand in config.yaml. */
	default_role_editable?: boolean;
}

export interface KBListResponse {
	kbs: KBInfo[];
	total: number;
}

export interface KBHealthResponse {
	name: string;
	healthy: boolean;
	path_exists: boolean;
	path: string;
	file_count: number;
	entry_count: number;
	last_indexed: string | null;
	source: string;
}

export interface KBReindexResponse {
	name: string;
	added: number;
	updated: number;
	removed: number;
}

// Orient

export interface KBOrientResponse {
	kb: string;
	description: string;
	kb_type: string;
	read_only: boolean;
	guidelines: Record<string, string>;
	total_entries: number;
	types: Array<{ type: string; count: number }>;
	top_tags: Array<{ name: string; count: number }>;
	recent: Array<{ id: string; title: string; entry_type: string; updated_at: string }>;
	schema: Record<string, unknown>;
	[key: string]: unknown;
}

// Permissions

export interface KBPermissionGrant {
	user_id: number;
	username: string;
	role: string;
	granted_by: number | null;
	created_at: string;
}

export interface KBPermissionsResponse {
	kb_name: string;
	permissions: KBPermissionGrant[];
}

export interface UserInfo {
	id: number;
	username: string;
	display_name: string | null;
	role: string;
	auth_provider: string;
	avatar_url: string | null;
}

export interface UserListResponse {
	users: UserInfo[];
}

export interface SearchResult {
	id: string;
	kb_name: string;
	entry_type: string;
	title: string;
	snippet?: string;
	date?: string;
	importance?: number;
	tags: string[];
}

export interface SearchResponse {
	query: string;
	count: number;
	results: SearchResult[];
	/**
	 * Anything the search could not do as asked — today, a filter a backend's
	 * vector leg cannot honour, which costs the semantic leg entirely rather
	 * than returning rows that violate the filter. Absent (never null) when
	 * every filter was applied on every leg that ran, so test for the key's
	 * presence. No UI consumes this yet.
	 */
	warnings?: string[];
}

export interface EntryResponse {
	id: string;
	kb_name: string;
	entry_type: string;
	title: string;
	body?: string;
	summary?: string;
	date?: string;
	importance?: number;
	status?: string;
	tags: string[];
	participants: string[];
	sources: Record<string, unknown>[];
	outlinks: Record<string, unknown>[];
	backlinks: Record<string, unknown>[];
	file_path: string;
	created_at?: string;
	updated_at?: string;
}

export interface EntryListResponse {
	entries: EntryResponse[];
	total: number;
	limit: number;
	offset: number;
}

export interface EntryTypesResponse {
	types: string[];
}

export interface TypeFieldSchema {
	type: string;
	description?: string;
	required?: boolean;
	default?: unknown;
	options?: string[];
}

export interface TypeSchemaInfo {
	description: string;
	fields: Record<string, TypeFieldSchema>;
	subdirectory?: string;
	file_pattern?: string;
}

export interface TypeSchemasResponse {
	types: Record<string, TypeSchemaInfo>;
	/** The KB's own kb.yaml vocabulary. Empty or absent when the KB declares
	 *  no types (an older server may omit this additive field entirely), in
	 *  which case every type in `types` is offered and none is "undeclared".
	 *  Callers must treat a missing `declared` as `[]`, never as "every type
	 *  in `types` is declared". */
	declared?: string[];
}

export interface CreateEntryRequest {
	kb: string;
	entry_type?: string;
	title: string;
	body?: string;
	date?: string;
	importance?: number;
	tags?: string[];
	participants?: string[];
	role?: string;
	metadata?: Record<string, unknown>;
	/** Allow a type the KB's kb.yaml does not declare (the web client sends true). */
	allow_undeclared?: boolean;
}

export interface UpdateEntryRequest {
	kb: string;
	title?: string;
	body?: string;
	importance?: number;
	tags?: string[];
	metadata?: Record<string, unknown>;
}

export interface CreateResponse {
	created: boolean;
	id: string;
	kb_name: string;
	file_path: string;
}

export interface UpdateResponse {
	updated: boolean;
	id: string;
}

export interface DeleteResponse {
	deleted: boolean;
	id: string;
}

export interface TagCount {
	name: string;
	count: number;
}

export interface TagsResponse {
	count: number;
	tags: TagCount[];
}

export interface TagTreeNode {
	name: string;
	full_path: string;
	count: number;
	children: TagTreeNode[];
}

export interface TagTreeResponse {
	tree: TagTreeNode[];
}

export interface TimelineEvent {
	id: string;
	date: string;
	title: string;
	importance: number;
	tags: string[];
}

export interface TimelineResponse {
	count: number;
	date_from?: string;
	date_to?: string;
	events: TimelineEvent[];
}

export interface StatsResponse {
	total_entries: number;
	kbs: Record<string, unknown>;
	total_tags: number;
	total_links: number;
	type_counts: { entry_type: string; count: number }[];
}

export interface ApiError {
	code: string;
	message: string;
	hint?: string;
}

// Wikilinks

export interface EntryTitle {
	id: string;
	title: string;
	kb_name: string;
	entry_type: string;
	aliases?: string[];
}

export interface EntryTitlesResponse {
	entries: EntryTitle[];
}

export interface ResolveResponse {
	resolved: boolean;
	entry: EntryTitle | null;
}

export interface ResolveBatchResponse {
	resolved: Record<string, boolean>;
}

export interface WantedPage {
	target_id: string;
	target_kb: string;
	ref_count: number;
	referenced_by: string[];
}

export interface WantedPagesResponse {
	count: number;
	pages: WantedPage[];
}

// Starred Entries

export interface StarredEntryItem {
	entry_id: string;
	kb_name: string;
	title?: string | null;
	sort_order: number;
	created_at: string;
}

export interface StarredEntryListResponse {
	count: number;
	starred: StarredEntryItem[];
}

export interface StarEntryResponse {
	starred: boolean;
	entry_id: string;
	kb_name: string;
}

export interface UnstarEntryResponse {
	unstarred: boolean;
	entry_id: string;
}

export interface ReorderStarredItem {
	entry_id: string;
	kb_name: string;
	sort_order: number;
}

export interface ReorderStarredRequest {
	entries: ReorderStarredItem[];
}

export interface ReorderStarredResponse {
	reordered: boolean;
	count: number;
}

// Templates

export interface TemplateSummary {
	name: string;
	description: string;
	entry_type: string;
}

export interface TemplateListResponse {
	templates: TemplateSummary[];
	total: number;
}

export interface TemplateDetail {
	name: string;
	description: string;
	entry_type: string;
	frontmatter: Record<string, unknown>;
	body: string;
}

export interface RenderedTemplate {
	entry_type: string;
	frontmatter: Record<string, unknown>;
	body: string;
}

// Daily Notes

export interface DailyDatesResponse {
	dates: string[];
}

// Settings

export interface SettingsResponse {
	/** Secret settings (e.g. ai.apiKey) carry a fixed mask here, never their value. */
	settings: Record<string, string>;
	/** Keys whose value in `settings` is the mask: set on the server, not readable. */
	masked?: string[];
}

export interface SettingResponse {
	key: string;
	value: string | null;
}

// AI

export interface AIStatusResponse {
	configured: boolean;
	provider: string;
	model: string | null;
}

export interface AISummarizeRequest {
	entry_id: string;
	kb_name: string;
}

export interface AISummarizeResponse {
	summary: string;
}

export interface AITagSuggestion {
	name: string;
	is_new: boolean;
	reason: string;
}

export interface AIAutoTagResponse {
	suggested_tags: AITagSuggestion[];
}

export interface AILinkSuggestion {
	target_id: string;
	target_kb: string;
	target_title: string;
	reason: string;
}

export interface AILinkSuggestResponse {
	suggestions: AILinkSuggestion[];
}

// Graph

export interface GraphNode {
	id: string;
	kb_name: string;
	title: string;
	entry_type: string;
	link_count: number;
	centrality?: number;
}

export interface GraphEdge {
	source_id: string;
	source_kb: string;
	target_id: string;
	target_kb: string;
	relation: string | null;
}

export interface GraphResponse {
	nodes: GraphNode[];
	edges: GraphEdge[];
}

export interface ChatMessage {
	role: 'user' | 'assistant';
	content: string;
}

export interface ChatRequest {
	messages: ChatMessage[];
	kb?: string;
	entry_id?: string;
}

export interface ChatSourceEntry {
	id: string;
	kb_name: string;
	title: string;
	snippet: string;
}

export interface EntryVersion {
	commit_hash: string;
	author_name?: string;
	author_email?: string;
	commit_date: string;
	message?: string;
	change_type?: string;
}

export interface VersionListResponse {
	entry_id: string;
	kb_name: string;
	count: number;
	versions: EntryVersion[];
}

// Plugins

export interface PluginInfo {
	name: string;
	entry_types: string[];
	kb_types: string[];
	tools: string[];
	hooks: string[];
	has_cli: boolean;
}

export interface PluginListResponse {
	plugins: PluginInfo[];
	total: number;
}

export interface PluginDetail {
	name: string;
	entry_types: Record<string, string>;
	kb_types: string[];
	hooks: Record<string, number>;
	tools: Record<string, { tier: string; description: string }>;
}

// Collections

export interface CollectionResponse {
	id: string;
	title: string;
	description: string;
	source_type: string;
	icon: string;
	view_config: Record<string, unknown>;
	entry_count: number;
	kb_name: string;
	folder_path: string;
	query?: string;
	tags: string[];
}

export interface CollectionListResponse {
	collections: CollectionResponse[];
	total: number;
}

export interface CollectionEntriesResponse {
	entries: EntryResponse[];
	total: number;
	collection_id: string;
}

// Blocks

export interface BlockResponse {
	block_id: string;
	heading: string | null;
	content: string;
	position: number;
	block_type: string;
}

export interface BlockListResponse {
	entry_id: string;
	kb_name: string;
	blocks: BlockResponse[];
	total: number;
}

// Import/Export

export interface ImportResult {
	imported: number;
	errors: number;
	entries: Array<{ id: string; title: string }>;
	error_details: Array<{ title: string; error: string }>;
}

// GitHub Connection

export interface GitHubConnectionStatus {
	connected: boolean;
	github_configured: boolean;
	username?: string;
	scopes?: string;
	reason?: string;
}

// Repos

export interface RepoInfo {
	id: number;
	name: string;
	local_path: string;
	remote_url: string | null;
	owner: string | null;
	visibility: string;
	default_branch: string;
	is_fork: boolean;
	last_synced: string | null;
	last_synced_commit: string | null;
	kb_count?: number;
	kb_names?: string[];
	total_entries?: number;
}

export interface RepoListResponse {
	repos: RepoInfo[];
}

export interface SyncResult {
	success: boolean;
	repos?: Record<string, { success: boolean; message?: string; changes?: number; reindexed?: number; error?: string }>;
	error?: string;
}

export interface PRResult {
	success: boolean;
	pr_url?: string;
	pr_number?: number;
	error?: string;
}

export interface GitHubRepoInfo {
	full_name: string;
	description: string | null;
	html_url: string;
	clone_url: string;
	private: boolean;
	fork: boolean;
}

// Web Clipper

export interface ClipRequest {
	url: string;
	kb: string;
	title?: string;
	tags?: string[];
	entry_type?: string;
	allow_undeclared?: boolean;
}

export interface ClipResponse {
	created: boolean;
	id: string;
	kb_name: string;
	title: string;
	source_url: string;
}

// Pending Changes (Review & Publish)

export interface PendingChange {
	change_type: 'created' | 'modified' | 'deleted';
	file_path: string;
	title: string;
	entry_type: string;
	entry_id: string | null;
	current_body: string | null;
	previous_body: string | null;
}

export interface PendingChangesResponse {
	changes: PendingChange[];
	summary: {
		total: number;
		created: number;
		modified: number;
		deleted: number;
	};
}

export interface PublishResponse {
	success: boolean;
	commit_hash: string | null;
	entries_published: number;
	message: string;
	push_error?: string | null;
	error?: string;
}

// Index Management

export interface EmbedStatusResponse {
	pending: number;
	processing: number;
	failed: number;
	total: number;
}

export interface IndexJob {
	id: string;
	kb: string;
	operation: string;
	status: string;
	progress?: number;
	result?: Record<string, unknown>;
	error?: string;
	created_at?: string;
}

export interface IndexJobsResponse {
	jobs: IndexJob[];
}
