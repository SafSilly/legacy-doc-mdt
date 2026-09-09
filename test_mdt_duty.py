"""Duty integration tests use test_mdt's disposable database, never live data."""
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timezone
from unittest.mock import patch
from zoneinfo import ZoneInfo

import test_mdt as baseline
site = baseline.site
from mdt_duty import format_duty_time, week_bounds


def ts(value):
    return int(datetime.fromisoformat(value.replace('Z', '+00:00')).timestamp())


class DutyTests(unittest.TestCase):
    client = baseline.MDTTests.client

    def setUp(self):
        self.original_config = {key: site.app.config[key] for key in ('DUTY_API_TOKEN', 'DUTY_API_ACTOR_ID', 'APP_TIMEZONE', 'DUTY_SETTINGS')}
        with site.db_connection() as db:
            db.execute('DELETE FROM duty_sessions')
        baseline.MDTTests.setUp(self)
        with site.db_connection() as db:
            db.execute("UPDATE supervisor_users SET username=CAST(id AS TEXT)")
        site.app.config.update(DUTY_API_TOKEN='x'*40, DUTY_API_ACTOR_ID='1', APP_TIMEZONE='America/Denver',
                               DUTY_SETTINGS={'week_start_day':0, 'required_hours':{'CO':2, 'S':3}})
        self.clock = patch('mdt_duty.utc_now', return_value=ts('2026-09-09T18:00:00Z'))
        self.clock.start()

    def tearDown(self):
        self.clock.stop()
        self.roster_patch.stop()
        site.app.config.update(self.original_config)
        with site.db_connection() as db:
            db.execute('DELETE FROM duty_sessions')

    def event(self, action, key='shift-1', kind='normal', when=None, **extra):
        body = dict(characterId='2', department='DOC', sessionKey=key, dutyType=kind, **extra)
        with patch('mdt_duty.utc_now', return_value=ts(when) if when else ts('2026-09-09T18:00:00Z')):
            return site.app.test_client().post('/mdt/api/duty/'+action, json=body, headers={'Authorization':'Bearer '+'x'*40})

    def totals(self, week=None):
        response = self.client(2).get('/mdt/api/employees/2/duty-hours', query_string={'week':week} if week else {})
        self.assertEqual(response.status_code, 200)
        return response.json

    def correction(self, sid, **fields):
        return self.client().post(f'/mdt/duty/sessions/{sid}/correct', data=dict(csrf_token='test-token', revision='1', reason='Game disconnected', **fields))

    def test_one_hour_and_persisted_seconds(self):
        self.assertEqual(self.event('start', when='2026-09-09T16:00:00Z').status_code, 201)
        self.assertEqual(self.event('end', when='2026-09-09T17:00:00Z').json['durationSeconds'], 3600)
        self.assertEqual(self.totals()['weeks'][0]['totalSeconds'], 3600)
        with site.db_connection() as db:
            self.assertEqual(db.execute('SELECT duration_seconds FROM duty_sessions').fetchone()[0], 3600)

    def test_types_total_and_requirement(self):
        for i, kind in enumerate(('normal','training','extra')):
            self.event('start', key=kind, kind=kind, when=f'2026-09-09T{12+i:02}:00:00Z')
            self.event('end', key=kind, when=f'2026-09-09T{13+i:02}:00:00Z')
        week = self.totals()['weeks'][0]
        self.assertEqual(week['seconds'], dict(normal=3600, training=3600, extra=3600))
        self.assertEqual(week['totalSeconds'], 10800)
        self.assertTrue(week['meetsRequirement'])

    def test_formatting_over_24_hours(self):
        self.assertEqual(format_duty_time(9900), '02:45:00')
        self.assertEqual(format_duty_time(98048), '27:14:08')
        self.assertEqual(format_duty_time(0), '00:00:00')

    def test_cross_week_and_midnight(self):
        # Denver Sunday 23:30 -> Monday 01:30.
        self.event('start', when='2026-09-07T05:30:00Z')
        self.event('end', when='2026-09-07T07:30:00Z')
        weeks = self.totals()['weeks']
        self.assertEqual(weeks[0]['totalSeconds'], 5400)
        self.assertEqual(weeks[1]['totalSeconds'], 1800)

    def test_duplicate_and_delayed_events(self):
        first = self.event('start').json['sessionId']
        self.assertEqual(self.event('start').json['status'], 'duplicate')
        self.assertEqual(self.event('start', key='different').status_code, 409)
        self.event('end')
        self.assertEqual(self.event('end').json['status'], 'duplicate')
        self.event('start', key='new-shift')
        self.event('end')  # Old shift retry must not close new shift.
        self.assertEqual(self.event('start').json['sessionId'], first)
        with site.db_connection() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM duty_sessions WHERE ended_at IS NULL').fetchone()[0], 1)

    def test_concurrent_start(self):
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: self.event('start').status_code, range(2)))
        self.assertEqual(sorted(results), [200, 201])

    def test_off_without_on(self):
        self.assertEqual(self.event('end').json['status'], 'ignored')
        self.assertEqual(self.totals()['weeks'][0]['totalSeconds'], 0)
        with site.db_connection() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM duty_sessions').fetchone()[0], 0)

    def test_active_session_database_survives_new_connections(self):
        self.event('start', when='2026-09-09T17:23:00Z')
        data = self.totals()
        self.assertTrue(data['active'])
        self.assertEqual(data['weeks'][0]['totalSeconds'], 2220)
        with site.db_connection() as db:
            self.assertIsNone(db.execute('SELECT duration_seconds FROM duty_sessions').fetchone()[0])

    def test_historical_navigation_and_zero_weeks(self):
        data = self.totals()
        older = self.totals(data['older'])
        self.assertEqual([week['label'] for week in older['weeks']], ['5 Weeks','6 Weeks','7 Weeks','8 Weeks','9 Weeks'])
        self.assertEqual(self.totals(older['newer'])['selected'], data['selected'])
        self.assertEqual(len(data['weeks']), 5)
        self.assertTrue(all(week['totalSeconds']==0 for week in data['weeks']))
        self.assertEqual(self.totals('2026-09-02')['selected'], '2026-08-31')
        for value in ('2027-01-01', 'invalid', '0001-01-01'):
            self.assertEqual(self.client().get('/mdt/api/employees/2/duty-hours?week='+value).status_code, 400)

    def test_dst_and_configured_week_boundaries(self):
        zone = ZoneInfo('America/Denver')
        spring = week_bounds(date(2026,3,8), zone)
        fall = week_bounds(date(2026,11,1), zone)
        self.assertEqual(spring[3]-spring[2], 167*3600)
        self.assertEqual(fall[3]-fall[2], 169*3600)
        self.assertEqual(week_bounds(date(2026,9,9), zone, 6)[0], date(2026,9,6))
        self.event('start', when='2026-03-08T08:30:00Z')
        self.event('end', when='2026-03-08T09:30:00Z')
        self.assertEqual(self.totals('2026-03-08')['weeks'][0]['totalSeconds'], 3600)

    def test_auth_validation_and_nonexistent_employees(self):
        anonymous = site.app.test_client()
        self.assertEqual(anonymous.get('/mdt/api/employees/2/duty-hours').status_code, 302)
        self.assertEqual(anonymous.post('/mdt/api/duty/start', json={}).status_code, 401)
        self.assertEqual(self.event('start', durationSeconds=999999).status_code, 400)
        self.assertEqual(self.event('start', kind='bad').status_code, 400)
        self.assertEqual(self.client().get('/mdt/api/employees/999/duty-hours').status_code, 404)
        with site.db_connection() as db:
            db.execute('DELETE FROM supervisor_users WHERE id=2')
        self.assertEqual(self.event('start').status_code, 404)

    def test_profile_and_rank_change_keep_history(self):
        self.event('start', when='2026-09-09T17:00:00Z')
        self.event('end')
        with site.db_connection() as db:
            db.execute("UPDATE supervisor_users SET department_rank='S' WHERE id=2")
        data = self.totals()
        self.assertEqual(data['rank'], 'Sergeant')
        self.assertEqual(data['weeklyRequirementHours'], 3)
        self.assertEqual(data['weeks'][0]['totalSeconds'], 3600)
        self.assertFalse(data['weeks'][0]['meetsRequirement'])
        html = self.client(2).get('/employee/profile?embed=1').data
        self.assertIn(b'Duty Hours', html)
        self.assertIn(b'Sergeant', html)
        self.assertIn(b'01:00:00', html)
        self.assertNotIn(b'Supervisor session corrections', html)

    def test_corrections_permissions_overlap_revision_and_audit(self):
        sid = self.event('start', when='2026-09-09T16:00:00Z').json['sessionId']
        denied = self.client(2).post(f'/mdt/duty/sessions/{sid}/correct', data={'csrf_token':'test-token'})
        self.assertEqual(denied.status_code,403)
        self.assertEqual(self.client().post(f'/mdt/duty/sessions/{sid}/correct').status_code,400)
        fields = dict(startedAt='2026-09-09T16:00:00Z', endedAt='2026-09-09T17:00:00Z', dutyType='extra')
        self.assertEqual(self.correction(sid, **fields).status_code,200)
        self.assertEqual(self.correction(sid, **fields).status_code,409)
        self.assertEqual(self.totals()['weeks'][0]['extraSeconds'],3600)
        self.event('start', key='second', when='2026-09-09T17:00:00Z')
        response = self.client().post(f'/mdt/duty/sessions/{sid}/correct', data=dict(csrf_token='test-token', revision=2, reason='Overlap test', **{**fields,'endedAt':'2026-09-09T18:00:00Z'}))
        self.assertEqual(response.status_code,409)
        response = self.client().post(f'/mdt/duty/sessions/{sid}/correct', data=dict(csrf_token='test-token', revision=2, reason='Invalid shift', action='delete'))
        self.assertEqual(response.status_code,200)
        with site.db_connection() as db:
            actions = [row[0] for row in db.execute("SELECT action FROM mdt_personnel_events WHERE action LIKE 'DUTY_%'")]
        self.assertIn('DUTY_SESSION_CORRECTED',actions)
        self.assertIn('DUTY_SESSION_DELETED',actions)
        self.assertEqual(self.totals()['weeks'][0]['extraSeconds'],0)

    def test_unconfigured_minimum_and_no_service_configuration(self):
        site.app.config['DUTY_SETTINGS']['required_hours'] = {}
        self.assertIsNone(self.totals()['weeklyRequirementHours'])
        self.assertIsNone(self.totals()['weeks'][0]['meetsRequirement'])
        site.app.config['DUTY_API_TOKEN'] = ''
        self.assertEqual(self.event('start').status_code,503)

    def test_inactive_roster_profile_retains_history(self):
        self.event('start', when='2026-09-09T17:00:00Z')
        self.event('end')
        member = {'callsign':'CO-401', 'name':'Officer 2', 'status':'Inactive', 'rank':'Corrections Officer'}
        with patch.object(site, 'official_doc_roster_members', return_value=([member], True)):
            response = self.client(2).get('/employee/profile/CO-401?embed=1')
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'Inactive', response.data)
        self.assertIn(b'01:00:00', response.data)

    def test_correction_rejects_future_naive_and_negative_times(self):
        sid = self.event('start', when='2026-09-09T16:00:00Z').json['sessionId']
        for start, end in [('2026-09-09T16:00:00','2026-09-09T17:00:00Z'),
                           ('2026-09-09T16:00:00Z','2026-09-09T15:00:00Z'),
                           ('2026-09-09T16:00:00Z','2026-09-10T16:00:00Z')]:
            self.assertEqual(self.correction(sid, startedAt=start, endedAt=end, dutyType='normal').status_code,400)
        self.assertEqual(self.totals()['weeks'][0]['totalSeconds'],7200)

    def test_machine_credential_cannot_override_actor_permissions(self):
        with site.db_connection() as db:
            db.execute("UPDATE supervisor_users SET role='officer' WHERE id=1")
        self.assertEqual(self.event('start').status_code,403)

    def test_reused_account_id_does_not_inherit_different_cid_history(self):
        self.event('start', when='2026-09-09T17:00:00Z')
        self.event('end')
        with site.db_connection() as db:
            db.execute("UPDATE supervisor_users SET username='999' WHERE id=2")
        self.assertEqual(self.totals()['weeks'][0]['totalSeconds'],0)


if __name__ == '__main__':
    unittest.main()
