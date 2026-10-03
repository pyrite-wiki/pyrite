"""
SQLAlchemy ORM Models for pyrite

Declarative models for standard tables. Virtual tables (FTS5, sqlite-vec)
are handled separately in virtual_tables.py since they have no ORM equivalent.
"""

from sqlalchemy import (
    BigInteger,
    Column,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, relationship


class Base(DeclarativeBase):
    pass


class KB(Base):
    __tablename__ = "kb"

    name = Column(String, primary_key=True)
    kb_type = Column(String, nullable=False)
    path = Column(String, nullable=False)
    description = Column(Text)
    last_indexed = Column(String)
    entry_count = Column(Integer, default=0)

    source = Column(String, server_default="user")
    default_role = Column(String, nullable=True)

    # Phase 7: repo association
    repo_id = Column(Integer, ForeignKey("repo.id", ondelete="SET NULL"), nullable=True)
    repo_subpath = Column(String, default="")

    entries = relationship("Entry", back_populates="kb_rel", cascade="all, delete-orphan")
    repo_rel = relationship("Repo", back_populates="knowledge_bases")


class Entry(Base):
    __tablename__ = "entry"

    id = Column(String, primary_key=True)
    kb_name = Column(
        String, ForeignKey("kb.name", ondelete="CASCADE"), primary_key=True, nullable=False
    )
    entry_type = Column(String, nullable=False)
    title = Column(String, nullable=False)
    body = Column(Text)
    summary = Column(Text)
    file_path = Column(String)

    # Common indexed fields
    date = Column(String)
    importance = Column(Integer)
    status = Column(String)
    location = Column(String)

    # Protocol fields (ADR-0017)
    assignee = Column(String)
    assigned_at = Column(String)
    priority = Column(String)
    due_date = Column(String)
    start_date = Column(String)
    end_date = Column(String)
    coordinates = Column(String)
    fips = Column(String)
    state = Column(String)

    # Lifecycle (active / archived)
    lifecycle = Column(String, server_default="active")

    # Extension fields (JSON) — attribute named extra_data to avoid SQLAlchemy reserved 'metadata'
    extra_data = Column("metadata", Text, default="{}")

    # Timestamps
    created_at = Column(String)
    updated_at = Column(String)
    indexed_at = Column(String, server_default="CURRENT_TIMESTAMP")

    # SHA-256 of the on-disk file content at index time. Lets check_staleness
    # catch same-second content edits that mtime comparison alone misses.
    content_hash = Column(String(64))

    # The file's st_mtime_ns and st_size when it was indexed. A reconcile
    # re-reads a known file only when either differs, and the content hash
    # decides whether it changed (ADR-0038 decision 4). Null on rows indexed
    # before v27: the first reconcile hashes those once.
    file_mtime_ns = Column(BigInteger)
    file_size = Column(BigInteger)

    # Attribution
    created_by = Column(String, nullable=True)
    modified_by = Column(String, nullable=True)

    # Relationships
    kb_rel = relationship("KB", back_populates="entries")
    tags = relationship("EntryTag", back_populates="entry", cascade="all, delete-orphan")
    sources = relationship("Source", back_populates="entry", cascade="all, delete-orphan")
    outgoing_links = relationship(
        "Link", back_populates="source_entry", cascade="all, delete-orphan"
    )
    versions = relationship("EntryVersion", back_populates="entry", cascade="all, delete-orphan")
    reviews = relationship("Review", back_populates="entry", cascade="all, delete-orphan")
    blocks = relationship("Block", cascade="all, delete-orphan")


class Tag(Base):
    __tablename__ = "tag"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String, unique=True, nullable=False, index=True)

    entry_tags = relationship("EntryTag", back_populates="tag")


class EntryTag(Base):
    __tablename__ = "entry_tag"

    entry_id = Column(String, nullable=False, primary_key=True)
    kb_name = Column(String, nullable=False, primary_key=True)
    tag_id = Column(
        Integer, ForeignKey("tag.id", ondelete="CASCADE"), nullable=False, primary_key=True
    )

    __table_args__ = (
        ForeignKeyConstraint(
            ["entry_id", "kb_name"],
            ["entry.id", "entry.kb_name"],
            ondelete="CASCADE",
        ),
        Index("idx_entry_tag_entry", "entry_id", "kb_name"),
        Index("idx_entry_tag_tag", "tag_id"),
    )

    entry = relationship("Entry", back_populates="tags")
    tag = relationship("Tag", back_populates="entry_tags")


