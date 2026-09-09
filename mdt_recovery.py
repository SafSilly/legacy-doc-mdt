"""Administrator-assisted recovery for DOC accounts without registered email."""
import hashlib
import secrets
import time

from flask import abort, flash, redirect, render_template, request, session, url_for
from werkzeug.security import generate_password_hash


def make_reset_code(db, target):
    code = '-'.join(secrets.token_hex(3).upper() for _ in range(4))
    digest = hashlib.sha256(code.replace('-', '').encode()).hexdigest()
    db.execute('INSERT INTO mdt_reset_codes VALUES (?, ?, ?, 0, ?) ON CONFLICT(user_id) DO UPDATE SET code_hash=excluded.code_hash, expires_at=excluded.expires_at, attempts=0, password_at_issue=excluded.password_at_issue',
               (target['id'], digest, time.time() + 900, target['password_hash']))
    return code


def register_recovery(app, hub, token, check_token):
    connect = hub['db_connection']
    with connect() as db:
        columns = {row['name'] for row in db.execute('PRAGMA table_info(supervisor_users)')}
        if 'auth_version' not in columns:
            db.execute('ALTER TABLE supervisor_users ADD COLUMN auth_version INTEGER NOT NULL DEFAULT 0')
        db.executescript('''
            CREATE TABLE IF NOT EXISTS mdt_reset_requests (
                user_id INTEGER PRIMARY KEY, requested_at REAL NOT NULL, status TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS mdt_reset_codes (
                user_id INTEGER PRIMARY KEY, code_hash TEXT NOT NULL, expires_at REAL NOT NULL,
                attempts INTEGER NOT NULL DEFAULT 0, password_at_issue TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS mdt_recovery_limits (
                bucket TEXT PRIMARY KEY, started_at REAL NOT NULL, attempts INTEGER NOT NULL);
        ''')

    def limited(action, maximum):
        key = hashlib.sha256(f'{action}:{request.remote_addr}'.encode()).hexdigest()
        now = time.time()
        with connect() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute('DELETE FROM mdt_recovery_limits WHERE started_at < ?', (now - 900,))
            db.execute('INSERT INTO mdt_recovery_limits VALUES (?, ?, 0) ON CONFLICT(bucket) DO NOTHING', (key, now))
            db.execute('UPDATE mdt_recovery_limits SET attempts = attempts + 1 WHERE bucket = ?', (key,))
            return db.execute('SELECT attempts FROM mdt_recovery_limits WHERE bucket = ?', (key,)).fetchone()['attempts'] > maximum

    @app.route('/mdt/forgot-password', methods=['GET', 'POST'])
    def mdt_forgot_password():
        if request.method == 'POST':
            check_token()
            if limited('request', 5):
                flash('Too many requests. Please wait 15 minutes and try again.', 'error')
                return render_template('mdt_recovery.html', csrf_token=token()), 429
            user = hub['row_one']('SELECT id FROM supervisor_users WHERE lower(username) = lower(?)', (request.form.get('username', '').strip(),))
            if user:
                with connect() as db:
                    db.execute("INSERT INTO mdt_reset_requests VALUES (?, ?, 'pending') ON CONFLICT(user_id) DO UPDATE SET requested_at=excluded.requested_at, status='pending'", (user['id'], time.time()))
            flash('If that CID matches an account, a request is now available to command. Contact an owner or administrator to verify your identity and receive a reset code.', 'success')
            return redirect(url_for('mdt_forgot_password'))
        return render_template('mdt_recovery.html', csrf_token=token())

    @app.post('/mdt/personnel/<int:user_id>/reset-code')
    @hub['admin_required']
    def mdt_issue_reset_code(user_id):
        check_token()
        actor = hub['current_user']()
        with connect() as db:
            db.execute('BEGIN IMMEDIATE')
            target = db.execute('SELECT * FROM supervisor_users WHERE id=?', (user_id,)).fetchone()
            if not target:
                abort(404)
            if target['role'] == 'owner' and actor['role'] != 'owner':
                abort(403)
            code = make_reset_code(db, target)
            db.execute("UPDATE mdt_reset_requests SET status='issued' WHERE user_id=?", (user_id,))
            db.execute('INSERT INTO mdt_personnel_events (actor_id,user_id,action,old_value,new_value,created_at) VALUES (?,?,?,?,?,?)',
                       (actor['id'], user_id, 'reset_code_issued', '', '', hub['now_text']()))
        # The raw code is shown once, never stored in the database, URL, or session.
        return render_template('mdt_reset_issued.html', target=target, reset_code=code, setup=bool(target['setup_pending']))

    @app.post('/mdt/reset-password')
    def mdt_reset_password():
        check_token()
        if limited('redeem', 10):
            flash('Too many attempts. Please wait 15 minutes and try again.', 'error')
            return render_template('mdt_recovery.html', csrf_token=token()), 429
        password = request.form.get('password', '')
        if len(password) < 12 or len(password) > 128 or password != request.form.get('confirm_password', ''):
            flash('Use 12–128 characters and enter the same new password twice.', 'error')
            return redirect(url_for('mdt_forgot_password', _anchor='use-code'))
        supplied = ''.join(request.form.get('reset_code', '').upper().split()).replace('-', '')
        digest = hashlib.sha256(supplied.encode()).hexdigest()
        success = False
        with connect() as db:
            db.execute('BEGIN IMMEDIATE')
            user = db.execute('SELECT * FROM supervisor_users WHERE lower(username)=lower(?)', (request.form.get('username', '').strip(),)).fetchone()
            stored = db.execute('SELECT * FROM mdt_reset_codes WHERE user_id=?', (user['id'],)).fetchone() if user else None
            if stored:
                db.execute('UPDATE mdt_reset_codes SET attempts=attempts+1 WHERE user_id=?', (user['id'],))
                valid = stored['expires_at'] > time.time() and stored['attempts'] < 5 and secrets.compare_digest(stored['code_hash'], digest) and stored['password_at_issue'] == user['password_hash']
                if valid:
                    db.execute('UPDATE supervisor_users SET password_hash=?, auth_version=auth_version+1, setup_pending=0 WHERE id=?', (generate_password_hash(password), user['id']))
                    db.execute('DELETE FROM mdt_reset_codes WHERE user_id=?', (user['id'],))
                    db.execute("UPDATE mdt_reset_requests SET status='completed' WHERE user_id=?", (user['id'],))
                    db.execute('INSERT INTO mdt_personnel_events (actor_id,user_id,action,old_value,new_value,created_at) VALUES (?,?,?,?,?,?)',
                               (user['id'], user['id'], 'password_reset', '', '', hub['now_text']()))
                    success = True
        if success:
            session.clear()
            flash('Password updated. Sign in with your new password.', 'success')
            return redirect(url_for('mdt_login'))
        flash('That CID and reset code could not be verified. Codes expire after 15 minutes or five failed attempts. Contact command for a new code.', 'error')
        return redirect(url_for('mdt_forgot_password', _anchor='use-code'))
