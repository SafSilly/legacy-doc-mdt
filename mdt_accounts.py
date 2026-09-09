"""Simple account management using the shared DOC account database."""
import secrets

from flask import abort, flash, jsonify, redirect, render_template, request, url_for
from werkzeug.security import generate_password_hash
from mdt import RANKS, callsign_number, rank_prefix, rank_numbers
from mdt_recovery import make_reset_code


def register_accounts(app, hub, token, check_token):
    connect = hub['db_connection']
    with connect() as db:
        columns = {r['name'] for r in db.execute('PRAGMA table_info(supervisor_users)')}
        for name, definition in [('discord_username', "TEXT NOT NULL DEFAULT ''"), ('standard_role', "TEXT NOT NULL DEFAULT 'officer'"), ('setup_pending', 'INTEGER NOT NULL DEFAULT 0')]:
            if name not in columns:
                db.execute(f'ALTER TABLE supervisor_users ADD COLUMN {name} {definition}')
        db.execute("UPDATE supervisor_users SET standard_role=role WHERE role IN ('cadet','officer','supervisor')")

    def numbers(db, rank, roster):
        taken = {callsign_number(r['callsign']) for r in db.execute('SELECT callsign FROM supervisor_users')}
        taken.update(callsign_number(r.get('callsign')) for r in roster)
        return [n for n in rank_numbers(rank) if n not in taken]

    @app.get('/mdt/accounts')
    @hub['admin_required']
    def mdt_accounts():
        return render_template('mdt_accounts.html', current_user=hub['current_user'](),
            accounts=hub['rows_all']('SELECT u.*, r.status AS reset_status FROM supervisor_users u LEFT JOIN mdt_reset_requests r ON r.user_id=u.id ORDER BY display_name, username'),
            csrf_token=token(), ranks=RANKS, division_labels=hub['DIVISION_RANK_LABELS'],
            ert_ranks=hub['ERT_DIVISION_RANKS'], ftp_ranks=hub['FTP_DIVISION_RANKS'],
            division_rank_in=hub['user_division_rank_in'])

    @app.get('/mdt/accounts/callsigns')
    @hub['admin_required']
    def mdt_account_callsigns():
        rank=request.args.get('rank', '')
        if rank not in RANKS:
            abort(400)
        roster, available=hub['official_doc_roster_members']()
        if not available:
            return jsonify(error='The official roster is unavailable. Retry before creating the account.'), 503
        with connect() as db:
            return jsonify(numbers=numbers(db, rank, roster), prefix=rank_prefix(rank))

    @app.post('/mdt/accounts/create')
    @hub['admin_required']
    def mdt_create_account():
        check_token()
        actor=hub['current_user']()
        cid=request.form.get('username','').strip()
        name=request.form.get('display_name','').strip()
        discord=request.form.get('discord_username','').strip()
        phone=request.form.get('phone_number','').strip()
        rank=request.form.get('rank','')
        access=request.form.get('access','standard')
        if not cid.isascii() or not cid.isdigit() or len(cid)>32 or not name or len(name)>120 or not discord or len(discord)>80 or len(phone)>40 or rank not in RANKS or access not in ('standard','admin'):
            flash('Enter a numeric Character ID, character name, Discord username, valid rank and access.', 'error')
            return redirect(url_for('mdt_accounts'))
        roster, available=hub['official_doc_roster_members'](force_refresh=True)
        if not available:
            flash('The official roster is unavailable. No account was created. Please retry.', 'error')
            return redirect(url_for('mdt_accounts'))
        try:
            number=int(request.form.get('number',''))
        except ValueError:
            abort(400)
        with connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if db.execute('SELECT 1 FROM supervisor_users WHERE lower(username)=lower(?)',(cid,)).fetchone():
                flash('That Character ID already has an account.', 'error')
                return redirect(url_for('mdt_accounts'))
            if number not in numbers(db,rank,roster):
                flash('That callsign is no longer available. Choose another number.', 'error')
                return redirect(url_for('mdt_accounts'))
            standard='cadet' if rank=='CC' else 'officer'
            cursor=db.execute('''INSERT INTO supervisor_users (username,display_name,discord_username,phone_number,callsign,department_rank,role,standard_role,password_hash,created_at,setup_pending)
                VALUES (?,?,?,?,?,?,?,?,?,?,1)''', (cid,name,discord,phone,f'{rank_prefix(rank)}-{number}',rank,'admin' if access=='admin' else standard,standard,generate_password_hash(secrets.token_urlsafe(48)),hub['now_text']()))
            target=db.execute('SELECT * FROM supervisor_users WHERE id=?',(cursor.lastrowid,)).fetchone()
            code=make_reset_code(db,target)
            db.execute('INSERT INTO mdt_personnel_events (actor_id,user_id,action,old_value,new_value,created_at) VALUES (?,?,?,?,?,?)',(actor['id'],target['id'],'account_created','',access,hub['now_text']()))
        return render_template('mdt_reset_issued.html', target=target,reset_code=code,setup=True)

    @app.post('/mdt/accounts/<int:user_id>/update')
    @hub['admin_required']
    def mdt_update_account(user_id):
        check_token()
        actor=hub['current_user']()
        with connect() as db:
            db.execute('BEGIN IMMEDIATE')
            target=db.execute('SELECT * FROM supervisor_users WHERE id=?',(user_id,)).fetchone()
            if not target:
                abort(404)
            if target['role']=='owner' and actor['role']!='owner':
                abort(403)
            name=request.form.get('display_name','').strip()
            discord=request.form.get('discord_username','').strip()
            phone=request.form.get('phone_number','').strip()
            access=request.form.get('access','standard')
            assignments=hub['normalized_division_assignments'](request.form.get('ert_rank',hub['user_division_rank_in'](target,hub['ERT_DIVISION_RANKS'])),request.form.get('ftp_rank',hub['user_division_rank_in'](target,hub['FTP_DIVISION_RANKS'])))
            if not name or len(name)>120 or len(discord)>80 or len(phone)>40 or access not in ('standard','admin') or assignments is None:
                abort(400)
            standard=target['role'] if target['role'] in ('cadet','officer','supervisor') else target['standard_role']
            role='owner' if target['role']=='owner' else ('admin' if access=='admin' else standard)
            db.execute('UPDATE supervisor_users SET display_name=?,discord_username=?,phone_number=?,role=?,standard_role=?,division_rank=?,additional_division_rank=? WHERE id=?',(name,discord,phone,role,standard,*assignments,user_id))
            db.execute('INSERT INTO mdt_personnel_events (actor_id,user_id,action,old_value,new_value,created_at) VALUES (?,?,?,?,?,?)',(actor['id'],user_id,'account_updated',target['role'],role,hub['now_text']()))
        flash('Account saved.', 'success')
        return redirect(url_for('mdt_accounts') if role in ('admin','owner') or actor['id']!=user_id else url_for('mdt_home'))
