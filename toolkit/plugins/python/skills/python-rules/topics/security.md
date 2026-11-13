## 10. Security defaults

- Deny by default. Enforce authentication by position (middleware before routing), so a new endpoint is protected the moment it is mounted. Public paths are an explicit set, matched on whole path segments.
- One identical failure response for every authentication failure: unknown subject, wrong secret, expired, wrong token type, inactive user. Equalise timing where a lookup is skipped by verifying against a dummy hash.
- Signed is not encrypted. Restrict accepted algorithms to an explicit allowlist and require the claims relied on.
- Verify the token type where it is consumed; a refresh token must never be accepted as an access token.
- Access tokens are short-lived and stateless. Refresh sessions are persisted by `jti`, single-use and rotated on every refresh; the stored expiry is authoritative.
- Long-lived credentials get the narrowest transport scope that works: the refresh token travels only as an `HttpOnly` cookie scoped to the auth path and never appears in a response body.
- Password hashing is Argon2id with cost parameters from settings, run in a worker thread.
- Operations a client may retry are idempotent: logout with no session still succeeds.
- Response models omit sensitive fields by construction (no `password_hash` field to leak).
