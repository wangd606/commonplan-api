# Zhitong system architecture

## Service and trust boundaries

```mermaid
flowchart LR
    subgraph browser [Browser]
        react[React application]
        access[Access JWT in memory]
        browserSession[Rotating HttpOnly BFF session cookie]
        oauthState[Short-lived OAuth state cookie]
    end

    subgraph identity [Identity boundary]
        auth[Auth Service :8001]
        credentials[Password and Google identity logic]
        issuer[RS256 token issuer]
        jwks[JWKS public keys]
        authdb[(zhitong_auth PostgreSQL)]
        privateKey[(RSA private key volume)]
    end

    subgraph product [Product boundary]
        api[Business API :8000]
        bff[Web BFF auth adapter]
        vault[Encrypted refresh-token vault]
        guard[Router-level auth guard]
        policies[Resource authorization policies]
        domain[Business services]
        appdb[(zhitong PostgreSQL)]
        redis[(Redis disposable cache)]
    end

    google[Google OAuth and OIDC]

    react -->|Register, login, refresh, logout| bff
    react -->|Google redirect through BFF| auth
    bff -->|Authenticated internal token calls| auth
    auth <-->|Authorization code exchange| google
    auth --> credentials
    credentials --> authdb
    auth --> issuer
    issuer --> privateKey
    issuer --> access
    auth -->|Raw refresh only over internal channel| bff
    bff --> vault
    bff --> browserSession
    auth --> jwks

    react -->|Bearer access JWT| api
    api --> guard
    guard -->|Fetch and cache public keys| jwks
    guard --> policies
    policies --> domain
    domain --> appdb
    domain --> redis
```

The private signing key and all credentials stop at the identity boundary. Business API trusts only
tokens that match the configured issuer and audience and verify against Auth Service's public JWKS.
It keys the application profile by the immutable pair `(auth_issuer, auth_subject)` rather than by
email. Email is a mutable display/contact attribute, not the security identifier.

## Data ownership

| Owner | Data | Purpose |
| --- | --- | --- |
| Auth Service database | identity users, password hashes, external provider subjects | Authentication identity |
| Auth Service database | refresh-token hashes, families, expiry and revocation | Rotation and replay response |
| Auth Service database | hashed login identifiers and lock windows | Persistent brute-force throttling |
| Auth Service key store | RSA private/public key pair | Signing access tokens and publishing JWKS |
| Business database | local user/profile linked by issuer + subject | Application-facing representation |
| Business database | encrypted refresh-token vault, application sessions and future domain entities | BFF credential custody and durable product state |
| Redis | cached application-session payloads | Disposable acceleration only |
| Browser memory | short-lived access JWT | Bearer authentication to Business API |
| Browser HTTP-only cookie | opaque rotating BFF session id | Select a server-side refresh credential without exposing it |

Authentication storage exists only in the Auth Service database. The Business database contains the
application-facing user projection, browser-session vault, and independent application sessions; it
does not retain password hashes, provider subjects, or refresh-token ledger rows.

## Password registration and login

```mermaid
sequenceDiagram
    participant User
    participant React
    participant BFF as Web BFF
    participant Auth as Auth Service
    participant AuthDB as Auth PostgreSQL
    participant API as Business API
    participant AppDB as Business PostgreSQL

    User->>React: Submit registration or login
    React->>BFF: POST /auth/register or /auth/login
    BFF->>Auth: Authenticated internal request
    Auth->>AuthDB: Create identity or verify Argon2 hash
    Auth->>AuthDB: Persist refresh-token hash and family
    Auth-->>BFF: Access JWT plus raw refresh token
    BFF->>BFF: Encrypt refresh token in PostgreSQL
    BFF-->>React: Access JWT plus HttpOnly opaque session cookie
    React->>API: GET /users/me with Bearer JWT
    API->>Auth: Resolve signing key from JWKS cache
    API->>API: Validate typ, signature, issuer, audience and time claims
    API->>AppDB: Find or create profile by issuer plus subject
    API-->>React: Authenticated application profile
```

Invalid login responses do not reveal whether an email exists. Failures are recorded against a
SHA-256 hash of the normalized identifier and temporarily locked after the configured threshold.

## Google login

