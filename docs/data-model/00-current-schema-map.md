# 00 — Current implemented schema

This page is the clean implementation baseline for CommonPlan. It contains only the tables and columns that exist after applying the current migration heads:

- Auth DB: `20260910_0002`
- Business DB: `20261009_0020`

`IMPLEMENTED` means the table exists in PostgreSQL and has an active SQLAlchemy runtime model. `PROPOSED` means the entity belongs to the target product design but has no migration yet.

Each database also contains Alembic's `alembic_version` table. It is migration infrastructure rather than a CommonPlan domain entity and is therefore omitted from the ERDs.

## Auth database

```mermaid
erDiagram
    IDENTITY_USERS ||--o{ EXTERNAL_IDENTITIES : has
    IDENTITY_USERS ||--o{ REFRESH_TOKENS : owns
    IDENTITY_USERS ||--o{ LOGIN_CODES : receives

    IDENTITY_USERS {
        varchar_36 id PK
        varchar_320 email UK
        varchar_255 name
        varchar_512 password_hash
        varchar_1024 avatar_url
        boolean is_active
        timestamptz created_at
        timestamptz updated_at
    }
    EXTERNAL_IDENTITIES {
        integer id PK
        varchar_64 provider UK
        varchar_255 subject UK
        varchar_36 user_id FK
        timestamptz created_at
    }
    REFRESH_TOKENS {
        integer id PK
        varchar_64 token_hash UK
        varchar_36 family_id
        varchar_36 user_id FK
        timestamptz expires_at
        timestamptz revoked_at
        varchar_64 replaced_by_hash
        timestamptz created_at
    }
    LOGIN_ATTEMPTS {
        varchar_64 identifier_hash PK
        integer failed_attempts
        timestamptz window_started_at
        timestamptz locked_until
    }
    LOGIN_CODES {
        varchar_64 code_hash PK
        varchar_36 user_id FK
        timestamptz expires_at
        timestamptz used_at
        timestamptz created_at
    }
```

`login_attempts` intentionally has no user foreign key because authentication can fail for an identifier that does not belong to an account. `external_identities` uses one composite unique constraint on `(provider, subject)`; the two `UK` badges denote participation in that constraint.

## Business database

