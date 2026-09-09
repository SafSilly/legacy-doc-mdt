"""Authoritative DOC duty sessions. UTC seconds, organization-local weeks."""
import json
import math
import os
import re
import secrets
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from flask import abort, jsonify, request

DUTY_TYPES = ('normal', 'training', 'extra')


def utc_now():
    return int(time.time())


def format_duty_time(seconds):
    hours, remainder = divmod(max(0, int(seconds)), 3600)
    minutes, seconds = divmod(remainder, 60)
    return f'{hours:02}:{minutes:02}:{seconds:02}'


def week_start(day, start_day=0):
    return day - timedelta(days=(day.weekday() - start_day) % 7)


def week_bounds(day, zone, start_day=0):
    start = week_start(day, start_day)
    end = start + timedelta(days=7)
    # Construct both local midnights separately so DST weeks are 167/169 hours.
    return start, end, int(datetime.combine(start, datetime.min.time(), zone).timestamp()), int(datetime.combine(end, datetime.min.time(), zone).timestamp())


def iso_utc(seconds):
    return datetime.fromtimestamp(seconds, timezone.utc).isoformat().replace('+00:00', 'Z')


def parse_timestamp(value):
    if not isinstance(value, str) or len(value) > 40:
        abort(400, 'Use an ISO timestamp with a timezone offset.')
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if parsed.tzinfo is None or parsed.year < 1970:
            raise ValueError()
        return int(parsed.timestamp())
    except (ValueError, OverflowError, OSError):
        abort(400, 'Use an ISO timestamp with a timezone offset.')


