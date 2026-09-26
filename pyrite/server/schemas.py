"""
Pydantic models for the pyrite REST API.

Extracted from api.py to reduce file size and improve organization.
"""

from typing import Any

from pydantic import BaseModel, Field

# =============================================================================
# Knowledge Bases
# =============================================================================


class KBInfo(BaseModel):
    """Knowledge base information."""

    name: str
    type: str
    path: str
    entries: int
    indexed: bool
    source: str = "user"
    description: str = ""
    read_only: bool = False
    last_indexed: str | None = None
    shortname: str | None = None
    default_role: str | None = None
    #: False for a KB whose default_role an operator set by hand in
    #: config.yaml: `PUT /api/kbs/{name}/default-role` refuses it there.
    default_role_editable: bool = True


class KBListResponse(BaseModel):
    """Response for listing knowledge bases."""

    kbs: list[KBInfo]
    total: int


class KBHealthResponse(BaseModel):
    """Response for KB health check."""

    name: str
    healthy: bool
    path_exists: bool
    path: str
    file_count: int
    entry_count: int
    last_indexed: str | None = None
    source: str = "user"


class KBReindexResponse(BaseModel):
    """Response for KB reindex."""

    name: str
    added: int
    updated: int
    removed: int
    malformed: list[dict[str, str]] = Field(default_factory=list)


# =============================================================================
# Search
# =============================================================================


class SearchResult(BaseModel):
    """Single search result."""

    id: str
    kb_name: str
    entry_type: str
    title: str
    snippet: str | None = None
    date: str | None = None
    importance: int | None = None
    tags: list[str] = []


class SearchResponse(BaseModel):
    """Response for search queries."""

    query: str
    count: int
    results: list[SearchResult]
    #: Anything the search could not do as asked — today, a filter a backend's
    #: vector leg cannot honour, which costs the semantic leg entirely rather
    #: than returning rows that violate the filter. ``None`` here, and *omitted
    #: from the JSON* — the route sets ``response_model_exclude_none`` — when
    #: every filter was applied on every leg that ran. See
    #: ``SearchService.search``'s docstring, "what a search response owes its
    #: caller", for the convention this follows (#56).
    warnings: list[str] | None = None


# =============================================================================
# Entries
# =============================================================================


class EntryBase(BaseModel):
    """Base fields for entries."""

    title: str
    body: str | None = None
    tags: list[str] = []
    importance: int | None = Field(None, ge=1, le=10)


class EventCreate(EntryBase):
    """Fields for creating an event."""

    date: str = Field(..., pattern=r"^\d{4}-\d{2}-\d{2}$")
    participants: list[str] = []
    status: str = "confirmed"


class PersonCreate(EntryBase):
    """Fields for creating a person entry."""

    role: str | None = None


class EntryResponse(BaseModel):
    """Full entry response."""

    id: str
    kb_name: str
    entry_type: str
    title: str
    body: str | None = None
    summary: str | None = None
    date: str | None = None
    importance: int | None = None
    status: str | None = None
    tags: list[str] = []
    participants: list[str] = []
    sources: list[dict] = []
    outlinks: list[dict] = []
    backlinks: list[dict] = []
    file_path: str | None = None
    metadata: dict = {}
    created_at: str | None = None
    updated_at: str | None = None


# =============================================================================
# Timeline
# =============================================================================


class TimelineEvent(BaseModel):
    """Timeline event."""

    id: str
    date: str
    title: str
    importance: int
    participants: list[str] = []
    tags: list[str] = []


class TimelineResponse(BaseModel):
    """Response for timeline queries."""

    count: int
    date_from: str | None
    date_to: str | None
    events: list[TimelineEvent]


# =============================================================================
# Tags
# =============================================================================


class TagCount(BaseModel):
    """Tag with count."""

    name: str
    count: int


class TagsResponse(BaseModel):
    """Response for tags list."""

    count: int
    tags: list[TagCount]


class TagTreeNode(BaseModel):
    """Hierarchical tag tree node."""

    name: str
    full_path: str
    count: int = 0
    children: list["TagTreeNode"] = []