```mermaid
erDiagram
    USERS ||--o{ APPLICATION_SESSIONS : may_own
    USERS ||--o{ WORKSPACE_MEMBERSHIPS : joins
    WORKSPACES ||--o{ WORKSPACE_MEMBERSHIPS : has
    WORKSPACES ||--o{ TEAMS : contains
    TEAMS ||--o{ TEAM_MEMBERSHIPS : has
    USERS ||--o{ TEAM_MEMBERSHIPS : joins
    TEAMS ||--o{ WORKFLOW_STATES : defines
    TEAMS ||--o{ CYCLES : plans
    TEAMS ||--o{ LABELS : defines
    TEAMS ||--o{ ISSUES : owns
    TEAMS ||--o{ PROJECTS : owns
    PROJECTS ||--o{ PROJECT_OBJECTIVES : defines
    PROJECTS ||--o{ PROJECT_MILESTONES : plans
    PROJECTS ||--o{ PROJECT_UPDATES : reports
    PROJECTS |o--o{ ISSUES : groups
    PROJECT_MILESTONES |o--o{ ISSUES : tracks
    WORKFLOW_STATES ||--o{ ISSUES : classifies
    CYCLES ||--o{ ISSUES : groups
    ISSUES ||--o{ ISSUE_LABELS : tagged
    LABELS ||--o{ ISSUE_LABELS : applies
    ISSUES ||--o{ ISSUE_COMMENTS : discusses
    ISSUES ||--o{ ISSUE_EVENTS : records
    WORKSPACES ||--o{ ISSUE_RELATION_TYPES : defines
    ISSUE_RELATION_TYPES ||--o{ ISSUE_RELATIONS : types
    ISSUES ||--o{ ISSUE_RELATIONS : source
    ISSUES ||--o{ ISSUE_RELATIONS : target
    WORKSPACES ||--o{ FILE_ASSETS : scopes
    USERS ||--o{ FILE_ASSETS : uploads
    ISSUES ||--o{ ISSUE_ATTACHMENTS : has
    FILE_ASSETS ||--o| ISSUE_ATTACHMENTS : links
    PROJECTS ||--o{ PROJECT_ATTACHMENTS : has
    FILE_ASSETS ||--o| PROJECT_ATTACHMENTS : links
    FILE_ASSETS ||--o{ OUTBOX_EVENTS : emits
    FILE_ASSETS ||--o| DOCUMENT_INGESTIONS : ingests
    WORKSPACES ||--o{ DOCUMENT_INGESTIONS : scopes
    DOCUMENT_INGESTIONS ||--o{ DOCUMENT_PAGES : contains
    DOCUMENT_INGESTIONS ||--o{ DOCUMENT_CHUNKS : contains
    DOCUMENT_PAGES ||--o{ DOCUMENT_CHUNKS : parents
    WORKSPACES ||--o{ DOCUMENT_CHUNKS : scopes
    TEAMS ||--o{ DOCUMENT_CHUNKS : scopes
    ISSUES |o--o{ DOCUMENT_CHUNKS : sources
    PROJECTS |o--o{ DOCUMENT_CHUNKS : sources

    USERS {
        integer id PK
        varchar_255 email UK
        varchar_255 name
        varchar_1024 avatar_url
        boolean is_deleted
        timestamptz created_at
        timestamptz updated_at
        varchar_255 auth_issuer UK
        varchar_255 auth_subject UK
    }
    APPLICATION_SESSIONS {
        integer id PK
        varchar_64 session_hash UK
        integer user_id FK
        jsonb data
        timestamptz expires_at
        timestamptz created_at
        timestamptz updated_at
    }
    WORKSPACE_MEMBERSHIPS {
        varchar_36 workspace_id PK, FK
        integer user_id PK, FK
        varchar_16 role
        varchar_16 status
    }
    TEAM_MEMBERSHIPS {
        varchar_36 team_id PK, FK
        integer user_id PK, FK
        varchar_16 role
    }
    WORKSPACES {
        varchar_36 id PK
        varchar_80 slug UK
        varchar_255 name
        integer created_by_user_id FK
    }
    TEAMS {
        varchar_36 id PK
        varchar_36 workspace_id FK
        varchar_12 issue_prefix
        bigint next_issue_number
    }
    WORKFLOW_STATES {
        varchar_36 id PK
        varchar_36 team_id FK
        varchar_80 name
        varchar_24 category
    }
    CYCLES {
        varchar_36 id PK
        varchar_36 team_id FK
        date starts_on
        date ends_on
    }
    LABELS {
        varchar_36 id PK
        varchar_36 team_id FK
        varchar_80 name
        varchar_16 color
    }
    PROJECTS {
        varchar_36 id PK
        varchar_36 team_id FK
        varchar_255 name
        varchar_80 slug
        varchar_24 status
        integer lead_user_id FK
        date target_date
    }
    PROJECT_OBJECTIVES {
        varchar_36 id PK
        varchar_36 project_id FK
        varchar_24 kind
        text body
        boolean is_met
    }
    PROJECT_MILESTONES {
        varchar_36 id PK
        varchar_36 project_id FK
        varchar_255 name
        varchar_24 status
        date target_date
    }
    PROJECT_UPDATES {
        varchar_36 id PK
        varchar_36 project_id FK
        integer author_user_id FK
        text body
        varchar_24 health
    }
    ISSUES {
        varchar_36 id PK
        varchar_36 workspace_id FK
        varchar_36 team_id FK
        varchar_32 key UK
        varchar_500 title
        varchar_36 workflow_state_id FK
        varchar_36 project_id FK
        varchar_36 milestone_id FK
        integer version
    }
    ISSUE_LABELS {
        varchar_36 issue_id PK, FK
        varchar_36 label_id PK, FK
    }
    ISSUE_COMMENTS {
        varchar_36 id PK
        varchar_36 issue_id FK
        integer author_user_id FK
        text body
        timestamptz created_at
    }
    ISSUE_EVENTS {
        varchar_36 id PK
        varchar_36 issue_id FK
        integer actor_user_id FK
        varchar_64 event_type
        jsonb changes
    }
    ISSUE_RELATION_TYPES {
        varchar_36 id PK
        varchar_36 workspace_id FK
        varchar_64 key
        varchar_120 forward_label
        varchar_120 inverse_label
        varchar_24 category
        boolean is_symmetric
        boolean allow_cycles
    }
    ISSUE_RELATIONS {
        varchar_36 id PK
        varchar_36 workspace_id FK
        varchar_36 relation_type_id FK
        varchar_36 source_issue_id FK
        varchar_36 target_issue_id FK
        integer created_by_user_id FK
    }
    BROWSER_AUTH_SESSIONS {
        varchar_64 session_hash PK
        varchar_36 auth_subject
        varchar_1024 encrypted_refresh_token
        timestamptz expires_at
        timestamptz revoked_at
        timestamptz created_at
        timestamptz updated_at
    }
    FILE_ASSETS {
        varchar_36 id PK
        varchar_36 workspace_id FK
        integer uploaded_by_user_id FK
        varchar_255 original_filename
        varchar_768 storage_key UK
        varchar_255 content_type
        bigint byte_size
        varchar_64 sha256
        varchar_24 upload_status
        varchar_24 scan_status
        timestamptz ready_at
        timestamptz deleted_at
    }
    ISSUE_ATTACHMENTS {
        varchar_36 issue_id PK, FK
        varchar_36 file_asset_id PK, FK
        integer added_by_user_id FK
    }
    PROJECT_ATTACHMENTS {
        varchar_36 project_id PK, FK
        varchar_36 file_asset_id PK, FK
        integer added_by_user_id FK
    }
    OUTBOX_EVENTS {
        varchar_36 id PK
        varchar_36 workspace_id FK
        varchar_64 aggregate_type
        varchar_36 aggregate_id
        varchar_120 event_type
        jsonb payload
        varchar_24 status
        integer attempts
        timestamptz available_at
        timestamptz locked_at
        varchar_120 locked_by
        text last_error
        timestamptz published_at
    }
    DOCUMENT_INGESTIONS {
        varchar_36 id PK
        varchar_36 file_asset_id FK, UK
        varchar_36 workspace_id FK
        varchar_24 status
        varchar_80 parser_name
        varchar_40 parser_version
        varchar_40 chunker_version
        varchar_40 embedding_provider
        varchar_120 embedding_model
        integer embedding_dimensions
        varchar_64 content_sha256
        integer attempts
        text error_message
        timestamptz completed_at
    }
    DOCUMENT_PAGES {
        varchar_36 id PK
        varchar_36 ingestion_id FK
        integer page_number
        text heading_path
        text markdown_content
        text plain_text
        integer token_count
    }
    DOCUMENT_CHUNKS {
        varchar_36 id PK
        varchar_36 ingestion_id FK
        varchar_36 parent_page_id FK
        varchar_36 workspace_id FK
        varchar_36 team_id FK
        varchar_36 issue_id FK
        varchar_36 project_id FK
        integer chunk_index
        text content
        vector_1024 embedding
        jsonb chunk_metadata
    }
```

