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
| M2 — Issue planning core | Workflow states, cycles, issues, labels, assignment | M1 | Create `KEY-1` and update status, priority, assignee, cycle, and labels |
| M3 — Projects | Project brief, objectives, milestones, updates, linked issues | M2 | Create and manage a project with description, progress, and work items |
| M4 — Team Summary and analytics | Per-team summary, filters, visualizations, and drill-down | M2; richer project charts use M3 | Switch teams and see filter-consistent Jira-style analytics |
| M5 — Collaboration | Comments, activity, sub-issues, and complete resource policies | M2 | Collaborate on an issue while cross-team access fails closed |
| M6 — Saved views and Inbox | Reusable filters, My Issues, notifications, unread state | M4, M5 | Save a Summary/issue filter and receive actionable notifications |
| M7 — GitHub integration | Signed webhook intake and PR-to-issue auto-linking by item key | M2, M5 | A PR title containing `KEY-12` appears on that issue without project mapping |
| M8 — Settings and administration | Personal, connected-account, workspace, member, and domain settings | M1, M5 | Manage account/workspace settings with role checks and audit records |
| M9 — Hardening and release | E2E coverage, pagination, concurrency, observability, deployment readiness | M1–M8 | Critical flows pass automatically and survive service restart |
| M10 — AI foundation | Scoped agent identities, permissions, context, and execution audit | M9 | An agent performs an explicitly authorized work-item action with audit trail |

## M0 — Platform baseline

**Status:** implemented on the current design/auth branch and locally validated; publication remains part of the normal PR review flow.

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
- Add Team child menu destinations such as Overview, Issues, Projects, Cycles, Views, and Settings.
- Implement Team Overview counts as derived queries, not a stored overview table.
- Verify a user can belong to multiple teams and switching teams never leaks data.

Exit demo:

1. Create two workspaces and at least two teams.
2. Add one user to both teams and another user to only one team.
3. Switch workspace/team in the sidebar.
4. Confirm inaccessible team URLs return 403/404 according to the documented policy.

## M2 — Issue planning core

Scope:

- Add `workflow_states`, `cycles`, `issues`, `labels`, and `issue_labels`.
- Generate immutable team-prefixed keys under transaction/row lock.
- Implement issue list, create, detail, update, assignment, labels, cycle filtering, and optimistic `version` checks.
- Build Issues and Cycles pages plus issue create/detail UI.

Exit demo:

- Create `KEY-1`, assign it, change workflow state and priority, attach labels, and put it in a cycle.
- See different issue lists when switching teams.
- Submit a stale update and receive 409 rather than silently overwriting another edit.

## M3 — Projects

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

## M5 — Collaboration

Scope:

- Add `issue_comments` and append-only `issue_events`.
- Implement comments, edit/delete policy, activity timeline, sub-issues, and assignment/activity events.
- Centralize workspace/team/resource authorization and cover it with a role/resource matrix.
- Add comments, activity, and sub-issue sections to issue detail.

Exit demo:

- Two authorized users collaborate on one issue and see ordered activity.
- Removing team access blocks future reads without erasing authored history.
- A user cannot infer another team's issue by identifier, comment, event, or notification.

## M6 — Saved views and Inbox

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

## M9 — Hardening and release

Scope:

- Add cross-repository browser E2E coverage for registration/login, team switching, issue/project workflows, Summary, notifications, and GitHub linking.
- Complete cursor pagination, optimistic locking, rate limits, structured errors, request IDs, and audit/observability coverage.
- Test migrations on empty and populated databases and rehearse service restart.
- Document environment configuration, deployment, backup, key rotation, webhook secret rotation, and incident procedures.

Exit demo:

- Run the entire critical-path suite from clean databases.
- Restart Redis, Business API, and Auth Service and verify expected recovery behavior.
- Produce a release candidate with no undocumented public endpoint or unprotected Business route.

## M10 — AI foundation

Scope is intentionally deferred until concrete agent use cases are approved. The foundation must distinguish human and agent principals, use explicit scopes, record execution/audit metadata, and pass through the same workspace/team/resource authorization boundary. An AI integration must never gain ambient access merely because it runs inside CommonPlan.

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