def register_duty(app, hub, token, check_token, rank_of):
    from mdt import RANKS
    settings = json.loads((Path(app.root_path) / 'duty_settings.json').read_text(encoding='utf-8'))
    start_day = settings['week_start_day']
    if type(start_day) is not int or not 0 <= start_day <= 6:
        raise ValueError('duty_settings.json: week_start_day must be 0 (Monday) through 6 (Sunday).')
    for key, hours in settings['required_hours'].items():
        if key not in RANKS or (hours is not None and (type(hours) not in (int, float) or not math.isfinite(hours) or hours < 0)):
            raise ValueError('duty_settings.json: requirements must be a known rank and nonnegative hours or null.')
    app.config['DUTY_SETTINGS'] = settings
    app.config['DUTY_API_TOKEN'] = os.environ.get('DUTY_API_TOKEN', '')
    app.config['DUTY_API_ACTOR_ID'] = os.environ.get('DUTY_API_ACTOR_ID', '')
    ZoneInfo(app.config['APP_TIMEZONE'])
    connect = hub['db_connection']
    with connect() as db:
        db.executescript('''
            CREATE TABLE IF NOT EXISTS duty_sessions (
                id INTEGER PRIMARY KEY,
                employee_id INTEGER NOT NULL REFERENCES supervisor_users(id),
                employee_cid TEXT NOT NULL,
                department TEXT NOT NULL CHECK(department = 'DOC'),
                duty_type TEXT NOT NULL CHECK(duty_type IN ('normal','training','extra')),
                session_key TEXT NOT NULL,
                started_at INTEGER NOT NULL,
                ended_at INTEGER,
                duration_seconds INTEGER,
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL,
                deleted_at INTEGER,
                revision INTEGER NOT NULL DEFAULT 1,
                CHECK ((ended_at IS NULL AND duration_seconds IS NULL) OR
                       (ended_at >= started_at AND duration_seconds = ended_at - started_at)),
                UNIQUE(employee_id, employee_cid, session_key)
            );
            CREATE UNIQUE INDEX IF NOT EXISTS duty_one_active
              ON duty_sessions(employee_id) WHERE ended_at IS NULL AND deleted_at IS NULL;
            CREATE INDEX IF NOT EXISTS duty_employee_time
              ON duty_sessions(employee_id, employee_cid, started_at, ended_at) WHERE deleted_at IS NULL;
        ''')

    def account(db, uid):
        row = db.execute('SELECT * FROM supervisor_users WHERE id=?', (uid,)).fetchone()
        if row is None:
            abort(404, 'Employee does not exist.')
        return row

    def machine_actor():
        configured = app.config['DUTY_API_TOKEN']
        if len(configured) < 32:
            abort(503, 'Duty integration is not configured.')
        provided = request.headers.get('Authorization', '')
        if not secrets.compare_digest(provided.encode('utf-8'), ('Bearer ' + configured).encode('utf-8')):
            abort(401, 'Invalid integration credentials.')
        with connect() as db:
            actor = db.execute('SELECT * FROM supervisor_users WHERE id=?', (app.config['DUTY_API_ACTOR_ID'],)).fetchone()
        if actor is None or actor['role'] not in ('owner', 'admin'):
            abort(403, 'The integration requires an existing administrator account.')
        return actor

    def payload():
        body = request.get_json(silent=True)
        if not isinstance(body, dict) or set(body) - {'characterId', 'department', 'dutyType', 'sessionKey'}:
            abort(400, 'Expected characterId, department, dutyType, and sessionKey; timestamps and durations are not accepted.')
        cid = body.get('characterId')
        if not isinstance(cid, str) or not re.fullmatch(r'[0-9]{1,32}', cid):
            abort(400, 'characterId must be the numeric Character ID as a string.')
        key = body.get('sessionKey')
        if not isinstance(key, str) or not re.fullmatch(r'[A-Za-z0-9_.:-]{1,128}', key):
            abort(400, 'A unique sessionKey is required for each shift.')
        if body.get('department') != 'DOC' or body.get('dutyType', 'normal') not in DUTY_TYPES:
            abort(400, 'Invalid department or duty type.')
        return body

    def audit(db, actor, employee, action, old, new, reason):
        db.execute('INSERT INTO mdt_personnel_events (actor_id,user_id,action,old_value,new_value,created_at) VALUES (?,?,?,?,?,?)',
                   (actor['id'], employee['id'], action, json.dumps(old),
                    json.dumps({'session': new, 'reason': reason, 'characterId': employee['username']}), iso_utc(utc_now())))

    @app.post('/mdt/api/duty/start')
    def duty_start():
        actor = machine_actor()
        body = payload()
        with connect() as db:
            db.execute('BEGIN IMMEDIATE')
            employee = db.execute('SELECT * FROM supervisor_users WHERE username=?', (body['characterId'],)).fetchone()
            if employee is None:
                abort(404, 'No account matches this Character ID.')
            prior = db.execute('SELECT * FROM duty_sessions WHERE employee_id=? AND employee_cid=? AND session_key=?',
                               (employee['id'], employee['username'], body['sessionKey'])).fetchone()
            if prior:
                if prior['duty_type'] != body.get('dutyType', 'normal'):
                    abort(409, 'This sessionKey was already used with a different duty type.')
                return jsonify(status='duplicate', sessionId=prior['id'], ended=prior['ended_at'] is not None or prior['deleted_at'] is not None)
            if db.execute('SELECT 1 FROM duty_sessions WHERE employee_id=? AND ended_at IS NULL AND deleted_at IS NULL', (employee['id'],)).fetchone():
                abort(409, 'Employee already has an active session; close it before starting another.')
            now = utc_now()
            if db.execute('SELECT 1 FROM duty_sessions WHERE employee_id=? AND employee_cid=? AND deleted_at IS NULL AND ended_at>?', (employee['id'], employee['username'], now)).fetchone():
                abort(409, 'Server clock precedes the previous session end.')
            cursor = db.execute('INSERT INTO duty_sessions (employee_id,employee_cid,department,duty_type,session_key,started_at,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?)',
                                (employee['id'], employee['username'], 'DOC', body.get('dutyType', 'normal'), body['sessionKey'], now, now, now))
            record = dict(db.execute('SELECT * FROM duty_sessions WHERE id=?', (cursor.lastrowid,)).fetchone())
            audit(db, actor, employee, 'DUTY_STARTED', None, record, 'Authenticated game duty event')
        return jsonify(status='started', sessionId=record['id'], startedAt=iso_utc(now)), 201

    @app.post('/mdt/api/duty/end')
    def duty_end():
        actor = machine_actor()
        body = payload()
        with connect() as db:
            db.execute('BEGIN IMMEDIATE')
            employee = db.execute('SELECT * FROM supervisor_users WHERE username=?', (body['characterId'],)).fetchone()
            if employee is None:
                abort(404, 'No account matches this Character ID.')
            row = db.execute('SELECT * FROM duty_sessions WHERE employee_id=? AND employee_cid=? AND session_key=?',
                             (employee['id'], employee['username'], body['sessionKey'])).fetchone()
            if row is None or row['deleted_at'] is not None:
                app.logger.info('Duty end ignored: no session for employee %s', employee['id'])
                return jsonify(status='ignored', reason='No matching session')
            if row['ended_at'] is not None:
                return jsonify(status='duplicate', sessionId=row['id'], durationSeconds=row['duration_seconds'])
            now = utc_now()
            if now < row['started_at']:
                abort(409, 'Server clock precedes clock-in time.')
            duration = now - row['started_at']
            db.execute('UPDATE duty_sessions SET ended_at=?, duration_seconds=?, updated_at=?, revision=revision+1 WHERE id=?', (now, duration, now, row['id']))
            updated = dict(db.execute('SELECT * FROM duty_sessions WHERE id=?', (row['id'],)).fetchone())
            audit(db, actor, employee, 'DUTY_ENDED', dict(row), updated, 'Authenticated game duty event')
        return jsonify(status='ended', sessionId=row['id'], endedAt=iso_utc(now), durationSeconds=duration)

    def totals(employee, selected=None):
        now = utc_now()
        zone = ZoneInfo(app.config['APP_TIMEZONE'])
        first_day = app.config['DUTY_SETTINGS']['week_start_day']
        today = datetime.fromtimestamp(now, zone).date()
        current = week_start(today, first_day)
        try:
            day = date.fromisoformat(selected) if selected else today
            if day > today or day.year < 1971:
                raise ValueError()
        except (ValueError, TypeError):
            abort(400, 'Choose a date from 1971 through today.')
        anchor = week_start(day, first_day)
        bounds = [week_bounds(anchor - timedelta(weeks=i), zone, first_day) for i in range(5)]
        with connect() as db:
            rows = db.execute('''SELECT * FROM duty_sessions WHERE employee_id=? AND employee_cid=? AND department='DOC'
                AND deleted_at IS NULL AND started_at < ? AND (ended_at IS NULL OR ended_at > ?)''',
                (employee['id'], employee['username'], bounds[0][3], bounds[-1][2])).fetchall()
        rank = rank_of(employee)
        requirement = app.config['DUTY_SETTINGS']['required_hours'].get(rank)
        weeks = []
        for start, end, lo, hi in bounds:
            values = {kind: 0 for kind in DUTY_TYPES}
            for row in rows:
                stop = row['ended_at'] if row['ended_at'] is not None else now
                values[row['duty_type']] += max(0, min(stop, hi, now) - max(row['started_at'], lo))
            age = (current - start).days // 7
            total = sum(values.values())
            weeks.append(dict(weekStart=start.isoformat(), weekEnd=end.isoformat(), startUtc=iso_utc(lo), endUtc=iso_utc(hi),
                              dateRange=f'{start:%m/%d} – {end - timedelta(days=1):%m/%d}',
                              label='Current' if age == 0 else 'Last' if age == 1 else f'{age} Weeks', current=age == 0,
                              seconds=values, totalSeconds=total, normalSeconds=values['normal'], trainingSeconds=values['training'], extraSeconds=values['extra'],
                              meetsRequirement=None if requirement is None else total >= requirement * 3600))
        return dict(employeeId=employee['id'], characterId=employee['username'], weeklyRequirementHours=requirement,
                    rank=RANKS[rank][0] if rank in RANKS else 'Unassigned rank', dutyTypes=DUTY_TYPES,
                    weeks=weeks, timezone=zone.key, resetDay=first_day, asOf=iso_utc(now), today=today.isoformat(),
                    newer=min(current, anchor + timedelta(weeks=5)).isoformat() if anchor < current else None,
                    older=(anchor - timedelta(weeks=5)).isoformat() if anchor.year > 1971 else None,
                    selected=anchor.isoformat(), active=any(row['ended_at'] is None for row in rows))

    @app.get('/mdt/api/employees/<int:employee_id>/duty-hours')
    @hub['login_required']
    def duty_hours(employee_id):
        # Existing profile visibility: any signed-in account may view employees.
        with connect() as db:
            employee = account(db, employee_id)
        return jsonify(totals(employee, request.args.get('week')))

    @app.post('/mdt/duty/sessions/<int:session_id>/correct')
    @hub['supervisor_required']
    def duty_correct(session_id):
        check_token()
        reason = request.form.get('reason', '').strip()
        if not 5 <= len(reason) <= 1000:
            abort(400, 'Provide a correction reason (5–1000 characters).')
        try:
            revision = int(request.form.get('revision', ''))
        except ValueError:
            abort(400, 'Session revision is required.')
        action = request.form.get('action', 'correct')
        if action not in ('correct', 'delete'):
            abort(400, 'Invalid correction action.')
        now = utc_now()
        with connect() as db:
            db.execute('BEGIN IMMEDIATE')
            old = db.execute('SELECT * FROM duty_sessions WHERE id=? AND deleted_at IS NULL', (session_id,)).fetchone()
            if old is None:
                abort(404)
            employee = account(db, old['employee_id'])
            if employee['username'] != old['employee_cid']:
                abort(409, 'Employee identity has changed; this record cannot be reassigned.')
            if old['revision'] != revision:
                abort(409, 'Session changed. Refresh before correcting it.')
            if action == 'delete':
                db.execute('UPDATE duty_sessions SET deleted_at=?,updated_at=?,revision=revision+1 WHERE id=?', (now, now, session_id))
            else:
                start = parse_timestamp(request.form.get('startedAt'))
                end_value = request.form.get('endedAt', '').strip()
                end = parse_timestamp(end_value) if end_value else None
                kind = request.form.get('dutyType')
                if kind not in DUTY_TYPES or start > now or (end is not None and (end < start or end > now)):
                    abort(400, 'Invalid duty type or time interval; future timestamps are not allowed.')
                overlap = db.execute('''SELECT 1 FROM duty_sessions WHERE employee_id=? AND employee_cid=? AND deleted_at IS NULL
                    AND id != ? AND started_at < ? AND (ended_at IS NULL OR ended_at > ?)''',
                    (employee['id'], employee['username'], session_id, end if end is not None else 253402300799, start)).fetchone()
                if overlap:
                    abort(409, 'Correction would overlap another session.')
                db.execute('UPDATE duty_sessions SET started_at=?,ended_at=?,duration_seconds=?,duty_type=?,updated_at=?,revision=revision+1 WHERE id=?',
                           (start, end, None if end is None else end-start, kind, now, session_id))
            updated = dict(db.execute('SELECT * FROM duty_sessions WHERE id=?', (session_id,)).fetchone())
            audit(db, hub['current_user'](), employee, 'DUTY_SESSION_DELETED' if action == 'delete' else 'DUTY_SESSION_CORRECTED', dict(old), updated, reason)
        return jsonify(ok=True)

    def profile_duty(profile):
        employee = profile['account']
        if employee is None:
            return None
        with connect() as db:
            employee = account(db, employee['id'])
        result = totals(employee, request.args.get('duty_week'))
        viewer = hub['current_user']()
        result['canCorrect'] = bool(viewer and viewer['role'] in hub['SUPERVISOR_ROLES'])
        result['sessions'] = []
        if result['canCorrect']:
            with connect() as db:
                rows = db.execute('''SELECT * FROM duty_sessions WHERE employee_id=? AND employee_cid=? AND deleted_at IS NULL
                    AND started_at < ? AND (ended_at IS NULL OR ended_at > ?) ORDER BY started_at DESC LIMIT 100''',
                    (employee['id'], employee['username'], parse_timestamp(result['weeks'][0]['endUtc']), parse_timestamp(result['weeks'][-1]['startUtc']))).fetchall()
            result['sessions'] = [dict(row) for row in rows]
            result['csrf'] = token()
        return result

    app.jinja_env.filters['duty_time'] = format_duty_time
    app.jinja_env.filters['duty_utc'] = iso_utc
    app.context_processor(lambda: {'profile_duty': profile_duty})
    app.extensions['duty_totals'] = totals