The `(auth_issuer, auth_subject)` badges denote the partial composite unique index `ux_users_auth_identity`; neither column is independently unique. `browser_auth_sessions.auth_subject` logically refers to Auth `identity_users.id`, but PostgreSQL cannot enforce that cross-database relationship. It also has no direct foreign key to Business `users`.

## Implemented table catalog

| Database | Table | Runtime model | Responsibility |
| --- | --- | --- | --- |
| Auth | `identity_users` | `IdentityUser` | Canonical identity, password credential, activation state, and display identity |
| Auth | `external_identities` | `ExternalIdentity` | Google or future provider subjects linked to one identity |
| Auth | `refresh_tokens` | `RefreshToken` | Hashed refresh-token families, rotation, expiry, revocation, and replay response |
| Auth | `login_attempts` | `LoginAttempt` | Persistent identifier-based throttling and lockout state |
| Auth | `login_codes` | `LoginCode` | Single-use, short-lived Auth-to-BFF code exchange |
| Business | `users` | `User` | Application-facing user projection keyed by `(auth_issuer, auth_subject)` |
| Business | `application_sessions` | `ApplicationSession` | Durable product workflow state with Redis as an optional read-through cache |
| Business | `browser_auth_sessions` | `BrowserAuthSession` | Opaque-cookie lookup and encrypted refresh-token vault |
| Business | `workspaces` | `Workspace` | Top-level collaboration and authorization boundary |
| Business | `workspace_memberships` | `WorkspaceMembership` | Workspace role and active membership |
| Business | `workspace_invitations` | `WorkspaceInvitation` | Expiring invitations into a workspace and optional team |
| Business | `teams` | `Team` | Team scope, issue prefix, and atomic issue-number allocator |
| Business | `team_memberships` | `TeamMembership` | Team membership and lead/member role |
| Business | `workflow_states` | `WorkflowState` | Team-owned issue workflow states and categories |
| Business | `cycles` | `Cycle` | Non-overlapping team planning windows with idempotent completion tracking |
| Business | `team_cycle_settings` | `TeamCycleSettings` | Repeating Team cycle cadence, future-window count, and unfinished-work rollover policy |
| Business | `labels` | `Label` | Team-owned issue labels |
| Business | `projects` | `Project` | Team-owned project brief, status, lead, and target date |
| Business | `project_objectives` | `ProjectObjective` | Ordered objectives and measurable success criteria |
| Business | `project_milestones` | `ProjectMilestone` | Ordered project delivery checkpoints with derived issue progress |
| Business | `project_updates` | `ProjectUpdate` | Authored project health and status history |
| Business | `issues` | `Issue` | Versioned team work item with an immutable key |
| Business | `issue_labels` | `IssueLabel` | Many-to-many issue label assignment |
| Business | `issue_comments` | `IssueComment` | Authored issue discussion with soft-delete timestamps |
| Business | `issue_events` | `IssueEvent` | Append-only issue change and collaboration history |
| Business | `issue_relation_types` | `IssueRelationType` | Workspace-owned forward/inverse relationship vocabulary and cycle policy |
| Business | `issue_relations` | `IssueRelation` | Authorized typed edges between issues in one workspace |
| Business | `file_assets` | `FileAsset` | Private object metadata, lifecycle state, and workspace scope |
| Business | `issue_attachments` | `IssueAttachment` | Foreign-key-safe link from one file asset to an issue |
| Business | `project_attachments` | `ProjectAttachment` | Foreign-key-safe link from one file asset to a project |
| Business | `outbox_events` | `OutboxEvent` | Append-only transaction bridge captured from PostgreSQL WAL by Debezium |
| Business | `knowledge_sources` | `KnowledgeSource` | Current indexed identity, source/model versions, content hash, scope and status |
| Business | `knowledge_chunks` | `KnowledgeChunk` | ACL-scoped database-source text, metadata and `vector(1024)` embeddings |
| Business | `document_ingestions` | `DocumentIngestion` | Versioned per-file parser/chunker/embedding lifecycle, retry state, and tombstone |
| Business | `document_pages` | `DocumentPage` | Parsed parent-page Markdown/plain text and heading provenance |
| Business | `document_chunks` | `DocumentChunk` | ACL-scoped canonical chunk text, provenance, metadata, and `vector(1024)` embedding |