TagTreeNode.model_rebuild()


class TagTreeResponse(BaseModel):
    """Response for tag tree."""

    tree: list[TagTreeNode]


# =============================================================================
# Graph
# =============================================================================


class GraphNode(BaseModel):
    """Node in the knowledge graph."""

    id: str
    kb_name: str
    title: str
    entry_type: str
    link_count: int = 0
    centrality: float = 0.0


class GraphEdge(BaseModel):
    """Edge in the knowledge graph."""

    source_id: str
    source_kb: str
    target_id: str
    target_kb: str
    relation: str | None = None


class GraphResponse(BaseModel):
    """Response for graph queries."""

    nodes: list[GraphNode]
    edges: list[GraphEdge]


# =============================================================================
# Admin
# =============================================================================


class AIStatusResponse(BaseModel):
    """AI/LLM configuration status."""

    configured: bool
    provider: str
    model: str | None = None


class AIEntryRequest(BaseModel):
    """Request body for AI actions on an entry."""

    entry_id: str
    kb_name: str


class AISummarizeResponse(BaseModel):
    """Response for AI summarize."""

    summary: str


class AITagSuggestion(BaseModel):
    """Single tag suggestion from AI."""

    name: str
    is_new: bool
    reason: str


class AIAutoTagResponse(BaseModel):
    """Response for AI auto-tag."""

    suggested_tags: list[AITagSuggestion]


class AILinkSuggestion(BaseModel):
    """Single link suggestion from AI."""

    target_id: str
    target_kb: str
    target_title: str
    reason: str


class AILinkSuggestResponse(BaseModel):
    """Response for AI suggest-links."""

    suggestions: list[AILinkSuggestion]


class ChatMessageSchema(BaseModel):
    """Single chat message."""

    role: str
    content: str


class AIChatRequest(BaseModel):
    """Request body for AI chat."""

    messages: list[ChatMessageSchema]
    kb: str | None = None
    entry_id: str | None = None


class StatsResponse(BaseModel):
    """Index statistics."""

    total_entries: int
    kbs: dict = {}
    total_tags: int = 0
    total_links: int = 0
    type_counts: list[dict] = []


class CreateResponse(BaseModel):
    """Response for create operations."""

    created: bool
    id: str
    kb_name: str
    file_path: str
    #: Non-blocking schema findings from the write pipeline (#378).
    warnings: list[dict[str, Any]] = []


class UpdateResponse(BaseModel):
    """Response for update operations."""

    updated: bool
    id: str
    #: Non-blocking schema findings from the write pipeline (#378).
    warnings: list[dict[str, Any]] = []


class DeleteResponse(BaseModel):
    """Response for delete operations."""

    deleted: bool
    id: str


class SiteCacheSyncStatus(BaseModel):
    """Outcome of the site-cache render `wait=true` triggers alongside a sync.

    Independent of the sync's own success (#349's cold-read round): a sync
    that added, updated or removed entries has already committed by the time
    the render runs, so a render failure is reported here rather than
    failing the whole request. ``error`` is a generic message for callers;
    the real exception and its traceback go to the server log only.

    ``rendered`` means the render ran to completion (#408); it says nothing
    about whether every page came out clean. ``errors`` is always present --
    it is 0 when the render didn't get far enough to produce per-entry stats
    (e.g. the service failed to construct) and the count of per-entry
    failures otherwise -- so a caller reads ``rendered and errors == 0`` as
    "all pages are fresh."
    """

    rendered: bool
    errors: int = 0
    error: str | None = None


class SyncResponse(BaseModel):
    """Response for index sync."""

    synced: bool
    added: int
    updated: int
    removed: int
    site_cache: SiteCacheSyncStatus | None = None


class ErrorResponse(BaseModel):
    """Error response."""

    code: str
    message: str
    hint: str | None = None


# =============================================================================
# Web App Request/Response Models
# =============================================================================