class Link(Base):
    __tablename__ = "link"

    id = Column(Integer, primary_key=True, autoincrement=True)
    source_id = Column(String, nullable=False)
    source_kb = Column(String, nullable=False)
    target_id = Column(String, nullable=False)
    target_kb = Column(String, nullable=False)
    relation = Column(String, nullable=False)
    inverse_relation = Column(String, nullable=False)
    note = Column(Text)
    created_at = Column(String, server_default="CURRENT_TIMESTAMP")

    __table_args__ = (
        ForeignKeyConstraint(
            ["source_id", "source_kb"],
            ["entry.id", "entry.kb_name"],
            ondelete="CASCADE",
        ),
        Index("idx_link_source", "source_id", "source_kb"),
        Index("idx_link_target", "target_id", "target_kb"),
        Index("idx_link_relation", "relation"),
    )

    source_entry = relationship("Entry", back_populates="outgoing_links")


class EntryRef(Base):
    __tablename__ = "entry_ref"

    id = Column(Integer, primary_key=True, autoincrement=True)
    source_id = Column(String, nullable=False)
    source_kb = Column(String, nullable=False)
    target_id = Column(String, nullable=False)
    target_kb = Column(String, nullable=False)
    field_name = Column(String, nullable=False)
    target_type = Column(String)  # from schema constraint

    __table_args__ = (
        ForeignKeyConstraint(
            ["source_id", "source_kb"],
            ["entry.id", "entry.kb_name"],
            ondelete="CASCADE",
        ),
        Index("idx_entry_ref_source", "source_id", "source_kb"),
        Index("idx_entry_ref_target", "target_id", "target_kb"),
        Index("idx_entry_ref_field", "field_name"),
    )


class EdgeEndpoint(Base):
    __tablename__ = "edge_endpoint"

    id = Column(Integer, primary_key=True, autoincrement=True)
    edge_entry_id = Column(String, nullable=False)
    edge_entry_kb = Column(String, nullable=False)
    role = Column(String, nullable=False)  # e.g. 'source', 'target'
    field_name = Column(String, nullable=False)  # e.g. 'owner', 'asset'
    endpoint_id = Column(String, nullable=False)  # the referenced entry ID
    endpoint_kb = Column(String, nullable=False)
    edge_type = Column(String, nullable=False)  # e.g. 'ownership'
    created_at = Column(String, server_default="CURRENT_TIMESTAMP")

    __table_args__ = (
        ForeignKeyConstraint(
            ["edge_entry_id", "edge_entry_kb"],
            ["entry.id", "entry.kb_name"],
            ondelete="CASCADE",
        ),
        Index("idx_edge_endpoint_edge", "edge_entry_id", "edge_entry_kb"),
        Index("idx_edge_endpoint_target", "endpoint_id", "endpoint_kb"),
        Index("idx_edge_endpoint_type", "edge_type", "edge_entry_kb"),
    )


class Source(Base):
    __tablename__ = "source"

    id = Column(Integer, primary_key=True, autoincrement=True)
    entry_id = Column(String, nullable=False)
    kb_name = Column(String, nullable=False)
    title = Column(String, nullable=False)
    url = Column(String)
    outlet = Column(String)
    date = Column(String)
    verified = Column(Integer, default=0)

    __table_args__ = (
        ForeignKeyConstraint(
            ["entry_id", "kb_name"],
            ["entry.id", "entry.kb_name"],
            ondelete="CASCADE",
        ),
        Index("idx_source_entry", "entry_id", "kb_name"),
        Index("idx_source_url", "url"),
    )

    entry = relationship("Entry", back_populates="sources")


# =========================================================================
# Phase 7: Collaboration Models
# =========================================================================


class User(Base):
    __tablename__ = "user"

    id = Column(Integer, primary_key=True, autoincrement=True)
    github_login = Column(String, unique=True, nullable=False, index=True)
    github_id = Column(Integer, unique=True, nullable=False)
    display_name = Column(String)
    avatar_url = Column(String)
    email = Column(String)
    created_at = Column(String, server_default="CURRENT_TIMESTAMP")
    last_seen = Column(String)

    workspace_repos = relationship(
        "WorkspaceRepo", back_populates="user", cascade="all, delete-orphan"
    )


