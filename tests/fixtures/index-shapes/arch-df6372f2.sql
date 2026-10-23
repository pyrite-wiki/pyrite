-- Index shape arch-df6372f2: arch/2026-10 at df6372f2 (2026-10-17): schema 28 meant entry.aliases, so link.target_prefix is absent while 28 is recorded (#1260).
-- Written by build_index_shapes.py from git ref df6372f2; do not edit.
CREATE TABLE tag (
	id INTEGER NOT NULL,
	name VARCHAR NOT NULL,
	PRIMARY KEY (id)
);
CREATE TABLE user (
	id INTEGER NOT NULL,
	github_login VARCHAR NOT NULL,
	github_id INTEGER NOT NULL,
	display_name VARCHAR,
	avatar_url VARCHAR,
	email VARCHAR,
	created_at VARCHAR DEFAULT 'CURRENT_TIMESTAMP',
	last_seen VARCHAR,
	PRIMARY KEY (id),
	UNIQUE (github_id)
);
CREATE TABLE local_user (
	id INTEGER NOT NULL,
	username VARCHAR NOT NULL,
	display_name VARCHAR,
	password_hash VARCHAR NOT NULL,
	role VARCHAR DEFAULT 'read' NOT NULL,
	global_access INTEGER DEFAULT '0' NOT NULL,
	created_at VARCHAR DEFAULT 'CURRENT_TIMESTAMP',
	updated_at VARCHAR,
	auth_provider VARCHAR DEFAULT 'local',
	provider_id VARCHAR,
	avatar_url VARCHAR,
	ephemeral_kb_count INTEGER DEFAULT '0',
	usage_tier VARCHAR DEFAULT 'default',
	github_access_token VARCHAR,
	github_token_scopes VARCHAR,
	PRIMARY KEY (id)
);
CREATE TABLE repo (
	id INTEGER NOT NULL,
	name VARCHAR NOT NULL,
	local_path VARCHAR NOT NULL,
	remote_url VARCHAR,
	owner VARCHAR,
	visibility VARCHAR,
	default_branch VARCHAR,
	upstream_repo_id INTEGER,
	is_fork INTEGER,
	last_synced_commit VARCHAR,
	last_synced VARCHAR,
	created_at VARCHAR DEFAULT 'CURRENT_TIMESTAMP',
	PRIMARY KEY (id),
	FOREIGN KEY(upstream_repo_id) REFERENCES repo (id) ON DELETE SET NULL
);
CREATE TABLE starred_entry (
	id INTEGER NOT NULL,
	user_id INTEGER DEFAULT '0' NOT NULL,
	entry_id VARCHAR NOT NULL,
	kb_name VARCHAR NOT NULL,
	sort_order INTEGER,
	created_at VARCHAR NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT uq_starred_entry UNIQUE (user_id, entry_id, kb_name)
);
CREATE TABLE setting (
	id INTEGER NOT NULL,
	"key" VARCHAR NOT NULL,
	value TEXT,
	updated_at VARCHAR DEFAULT 'CURRENT_TIMESTAMP',
	PRIMARY KEY (id)
);
CREATE TABLE worktree (
	id INTEGER NOT NULL,
	user_id INTEGER NOT NULL,
	username VARCHAR NOT NULL,
	kb_name VARCHAR NOT NULL,
	repo_path VARCHAR NOT NULL,
	branch VARCHAR NOT NULL,
	worktree_path VARCHAR NOT NULL,
	diff_db_path VARCHAR NOT NULL,
	status VARCHAR DEFAULT 'active' NOT NULL,
	submitted_at VARCHAR,
	merged_at VARCHAR,
	rejected_at VARCHAR,
	feedback TEXT,
	created_at VARCHAR DEFAULT 'CURRENT_TIMESTAMP',
	updated_at VARCHAR DEFAULT 'CURRENT_TIMESTAMP',
	PRIMARY KEY (id)
);
CREATE TABLE kb (
	name VARCHAR NOT NULL,
	kb_type VARCHAR NOT NULL,
	path VARCHAR NOT NULL,
	description TEXT,
	last_indexed VARCHAR,
	entry_count INTEGER,
	source VARCHAR DEFAULT 'user',
	default_role VARCHAR,
	repo_id INTEGER,
	repo_subpath VARCHAR,
	PRIMARY KEY (name),
	FOREIGN KEY(repo_id) REFERENCES repo (id) ON DELETE SET NULL
);
CREATE TABLE session (
	id INTEGER NOT NULL,
	token_hash VARCHAR NOT NULL,
	user_id INTEGER NOT NULL,
	created_at VARCHAR DEFAULT 'CURRENT_TIMESTAMP',
	expires_at VARCHAR NOT NULL,
	last_used VARCHAR,
	PRIMARY KEY (id),
	FOREIGN KEY(user_id) REFERENCES local_user (id) ON DELETE CASCADE
);
CREATE TABLE kb_permission (
	id INTEGER NOT NULL,
	user_id INTEGER NOT NULL,
	kb_name VARCHAR NOT NULL,
	role VARCHAR NOT NULL,
	granted_by INTEGER,
	created_at VARCHAR DEFAULT 'CURRENT_TIMESTAMP',
	PRIMARY KEY (id),
	CONSTRAINT uq_kb_permission_user_kb UNIQUE (user_id, kb_name),
	FOREIGN KEY(user_id) REFERENCES local_user (id) ON DELETE CASCADE,
	FOREIGN KEY(granted_by) REFERENCES local_user (id)
);
CREATE TABLE workspace_repo (
	user_id INTEGER NOT NULL,
	repo_id INTEGER NOT NULL,
	added_at VARCHAR DEFAULT 'CURRENT_TIMESTAMP',
	role VARCHAR,
	auto_sync INTEGER,
	sync_interval INTEGER,
	PRIMARY KEY (user_id, repo_id),
	FOREIGN KEY(user_id) REFERENCES user (id) ON DELETE CASCADE,
	FOREIGN KEY(repo_id) REFERENCES repo (id) ON DELETE CASCADE
);
CREATE TABLE entry (
	id VARCHAR NOT NULL,
	kb_name VARCHAR NOT NULL,
	entry_type VARCHAR NOT NULL,
	title VARCHAR NOT NULL,
	body TEXT,
	summary TEXT,
	file_path VARCHAR,
	date VARCHAR,
	importance INTEGER,
	status VARCHAR,
	location VARCHAR,
	assignee VARCHAR,
	assigned_at VARCHAR,
	priority VARCHAR,
	due_date VARCHAR,
	start_date VARCHAR,
	end_date VARCHAR,
	coordinates VARCHAR,
	fips VARCHAR,
	state VARCHAR,
	lifecycle VARCHAR DEFAULT 'active',
	metadata TEXT,
	aliases TEXT,
	created_at VARCHAR,
	updated_at VARCHAR,
	indexed_at VARCHAR DEFAULT 'CURRENT_TIMESTAMP',
	content_hash VARCHAR(64),
	file_mtime_ns BIGINT,
	file_size BIGINT,
	created_by VARCHAR,
	modified_by VARCHAR,
	PRIMARY KEY (id, kb_name),
	FOREIGN KEY(kb_name) REFERENCES kb (name) ON DELETE CASCADE
);
CREATE TABLE entry_tag (
	entry_id VARCHAR NOT NULL,
	kb_name VARCHAR NOT NULL,
	tag_id INTEGER NOT NULL,
	PRIMARY KEY (entry_id, kb_name, tag_id),
	FOREIGN KEY(entry_id, kb_name) REFERENCES entry (id, kb_name) ON DELETE CASCADE,
	FOREIGN KEY(tag_id) REFERENCES tag (id) ON DELETE CASCADE
);
CREATE TABLE link (
	id INTEGER NOT NULL,
	source_id VARCHAR NOT NULL,
	source_kb VARCHAR NOT NULL,
	target_id VARCHAR NOT NULL,
	target_kb VARCHAR NOT NULL,
	relation VARCHAR NOT NULL,
	inverse_relation VARCHAR NOT NULL,
	note TEXT,
	created_at VARCHAR DEFAULT 'CURRENT_TIMESTAMP',
	PRIMARY KEY (id),
	FOREIGN KEY(source_id, source_kb) REFERENCES entry (id, kb_name) ON DELETE CASCADE
);
CREATE TABLE entry_ref (
	id INTEGER NOT NULL,
	source_id VARCHAR NOT NULL,
	source_kb VARCHAR NOT NULL,
	target_id VARCHAR NOT NULL,
	target_kb VARCHAR NOT NULL,
	field_name VARCHAR NOT NULL,
	target_type VARCHAR,
	PRIMARY KEY (id),
	FOREIGN KEY(source_id, source_kb) REFERENCES entry (id, kb_name) ON DELETE CASCADE
);
CREATE TABLE edge_endpoint (
	id INTEGER NOT NULL,
	edge_entry_id VARCHAR NOT NULL,
	edge_entry_kb VARCHAR NOT NULL,
	role VARCHAR NOT NULL,
	field_name VARCHAR NOT NULL,
	endpoint_id VARCHAR NOT NULL,
	endpoint_kb VARCHAR NOT NULL,
	edge_type VARCHAR NOT NULL,
	created_at VARCHAR DEFAULT 'CURRENT_TIMESTAMP',
	PRIMARY KEY (id),
	FOREIGN KEY(edge_entry_id, edge_entry_kb) REFERENCES entry (id, kb_name) ON DELETE CASCADE
);
CREATE TABLE source (
	id INTEGER NOT NULL,
	entry_id VARCHAR NOT NULL,
	kb_name VARCHAR NOT NULL,
	title VARCHAR NOT NULL,
	url VARCHAR,
	outlet VARCHAR,
	date VARCHAR,
	verified INTEGER,
	PRIMARY KEY (id),
	FOREIGN KEY(entry_id, kb_name) REFERENCES entry (id, kb_name) ON DELETE CASCADE
);
CREATE TABLE entry_version (
	id INTEGER NOT NULL,
	entry_id VARCHAR NOT NULL,
	kb_name VARCHAR NOT NULL,
	commit_hash VARCHAR(40) NOT NULL,
	author_name VARCHAR,
	author_email VARCHAR,
	author_github_login VARCHAR,
	commit_date VARCHAR NOT NULL,
	message TEXT,
	diff_summary TEXT,
	change_type VARCHAR,
	file_path VARCHAR,
	PRIMARY KEY (id),
	FOREIGN KEY(entry_id, kb_name) REFERENCES entry (id, kb_name) ON DELETE CASCADE
);
CREATE TABLE block (
	id INTEGER NOT NULL,
	entry_id VARCHAR NOT NULL,
	kb_name VARCHAR NOT NULL,
	block_id VARCHAR NOT NULL,
	heading VARCHAR,
	content TEXT NOT NULL,
	position INTEGER NOT NULL,
	block_type VARCHAR NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(entry_id, kb_name) REFERENCES entry (id, kb_name) ON DELETE CASCADE
);
CREATE TABLE review (
	id INTEGER NOT NULL,
	entry_id VARCHAR NOT NULL,
	kb_name VARCHAR NOT NULL,
	content_hash VARCHAR(40) NOT NULL,
	reviewer VARCHAR NOT NULL,
	reviewer_type VARCHAR NOT NULL,
	result VARCHAR NOT NULL,
	details TEXT,
	created_at VARCHAR DEFAULT 'CURRENT_TIMESTAMP',
	PRIMARY KEY (id),
	FOREIGN KEY(entry_id, kb_name) REFERENCES entry (id, kb_name) ON DELETE CASCADE
);
CREATE VIRTUAL TABLE entry_fts USING fts5(
    id,
    kb_name,
    entry_type,
    title,
    body,
    summary,
    location,
    content='entry',
    content_rowid='rowid',
    tokenize='porter unicode61'
);
CREATE TABLE schema_version (
                version INTEGER PRIMARY KEY,
                description TEXT NOT NULL,
                applied_at TEXT NOT NULL
            );
CREATE TABLE invite_code (
            code TEXT PRIMARY KEY,
            created_by TEXT NOT NULL,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            expires_at TEXT,
            used_by TEXT,
            used_at TEXT,
            role TEXT DEFAULT 'write',
            note TEXT DEFAULT ''
        );
CREATE TABLE user_api_key (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            provider TEXT NOT NULL,
            encrypted_key TEXT NOT NULL,
            model TEXT DEFAULT '',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(user_id, provider)
        );
CREATE TABLE oauth_state (
            state TEXT PRIMARY KEY,
            flow TEXT NOT NULL DEFAULT 'login',
            user_id INTEGER,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            expires_at TEXT NOT NULL
        );
CREATE TABLE llm_usage (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            provider TEXT NOT NULL,
            model TEXT NOT NULL,
            kind TEXT NOT NULL DEFAULT 'chat',
            input_tokens INTEGER NOT NULL DEFAULT 0,
            output_tokens INTEGER NOT NULL DEFAULT 0,
            cache_read_tokens INTEGER NOT NULL DEFAULT 0,
            cache_creation_tokens INTEGER NOT NULL DEFAULT 0,
            estimated_cost_usd REAL NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
CREATE UNIQUE INDEX ix_tag_name ON tag (name);
CREATE UNIQUE INDEX ix_user_github_login ON user (github_login);
CREATE UNIQUE INDEX ix_local_user_username ON local_user (username);
CREATE UNIQUE INDEX ix_repo_name ON repo (name);
CREATE INDEX idx_starred_entry_user ON starred_entry (user_id, sort_order);
CREATE INDEX idx_starred_entry_sort ON starred_entry (sort_order);
CREATE INDEX idx_starred_entry_kb ON starred_entry (kb_name);
CREATE UNIQUE INDEX ix_setting_key ON setting ("key");
CREATE UNIQUE INDEX ix_session_token_hash ON session (token_hash);
CREATE INDEX idx_kb_permission_user ON kb_permission (user_id);
CREATE INDEX idx_kb_permission_kb ON kb_permission (kb_name);
CREATE INDEX idx_entry_tag_tag ON entry_tag (tag_id);
CREATE INDEX idx_entry_tag_entry ON entry_tag (entry_id, kb_name);
CREATE INDEX idx_link_source ON link (source_id, source_kb);
CREATE INDEX idx_link_target ON link (target_id, target_kb);
CREATE INDEX idx_link_relation ON link (relation);
CREATE INDEX idx_entry_ref_target ON entry_ref (target_id, target_kb);
CREATE INDEX idx_entry_ref_field ON entry_ref (field_name);
CREATE INDEX idx_entry_ref_source ON entry_ref (source_id, source_kb);
CREATE INDEX idx_edge_endpoint_type ON edge_endpoint (edge_type, edge_entry_kb);
CREATE INDEX idx_edge_endpoint_target ON edge_endpoint (endpoint_id, endpoint_kb);
CREATE INDEX idx_edge_endpoint_edge ON edge_endpoint (edge_entry_id, edge_entry_kb);
CREATE INDEX idx_source_entry ON source (entry_id, kb_name);
CREATE INDEX idx_source_url ON source (url);
CREATE INDEX ix_entry_version_author_github_login ON entry_version (author_github_login);
CREATE INDEX idx_entry_version_entry ON entry_version (entry_id, kb_name);
CREATE INDEX ix_entry_version_commit_date ON entry_version (commit_date);
CREATE INDEX ix_entry_version_commit_hash ON entry_version (commit_hash);
CREATE INDEX idx_block_entry ON block (entry_id, kb_name);
CREATE INDEX idx_block_block_id ON block (block_id);
CREATE INDEX idx_review_content_hash ON review (content_hash);
CREATE INDEX idx_review_reviewer ON review (reviewer);
CREATE INDEX idx_review_entry ON review (entry_id, kb_name);
CREATE INDEX idx_entry_version_commit ON entry_version(commit_hash);
CREATE INDEX idx_entry_version_author ON entry_version(author_github_login);
CREATE INDEX idx_entry_version_date ON entry_version(commit_date);
CREATE INDEX idx_repo_name ON repo(name);
CREATE INDEX idx_user_github_login ON user(github_login);
CREATE INDEX idx_session_token ON session(token_hash);
CREATE INDEX idx_session_user ON session(user_id);
CREATE INDEX idx_session_expires ON session(expires_at);
CREATE INDEX idx_local_user_username ON local_user(username);
CREATE UNIQUE INDEX idx_local_user_provider
                ON local_user(auth_provider, provider_id)
                WHERE provider_id IS NOT NULL;
CREATE INDEX idx_entry_assignee ON entry(assignee);
CREATE INDEX idx_entry_priority ON entry(priority);
CREATE INDEX idx_entry_due_date ON entry(due_date);
CREATE INDEX idx_entry_lifecycle ON entry(lifecycle);
CREATE INDEX idx_worktree_user ON worktree(user_id);
CREATE INDEX idx_worktree_kb ON worktree(kb_name);
CREATE INDEX idx_worktree_status ON worktree(status);
CREATE INDEX idx_invite_code_used ON invite_code(used_by);
CREATE INDEX idx_user_api_key_user ON user_api_key(user_id);
CREATE INDEX idx_entry_fips ON entry(fips);
CREATE INDEX idx_entry_state ON entry(state);
CREATE INDEX idx_oauth_state_expires ON oauth_state(expires_at);
CREATE INDEX idx_llm_usage_user ON llm_usage(user_id, created_at);
CREATE TRIGGER entry_ai AFTER INSERT ON entry BEGIN
    INSERT INTO entry_fts(rowid, id, kb_name, entry_type, title, body, summary, location)
    VALUES (new.rowid, new.id, new.kb_name, new.entry_type, new.title,
            COALESCE(new.body, ''), COALESCE(new.summary, ''), COALESCE(new.location, ''));
END;
CREATE TRIGGER entry_ad AFTER DELETE ON entry BEGIN
    INSERT INTO entry_fts(entry_fts, rowid, id, kb_name, entry_type, title, body, summary, location)
    VALUES('delete', old.rowid, old.id, old.kb_name, old.entry_type, old.title,
           COALESCE(old.body, ''), COALESCE(old.summary, ''), COALESCE(old.location, ''));
END;
CREATE TRIGGER entry_au AFTER UPDATE ON entry BEGIN
    INSERT INTO entry_fts(entry_fts, rowid, id, kb_name, entry_type, title, body, summary, location)
    VALUES('delete', old.rowid, old.id, old.kb_name, old.entry_type, old.title,
           COALESCE(old.body, ''), COALESCE(old.summary, ''), COALESCE(old.location, ''));
    INSERT INTO entry_fts(rowid, id, kb_name, entry_type, title, body, summary, location)
    VALUES (new.rowid, new.id, new.kb_name, new.entry_type, new.title,
            COALESCE(new.body, ''), COALESCE(new.summary, ''), COALESCE(new.location, ''));
END;
INSERT INTO schema_version (version, description, applied_at) VALUES (1, 'Initial schema with FTS5', '2026-01-01T00:00:00+00:00');
INSERT INTO schema_version (version, description, applied_at) VALUES (2, 'Add vec_entry virtual table for vector search', '2026-01-01T00:00:00+00:00');
INSERT INTO schema_version (version, description, applied_at) VALUES (3, 'Add collaboration tables (user, repo, workspace_repo, entry_version)', '2026-01-01T00:00:00+00:00');
INSERT INTO schema_version (version, description, applied_at) VALUES (4, 'Add entry_ref table for typed object references', '2026-01-01T00:00:00+00:00');
INSERT INTO schema_version (version, description, applied_at) VALUES (5, 'Add block table for block-level references', '2026-01-01T00:00:00+00:00');
INSERT INTO schema_version (version, description, applied_at) VALUES (6, 'Add local_user and session tables for web authentication', '2026-01-01T00:00:00+00:00');
INSERT INTO schema_version (version, description, applied_at) VALUES (7, 'Add OAuth columns to local_user for GitHub OAuth', '2026-01-01T00:00:00+00:00');
INSERT INTO schema_version (version, description, applied_at) VALUES (8, 'Add kb_permission table and ephemeral_kb_count column', '2026-01-01T00:00:00+00:00');
INSERT INTO schema_version (version, description, applied_at) VALUES (9, 'Add protocol columns for ADR-0017 entry protocol mixins', '2026-01-01T00:00:00+00:00');
INSERT INTO schema_version (version, description, applied_at) VALUES (10, 'Add usage_tier column to local_user for personal KB tiers', '2026-01-01T00:00:00+00:00');
INSERT INTO schema_version (version, description, applied_at) VALUES (11, 'Add lifecycle column to entry for archive support', '2026-01-01T00:00:00+00:00');
INSERT INTO schema_version (version, description, applied_at) VALUES (12, 'Add source column to kb for DB-first registry', '2026-01-01T00:00:00+00:00');
INSERT INTO schema_version (version, description, applied_at) VALUES (13, 'Add default_role column to kb for per-KB access control', '2026-01-01T00:00:00+00:00');
INSERT INTO schema_version (version, description, applied_at) VALUES (14, 'Add GitHub token columns to local_user for web GitHub integration', '2026-01-01T00:00:00+00:00');
INSERT INTO schema_version (version, description, applied_at) VALUES (15, 'Add review table for QA reviews', '2026-01-01T00:00:00+00:00');
INSERT INTO schema_version (version, description, applied_at) VALUES (16, 'Add edge_endpoint table for typed relationship entries', '2026-01-01T00:00:00+00:00');
INSERT INTO schema_version (version, description, applied_at) VALUES (17, 'Add worktree table for multi-user collaboration', '2026-01-01T00:00:00+00:00');
INSERT INTO schema_version (version, description, applied_at) VALUES (18, 'Add invite_code table for invitation-only registration', '2026-01-01T00:00:00+00:00');
INSERT INTO schema_version (version, description, applied_at) VALUES (19, 'Add user_api_key table for BYOK per-user LLM API keys', '2026-01-01T00:00:00+00:00');
INSERT INTO schema_version (version, description, applied_at) VALUES (20, 'Add fips and state columns to entry for geographic filtering', '2026-01-01T00:00:00+00:00');
INSERT INTO schema_version (version, description, applied_at) VALUES (21, 'Add content_hash column to entry for hash-based staleness detection', '2026-01-01T00:00:00+00:00');
INSERT INTO schema_version (version, description, applied_at) VALUES (22, 'Add oauth_state table for DB-backed OAuth CSRF state (survives restarts)', '2026-01-01T00:00:00+00:00');
INSERT INTO schema_version (version, description, applied_at) VALUES (23, 'Add llm_usage table for per-user LLM cost/token tracking and quotas', '2026-01-01T00:00:00+00:00');
INSERT INTO schema_version (version, description, applied_at) VALUES (24, 'Scope starred_entry per user (user_id column, unique per user)', '2026-01-01T00:00:00+00:00');
INSERT INTO schema_version (version, description, applied_at) VALUES (25, 'Add file_path to entry_version so a pre-rename version reads at its own path (#432)', '2026-01-01T00:00:00+00:00');
INSERT INTO schema_version (version, description, applied_at) VALUES (26, 'Add local_user.global_access: self-registered users read public KBs only', '2026-01-01T00:00:00+00:00');
INSERT INTO schema_version (version, description, applied_at) VALUES (27, 'Add entry.file_mtime_ns and entry.file_size: reconcile staleness (ADR-0038)', '2026-01-01T00:00:00+00:00');
INSERT INTO schema_version (version, description, applied_at) VALUES (28, 'Add entry.aliases: a read spells a field where the file does (#1001, decision 5)', '2026-01-01T00:00:00+00:00');
