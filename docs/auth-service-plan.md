# Auth Service separation plan

## Goal

Build a Zhitong-owned identity authority with the same clear responsibility split used by mature
identity platforms, while keeping product data and authorization policy under Zhitong's control.
Authentication answers **who the caller is**. Business authorization answers **what that identity may
do to this resource**. Application sessions hold non-credential workflow state.

## Implemented in KEY-8

### 1. Independent deployable and data boundary

- Added a separately built and deployed FastAPI Auth Service on port `8001`.
- Added a dedicated `zhitong_auth` database and independent Alembic history.
- Moved runtime ownership of password hashes, Google subjects, refresh tokens, and login-attempt state
  out of Business API.
- Added a persistent RSA key volume. The private key is loaded only by Auth Service.
- Kept Business API and Auth Service source in one repository for now; deployable and ownership
  separation does not require premature repository separation.

### 2. First-party identity flows

- Email/password registration and login with Argon2.
- Google authorization-code login handled server-side, including state validation and cancellation.
- Safe linking by verified Google subject and verified email.
- Indistinguishable invalid email/password responses and persistent per-identifier throttling.
- Credential responses marked `Cache-Control: no-store`.

### 3. Token authority

- RS256 access JWTs with `typ=at+jwt`, `kid`, `iss`, `sub`, `aud`, `client_id`, `iat`, `exp`,
  and `jti`.
- Maximum access lifetime enforced at configuration load: 15 minutes.
- Public JWKS endpoint containing only public RSA parameters.
- Opaque refresh tokens stored as SHA-256 hashes, locked during rotation, and organized in rotation
  families.
- Replay of a rotated refresh token revokes the remaining family.
- Raw refresh tokens are returned only to the authenticated Web BFF, encrypted in its PostgreSQL
  token vault, and never sent to browser JavaScript or browser cookies.
- Google completion uses a hashed, single-use, 60-second login code so the browser can hand control
  back to the BFF without carrying a token pair.

### 4. Resource-server enforcement

- Business API contains no JWT signing code and no shared signing secret.
- It retrieves and caches Auth Service public keys, pins `RS256`, and verifies token type, signature,
  issuer, audience, client, expiry, issued-at, maximum lifetime, subject, and token ID.
- Every business endpoint is mounted under a router-level `get_current_user` dependency. Authentication
  runs before endpoint and service code.
- Auth outage or signing-key fetch failure fails closed with `503`; missing or invalid credentials fail
  with `401`.
- Profiles are provisioned and resolved by immutable `(issuer, subject)`.
- Mutating another user's profile fails with `403`; this is the initial resource-authorization policy.
- OpenAPI inventory tests guard against accidentally adding an unprotected business operation.

### 5. Storage boundary

- Business migration adds `auth_issuer` and `auth_subject` using raw DDL in `op.execute`.
- Auth migration creates the identity store using raw DDL in `op.execute`.
- Business migration `20260919_0006` removes the temporary password, Google-subject, and refresh-token
  storage from the Business database.
- New application profiles are provisioned from verified JWT identities; no compatibility bridge runs
  during startup.

## Security invariants

1. A Business API handler cannot run without a valid access token unless it is explicitly mounted on
   the public app router.
2. A valid signature is insufficient: issuer, audience, token type, lifetime, and required claims must
   also match.
3. Auth Service never publishes or transmits the private signing key.
4. Business handlers never accept refresh tokens, BFF session cookies, or application-session IDs as
   caller identity; only a verified access JWT creates the principal.
5. Refresh-token plaintext exists only transiently inside Auth Service and the BFF. Auth stores a
   hash; the BFF stores authenticated encryption; the browser never receives it.
6. Email cannot replace issuer plus subject as the durable identity key.
7. Authentication is followed by endpoint-specific authorization before a protected mutation.
8. Redis loss cannot delete identities, revoke durable refresh state, or erase application sessions.

## Route ownership

| Route family | Service | Protection |
| --- | --- | --- |
| `/health` | Both | Public liveness only |
| `/.well-known/jwks.json` | Auth | Public public-key material |
| `/auth/register`, `/auth/login` | Web BFF | Public entry points; BFF calls Auth internally |
| `/auth/google/login`, `/auth/google/complete` | Web BFF | Browser entry and one-time-code completion |
| `/auth/google/callback` | Auth | Google OAuth state and identity validation |
| `/auth/refresh`, `/auth/logout` | Web BFF | Rotating opaque session cookie; refresh remains server-side |
| `/internal/auth/*` | Auth | HTTP Basic confidential-client authentication; BFF only |
| `/auth/me` | Auth | Auth Service bearer validation |
| `/users/me`, `/users/*` | Business | Router-level bearer validation plus policies |
| Future `/teams`, `/projects`, `/issues` | Business | Same router guard plus membership/role policies |

