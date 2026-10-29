# SaveBite — accounts and final integration

This release completes the agreed local application workflow. It is not an externally hosted deployment.

- Username/password registration is limited to restaurant accounts. The initial administrator and administrator password recovery are created locally with setup_admin.py; there is no web bootstrap or embedded default password.
- Passwords use salted scrypt (N=32768, r=8, p=3). Login attempts are bounded in SQLite. Random 256-bit session tokens are kept in server-side session state; only their hashes and eight-hour expiry are stored in the database.
- Services validate the active session from SQLite, including role and donation ownership. Checks occur inside write transactions before idempotent receipt/event replay. ContextVar binding is reset after every script run to isolate concurrent users.
- Administrators can read all operational data, record needs, reset activity and manage restaurant accounts. Restaurants read and act on their own donations and reservations. Active recipient needs remain available for matching.
- Legacy records remain unowned and visible only to administrators. The first account migration makes a verified SQLite backup when donations already exist; schema changes are additive. Account password changes, administrative account updates and sign-out revoke sessions.
- Activity reset preserves accounts, static venue catalogues and road snapshots; the UI states that scope and requests current administrator password and RESET. Operational data are backed up before deletion.
- Native Streamlit navigation hides admin pages from restaurants. Protected page functions also deny unauthenticated/direct execution.
- Source assets and both languages remain bundled. Technical explanation stays in local academic notes, outside the public UI.

## Verification

The included tests use temporary databases, never user data. They cover two-account data isolation and replay authorization, atomic reservation/dispatch/delivery/cancellation, cancellation replay without double restoration, stale reset rejection, backup and reset, password changes, throttling, logout, disabling/expiry, concurrent context isolation, and legacy visibility.

The separate offline verifier checks 400 A*/Dijkstra pairs on synthetic directed graphs, valid edges and heuristic consistency. The routing algorithm and OSM filtering have not been replaced by this release. A route is shortest within its filtered graph; this does not certify complete live OSM coverage, all traffic rules, live traffic or endpoint access. See DOCTOR_NOTES.md.

No user database, live credentials, session token, road cache, virtual environment or browser screenshot is distributed.

## Operation

Run RUN_SAVEBITE.bat on Windows, or install requirements.txt and run `python -m streamlit run app.py`. SETUP_ADMIN.bat resets an existing administrator only when its exact username is supplied by the local operator. Server filesystem access is trusted operator access.

Before public deployment configure HTTPS and persistent storage, keep data/backups private, establish backups, and verify the target hosting environment. Restaurant signup is not legal business verification; recipient coordination and physical delivery remain human tasks. No email verification or email password-reset provider is configured. Do not place the database in a publicly served directory.

## Integrated interface checks for this release

Real browser tests exercised sign-in/out, restaurant/admin navigation, saving a donation, reserving meals, dispatch, delivery and administrator account controls. The resulting isolated database contained exactly one start and one delivery event. Arabic sign-in was visually inspected on desktop and 390-pixel mobile with no horizontal overflow; direct protected URLs showed sign-in without a page-not-found dialog. Streamlit AppTest also passed Arabic/English login and logout, retained language and unauthenticated page guards.

The browser environment intermittently rejected the external Leaflet CDN with ERR_EMPTY_RESPONSE. Map navigation and iframe creation were checked, but this run does not certify live map tiles or external CDN availability. Existing map/routing core code was retained. The Windows launch scripts were reviewed here; they have not been executed on the user's Windows computer.
