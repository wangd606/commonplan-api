# CommonPlan implementation milestones

This plan delivers CommonPlan as runnable vertical slices across the `commonplan-api` and `commonplan-web` repositories. Every milestone includes database, API, authorization, React UI, automated checks, and a manual browser walkthrough. A milestone is not complete merely because its schema or backend endpoint exists.

## Delivery principles

1. Keep both applications runnable after every merged milestone.
2. Use one coordinated feature branch and PR per repository for each milestone. Link the API and Web PRs to each other.
3. Add forward and downgrade Alembic logic for every schema change. Prefer explicit raw SQL where it makes the intended DDL easier to review.
4. Put every Business API route behind JWT authentication, then enforce workspace/team/resource authorization in policy or service code.
5. Ship one useful end-to-end path before adding secondary controls.
6. Update the data-model/API documentation when implementation changes a documented contract.
7. Validate empty, loading, error, unauthorized, and populated UI states—not only the happy path.

## Milestone map

| Milestone | Outcome | Depends on | Runnable acceptance result |
| --- | --- | --- | --- |
| M0 — Platform baseline | Independent Auth Service, JWT-protected Business API, BFF refresh vault, clean databases | None | Register/login with password or Google, refresh, call protected API |
| M1 — Workspace and Team | Multi-workspace and multi-team membership plus navigation | M0 | Switch workspace and Team 1/Team 2; see only authorized teams |
| M2 — Issue planning core | Workflow states, rolling cycles, sprint/Kanban board, issues, labels, full issue detail, comments, and activity | M1 | Create `KEY-1`, plan it into a repeating cycle, move it across the board, and inspect its history |
| M3 — Projects | Project brief, objectives, milestones, updates, linked issues | M2 | Create and manage a project with description, progress, and work items |
| M4 — Team Summary and analytics | Per-team summary, filters, visualizations, and drill-down | M2; richer project charts use M3 | Switch teams and see filter-consistent Jira-style analytics |
| M5 — Collaboration | Comment lifecycle, sub-issues, mentions, notifications, and complete resource policies | M2 | Collaborate on an issue while cross-team access fails closed |
| M6 — Saved views and Inbox | Reusable filters, My Issues, notifications, unread state | M4, M5 | Save a Summary/issue filter and receive actionable notifications |
| M7 — GitHub integration | Signed webhook intake and PR-to-issue auto-linking by item key | M2, M5 | A PR title containing `KEY-12` appears on that issue without project mapping |
| M8 — Settings and administration | Personal, connected-account, workspace, member, and domain settings | M1, M5 | Manage account/workspace settings with role checks and audit records |
| M9 — Attachments and document foundation | Secure Issue/Project uploads, private object storage, lifecycle events | M2, M3 | Upload, download, and remove an authorized attachment |
| M10 — Hardening and release | E2E coverage, pagination, concurrency, observability, deployment readiness | M1–M9 | Critical flows pass automatically and survive service restart |
| M11 — AI foundation | Scoped agent identities, permissions, context, and execution audit | M10 | An agent performs an explicitly authorized work-item action with audit trail |
| M12 — RAG ingestion and retrieval | Async document extraction, chunking, indexing, scoped retrieval | M9; M11 only for agent-facing actions | A ready document becomes searchable only inside its authorized scope |

## M0 — Platform baseline

**Status:** implemented and merged; later milestones continue to use this Auth/Business boundary.

Scope:

- Auth PostgreSQL owns identities, password hashes, Google identities, refresh-token families, login codes, and login throttles.
- Business PostgreSQL owns the local user projection, browser refresh vault, and independent application sessions.
- React receives a short-lived access token and an opaque HttpOnly BFF session cookie; it never receives a raw refresh token.
- Every Business API operation is mounted behind the shared fail-closed JWT guard.

Exit checks:

- Password registration/login and Google OAuth work.
- Refresh rotation works after access-token expiry.
- Invalid issuer, audience, signature, token type, or expiry fails.
- Auth/Business migrations upgrade, downgrade, and re-upgrade.
- Both service health endpoints respond after restart.

## M1 — Workspace and Team

### M1.1 — Workspace vertical slice