class CreateEntryRequest(BaseModel):
    """JSON body for creating an entry."""

    kb: str
    entry_type: str = "note"
    title: str
    body: str = ""
    date: str | None = None
    importance: int | None = Field(None, ge=1, le=10)
    tags: list[str] = []
    participants: list[str] = []
    role: str | None = None
    metadata: dict[str, Any] = {}
    #: Allow a type the KB's kb.yaml does not declare (refused otherwise).
    allow_undeclared: bool = False


class UpdateEntryRequest(BaseModel):
    """JSON body for updating an entry."""

    kb: str
    title: str | None = None
    body: str | None = None
    importance: int | None = Field(None, ge=1, le=10)
    tags: list[str] | None = None
    metadata: dict[str, Any] | None = None


class PatchEntryRequest(BaseModel):
    """Request to update a single field on an entry."""

    kb: str
    field: str
    value: str


class EntryListResponse(BaseModel):
    """Paginated entry list response."""

    entries: list[EntryResponse]
    total: int
    limit: int
    offset: int


class EntryTypesResponse(BaseModel):
    """Response for distinct entry types."""

    types: list[str]


# =============================================================================
# Wikilinks
# =============================================================================


class ResolveBatchRequest(BaseModel):
    """Request body for batch wikilink resolution."""

    targets: list[str]
    kb: str | None = None


class ResolveBatchResponse(BaseModel):
    """Response for batch wikilink resolution."""

    resolved: dict[str, bool]


class WantedPage(BaseModel):
    """A link target that doesn't exist as an entry."""

    target_id: str
    target_kb: str
    ref_count: int
    referenced_by: list[str] = []


class WantedPagesResponse(BaseModel):
    """Response for wanted pages listing."""

    count: int
    pages: list[WantedPage]


class EntryTitle(BaseModel):
    """Lightweight entry reference for autocomplete."""

    id: str
    title: str
    kb_name: str
    entry_type: str
    aliases: list[str] = []


class EntryTitlesResponse(BaseModel):
    """Response for entry titles listing."""

    entries: list[EntryTitle]


class ResolveResponse(BaseModel):
    """Response for wikilink target resolution."""

    resolved: bool
    entry: EntryTitle | None = None
    heading: str | None = None
    block_id: str | None = None
    block_content: str | None = None


# =============================================================================
# Starred Entries
# =============================================================================


class StarredEntryItem(BaseModel):
    """A single starred entry."""

    entry_id: str
    kb_name: str
    title: str | None = None
    sort_order: int = 0
    created_at: str


class StarredEntryListResponse(BaseModel):
    """Response for listing starred entries."""

    count: int
    starred: list[StarredEntryItem]


class StarEntryRequest(BaseModel):
    """Request to star an entry."""

    entry_id: str
    kb_name: str


class StarEntryResponse(BaseModel):
    """Response for starring an entry."""

    starred: bool
    entry_id: str
    kb_name: str


class UnstarEntryResponse(BaseModel):
    """Response for unstarring an entry."""

    unstarred: bool
    entry_id: str


class ReorderStarredItem(BaseModel):
    """Single item in a reorder request."""

    entry_id: str
    kb_name: str
    sort_order: int


class ReorderStarredRequest(BaseModel):
    """Request to reorder starred entries."""

    entries: list[ReorderStarredItem]


class ReorderStarredResponse(BaseModel):
    """Response for reordering starred entries."""

    reordered: bool
    count: int


# =============================================================================
# Templates
# =============================================================================


class TemplateSummary(BaseModel):
    """Template list item."""

    name: str
    description: str
    entry_type: str


class TemplateListResponse(BaseModel):
    """Response for listing templates."""

    templates: list[TemplateSummary]
    total: int


class TemplateDetail(BaseModel):
    """Full template with frontmatter and body."""

    name: str
    description: str
    entry_type: str
    frontmatter: dict[str, Any] = {}
    body: str


class RenderTemplateRequest(BaseModel):
    """Request body for rendering a template."""

    variables: dict[str, str] = {}


class RenderedTemplate(BaseModel):
    """Rendered template output."""

    entry_type: str
    frontmatter: dict[str, Any] = {}
    body: str


# =============================================================================
# Daily Notes
# =============================================================================


class DailyDatesResponse(BaseModel):
    """Response listing dates that have daily notes."""

    dates: list[str]


