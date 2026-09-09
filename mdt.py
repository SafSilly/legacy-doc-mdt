"""Parallel DOC terminal using the Hub's accounts, records and permissions."""
import re
import secrets
import sqlite3
from urllib.parse import urlsplit

from flask import abort, flash, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash

RANKS = {
    'W': ('Warden', 100, 110),
    'DW': ('Deputy Warden', 101, 101),
    'AW': ('Assistant Warden', 102, 102),
    'C': ('Captain', 110, 119),
    'LT': ('Lieutenant', 120, 198), 'S': ('Sergeant', 200, 298),
    'SO': ('Senior Corrections Officer', 301, 398),
    'CO': ('Corrections Officer', 401, 498), 'CC': ('Corrections Cadet', 501, 598),
}


def rank_prefix(rank):
    return 'W' if rank in ('DW', 'AW') else rank


def rank_numbers(rank):
    return [n for n in range(RANKS[rank][1], RANKS[rank][2] + 1)
            if rank != 'W' or n not in (101, 102)]


def callsign_number(value):
    match = re.fullmatch(r'(?:W|C|LT|S|SO|CO|CC)?[\s-]*(\d{3})', str(value or '').strip().strip('[]').strip(), re.I)
    return int(match[1]) if match else None


def register_mdt(app, hub):
    connect = hub['db_connection']
    current_user = hub['current_user']

    def migrate():
        with connect() as db:
            columns = {row['name'] for row in db.execute('PRAGMA table_info(supervisor_users)')}
            if 'department_rank' not in columns:
                db.execute("ALTER TABLE supervisor_users ADD COLUMN department_rank TEXT NOT NULL DEFAULT ''")
            if 'callsign_pending' not in columns:
                db.execute('ALTER TABLE supervisor_users ADD COLUMN callsign_pending INTEGER NOT NULL DEFAULT 0')
            db.execute('''CREATE TABLE IF NOT EXISTS mdt_personnel_events (
                id INTEGER PRIMARY KEY, actor_id INTEGER NOT NULL, user_id INTEGER NOT NULL,
                action TEXT NOT NULL, old_value TEXT NOT NULL, new_value TEXT NOT NULL, created_at TEXT NOT NULL)''')
            # Shared database enforcement also covers edits made through legacy Admin Accounts.
            for operation in ('INSERT', 'UPDATE OF callsign'):
                name = 'mdt_unique_' + operation.split()[0].lower()
                db.execute(f'''CREATE TRIGGER IF NOT EXISTS {name} BEFORE {operation} ON supervisor_users
                    WHEN callsign_number(NEW.callsign) IS NOT NULL AND EXISTS (
                        SELECT 1 FROM supervisor_users WHERE id != NEW.id
                        AND callsign_number(callsign) = callsign_number(NEW.callsign))
                    BEGIN SELECT RAISE(ABORT, 'Callsign number already assigned'); END''')

    def token():
        if 'mdt_csrf' not in session:
            session['mdt_csrf'] = secrets.token_urlsafe(32)
        return session['mdt_csrf']

    def check_token():
        if not secrets.compare_digest(session.get('mdt_csrf', ''), request.form.get('csrf_token', '')) or not session.get('mdt_csrf'):
            abort(400, 'Session expired. Refresh the page and try again.')

    def account(db, user_id):
        return db.execute('SELECT * FROM supervisor_users WHERE id = ?', (user_id,)).fetchone()

    def rank_of(user):
        if user['department_rank']:
            return user['department_rank']
        prefix = str(user['callsign']).strip().strip('[]').split('-')[0].upper()
        if prefix == 'W' and callsign_number(user['callsign']) in (101, 102):
            return 'DW' if callsign_number(user['callsign']) == 101 else 'AW'
        return user['department_rank'] or (prefix if prefix in RANKS else '')

    def available(db, user, roster):
        rank = user['department_rank']
        if rank not in RANKS:
            return []
        taken = {callsign_number(row['callsign']) for row in db.execute('SELECT callsign FROM supervisor_users WHERE id != ?', (user['id'],))}
        # Reserve every occupied official roster number, including people without logins.
        taken.update(callsign_number(member.get('callsign')) for member in roster)
        return [n for n in rank_numbers(rank) if n not in taken]

    @app.after_request
    def mdt_return(response):
        # Only MDT forms opt into returning to the terminal after shared Hub actions.
        if request.form.get('mdt_return') == '1' and response.status_code in (302, 303):
            location = urlsplit(response.headers.get('Location', ''))
            if not location.netloc and location.path == '/hub':
                response.headers['Location'] = '/mdt' + ('?' + location.query if location.query else '') + ('#' + location.fragment if location.fragment else '')
        return response

    @app.route('/mdt/login', methods=['GET', 'POST'])
    def mdt_login():
        if request.method == 'POST':
            check_token()
            user = hub['row_one']('SELECT * FROM supervisor_users WHERE lower(username) = lower(?)', (request.form.get('username', '').strip(),))
            if user and check_password_hash(user['password_hash'], request.form.get('password', '')):
                session.clear()
                session.update(supervisor_user_id=user['id'], supervisor_username=user['username'], supervisor_role=user['role'], auth_version=user['auth_version'])
                hub['record_login_event'](user)
                return redirect(url_for('mdt_home'))
            flash('Invalid account ID or password.', 'error')
        elif current_user():
            return redirect(url_for('mdt_home'))
        return render_template('mdt_signin.html', csrf_token=token())

    @app.post('/mdt/logout')
    def mdt_logout():
        check_token()
        session.clear()
        return redirect(url_for('mdt_login'))

    @app.get('/mdt')
    def mdt_home():
        user = current_user()
        if not user:
            return redirect(url_for('mdt_login'))
        announcements, events = hub['dashboard_announcements'](), hub['dashboard_events']()
        with connect() as db:
            personnel = [dict(row) for row in db.execute("SELECT u.id, username, display_name, callsign, role, department_rank, callsign_pending, r.status AS reset_status FROM supervisor_users u LEFT JOIN mdt_reset_requests r ON r.user_id=u.id ORDER BY display_name, username")]
            me = account(db, user['id'])
            pending = bool(me['callsign_pending'])
            roster, roster_ok = hub['official_doc_roster_members']() if pending else ([], True)
            numbers = available(db, me, roster) if pending and roster_ok else []
        return render_template('mdt.html', show_cadet_shortcuts=(user['role'] == 'cadet' or me['department_rank'] == 'CC' or (app.config.get('MDT_PREVIEW_SHORTCUTS', False) and request.remote_addr in ('127.0.0.1', '::1') and user['role'] == 'owner')), current_user=user, announcements=announcements, events=events,
            calendar_view=hub['dashboard_calendar'](events, request.args.get('calendar_month', ''), request.args.get('timezone', '')),
            dashboard_stats=hub['hub_dashboard_stats'](announcements, events),
            notifications=hub['hub_notifications'](user, announcements),
            can_manage_dashboard=hub['user_can_manage_dashboard'](user),
            ert_division_ranks=hub['ERT_DIVISION_RANKS'], ftp_division_ranks=hub['FTP_DIVISION_RANKS'],
            personnel=personnel, ranks=RANKS, rank_of=rank_of, rank_prefix=rank_prefix, me=me, pending=pending,
            available_numbers=numbers, roster_ok=roster_ok, csrf_token=token())

    @app.post('/mdt/personnel/<int:user_id>/promote')
    @hub['admin_required']
    def mdt_promote(user_id):
        check_token()
        actor = current_user()
        rank = request.form.get('rank', '')
        if rank not in RANKS:
            abort(400)
        with connect() as db:
            db.execute('BEGIN IMMEDIATE')
            target = account(db, user_id)
            if not target:
                abort(404)
            if target['role'] == 'owner' and actor['role'] != 'owner':
                abort(403)
            if rank == rank_of(target):
                flash('This person already holds that rank.', 'error')
                return redirect(url_for('mdt_home', _anchor='personnel'))
            db.execute('UPDATE supervisor_users SET department_rank = ?, callsign_pending = 1 WHERE id = ?', (rank, user_id))
            db.execute('INSERT INTO mdt_personnel_events (actor_id,user_id,action,old_value,new_value,created_at) VALUES (?,?,?,?,?,?)',
                (actor['id'], user_id, 'rank', rank_of(target), rank, hub['now_text']()))
        flash('Rank updated. They will choose an available callsign when they refresh the MDT.', 'success')
        return redirect(url_for('mdt_home', _anchor='personnel'))

    @app.post('/mdt/callsign')
    @hub['login_required']
    def mdt_claim_callsign():
        check_token()
        user = current_user()
        roster, roster_ok = hub['official_doc_roster_members'](force_refresh=True)
        if not roster_ok:
            flash('The official roster is unavailable. Please retry once it reconnects.', 'error')
            return redirect(url_for('mdt_home'))
        try:
            number = int(request.form.get('number', ''))
        except ValueError:
            abort(400)
        with connect() as db:
            db.execute('BEGIN IMMEDIATE')
            target = account(db, user['id'])
            if not target['callsign_pending'] or request.form.get('rank') != target['department_rank']:
                flash('Your rank or callsign has changed. Please review the latest assignment.', 'error')
            elif number not in available(db, target, roster):
                flash('That number is no longer available. Choose another callsign.', 'error')
            else:
                callsign = f"{rank_prefix(target['department_rank'])}-{number}"
                db.execute('UPDATE supervisor_users SET callsign = ?, callsign_pending = 0 WHERE id = ?', (callsign, user['id']))
                db.execute('INSERT INTO mdt_personnel_events (actor_id,user_id,action,old_value,new_value,created_at) VALUES (?,?,?,?,?,?)',
                    (user['id'], user['id'], 'callsign', target['callsign'], callsign, hub['now_text']()))
                flash(f'Callsign {callsign} assigned.', 'success')
        return redirect(url_for('mdt_home'))

    migrate()
    from mdt_recovery import register_recovery
    register_recovery(app, hub, token, check_token)
    from mdt_accounts import register_accounts
    register_accounts(app, hub, token, check_token)
    from mdt_duty import register_duty
    register_duty(app, hub, token, check_token, rank_of)

    from mdt_notes import register_notes
    register_notes(app, hub, token, check_token, rank_of)
