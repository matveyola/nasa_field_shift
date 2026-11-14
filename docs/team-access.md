# Team access

Field Shift can run as a trusted local workspace or require individual team accounts. This deployment owns one farm. All team members belong to that farm; this release does not create or switch between independent customer farms.

## Enable sign-in

1. Back up PostgreSQL and apply migrations through `0008_team_access` or the current head. Existing fields, notes and jobs remain unchanged.
2. Generate a random setup token with `python -c "import secrets; print(secrets.token_urlsafe(32))"` in your own terminal. Store it in the server `.env` as `FIELD_SHIFT_BOOTSTRAP_TOKEN`. Do not put it in a frontend environment variable.
3. Set `FIELD_SHIFT_ACCESS_MODE=team`. For a public deployment also set `FIELD_SHIFT_ENVIRONMENT=production`, `FIELD_SHIFT_SECURE_COOKIE=1`, and `FIELD_SHIFT_PUBLIC_ORIGIN=https://your-farm.example`. An origin has no path. Production refuses to start with local access or insecure cookies.
4. Recreate the API after changing its environment. Open the app and create the first administrator with the setup token, name, email and a password of 12–128 characters. No administrator or password is created automatically.
5. Remove the bootstrap token from the server environment and recreate the API. The setup endpoint also refuses reuse once a farm membership has been created.

For local team testing, `FIELD_SHIFT_SECURE_COOKIE=0` permits plain HTTP on localhost. The normal local default remains `FIELD_SHIFT_ACCESS_MODE=local`, which deliberately has no login and must not be shared as a public service. Browser hosts are limited to localhost, loopback and the configured public-origin host.

## Roles and invitations

Farm → Farm team lists members. An administrator creates a one-use invitation for an exact email address and shares its link privately. The app does not send email. Invitations expire after seven days; creating another for the same address revokes the earlier pending one. A token is displayed only when created, stored hashed in PostgreSQL and placed in the URL fragment rather than a query parameter. The page removes the fragment after reading it.

| Role | Rights |
| --- | --- |
| Administrator | All editor rights, invite/revoke members and change roles |
| Editor | Update fields, notes and plans, launch calculations, use the assistant, approve and import precision results |
| Viewer | Read farm data, maps, observations and conversations, download existing exports |

Viewers cannot launch server computations or send agent messages, because these create records and may incur costs. Role enforcement applies centrally to every API mutation, including newly registered modules. Domain reads also enforce the farm boundary. Jobs belonging to another farm are excluded from both lists and direct lookup. Photos and conversations retain field ownership checks.

Revoking access immediately invalidates the member's sessions. Restoring access requires a new login. Role changes apply to subsequent requests without waiting for a cookie to expire. The last active administrator cannot be removed or demoted. Changing a password signs out every previous session and issues a fresh session to the current browser. Existing accounts accepting a replacement invitation must provide their existing password; an invitation cannot reset it.

## Session and deployment behavior

Sessions are opaque random tokens, with only a SHA-256 digest stored in PostgreSQL. Cookies are HttpOnly, SameSite=Strict, path `/api`, and Secure in production. Sessions have an eight-hour absolute lifetime and are capped at five per account. User data and auth responses use `Cache-Control: no-store`. Credentials and session tokens are not stored in browser localStorage. Device-local scouting drafts are isolated by signed-in account.

Authenticated mutations require a session-specific CSRF header. Public sign-in/setup/invitation requests require a custom application header. Origin, host and cross-site fetch checks supplement SameSite cookies. Reverse proxies must preserve the original Host including port; the bundled nginx and Vite proxy do so. Never enable wildcard CORS for these endpoints.

Passwords use the Python/OpenSSL scrypt implementation with unique salts, N=2^15, r=8, p=3, a 32 MiB work area and at most two concurrent hashes per API process. These parameters follow an [OWASP recommended scrypt setting](https://cheatsheetseries.owasp.org/cheatsheets/Password_Storage_Cheat_Sheet.html). Auth throttling is PostgreSQL-backed, persists across API restarts and does not depend on Redis. The default limit is ten requests per account identity and forty per source address per fifteen minutes; agent messages are limited to fifteen per user per minute. The server ignores arbitrary forwarding headers. Configure a trusted proxy allow-list in the ASGI deployment if client-level IP limiting is needed behind a proxy; otherwise the proxy address shares a conservative limit.

If a member forgets a password, an operator with trusted server access can run `python -m field_shift.modules.access.recover member@example.org` inside the API environment. It prompts for the new password without echoing or storing it in command history, leaves the role unchanged and revokes all sessions. With Compose use `docker compose exec api python -m field_shift.modules.access.recover member@example.org`.

This is password-based access without an external identity provider, email verification, MFA or a self-service password-reset email. Keep PostgreSQL backups and restrict server-operator access. A successful authorization check does not replace review of network exposure, HTTPS termination, host configuration and deployment secrets.