# =============================================================================
# Version History
# =============================================================================


class EntryVersionResponse(BaseModel):
    """Single version history entry."""

    commit_hash: str
    author_name: str | None = None
    author_email: str | None = None
    commit_date: str
    message: str | None = None
    change_type: str | None = None


class VersionListResponse(BaseModel):
    """Response for version history."""

    entry_id: str
    kb_name: str
    count: int
    versions: list[EntryVersionResponse]


# =============================================================================
# Settings
# =============================================================================


class SettingsResponse(BaseModel):
    """Response for all settings.

    ``masked`` names the secret settings that are set; their value in
    ``settings`` is a fixed mask, never the secret itself.
    """

    settings: dict[str, str]
    masked: list[str] = []


class SettingResponse(BaseModel):
    """Response for a single setting."""

    key: str
    value: str | None


class SettingUpdateRequest(BaseModel):
    """Request to update a setting."""

    value: str


class BulkSettingsUpdateRequest(BaseModel):
    """Request to bulk update settings."""

    settings: dict[str, str]


# =============================================================================
# Collections
# =============================================================================


class CollectionResponse(BaseModel):
    """Single collection response."""

    id: str
    title: str
    description: str = ""
    source_type: str = "folder"
    icon: str = ""
    view_config: dict = {}
    entry_count: int = 0
    kb_name: str = ""
    folder_path: str = ""
    query: str = ""  # For virtual collections
    tags: list[str] = []


class CollectionListResponse(BaseModel):
    """Response for listing collections."""

    collections: list[CollectionResponse]
    total: int


class CollectionEntriesResponse(BaseModel):
    """Response for entries within a collection."""

    entries: list[EntryResponse]
    total: int
    collection_id: str


class CreateCollectionRequest(BaseModel):
    """Request body for creating a new virtual collection."""

    kb: str
    title: str
    query: str
    description: str | None = None
    icon: str | None = None
    view_config: dict | None = None
    collection_type: str = "generic"


class QueryPreviewRequest(BaseModel):
    """Request to preview a collection query without saving."""

    query: str
    kb: str | None = None
    limit: int = 20


class QueryPreviewResponse(BaseModel):
    """Response for query preview."""

    entries: list[EntryResponse]
    total: int
    query_parsed: dict  # Shows how the query was interpreted


# =============================================================================
# Blocks
# =============================================================================


class BlockResponse(BaseModel):
    """Single block from an entry."""

    block_id: str
    heading: str | None = None
    content: str
    position: int
    block_type: str


class BlockListResponse(BaseModel):
    """Response for entry blocks."""

    entry_id: str
    kb_name: str
    blocks: list[BlockResponse]
    total: int


# =============================================================================
# Web Clipper
# =============================================================================


class ClipRequest(BaseModel):
    """Request to clip a URL."""

    url: str
    kb: str
    title: str | None = None
    tags: list[str] = []
    entry_type: str = "note"
    #: Allow a type the KB's kb.yaml does not declare (refused otherwise).
    allow_undeclared: bool = False


class ClipResponse(BaseModel):
    """Response for clip operations."""

    created: bool
    id: str
    kb_name: str
    title: str
    source_url: str


# =============================================================================
# Repos
# =============================================================================


class RepoInfo(BaseModel):
    """Repository information."""

    id: int
    name: str
    local_path: str
    remote_url: str | None = None
    owner: str | None = None
    visibility: str = "public"
    default_branch: str = "main"
    is_fork: bool = False
    last_synced: str | None = None
    last_synced_commit: str | None = None
    kb_count: int = 0
    kb_names: list[str] = []
    total_entries: int = 0


class RepoListResponse(BaseModel):
    """Response for listing repos."""

    repos: list[RepoInfo]


class SubscribeRequest(BaseModel):
    """Request to subscribe to a repo."""

    remote_url: str
    name: str | None = None
    branch: str = "main"


class ForkRequest(BaseModel):
    """Request to fork a repo."""

    remote_url: str


class PRRequest(BaseModel):
    """Request to create a pull request."""

    title: str
    body: str = ""
    branch: str | None = None