## Cross-database identity mapping

Auth DB is the source of truth for identity. On the first authenticated Business API request, a verified JWT supplies `(iss, sub)`. The Business API finds or provisions exactly one `users` row using `(auth_issuer, auth_subject)` and mirrors email, name, and avatar for product display.

Email is not the security join key. The current Business schema keeps it unique for projection consistency, while authentication and authorization continue to use the immutable issuer/subject pair.

## Detailed module design

The following modules mix implemented records with explicitly marked **PROPOSED** follow-ups.
References to Business `users` reuse the implemented table above; the catalog above remains the
authoritative list of tables that exist today.

| Module | Proposed scope |
| --- | --- |
| [01 — Identity and profile](01-identity-profile.md) | User and notification preferences |
| [02 — Workspace and team](02-workspace-team.md) | Workspaces, teams, memberships, and invitations |
| [03 — Work planning](03-work-planning.md) | Implemented planning core, structural sub-issues, and configurable issue relationships; production pagination remains proposed |
| [04 — Views and notifications](04-views-notifications.md) | Saved views, inbox notifications, and aggregate read models |
| [05 — GitHub webhook](05-github-webhook.md) | Pull-request snapshots, issue links, and webhook delivery deduplication |
| [06 — Administration](06-administration.md) | Workspace settings, allowed domains, and audit events |
| [07 — Attachments](07-attachments.md) | Implemented private attachment metadata, typed resource links, signed object access, and outbox events |
| [08 — RAG ingestion](08-rag-ingestion.md) | Implemented offline parsing/chunking/embedding foundation; online retrieval remains proposed |

## Current design constraints

1. Business profile writes for email, name, and avatar must flow through Auth Service and then project through JWT claims.
2. `application_sessions` never authenticate an API caller; Business endpoints require a verified access JWT.
3. The browser receives an opaque HttpOnly session cookie, while the raw refresh token remains encrypted in `browser_auth_sessions`.
4. Supporting more than one accepted Auth issuer requires adding issuer context to `browser_auth_sessions`; `auth_subject` alone is currently valid because the deployment has one configured issuer.
