import unittest
import test_mdt as baseline

class NotesTests(unittest.TestCase):
    setUp = baseline.MDTTests.setUp
    tearDown = baseline.MDTTests.tearDown
    client = baseline.MDTTests.client
    post = baseline.MDTTests.post

    def test_notes_permissions_validation_and_conflict(self):
        path='/mdt/employees/2/notes'
        self.assertEqual(self.post(self.client(2),path,body='Unauthorized').status_code,403)
        self.assertEqual(self.client().post(path,data={'body':'No token'}).status_code,400)
        self.assertEqual(self.post(self.client(),path,body=' ').status_code,400)
        self.assertEqual(self.post(self.client(),path,body='Service update').status_code,200)
        with baseline.site.db_connection() as db:
            note=db.execute('SELECT * FROM mdt_officer_notes WHERE employee_id=2 ORDER BY id DESC').fetchone()
        self.assertEqual(self.post(self.client(),path,body='Updated',note_id=note['id'],revision=1).status_code,200)
        self.assertEqual(self.post(self.client(),path,body='Stale',note_id=note['id'],revision=1).status_code,409)
        self.assertEqual(self.post(self.client(),'/mdt/employees/3/notes',body='Wrong officer',note_id=note['id'],revision=2).status_code,404)

    def test_profile_fields_and_escaped_notes(self):
        self.post(self.client(),'/mdt/employees/2/notes',body='<script>alert(1)</script>')
        page=self.client(2).get('/employee/profile').get_data(as_text=True)
        self.assertIn('Officer Notes',page)
        self.assertIn('&lt;script&gt;',page)
        for label in ['Rank','CID','Hired','Last Promotion','Phone','Timezone']:
            self.assertIn('<dt>'+label+'</dt>',page)
        self.assertNotIn('<dt>Authority</dt>',page)
        self.assertNotIn('<dt>Discord</dt>',page)
        self.assertNotIn('data-note-form',page.split('<script>')[0])