- Add `workspaces`, `workspace_memberships`, and invitation-ready ownership rules.
- Implement create/list/read/update Workspace APIs and authorization.
- Add workspace switcher and empty workspace state in React.
- Creating a workspace creates its owner membership in one transaction.

### M1.2 — Team vertical slice

- Add `teams` and `team_memberships`.
- Implement team create/list/read/update and membership APIs.
- Add Team 1/Team 2 as secondary navigation under the selected workspace.
- Require active workspace membership before team membership can grant access.

### M1.3 — Navigation and Team Overview

- Add current workspace/team routing and persistence through user preference or URL state.
- Add Team child menu destinations such as Overview, Summary, Issues, Projects, Cycles, and Members.
- Keep My Issues, Saved Views, Inbox, and Settings at workspace level; a saved view may still target one Team.
- Implement Team Overview counts as derived queries, not a stored overview table.
- Verify a user can belong to multiple teams and switching teams never leaks data.

Exit demo:

1. Create two workspaces and at least two teams.
2. Add one user to both teams and another user to only one team.
3. Switch workspace/team in the sidebar.
4. Confirm inaccessible team URLs return 403/404 according to the documented policy.

## M2 — Issue planning core

Scope:

- Add `workflow_states`, `cycles`, `issues`, `labels`, `issue_labels`, `issue_comments`, and append-only `issue_events`.
- Generate immutable team-prefixed keys under transaction/row lock.
- Implement issue list, create, detail, update, assignment, labels, cycle filtering, and optimistic `version` checks.
- Build Issues and Cycles pages plus a dedicated, deep-linkable issue detail route.
- Make each Cycle detail an operational sprint/Kanban board grouped by Team workflow state; moving a card updates the issue state with optimistic locking.
- Configure repeating 1–8 week cycles, retain 1–15 upcoming cycles, and optionally roll unfinished work into the next cycle when the prior cycle ends.
- Keep Team Summary as a separate analytical page; a Cycle board is not a Summary filter preset.
- The detail page edits title, description, status, priority, assignee, cycle, due date, and labels; it also supports adding comments and reading the chronological activity stream.
- Keep property changes immediate, but save long-form title/description edits explicitly so users do not lose a draft to an accidental blur.

Exit demo:

- Create `KEY-1`, open `/workspaces/{workspaceId}/issues/KEY-1`, edit every core field, add a comment, and see creation/update/comment activity.
- See different issue lists when switching teams.
- Refresh the detail URL and use browser back/forward without losing navigation state.
- Submit a stale update and receive 409 rather than silently overwriting another edit.
- Enable a two-week rolling schedule, see future cycles created, and verify unfinished issues roll into the next cycle exactly once.

## M3 — Projects

**Status:** implemented locally on `codex/m3-projects`; automated validation is complete and browser acceptance remains before publication.

Scope:

- Add `projects`, `project_objectives`, `project_updates`, and `project_milestones`.
- Implement project list/detail, long description, summary, lead, target date, objectives, success criteria, updates, milestones, and linked issues.
- Build the complete Project page depicted in KEY-3, including the project description/brief area and progress sections.
- Derive progress from issue state; do not store a percentage that can drift.

Exit demo:

- Create a project within one team, write its brief, add objectives and milestones, link issues, post an update, and see derived progress.
- Verify a project cannot reference a cycle, milestone, issue, or lead from another team.

## M4 — Team Summary and analytics

The canonical product/API contract is [04 — Team Summary, saved views, search, and inbox](../data-model/04-views-notifications.md).

**Status:** implemented locally on `codex/m4-team-summary`; automated validation and the Team A/filter URL/Team B empty-state browser walkthrough are complete. Publication remains pending review.

Scope:

- Implement `GET /api/v1/workspaces/{workspaceId}/teams/{teamId}/summary`.
- Compile all aggregates from one normalized, allowlisted filter object.
- Support cycle, project, status, priority, assignee, label, date range, due-date state, archive state, and ownership filters.
- Encode active filter state in the URL and provide a reset-to-team-default action.
- Render headline cards, status and priority distribution, assignee workload, cycle progress, project distribution, created/completed trend, attention issues, and recent activity.
- Make chart segments drill into the matching issue filter without changing team scope.
- Keep Summary derived from source tables. Add cache/materialization only after measuring a real performance need.

Exit demo:

1. Seed Team 1 and Team 2 with deliberately different issue distributions.
2. Switch teams and verify every card/chart changes to the selected team only.
3. Apply several filters together and verify cards, charts, attention lists, and drill-down agree.
4. Refresh and share the URL; the filter state must be restored.
5. Verify empty team, no current cycle, no project, and zero-result states.

## M5 — Collaboration expansion

**Status:** implemented locally on `codex/m5-collaboration`; automated API, Web build, and migration round-trip validation are complete. Browser acceptance and stacked publication remain.

Scope:

- Add comment edit/delete policy, mentions, sub-issues, typed issue relationships, watchers, and notification-producing collaboration events.
- Centralize workspace/team/resource authorization and cover it with a role/resource matrix.
- Expand the M2 issue detail with comment actions, sub-issues, workspace-configurable relationships, mentions, and notification controls.

Exit demo:

- Two authorized users collaborate on one issue and see ordered activity.
- Removing team access blocks future reads without erasing authored history.
- A user cannot infer another team's issue by identifier, comment, event, or notification.
- A parent shows child progress in the issue list; directional relationship labels reverse correctly, and dependency cycles are rejected.

## M6 — Saved views and Inbox

**Status:** implemented locally on `codex/m6-views-inbox`; API tests, Web build, and migration round-trip validation are complete. Browser acceptance and stacked publication remain.

Scope:

- Add `saved_views` and `notifications`.
- Reuse the M4 filter grammar for saved views; never store raw SQL.
- Implement My Issues, saved view CRUD/execution, Inbox, unread counts, and mark-read.
- Create notifications transactionally with issue/comment events or through a transactional outbox.

Exit demo:

- Save a filtered issue/Summary configuration, reload it, and share it according to visibility.
- Generate a notification from a collaboration event and mark it read.
- Confirm saved view visibility never bypasses current team membership.

## M7 — GitHub integration

Scope:

- Add `github_pull_requests`, `issue_pr_links`, and `github_webhook_deliveries`.
- Verify GitHub HMAC signatures and deduplicate `X-GitHub-Delivery`.
- Parse item keys from PR titles and link every matching authorized issue key.
- Do not add project-to-repository mapping; title key matching is sufficient.
- Show linked PR state on issue detail and integration health in workspace settings.

Exit demo:

- Deliver the same webhook twice and process it once.
- Create/update a PR title containing `KEY-12` and see the correct issue link attach/detach.
- Reject invalid signatures and ignore unknown workspace/team keys safely.

## M8 — Settings and administration

Scope:

- Implement personal profile/preferences, Security & access, and Connected accounts as user settings.
- Implement workspace profile, members, invitations, domain policy, and Applications/GitHub as workspace settings.
- Add `workspace_settings`, optional verified domains, and security-sensitive `audit_events`.
- Keep settings in their user/workspace sub-navigation rather than duplicating the primary product menu.

Exit demo:

- Update personal and workspace settings at the correct ownership boundary.
- Connect/disconnect Google without removing the last usable login method.
- Verify member, admin, and owner permissions plus last-owner protection.

## M9 — Attachments and document foundation

**Status:** implemented. Production malware scanning and byte-level MIME detection remain explicit
deployment gates rather than silently assumed capabilities.

Scope:

- Add `file_assets`, `issue_attachments`, `project_attachments`, and a durable `outbox_events` boundary.
- Store bytes outside PostgreSQL through a signed local-volume driver for development and a private S3-compatible driver for deployment; keep metadata and authorization scope in PostgreSQL.
- Use short-lived signed upload/download URLs and recheck resource authorization for complete, download, and delete.
- Add consistent attachment controls to Issue and Project details with progress, empty, error, ready, and delete states.
- Append `knowledge.source.upserted` and `knowledge.source.deleted` without performing extraction or embedding inside the API request.

Exit demo:

- Upload a PDF to an Issue and a Markdown file to a Project, refresh, download both, and remove one.
- Verify a user without Team access cannot list, complete, download, or delete the files.
- Stop object storage and receive an explicit service error without corrupting file metadata.

Known production gate: malware scanning and byte-level MIME detection are intentionally visible as not configured until a scanning worker is deployed.

## M10 — Hardening and release

Scope:

