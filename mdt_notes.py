"""Officer notes: signed-in readers, supervisor authors and author/admin edits."""
from flask import abort, jsonify, request
from mdt import RANKS


def register_notes(app, hub, token, check_token, rank_of):
    connect = hub['db_connection']
    with connect() as db:
        db.execute('''CREATE TABLE IF NOT EXISTS mdt_officer_notes (
            id INTEGER PRIMARY KEY, employee_id INTEGER NOT NULL REFERENCES supervisor_users(id) ON DELETE CASCADE,
            author_id INTEGER REFERENCES supervisor_users(id) ON DELETE SET NULL,
            author_name TEXT NOT NULL, body TEXT NOT NULL, created_at TEXT NOT NULL,
            updated_at TEXT, revision INTEGER NOT NULL DEFAULT 1)''')

    def profile_record(profile):
        viewer = hub['current_user']()
        employee = profile['account']
        if not viewer or not employee:
            return None
        with connect() as db:
            employee = db.execute('SELECT * FROM supervisor_users WHERE id=?', (employee['id'],)).fetchone()
            notes = [dict(row) for row in db.execute('SELECT * FROM mdt_officer_notes WHERE employee_id=? ORDER BY id DESC', (employee['id'],))]
        can_add = viewer['role'] in hub['SUPERVISOR_ROLES']
        for note in notes:
            note['can_edit'] = can_add and (note['author_id'] == viewer['id'] or viewer['role'] in hub['ADMIN_ROLES'])
        return dict(id=employee['id'], cid=employee['username'], phone=employee['phone_number'] or 'Not listed',
                    rank=RANKS.get(rank_of(employee), ('Not listed',))[0], notes=notes, can_add=can_add, csrf=token())

    @app.post('/mdt/employees/<int:employee_id>/notes')
    def mdt_officer_note(employee_id):
        viewer = hub['current_user']()
        if not viewer or viewer['role'] not in hub['SUPERVISOR_ROLES']:
            abort(403)
        check_token()
        body = request.form.get('body', '').strip()
        if not body or len(body) > 2000:
            return jsonify(error='Enter a note between 1 and 2,000 characters.'), 400
        with connect() as db:
            if not db.execute('SELECT id FROM supervisor_users WHERE id=?', (employee_id,)).fetchone():
                abort(404)
            note_id = request.form.get('note_id', type=int)
            if note_id:
                note = db.execute('SELECT * FROM mdt_officer_notes WHERE id=? AND employee_id=?', (note_id, employee_id)).fetchone()
                if not note:
                    abort(404)
                if note['author_id'] != viewer['id'] and viewer['role'] not in hub['ADMIN_ROLES']:
                    abort(403)
                result = db.execute('UPDATE mdt_officer_notes SET body=?, updated_at=?, revision=revision+1 WHERE id=? AND revision=?',
                                    (body, hub['now_text'](), note_id, request.form.get('revision', type=int)))
                if result.rowcount != 1:
                    return jsonify(error='This note changed. Refresh before editing again.'), 409
            else:
                db.execute('INSERT INTO mdt_officer_notes (employee_id,author_id,author_name,body,created_at) VALUES (?,?,?,?,?)',
                           (employee_id, viewer['id'], viewer['display_name'] or viewer['username'], body, hub['now_text']()))
        return jsonify(ok=True)

    app.context_processor(lambda: {'profile_record': profile_record})
