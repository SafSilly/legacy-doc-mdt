import unittest
from unittest.mock import patch
import test_mdt as baseline


class ProfileDivisionTests(unittest.TestCase):
    def test_badges_follow_division_rosters(self):
        with patch.object(baseline.site, 'ert_roster_members', return_value=([{'name':'Jane Doe'}], True)), patch.object(baseline.site, 'ftp_roster_members', return_value=([{'name':'Jane Doe'}], True)):
            self.assertEqual([item['name'] for item in baseline.site.profile_division_badges('Jane Doe')], ['ERT','FTP'])
            self.assertEqual(baseline.site.profile_division_badges('Jane Other'), [])

    def test_unavailable_or_empty_roster_does_not_claim_membership(self):
        with patch.object(baseline.site, 'ert_roster_members', return_value=([{'name':'Jane Doe'}], False)), patch.object(baseline.site, 'ftp_roster_members', return_value=([], True)):
            self.assertEqual(baseline.site.profile_division_badges('Jane Doe'), [])
