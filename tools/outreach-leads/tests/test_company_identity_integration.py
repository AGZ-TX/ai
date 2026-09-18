"""Real CLI/import integration; all HTTP responses are synthetic."""
import json
import unittest
import sys
from pathlib import Path
from copy import deepcopy
from unittest.mock import patch
from urllib.parse import urlencode

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import linkedin_contacts as contacts
import linkedin_public as public
from test_company_identity import HTTP
import test_linkedin_public as fixtures
COMPANY, INDEED_COMPANY = fixtures.COMPANY, fixtures.INDEED_COMPANY
company_page, job, job_detail = fixtures.company_page, fixtures.job, fixtures.job_detail


class IdentityIntegrationTests(unittest.TestCase):
    setUp = fixtures.VaultTests.setUp
    import_row = fixtures.VaultTests.import_row

    def test_same_name_wrong_linkedin_domain_never_becomes_hiring(self):
        http = HTTP({COMPANY: company_page().replace("https://example.test", "https://wrong.test")})
        with patch.object(contacts, "PublicHTTP", return_value=http):
            self.assertEqual(contacts.run(["--vault", str(self.vault), "--skip-indeed", "--no-push"]), 0)
        profile = json.loads(self.profile.read_text())
        self.assertIsNone(profile["hiring"]["is_hiring"])
        self.assertEqual(profile["contacts"], [])
        self.assertEqual(profile["company_identity"]["sources"]["linkedin"]["status"], "conflict")
        self.assertFalse(any("/jobs/view/" in u for u, _ in http.calls))

    def test_repairs_wrong_stored_linkedin_company_and_fetches_details(self):
        wrong = public.BASE + "/company/wrong/"
        self.note.write_text(self.note.read_text().replace(COMPANY, wrong))
        listing = public.GUEST_JOBS + "?" + urlencode({"f_C": "101", "location": "Worldwide", "start": 0})
        http = HTTP({wrong: company_page(company=wrong).replace("https://example.test", "https://wrong.test"),
                     "https://example.test": f'<footer><a href="{COMPANY}">LinkedIn</a></footer>',
                     COMPANY: company_page(), listing: job(), public.BASE + "/jobs/view/501/": job_detail()})
        with patch.object(contacts, "PublicHTTP", return_value=http):
            self.assertEqual(contacts.run(["--vault", str(self.vault), "--skip-indeed", "--job-pages", "1", "--no-push"]), 0)
        profile = json.loads(self.profile.read_text())
        self.assertEqual(profile["linkedin"]["company_url"], COMPANY)
        self.assertIn('linkedin_company: "' + COMPANY + '"', self.note.read_text())
        self.assertTrue(profile["hiring"]["is_hiring"])
        self.assertIn("review case records", profile["hiring"]["jobs"][0]["description"])

    def test_same_name_wrong_indeed_domain_never_becomes_hiring(self):
        http = HTTP({INDEED_COMPANY: company_page(company=INDEED_COMPANY).replace("https://example.test", "https://wrong.test")})
        with patch.object(contacts, "IndeedHTTP", return_value=http):
            self.assertEqual(contacts.run(["--vault", str(self.vault), "--indeed-only", "--no-push"]), 0)
        profile = json.loads(self.profile.read_text())
        self.assertIsNone(profile["hiring"]["is_hiring"])
        self.assertEqual(profile["hiring"]["jobs"], [])
        self.assertFalse(any("/viewjob" in u for u, _ in http.calls))

    def test_pre_identity_public_import_is_quarantined_not_promoted(self):
        row = deepcopy(self.result)
        row.pop("identity")
        self.assertEqual(self.import_row(row)["errors"], 0)
        profile = json.loads(self.profile.read_text())
        self.assertEqual(profile["contacts"], [])
        self.assertEqual(profile["hiring"]["jobs"], [])
        self.assertIn("hiring", profile["company_identity"]["quarantine"]["linkedin"])

    def test_report_for_previous_domain_cannot_be_reused_on_changed_company(self):
        self.note.write_text(self.note.read_text().replace("https://example.test", "https://other.test"))
        self.assertEqual(self.import_row()["errors"], 0)
        profile = json.loads(self.profile.read_text())
        self.assertEqual(profile["hiring"]["status"], "unknown")
        self.assertEqual(profile["contacts"], [])

    def test_identity_evidence_survives_apply_and_repeated_apply(self):
        self.import_row()
        profile = json.loads(self.profile.read_text())
        self.assertEqual(profile["company_identity"]["sources"]["linkedin"]["status"], "verified")
        self.assertEqual(profile["hiring_sources"]["linkedin"]["company_identity"]["target"]["official_host"], "example.test")
        self.assertIn("## Company identity", self.note.read_text())
        self.assertIn("Evidence:", self.note.read_text())
        self.assertEqual(self.import_row()["changed"], 0)

    def test_note_body_cannot_override_identity_frontmatter(self):
        text = self.note.read_text() + '\nname: Wrong Business\nwebsite: https://wrong.test\ncompany_aliases: ["Wrong Business"]\n'
        row = contacts.note_row(self.note, text)
        self.assertEqual(row["firm"], "Example Law")
        self.assertEqual(row["website"], "https://example.test")
        self.assertEqual(row["company_aliases"], [])

    def test_recrawl_aggregate_preserves_verification_and_known_email(self):
        self.import_row()
        profile = json.loads(self.profile.read_text())
        profile["contacts"][0]["email"] = "known@example.test"
        # Match prep's existing recrawl: it preserves aggregate hiring/contacts,
        # but may omit newly added top-level fields before post-POC restoration.
        prior = {k: profile[k] for k in ("contacts", "hiring", "best_poc")}
        self.profile.write_text(json.dumps(prior))
        self.assertEqual(self.import_row()["errors"], 0)
        profile = json.loads(self.profile.read_text())
        self.assertEqual(profile["contacts"][0]["email"], "known@example.test")
        self.assertEqual(profile["company_identity"]["sources"]["linkedin"]["status"], "verified")


if __name__ == "__main__":
    unittest.main()
