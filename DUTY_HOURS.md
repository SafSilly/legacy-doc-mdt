# DOC MDT Duty Hours

## What is implemented

Duty Hours is rendered inside the real employee profile (`/employee/profile` and
the existing callsign profile route), including the MDT's embedded profile.
Five cards show Normal, Training, Extra, and Total in HH:MM:SS. Hours do not wrap
at 24. Newer/Older move five weeks; the date picker anchors five weeks at the
selected date's containing week. Future dates are rejected by the server.

There is no game connection configured by default and no fabricated history.
Zero totals mean there are no counted sessions in that range. A roster-only
employee without a linked account gets an unavailable message instead of fake
zero-hour history. Existing account/roster matching is unchanged.

## Reference findings

Reviewed the upstream master versions of:

- [StaffCommon.cs](https://github.com/CloudTheWolf/Legacy-RP-Discord-Bots/blob/master/DOC.Module/Common/StaffCommon.cs)
- [TimeActions.cs](https://github.com/CloudTheWolf/Legacy-RP-Discord-Bots/blob/master/DOC.Module/Actions/TimeActions.cs)
- [Heartbeat.cs](https://github.com/CloudTheWolf/Legacy-RP-Discord-Bots/blob/master/DOC.Module/Actions/Heartbeat.cs)
- [Options.cs](https://github.com/CloudTheWolf/Legacy-RP-Discord-Bots/blob/master/DOC.Module/Options.cs)

The bot consumes `characters.on_duty_time`. Under `Law Enforcement`, weeks below
107 contain scalar seconds; later weeks contain `normal`, `training`, and
`undercover` seconds. Its week index uses epoch 1609113600 plus fixed 604800-second
intervals. The duty heartbeat reads `/op-framework/duty.json`, filters department
`doc`, and matches `characterId` to `character_id`. It is a status consumer, not
the game clock. The batch character query filters `Bolingbroke Penitentiary`.

Our session model deliberately does not import those aggregates as artificial
sessions: they contain no start/end timestamps. `undercover` is not silently
treated as Extra. A historical import requires a separately agreed mapping and
aggregate provenance. Local-calendar weeks use the existing organization
timezone, rather than copying the bot's fixed UTC week index.

## Files in this implementation

Modified:

- `app.py`: adds the duty CSS/JS to static asset cache versioning.
- `mdt.py`: registers duty functionality after existing MDT migrations/auth helpers.
- `templates/employee_profile.html`: includes the Duty Hours section and assets.

Created:

- `mdt_duty.py`: migration, integration endpoints, totals, correction route, formatting.
- `duty_settings.json`: weekly reset day and optional minimums by existing rank code.
- `templates/_mdt_duty.html`: five-week view and supervisor correction forms.
- `static/mdt-duty.css`: scoped styling using existing profile/theme variables.
- `static/mdt-duty.js`: navigation and correction submissions; no duty timer.
- `test_mdt_duty.py`: disposable-database integration and calendar tests.
- `DUTY_HOURS.md`: this implementation and integration guide.

Earlier MDT/dashboard work in the working tree is separate from this change.

## Database and migration

SQLite `duty_sessions` stores:

- `employee_id`: existing `supervisor_users.id`.
- `employee_cid`: snapshot of the existing account's `username` (Character ID),
  guarding against accidentally attaching history to a reused numeric row ID.
- `department`: DOC only; `duty_type`: normal/training/extra.
- `session_key`: stable, unique game shift identifier.
- `started_at`, `ended_at`, `created_at`, `updated_at`, `deleted_at`: UTC Unix seconds.
- `duration_seconds`: null while active, otherwise end minus start.
- `revision`: optimistic concurrency counter for corrections.

Indexes enforce one active, nondeleted session per employee and unique shift
keys per employee/CID. The employee/CID/time index supports range queries.
Check constraints enforce valid types and duration consistency. Sessions are
the only source of duty totals; there is no aggregation cache.

Migration runs idempotently during normal application startup after `init_db()`
and the MDT migration. Back up the configured SQLite database before rollout,
then restart the app; no separate migration command or new package is required.
Starting the local preview migrates only `instance/mdt-preview.db` when that
is the selected `DATABASE_URL`. Production has not been migrated or deployed.

Session deletion is soft deletion. The existing `mdt_personnel_events` table
records DUTY_STARTED, DUTY_ENDED, DUTY_SESSION_CORRECTED, DUTY_SESSION_DELETED,
including actor account ID, affected employee ID/CID, old/new session values,
reason, and UTC timestamp. No second audit system was created.
Inactive roster status does not remove or filter history. Deleted/nonexistent
accounts return 404; retained session rows are not reassigned to another CID.

## Settings

`APP_TIMEZONE` already exists and defaults to `America/Denver`. Duty uses that
organization timezone, not the browser's timezone or a user-supplied query value.

In `duty_settings.json`:

- `week_start_day`: 0=Monday through 6=Sunday, at local midnight. Default: Monday.
- `required_hours`: W, DW, AW, C, LT, S, SO, CO, CC. Values are nonnegative hours
  or `null` (unset). All ranks are initially unset; examples were not adopted as policy.

Restart after editing settings. Unset minimums display “not configured” and
neither failure nor success coloring. Explicit 0 means no minimum. Below-minimum
totals use danger color; met minimums use success color. The person's current
MDT department rank determines the requirement for all displayed weeks. Historical
rank-specific requirements are not inferred. Changing timezone/reset settings
recalculates the presentation of historical sessions; it never changes timestamps.

## Authentication

Browser reads use the existing `login_required` and current session. This matches
existing profile permissions: signed-in accounts can view other employee profiles.
Corrections use existing `supervisor_required` (owner/admin/supervisor) plus the
existing MDT CSRF token. Normal employees have no clock or correction controls.

There was no existing machine authentication or FiveM resource in this repository.
The start/end integration therefore needs these **new environment variables**:

- `DUTY_API_TOKEN`: a random secret of at least 32 characters, kept only on the
  MDT host and FiveM server. Generate privately with `secrets.token_urlsafe(48)`.
- `DUTY_API_ACTOR_ID`: the internal database ID of an existing owner/admin account
  responsible for this integration, not their CID. You can identify it from the
  existing Accounts card or database. The account and its admin role are checked
  on each call. Removing that role disables integration writes.

This narrowly scoped bearer credential can only start/end DOC sessions. It grants
no profile access, corrections, account management, or browser login. Never put it
in HTML, client Lua, shared scripts, or a replicated FiveM convar. Use HTTPS between
hosts. Missing/short token returns 503; wrong credentials return 401; an invalid
attribution account returns 403. No service credential has been installed for you.

## Start and end payloads

`POST /mdt/api/duty/start`

Header: `Authorization: Bearer <DUTY_API_TOKEN>`

Header: `Content-Type: application/json`

```json
{
  "characterId": "666",
  "department": "DOC",
  "dutyType": "normal",
  "sessionKey": "unique-server-generated-shift-id"
}
```

`characterId` is the existing numeric account username as a string. No separate
identity is created. Unknown CIDs return 404. `dutyType` defaults to normal and
accepts normal/training/extra. `sessionKey` is required, 1–128 letters, numbers,
underscores, periods, colons, or hyphens. Generate a new key per shift and retain
the same key for every retry and the matching clock-out.

First start: 201 with `status: started`, `sessionId`, and `startedAt` (UTC ISO).
Same key retry: 200 duplicate, even after that shift ended. A different key while
already active: 409; never creates a second session. A same-key type mismatch is
409. To change duty type, end the existing shift and start a new key/type.

`POST /mdt/api/duty/end`, same headers:

```json
{
  "characterId": "666",
  "department": "DOC",
  "sessionKey": "unique-server-generated-shift-id"
}
```

Success: 200 with `status: ended`, `sessionId`, `endedAt`, `durationSeconds`.
Repeated end: 200 duplicate. Unknown/already-removed session: 200 ignored and no
time is created. An old end retry cannot close a newer shift because keys must
match. Unknown fields, including durations or timestamps, are rejected with 400.
Clock regression is rejected rather than generating negative time.

## Read API and weekly calculations

`GET /mdt/api/employees/<internal-account-id>/duty-hours?week=YYYY-MM-DD`

Omit `week` for current. Uses browser session authentication, not the integration
token. Returns employeeId, characterId, rank, weeklyRequirementHours, timezone,
resetDay, asOf, active, selected, newer, older, and five `weeks`.
Each week has explicit local `weekStart` and exclusive `weekEnd`, UTC `startUtc`
and exclusive `endUtc`, normalSeconds/trainingSeconds/extraSeconds/totalSeconds,
and a type-keyed seconds object. No browser week calculations are required.

The helper constructs each boundary at midnight in the organization's timezone,
then converts both to UTC. DST weeks can be 167 or 169 hours. For every session,
the contribution is `max(0, min(end, weekEnd, serverNow) - max(start, weekStart))`.
An active session uses serverNow as its end without writing to the database.
Sunday 23:30 to Monday 01:30 contributes 30 minutes to the prior week and 90
minutes to the new week. Rendering snapshots does not advance or persist time;
refresh the profile to request a new authoritative snapshot.

## Supervisor corrections

Open the employee profile, expand **Supervisor session corrections**, then expand
a session. Edit start/end (ISO UTC or explicit offset), duty type, and reason.
An empty end leaves/reopens the session as active. Choose **Remove session from
totals** to soft-delete it. Click **Apply correction**. The date window also
controls which sessions appear; at most 100 overlapping records are listed.

`POST /mdt/duty/sessions/<id>/correct` receives form fields `csrf_token`, `revision`,
`startedAt`, `endedAt`, `dutyType`, `reason`, and `action` (`correct` or `delete`).
Deletion only requires token/revision/reason/action. Changes reject future times,
naive timestamps, negative intervals, stale revisions, overlapping sessions, and
missing reasons. Duration is always recomputed, never accepted from the client.
No arbitrary “add hours” form is available.

## FiveM work still required

The repository has no game framework resource or authoritative DOC duty event.
Connect the two HTTP endpoints to the real **server-side** DOC duty transition.
Resolve CID and department from trusted server character state; never accept an
unverified CID/duty transition from a client net event. Default to normal until
the game can distinguish training/extra.

Use FiveM server-side `PerformHttpRequest` with the payload/headers above. Persist
the active sessionKey by character on the FiveM side so resource restarts reuse
it. A typical framework adapter calls a private helper of this shape:

```lua
-- SERVER ONLY. cid/type/key must come from verified game state.
local function sendDocDuty(action, cid, dutyType, sessionKey, callback)
    local payload = {characterId = tostring(cid), department = 'DOC',
                     dutyType = dutyType or 'normal', sessionKey = sessionKey}
    PerformHttpRequest(GetConvar('doc_mdt_url', '') .. '/mdt/api/duty/' .. action,
        callback, 'POST', json.encode(payload), {
            ['Content-Type'] = 'application/json',
            ['Authorization'] = 'Bearer ' .. GetConvar('doc_mdt_token', '')
        })
end
```

Use private `set` convars, never `setr`. Queue requests per character, await the
start response before delivering the corresponding end, retry transient failures
with the same key, and reconcile 409 responses rather than inventing new keys.
No public net event is supplied because the authoritative framework is unknown.
An MDT restart retains active sessions without any memory timer. An unreported
game crash/disconnect leaves a session open until a real end or an audited
supervisor correction. Network outages can shift receipt-time boundaries; the
integration does not backdate untrusted queued timestamps. Reconciliation of
missed transitions requires a supervisor correction. There is no bot polling.

## Manual verification and checks

1. Start the preview with `DATABASE_URL=instance/mdt-preview.db`; use a known
   test employee account in that separate database.
2. Configure the two duty environment variables privately and restart Flask.
3. Send the start JSON for that account's CID/key. Expect 201; resend and expect
   duplicate. A different key while active must return 409.
4. Wait at least a minute; open that employee's Profile. Current totals should
   include elapsed server time. No page needs to stay open.
5. Send end with the same key; note durationSeconds. Repeating end does not add time.
6. Use Older/Newer and the date picker. Check the configured timezone and date labels.
7. As supervisor, correct the test session across a weekly boundary with a reason;
   verify each week's portion. Inspect `mdt_personnel_events` for the audit entry.
8. As a normal employee, verify correction controls are absent and POST returns 403.
9. Set rank minimums in `duty_settings.json`, restart, and verify total colors.

Automated checks (all database writes go to a temporary database):

```powershell
& .\instance\mdt-venv\Scripts\python.exe -m unittest discover -v
& .\instance\mdt-venv\Scripts\python.exe -m py_compile app.py mdt.py mdt_duty.py
```

This is a Flask/Python application; no package.json, TypeScript build/typecheck,
or configured lint command exists. Existing dependencies remain unchanged.

Validation completed: all 41 tests pass under unittest discovery; Python compilation
and `git diff --check` pass. The embedded profile and Older/Newer navigation were
checked in the running browser. Inactive history, identity reuse, invalid correction
timestamps, and integration-account permission revocation have regression coverage.
