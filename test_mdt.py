"""MDT integration checks. All writes use a disposable database, never instance/doc_quiz.db."""
import os
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

_temporary = tempfile.TemporaryDirectory()
os.environ['DATABASE_URL'] = os.path.join(_temporary.name, 'test.db')
import app as site
from mdt import callsign_number
from werkzeug.security import generate_password_hash

site.app.config.update(TESTING=True, SECRET_KEY='test-only')
PASSWORD = generate_password_hash('test-password')


class MDTTests(unittest.TestCase):
    def setUp(self):
        for loader in ('ert_roster_members', 'ftp_roster_members'):
            division_mock = patch.object(site, loader, return_value=([], True))
            division_mock.start()
            self.addCleanup(division_mock.stop)
        self.roster_patch = patch.object(site, 'official_doc_roster_members', return_value=([], True))
        self.roster_patch.start()
        with site.db_connection() as db:
            db.execute('DELETE FROM supervisor_users')
            db.execute('DELETE FROM mdt_personnel_events')
            db.execute('DELETE FROM mdt_reset_requests')
            db.execute('DELETE FROM mdt_reset_codes')
            db.execute('DELETE FROM mdt_recovery_limits')
            for uid, role, callsign in [(1, 'owner', 'W-100'), (2, 'officer', 'CO-401'), (3, 'officer', 'CO-402')]:
                db.execute('INSERT INTO supervisor_users (id,username,display_name,callsign,password_hash,role,created_at) VALUES (?,?,?,?,?,?,?)',
                    (uid, f'user{uid}', f'Officer {uid}', callsign, PASSWORD, role, site.now_text()))

    def tearDown(self):
        self.roster_patch.stop()

    def client(self, uid=1):
        client = site.app.test_client()
        with client.session_transaction() as session:
            session['supervisor_user_id'] = uid
            session['mdt_csrf'] = 'test-token'
        return client

    def post(self, client, path, **data):
        return client.post(path, data=dict(csrf_token='test-token', **data))

    def promote(self, uid=2, rank='S'):
        return self.post(self.client(), f'/mdt/personnel/{uid}/promote', rank=rank)

    def user(self, uid):
        return site.row_one('SELECT * FROM supervisor_users WHERE id=?', (uid,))

    def test_existing_login_and_separate_routes(self):
        client = site.app.test_client()
        self.assertEqual(client.get('/mdt').location, '/mdt/login')
        self.assertIn(b'Forgot password / reset code', client.get('/mdt/login').data)
        with client.session_transaction() as session:
            token = session['mdt_csrf']
        response = client.post('/mdt/login', data={'csrf_token': token, 'username': 'user2', 'password': 'test-password'})
        self.assertEqual(response.location, '/mdt')
        self.assertIn(b'<h1>Dashboard</h1>', client.get('/mdt').data)
        self.assertIn(b'<h1>Dashboard</h1>', client.get('/hub').data)

    def test_authentication_and_csrf(self):
        self.assertEqual(self.client().post('/mdt/personnel/2/promote', data={'rank': 'S'}).status_code, 400)
        self.assertEqual(self.post(self.client(2), '/mdt/personnel/3/promote', rank='S').status_code, 403)
        self.assertEqual(self.promote(rank='ADMIN').status_code, 400)
        self.assertEqual(self.client().post('/mdt/logout').status_code, 400)

    def test_promotion_refresh_and_claim(self):
        self.assertEqual(self.promote().status_code, 302)
        target = self.user(2)
        self.assertEqual((target['department_rank'], target['callsign'], target['role']), ('S', 'CO-401', 'officer'))
        self.assertEqual(target['callsign_pending'], 1)
        page = self.client(2).get('/mdt').data
        self.assertIn(b'data-pending-callsign', page)
        self.assertIn(b'value="200"', page)
        self.assertNotIn(b'value="299"', page)
        self.post(self.client(2), '/mdt/callsign', number='200', rank='S')
        self.assertEqual(self.user(2)['callsign'], 'S-200')
        self.assertEqual(self.user(2)['callsign_pending'], 0)
        self.assertNotIn(b'data-pending-callsign', self.client(2).get('/mdt').data)

    def test_taken_numbers_and_official_roster(self):
        self.promote()
        site.official_doc_roster_members.return_value = ([{'callsign': '[S-200]', 'name': 'Roster only'}], True)
        self.assertNotIn(b'name="number" value="200"', self.client(2).get('/mdt').data)
        self.post(self.client(2), '/mdt/callsign', number='200', rank='S')
        self.assertEqual(self.user(2)['callsign'], 'CO-401')

    def test_out_of_range_and_stale_rank(self):
        self.promote()
        self.post(self.client(2), '/mdt/callsign', number='299', rank='S')
        self.assertEqual(self.user(2)['callsign_pending'], 1)
        self.promote(rank='LT')
        self.post(self.client(2), '/mdt/callsign', number='200', rank='S')
        self.assertEqual(self.user(2)['callsign'], 'CO-401')

    def test_roster_outage_keeps_old_callsign(self):
        self.promote()
        site.official_doc_roster_members.return_value = ([], False)
        self.post(self.client(2), '/mdt/callsign', number='200', rank='S')
        self.assertEqual(self.user(2)['callsign'], 'CO-401')
        self.assertIn(b'Retry roster check', self.client(2).get('/mdt').data)

    def test_overlap_and_concurrent_claims(self):
        self.promote(2, 'W')
        self.promote(3, 'C')
        clients = [self.client(2), self.client(3)]
        with ThreadPoolExecutor(max_workers=2) as pool:
            responses = list(pool.map(lambda pair: self.post(pair[0], '/mdt/callsign', number='110', rank=pair[1]), zip(clients, ['W', 'C'])))
        self.assertTrue(all(response.status_code == 302 for response in responses))
        self.assertEqual(sum(callsign_number(self.user(uid)['callsign']) == 110 for uid in (2, 3)), 1)
        self.assertEqual(sum(self.user(uid)['callsign_pending'] for uid in (2, 3)), 1)

    def test_legacy_admin_cannot_duplicate_number(self):
        response = self.client().post('/supervisor/users/2/update', data={'display_name': 'Officer 2', 'callsign': '[C-100]', 'role': 'officer'})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.user(2)['callsign'], 'CO-401')

    def test_shared_announcements_return_to_correct_interface(self):
        client = self.client()
        response = client.post('/hub/announcements/add', data={'title': 'MDT test notice', 'body': 'Shared records', 'mdt_return': '1'})
        self.assertEqual(response.location, '/mdt#announcements')
        self.assertIn(b'MDT test notice', client.get('/hub').data)
        self.assertIn(b'MDT test notice', client.get('/mdt').data)
        response = client.post('/hub/announcements/add', data={'title': 'Hub test', 'body': 'Still here'})
        self.assertEqual(response.location, '/hub#announcements')

    def test_existing_navigation_preserved(self):
        import re
        client = self.client()
        hub_links = re.findall(rb'data-panel-title="([^"]+)"', client.get('/hub').data)
        mdt_links = re.findall(rb'data-panel-title="([^"]+)"', client.get('/mdt').data)
        self.assertEqual([link for link in hub_links if link != b'Cadet - Overview'], [link for link in mdt_links if link not in (b'Cadet - Timestamp', b'Cadet - FTO evaluation')])
        self.assertNotIn(b'data-promote=', self.client(2).get('/mdt').data)

    def test_audit_and_owner_protection(self):
        with site.db_connection() as db:
            db.execute("UPDATE supervisor_users SET role='admin' WHERE id=3")
        self.assertEqual(self.post(self.client(3), '/mdt/personnel/1/promote', rank='C').status_code, 403)
        self.promote()
        self.assertEqual(site.row_one('SELECT COUNT(*) AS n FROM mdt_personnel_events')['n'], 1)

    def issue_code(self, uid=2):
        import re
        response = self.post(self.client(), f'/mdt/personnel/{uid}/reset-code')
        self.assertEqual(response.status_code, 200)
        self.assertIn('no-store', response.headers['Cache-Control'])
        return re.search(rb'id="issued-code"[^>]*value="([^"]+)"', response.data)[1].decode()

    def redeem(self, code, username='user2', client=None):
        return self.post(client or self.client(3), '/mdt/reset-password', username=username, reset_code=code,
                         password='replacement-password', confirm_password='replacement-password')

    def test_recovery_request_and_admin_visibility(self):
        self.post(self.client(2), '/mdt/forgot-password', username='user2')
        self.assertIn(b'requested a password reset', self.client().get('/mdt/accounts').data)
        self.assertNotIn(b'Issue reset code', self.client(2).get('/mdt').data)
        response = self.post(self.client(2), '/mdt/personnel/3/reset-code')
        self.assertEqual(response.status_code, 403)
        with site.db_connection() as db:
            db.execute("UPDATE supervisor_users SET role='admin' WHERE id=3")
        self.assertEqual(self.post(self.client(3), '/mdt/personnel/1/reset-code').status_code, 403)

    def test_password_reset_one_use_and_session_revocation(self):
        from werkzeug.security import check_password_hash
        old_session = self.client(2)
        code = self.issue_code()
        self.assertNotIn(code, str(dict(site.row_one('SELECT * FROM mdt_reset_codes WHERE user_id=2'))))
        self.assertEqual(self.redeem(code).location, '/mdt/login')
        self.assertTrue(check_password_hash(self.user(2)['password_hash'], 'replacement-password'))
        self.assertFalse(check_password_hash(self.user(2)['password_hash'], 'test-password'))
        self.assertEqual(old_session.get('/mdt').location, '/mdt/login')
        self.assertEqual(old_session.get('/hub').location, '/supervisor/login')
        self.assertIn('/mdt/forgot-password', self.redeem(code).location)
        client = self.client(3)
        self.assertEqual(self.post(client, '/mdt/login', username='user2', password='replacement-password').location, '/mdt')
        self.assertEqual(client.get('/mdt').status_code, 200)
        legacy = site.app.test_client()
        self.assertEqual(legacy.post('/supervisor/login', data={'username':'user2','password':'replacement-password'}).location, '/hub')
        self.assertEqual(legacy.get('/hub').status_code, 200)

    def test_reset_expiry_reissue_and_lockout(self):
        first = self.issue_code()
        second = self.issue_code()
        self.assertIn('/mdt/forgot-password', self.redeem(first).location)
        with site.db_connection() as db:
            db.execute('UPDATE mdt_reset_codes SET expires_at=0 WHERE user_id=2')
        self.assertIn('/mdt/forgot-password', self.redeem(second).location)
        third = self.issue_code()
        for _ in range(5):
            self.redeem('wrong-code')
        self.assertIn('/mdt/forgot-password', self.redeem(third).location)
        self.assertEqual(self.user(2)['auth_version'], 0)

    def test_reset_validation_throttling_and_cid_binding(self):
        code = self.issue_code()
        self.assertEqual(self.client(2).post('/mdt/reset-password').status_code, 400)
        self.assertIn('/mdt/forgot-password', self.redeem(code, username='user3').location)
        self.post(self.client(3), '/mdt/reset-password', username='user2', reset_code=code, password='short', confirm_password='short')
        self.assertEqual(self.user(2)['auth_version'], 0)
        for _ in range(5):
            self.post(self.client(3), '/mdt/forgot-password', username='unknown')
        self.assertEqual(self.post(self.client(3), '/mdt/forgot-password', username='unknown').status_code, 429)

    def test_admin_password_change_invalidates_outstanding_code(self):
        code = self.issue_code()
        self.client().post('/supervisor/users/2/update', data={'display_name':'Officer 2','callsign':'CO-401','role':'officer','password':'different-password'})
        self.assertIn('/mdt/forgot-password', self.redeem(code).location)

    def create_account(self, **overrides):
        fields=dict(username='123456',display_name='New Officer',discord_username='new.officer',phone_number='',rank='CC',number='501',access='standard')
        fields.update(overrides)
        return self.post(self.client(), '/mdt/accounts/create', **fields)

    def test_deputy_and_assistant_warden_ranks(self):
        for rank, number in [('DW',101),('AW',102)]:
            response=self.client().get(f'/mdt/accounts/callsigns?rank={rank}')
            self.assertEqual(response.json,dict(numbers=[number],prefix='W'))
            self.promote(2,rank)
            self.post(self.client(2),'/mdt/callsign',number=str(number),rank=rank)
            self.assertEqual(self.user(2)['callsign'],f'W-{number}')
            self.assertEqual(self.user(2)['department_rank'],rank)
        warden=self.client().get('/mdt/accounts/callsigns?rank=W').json['numbers']
        self.assertNotIn(101,warden)
        self.assertNotIn(102,warden)

    def test_account_creation_and_first_password(self):
        import re
        response=self.create_account()
        self.assertEqual(response.status_code,200)
        self.assertIn(b'Account created',response.data)
        user=site.row_one('SELECT * FROM supervisor_users WHERE username=?',('123456',))
        self.assertEqual((user['discord_username'],user['callsign'],user['role'],user['setup_pending']),('new.officer','CC-501','cadet',1))
        code=re.search(rb'id="issued-code"[^>]*value="([^"]+)"',response.data)[1].decode()
        self.assertEqual(self.redeem(code,username='123456').location,'/mdt/login')
        self.assertEqual(self.user(user['id'])['setup_pending'],0)

    def test_account_creation_conflicts_and_validation(self):
        self.assertEqual(self.create_account().status_code,200)
        self.assertEqual(self.create_account(number='502').status_code,302)
        self.assertEqual(self.create_account(username='123457').status_code,302)
        self.assertEqual(self.create_account(username='nonnumeric',number='503').status_code,302)
        self.assertEqual(self.create_account(username='123458',number='503',access='owner').status_code,302)
        self.assertEqual(site.row_one('SELECT COUNT(*) AS n FROM supervisor_users')['n'],4)

    def test_simple_access_preserves_training_and_divisions(self):
        with site.db_connection() as db:
            db.execute("UPDATE supervisor_users SET role='supervisor',division_rank='ftp_supervisor' WHERE id=2")
        data=dict(display_name='Officer Updated',discord_username='officer.two',phone_number='123',access='admin')
        self.post(self.client(),'/mdt/accounts/2/update',**data)
        self.assertEqual(self.user(2)['role'],'admin')
        data['access']='standard'
        self.post(self.client(),'/mdt/accounts/2/update',**data)
        self.assertEqual((self.user(2)['role'],self.user(2)['division_rank']),('supervisor','ftp_supervisor'))
        self.assertEqual(self.user(2)['discord_username'],'officer.two')

    def test_simple_accounts_permissions_and_owner_protection(self):
        self.assertEqual(self.client(2).get('/mdt/accounts').status_code,403)
        self.assertEqual(self.client(2).get('/mdt/accounts/callsigns?rank=CC').status_code,403)
        self.assertEqual(self.client().post('/mdt/accounts/create').status_code,400)
        with site.db_connection() as db:
            db.execute("UPDATE supervisor_users SET role='admin' WHERE id=3")
        self.assertEqual(self.post(self.client(3),'/mdt/accounts/1/update',display_name='Owner',access='standard').status_code,403)
        self.post(self.client(),'/mdt/accounts/1/update',display_name='Owner',access='standard')
        self.assertEqual(self.user(1)['role'],'owner')

    def test_account_callsign_endpoint_and_roster_failure(self):
        site.official_doc_roster_members.return_value=([{'callsign':'CC-501'}],True)
        response=self.client().get('/mdt/accounts/callsigns?rank=CC')
        self.assertEqual(response.status_code,200)
        self.assertNotIn(501,response.json['numbers'])
        self.assertIn(598,response.json['numbers'])
        site.official_doc_roster_members.return_value=([],False)
        self.assertEqual(self.client().get('/mdt/accounts/callsigns?rank=CC').status_code,503)
        self.assertEqual(self.create_account().status_code,302)
        self.assertEqual(site.row_one('SELECT COUNT(*) AS n FROM supervisor_users')['n'],3)

    def test_accounts_page_renders_and_new_admin_has_saved_standard_role(self):
        self.create_account(access='admin')
        response=self.client().get('/mdt/accounts')
        self.assertEqual(response.status_code,200)
        self.assertIn(b'new.officer',response.data)
        user=site.row_one("SELECT * FROM supervisor_users WHERE username='123456'")
        self.assertEqual((user['role'],user['standard_role']),('admin','cadet'))


if __name__ == '__main__':
    unittest.main()