class LocalUser(Base):
    """Local username/password user for web UI authentication."""

    __tablename__ = "local_user"

    id = Column(Integer, primary_key=True, autoincrement=True)
    username = Column(String, unique=True, nullable=False, index=True)
    display_name = Column(String)
    password_hash = Column(String, nullable=False)
    role = Column(String, nullable=False, server_default="read")
    # Whether `role` also covers KBs without a `default_role`. 0 for a
    # self-registered user (public KBs and explicit grants only) until an
    # admin grants it; 1 for everyone an operator vetted. Defaults to 0 so an
    # insert that forgets the column grants nothing; migration v26 set it to
    # 1 for the users that existed before it.
    global_access = Column(Integer, nullable=False, server_default="0")
    created_at = Column(String, server_default="CURRENT_TIMESTAMP")
    updated_at = Column(String)
    auth_provider = Column(String, server_default="local")
    provider_id = Column(String, nullable=True)
    avatar_url = Column(String, nullable=True)

    ephemeral_kb_count = Column(Integer, server_default="0")
    usage_tier = Column(String, server_default="default")
    github_access_token = Column(String, nullable=True)
    github_token_scopes = Column(String, nullable=True)

    sessions = relationship("AuthSession", back_populates="user", cascade="all, delete-orphan")
    kb_permissions = relationship(
        "KBPermission",
        back_populates="user",
        cascade="all, delete-orphan",
        foreign_keys="KBPermission.user_id",
    )


class AuthSession(Base):
    """Session token for authenticated web UI users."""

    __tablename__ = "session"

    id = Column(Integer, primary_key=True, autoincrement=True)
    token_hash = Column(String, unique=True, nullable=False, index=True)
    user_id = Column(Integer, ForeignKey("local_user.id", ondelete="CASCADE"), nullable=False)
    created_at = Column(String, server_default="CURRENT_TIMESTAMP")
    expires_at = Column(String, nullable=False)
    last_used = Column(String)

    user = relationship("LocalUser", back_populates="sessions")


class KBPermission(Base):
    """Per-KB access control grant."""

    __tablename__ = "kb_permission"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("local_user.id", ondelete="CASCADE"), nullable=False)
    kb_name = Column(String, nullable=False)
    role = Column(String, nullable=False)
    granted_by = Column(Integer, ForeignKey("local_user.id"), nullable=True)
    created_at = Column(String, server_default="CURRENT_TIMESTAMP")

    __table_args__ = (
        UniqueConstraint("user_id", "kb_name", name="uq_kb_permission_user_kb"),
        Index("idx_kb_permission_user", "user_id"),
        Index("idx_kb_permission_kb", "kb_name"),
    )

    user = relationship("LocalUser", back_populates="kb_permissions", foreign_keys=[user_id])


class Repo(Base):
    __tablename__ = "repo"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String, unique=True, nullable=False, index=True)
    local_path = Column(String, nullable=False)
    remote_url = Column(String)
    owner = Column(String)
    visibility = Column(String, default="public")
    default_branch = Column(String, default="main")
    upstream_repo_id = Column(Integer, ForeignKey("repo.id", ondelete="SET NULL"), nullable=True)
    is_fork = Column(Integer, default=0)
    last_synced_commit = Column(String)
    last_synced = Column(String)
    created_at = Column(String, server_default="CURRENT_TIMESTAMP")

    upstream = relationship("Repo", remote_side=[id], backref="forks")
    knowledge_bases = relationship("KB", back_populates="repo_rel")
    workspace_entries = relationship(
        "WorkspaceRepo", back_populates="repo", cascade="all, delete-orphan"
    )


class WorkspaceRepo(Base):
    __tablename__ = "workspace_repo"

    user_id = Column(
        Integer, ForeignKey("user.id", ondelete="CASCADE"), primary_key=True, nullable=False
    )
    repo_id = Column(
        Integer, ForeignKey("repo.id", ondelete="CASCADE"), primary_key=True, nullable=False
    )
    added_at = Column(String, server_default="CURRENT_TIMESTAMP")
    role = Column(String, default="subscriber")
    auto_sync = Column(Integer, default=1)
    sync_interval = Column(Integer, default=3600)

    user = relationship("User", back_populates="workspace_repos")
    repo = relationship("Repo", back_populates="workspace_entries")