- Add cross-repository browser E2E coverage for registration/login, team switching, issue/project workflows, Summary, notifications, and GitHub linking.
- Complete cursor pagination, optimistic locking, rate limits, structured errors, request IDs, and audit/observability coverage.
- Test migrations on empty and populated databases and rehearse service restart.
- Document environment configuration, deployment, backup, key rotation, webhook secret rotation, and incident procedures.

Exit demo:

- Run the entire critical-path suite from clean databases.
- Restart Redis, Business API, and Auth Service and verify expected recovery behavior.
- Produce a release candidate with no undocumented public endpoint or unprotected Business route.

## M11 — AI foundation

Scope is intentionally deferred until concrete agent use cases are approved. The foundation must distinguish human and agent principals, use explicit scopes, record execution/audit metadata, and pass through the same workspace/team/resource authorization boundary. An AI integration must never gain ambient access merely because it runs inside CommonPlan.

Model calls and tool calls use separate gateway boundaries. The optional LiteLLM MCP gateway profile
is deployable scaffolding only; M11 must define each upstream MCP server's owner, OAuth/service
credential custody, workspace/user propagation, allowed tools/resources, approval behavior, audit
fields and egress policy before registration. Permission to call a model alias never grants tool
discovery or execution.

## M12 — RAG ingestion and retrieval

**Status:** ingestion slice implemented in KEY-11 and under review; online retrieval, citations, and
answer generation remain pending.

Consume durable knowledge events asynchronously. Index both uploaded documents and contextual product records—issues, comments, projects, project objectives, milestones, and project updates—with workspace/team/resource ACL metadata. Extract supported documents, render database records deterministically, record parser/renderer/version/checksum, chunk content, generate embeddings, and store tombstones for deleted sources. Retrieval must apply authorization before returning chunks. Ingestion stays retryable and observable, and neither an upload nor a business mutation depends on model or vector-store availability.

Delivery is split into two reviewable vertical slices:

- KEY-11 ingestion foundation: business mutations append versioned knowledge events in the same PostgreSQL transaction; Debezium captures the append-only outbox from WAL and publishes to Redpanda through the Kafka protocol. A consumer-group worker handles retries and a dead-letter topic, then routes attachments through production Docling/hybrid chunking and database records through deterministic Markdown rendering/overlapping chunks. Both paths use the pinned local Qwen embedding alias through the private model gateway and persist ACL-scoped, versioned chunks plus delete tombstones. Consumer offsets—not mutable outbox rows—own delivery progress.
- Retrieval completion: add the vector similarity index and lexical index on the existing canonical
  `vector(1024)` chunks, authorization-filtered candidate queries, parent-page/neighbor expansion,
  fusion, reranking, citations, and bilingual evaluation fixtures. The pgvector storage foundation is
  implemented; retrieval is not shipped until an authorized search API and its quality/security tests
  exist.
- Model access boundary: the local ingestion path now calls the separately deployed,
  OpenAI-compatible LiteLLM Gateway described in the
  [model-gateway decision](../architecture/model-gateway-decision.md). Ingestion uses the immutable
  `commonplan-embedding-v1` alias; future query embedding must reuse it. A new vector space requires a
  new alias and re-ingestion rather than silent fallback. Production gateway persistence, restricted
  virtual keys, budgets, and HA remain deployment work.

## Verification loop for every milestone

### Automated

- API migration upgrade/downgrade/re-upgrade.
- Focused repository/service/policy/API tests.
- Authenticated versus unauthorized route tests.
- Web production build and focused UI tests when the Web test harness exists.
- Cross-repository E2E for the completed vertical slice.

### Local runtime

Start the API stack from `commonplan-api`:

```bash
cp .env.example .env
docker compose up --build
```

Start the Web app from `commonplan-web`:

```bash
cp .env.example .env
pnpm install --frozen-lockfile
pnpm dev
```

Open `http://localhost:5173`. API documentation remains available at `http://localhost:8000/docs`, with Auth Service documentation at `http://localhost:8001/docs`.

### Handoff

For each milestone, provide:

- API and Web branch/PR links.
- Migration list and tested downgrade target.
- Automated test/build results.
- Seed or fixture instructions.
- A numbered browser walkthrough with expected results.
- Known limitations assigned to a later milestone rather than hidden in implementation notes.