```mermaid
sequenceDiagram
    participant User
    participant Browser
    participant Auth as Auth Service
    participant BFF as Web BFF
    participant Google
    participant AuthDB as Auth PostgreSQL
    participant API as Business API

    User->>Browser: Continue with Google
    Browser->>Auth: GET /auth/google/login
    Auth-->>Browser: Signed OAuth state cookie and redirect
    Browser->>Google: Authorization request
    Google-->>User: Login and consent
    User->>Google: Approve or cancel
    alt approved
        Google-->>Browser: Callback with code and state
        Browser->>Auth: GET /auth/google/callback
        Auth->>Google: Server-side code exchange
        Google-->>Auth: Verified OIDC identity
        Auth->>AuthDB: Link provider subject to identity
        Auth->>AuthDB: Store single-use login-code hash
        Auth-->>Browser: Redirect to BFF with 60-second one-time code
        Browser->>BFF: GET /auth/google/complete?code=...
        BFF->>Auth: Authenticated one-time code exchange
        Auth-->>BFF: Access JWT plus refresh token
        BFF-->>Browser: Opaque HttpOnly session cookie and app redirect
        Browser->>BFF: POST /auth/refresh
        BFF-->>Browser: Rotated session cookie and access JWT
        Browser->>API: Business request with Bearer JWT
    else canceled
        Google-->>Browser: Callback with error=access_denied
        Browser->>Auth: GET callback with state and error
        Auth-->>Browser: Clear state and redirect with auth_error
        Browser-->>User: Show cancellation without creating an identity
    end
```

Google is an upstream identity provider, not the API credential issuer. Zhitong Auth Service exchanges
the code, validates the Google identity, and issues the same Zhitong access/refresh tokens used by
password login. Google secrets and tokens never enter React JavaScript or query strings.

## Business API request pipeline

```mermaid
flowchart LR
    request[Incoming business request] --> router[Protected APIRouter]
    router --> present{Bearer token present?}
    present -->|No| unauthorized[401 before handler]
    present -->|Yes| verify[Validate RS256 JWT with cached JWKS]
    verify -->|JWKS unavailable| unavailable[503 fail closed]
    verify -->|Invalid token or claims| unauthorized
    verify --> principal[Build immutable AuthPrincipal]
    principal --> profile[Resolve active local profile by iss plus sub]
    profile -->|Deleted| unauthorized
    profile --> policy[Apply endpoint resource policy]
    policy -->|Forbidden| forbidden[403 before business mutation]
    policy -->|Allowed| service[Execute business service]
    service --> response[Business response]
```

All business endpoints are registered on one `APIRouter` with
`dependencies=[Depends(get_current_user)]`. New product routes therefore fail closed unless a developer
consciously mounts a separate public router. Tests inventory OpenAPI operations and assert that every
business operation carries bearer security; only health and the explicit BFF auth routes are public.
Endpoint-specific policies still matter after
authentication; current profile writes use a self-only policy, and future team/project routes should
check membership and role before accessing their service.

## Refresh rotation and replay

```mermaid
sequenceDiagram
    participant React
    participant BFF as Web BFF
    participant Auth as Auth Service
    participant AuthDB as Auth PostgreSQL

    React->>BFF: POST /auth/refresh with opaque session cookie
    BFF->>BFF: Lock session row and decrypt server-side refresh token
    BFF->>Auth: Authenticated refresh request
    Auth->>AuthDB: Lock token hash row
    alt active and unexpired
        Auth->>AuthDB: Revoke old row and insert replacement in same family
        Auth-->>BFF: New refresh token plus 15-minute access JWT
        BFF->>BFF: Encrypt replacement and rotate browser session id
        BFF-->>React: New session cookie plus access JWT
    else already rotated or revoked
        Auth->>AuthDB: Revoke every active token in that family
        Auth-->>BFF: 401
        BFF-->>React: 401 and clear session cookie
    else missing or expired
        BFF-->>React: 401 and clear session cookie
    end
```

Access-token revocation is intentionally bounded by the 15-minute maximum lifetime. Account deletion
also blocks the local business profile immediately. A later phase can add a token-version or
introspection mechanism if the product needs immediate invalidation across all resource servers.

## Application sessions remain separate

```mermaid
flowchart LR
    feature[Workflow endpoint] --> sessionService[Application SessionService]
    sessionService --> store[DatabaseBackedSessionStore]
    store --> appdb[(PostgreSQL source of truth)]
    store --> redis[(Redis read-through cache)]
    redis -->|Miss or outage| appdb
```

Application sessions answer questions such as which onboarding step, draft, filter, or workflow is
active. They never identify the API caller and are never accepted in place of a bearer token. Clearing
Redis loses cached copies only; PostgreSQL remains authoritative.

## Identity-store boundary

The application is pre-production, so the temporary legacy identity bridge has been removed. New
accounts are created directly in Auth PostgreSQL, while the Business `users` row is provisioned from a
verified JWT on first API use. Migration `20260919_0006` removes the former Business password,
Google-subject, and refresh-token storage so future code has one unambiguous authentication owner.