class EntryVersion(Base):
    __tablename__ = "entry_version"

    id = Column(Integer, primary_key=True, autoincrement=True)
    entry_id = Column(String, nullable=False)
    kb_name = Column(String, nullable=False)
    commit_hash = Column(String(40), nullable=False, index=True)
    author_name = Column(String)
    author_email = Column(String)
    author_github_login = Column(String, index=True)
    commit_date = Column(String, nullable=False, index=True)
    message = Column(Text)
    diff_summary = Column(Text)
    change_type = Column(String)
    # The entry's path relative to the KB's repo root *at this commit*
    # (#432). Read at this path, not the entry's current path, so a
    # pre-rename version stays servable after the entry moves. Nullable:
    # rows recorded before #432 have none, and get_entry_at_version falls
    # back to the entry's current path for those, as it always has.
    file_path = Column(String, nullable=True)

    __table_args__ = (
        ForeignKeyConstraint(
            ["entry_id", "kb_name"],
            ["entry.id", "entry.kb_name"],
            ondelete="CASCADE",
        ),
        Index("idx_entry_version_entry", "entry_id", "kb_name"),
    )

    entry = relationship("Entry", back_populates="versions")


# =========================================================================
# Starred/Pinned Entries (engagement-tier, SQLite only per ADR-0003)
# =========================================================================


class StarredEntry(Base):
    __tablename__ = "starred_entry"

    id = Column(Integer, primary_key=True, autoincrement=True)
    # The local_user who starred it. 0 is the instance's own list: the caller
    # with no user identity (auth disabled, or an operator API key), and the
    # owner of every star made before stars were per-user (migration v24).
    # Not NULL, so the unique constraint below also holds for that list --
    # SQLite treats NULLs as distinct.
    user_id = Column(Integer, nullable=False, default=0, server_default="0")
    entry_id = Column(String, nullable=False)
    kb_name = Column(String, nullable=False)
    sort_order = Column(Integer, default=0)
    created_at = Column(String, nullable=False)

    __table_args__ = (
        UniqueConstraint("user_id", "entry_id", "kb_name", name="uq_starred_entry"),
        Index("idx_starred_entry_user", "user_id", "sort_order"),
        Index("idx_starred_entry_kb", "kb_name"),
        Index("idx_starred_entry_sort", "sort_order"),
    )


# =========================================================================
# Block References (Phase 1)
# =========================================================================


class Block(Base):
    __tablename__ = "block"

    id = Column(Integer, primary_key=True, autoincrement=True)
    entry_id = Column(String, nullable=False)
    kb_name = Column(String, nullable=False)
    block_id = Column(String, nullable=False)
    heading = Column(String)
    content = Column(Text, nullable=False)
    position = Column(Integer, nullable=False)
    block_type = Column(String, nullable=False)

    __table_args__ = (
        ForeignKeyConstraint(
            ["entry_id", "kb_name"],
            ["entry.id", "entry.kb_name"],
            ondelete="CASCADE",
        ),
        Index("idx_block_entry", "entry_id", "kb_name"),
        Index("idx_block_block_id", "block_id"),
    )


# =========================================================================
# Settings
# =========================================================================


class Review(Base):
    __tablename__ = "review"

    id = Column(Integer, primary_key=True, autoincrement=True)
    entry_id = Column(String, nullable=False)
    kb_name = Column(String, nullable=False)
    content_hash = Column(String(40), nullable=False)
    reviewer = Column(String, nullable=False)
    reviewer_type = Column(String, nullable=False)
    result = Column(String, nullable=False)
    details = Column(Text)
    created_at = Column(String, server_default="CURRENT_TIMESTAMP")

    __table_args__ = (
        ForeignKeyConstraint(
            ["entry_id", "kb_name"],
            ["entry.id", "entry.kb_name"],
            ondelete="CASCADE",
        ),
        Index("idx_review_entry", "entry_id", "kb_name"),
        Index("idx_review_content_hash", "content_hash"),
        Index("idx_review_reviewer", "reviewer"),
    )

    entry = relationship("Entry", back_populates="reviews")


# =========================================================================
# Settings
# =========================================================================


class Setting(Base):
    __tablename__ = "setting"

    id = Column(Integer, primary_key=True, autoincrement=True)
    key = Column(String, unique=True, nullable=False, index=True)
    value = Column(Text)
    updated_at = Column(String, server_default="CURRENT_TIMESTAMP")


class Worktree(Base):
    __tablename__ = "worktree"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, nullable=False)
    username = Column(String, nullable=False)
    kb_name = Column(String, nullable=False)
    repo_path = Column(String, nullable=False)
    branch = Column(String, nullable=False)
    worktree_path = Column(String, nullable=False)
    diff_db_path = Column(String, nullable=False)
    status = Column(String, nullable=False, server_default="active")
    submitted_at = Column(String)
    merged_at = Column(String)
    rejected_at = Column(String)
    feedback = Column(Text)
    created_at = Column(String, server_default="CURRENT_TIMESTAMP")
    updated_at = Column(String, server_default="CURRENT_TIMESTAMP")