The BFF cookie is scoped to `/auth`. Browser cookies are scoped by host and path, not by port, so
keeping product routes outside `/auth` prevents even the BFF session id from accompanying business
requests. The raw refresh token never enters the browser at all.

## Deployment sequence

1. Configure stable issuer/audience values.
2. Create both databases and apply the Auth Service and Business API migrations.
3. Start Auth Service and verify health plus JWKS.
4. Start Business API with the internal JWKS URL and public issuer value.
5. Start React with the Business API/BFF base URL; the browser reaches Auth Service only for the
   Google redirect flow.
6. Monitor authentication errors, JWKS availability, login throttles, refresh replay, and identity-link
   conflicts.
7. Back up both databases before production migrations once persistent environments exist.

The application is pre-production, so there is no legacy identity bridge or compatibility window.
Authentication records are created directly in Auth DB and Business users are provisioned from JWTs.

## What is intentionally not claimed yet

The new service is an independent first-party identity authority, not yet a complete OAuth 2.0 or
OpenID Connect provider. It does not publish discovery metadata because doing so would falsely promise
unsupported authorization and token endpoints or conformance behavior.

For the current React app, password/Google entry at Auth Service plus short-lived resource JWTs is a
valid staged architecture. If Zhitong later needs multiple public clients, third-party integrations,
SSO federation, or standardized client libraries, implement the next phases below or adopt a proven
provider instead of growing an ad hoc protocol surface.

## Phased roadmap toward Keycloak-class responsibilities

### Phase A — production hardening

- Verified-email workflow and resend policy.
- Password reset with single-use, short-lived recovery tokens.
- MFA and recovery codes.
- Security event/audit log, device/session view, and revoke-all controls.
- Administrator APIs with explicit roles and step-up authentication.
- Secret-manager-backed signing keys, automated rotation, and overlapping JWKS keys.
- Metrics and alerts for login failures, refresh replay, JWKS fetch failures, and token rejection
  reasons without logging credentials.

### Phase B — domain authorization

- Add team memberships and roles to the Business database.
- Build reusable `require_team_member`, `require_team_role`, and resource-ownership dependencies.
- Keep frequently changing permissions out of long-lived token claims; resolve them at the Business
  API from authoritative domain state.
- Add an authorization matrix test for every project/team/issue operation.

### Phase C — standards-complete browser authorization

- Authorization endpoint using Authorization Code flow with mandatory PKCE.
- Token endpoint with strict redirect-URI and client validation.
- OIDC discovery and user-info endpoints only when their advertised metadata is accurate.
- Consent, client registration/management, nonce handling, and conformance testing.
- Refresh-token rotation or sender-constrained refresh tokens for public clients.

### Phase D — optional operational capabilities

- Identity federation and additional social providers.
- Organization policies, account linking controls, and realm/tenant isolation if product requirements
  call for them.
- Optional full BFF proxy if the product later decides that JavaScript must never hold even the
  short-lived access token. The current token-mediating BFF deliberately returns access tokens to
  React while keeping refresh tokens server-side.

## Acceptance evidence

- Auth Service tests cover registration, login, invalid credentials, throttling, bearer identity,
  refresh rotation/replay, logout, Google linking/cancellation, JWT claims, and public-only JWKS.
- Business API tests cover public-route inventory, missing/invalid tokens, signature/issuer/audience/type
  validation, execution ordering, identity provisioning, deleted users, and self-only authorization.
- Migration validation covers upgrade, downgrade, re-upgrade, and idempotent legacy import.
- End-to-end validation covers real RS256 issuance, JWKS verification, successful business access,
  unauthenticated rejection, and tampered-token rejection.

## Standards used as design constraints

- [RFC 9700 — OAuth 2.0 Security Best Current Practice](https://www.rfc-editor.org/rfc/rfc9700.html):
  short-lived access credentials, mandatory PKCE for future public authorization-code clients, and
  refresh-token replay defenses.
- [RFC 9068 — JWT Profile for OAuth 2.0 Access Tokens](https://www.rfc-editor.org/rfc/rfc9068.html):
  asymmetric signed resource tokens, `typ=at+jwt`, issuer/audience restriction, and public-key
  publication.
- [RFC 7636 — Proof Key for Code Exchange](https://www.rfc-editor.org/rfc/rfc7636.html): future
  Authorization Code + PKCE requirements.
- [OpenID Connect Core 1.0](https://openid.net/specs/openid-connect-core-1_0.html): future provider
  discovery, ID token, nonce, and user-info behavior.
- [Keycloak Server Administration Guide](https://www.keycloak.org/docs/latest/server_admin/):
  responsibility reference for identity federation, sessions, recovery, MFA, and administration.
