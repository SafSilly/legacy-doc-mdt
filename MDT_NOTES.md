# DOC Mobile Data Terminal

The alternative interface is at `/mdt`, with its own login at `/mdt/login`.
The original `/hub` and its login remain available. Nothing has been deployed or
committed. The MDT reuses the existing Hub modules and database instead of
creating a second source of truth. Existing account credentials work in both.

## Preview on this PC

The current preview at http://127.0.0.1:5013/mdt uses
`instance/mdt-preview.db`, a separate copy of the local Hub database. Changes made
in this preview do not affect the original database or the live site.

To restart this preview in PowerShell from the project folder:

```powershell
$env:DATABASE_URL='instance/mdt-preview.db'
& .\instance\mdt-venv\Scripts\python.exe -m flask --app app run --host 127.0.0.1 --port 5013
```

## Promotions and callsigns

Owners/admins can select **Promote** in the MDT personnel directory and choose
a department rank. Admins cannot modify owner ranks. Department ranks do not
automatically grant system permissions; panel roles remain managed by the
existing Admin Accounts tools.

On the recipient's next MDT refresh/sign-in, a dialog lists available numbers.
Their old callsign remains assigned until they select a replacement. Selection
checks the latest account state and refreshes the official roster before saving.
A SQLite write transaction serializes competing claims; database triggers also
prevent duplicate numeric callsigns through the legacy account editor.

| Rank | Number range |
| --- | --- |
| W — Warden | 100–110, excluding reserved 101 and 102 |
| DW — Deputy Warden | W-101 |
| AW — Assistant Warden | W-102 |
| C | 110–119 |
| LT | 120–198 |
| S | 200–298 |
| SO | 301–398 |
| CO | 401–498 |
| CC | 501–598 |

110 is shared by the W/C ranges but can only have one account holder. Occupied
official roster numbers are reserved even when that person has no login. If the
roster is unavailable or a range is full, no new callsign is assigned. Rank and
callsign changes are recorded in `mdt_personnel_events`. Historical reports keep
their original callsigns. The Google roster remains an external source; this app
does not write rank/callsign changes back to the spreadsheet.

Existing accounts and historical callsigns are not rewritten during startup.
Two account columns, the audit table, and uniqueness triggers are added
idempotently. Existing duplicate callsigns should be resolved by an administrator;
the migration does not silently choose a winner or delete records.

## Coverage

Every role-filtered Hub navigation item is carried into the MDT, including
applications, exams, employee training, department/FTP/ERT rosters, profiles,
Admin, Developer, Supervisor, FTP, ERT, and Cadet panels. Shared embedded modules
retain their existing forms, data, and server-side permissions. Announcements,
notifications, and the complete calendar are also present.

The linked **MDT Request** Google Doc was reviewed. Its additional incident,
inmate, LOA, duty-hours, discipline, operational logs, risk-level, community
service, holds, and DOJ/Discord systems are future modules, not implemented
features of this Hub transfer. The user's explicit rank ranges take precedence
over the document's earlier rank outline. The login now follows the user's
reference image: a centered compact card, CID/password fields, a visibility
toggle, and recovery button, with DOC branding and the black/gold palette.

## Password recovery

## Simplified accounts

In the MDT, **Admin Panel → Accounts** opens the new searchable account list.
Click a person's name to expand their details and use the Standard/Admin dropdown,
then Save changes. Owner access cannot be removed here, and administrators cannot
edit owners. Discord username and optional character phone number are editable.
The Character ID remains the stable login ID. Rank/callsign changes continue
through the personnel promotion flow. FTP and ERT assignments remain separate.
Removing Admin restores the saved employee/training role (including existing
supervisor access), rather than discarding it.

**Add Account** requires a numeric unique Character ID, character name, Discord
username, department rank, and available callsign; Standard is the default.
The official roster and local accounts are checked again within the creation
workflow. New cadets receive cadet access; other new Standard accounts receive
officer access. New accounts have an unshared random password until the person
redeems their one-use setup code using the login recovery page. Setup codes use
the same 15-minute expiration and verification protections as reset codes.
Expired setup codes can be reissued from the expanded account card.

## Recovery workflow

From the login page, choose **Forgot password / reset code**. Users can request
a code using their CID, then contact command to verify their identity. The MDT
personnel table flags pending requests for owners/admins. **Issue reset code**
generates a code shown once to the administrator, who shares it privately with
the verified account holder. Admins cannot issue codes for owner accounts.

The user enters their CID, code, and a new password (12–128 characters). The
new password works in both the MDT and original Hub. Resetting invalidates
existing sessions for that account. Codes are hashed, valid for 15 minutes,
single-use, and limited to five failed verification attempts. Issuing another
code replaces the old one; other password changes invalidate outstanding codes.
Request and redemption endpoints also limit attempts by source IP. Codes and
passwords are not included in audit events or saved in the session.

There is no configured email delivery or registered account email, so this is
administrator-assisted recovery, not automatic email recovery. A logged-in
owner is needed to issue an owner reset code. The preview still uses only the
copied database.

## Verification

```powershell
& .\instance\mdt-venv\Scripts\python.exe -m unittest test_mdt -v
```

Tests use a disposable database. Coverage includes existing credentials, both
interfaces, permissions, CSRF, promotion/refresh/selection, range validation,
stale rank forms, external roster reservations/outages, simultaneous W/C-110
claims, legacy account conflicts, shared announcements, and complete navigation.
