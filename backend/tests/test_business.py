"""Security and business-boundary tests; no model or network required."""

from datetime import date
from pathlib import Path
import unittest

from app.business import FixtureRepository


ROOT = Path(__file__).resolve().parents[2]


class BusinessTests(unittest.TestCase):
    def setUp(self):
        self.repo = FixtureRepository(ROOT)
        self.identity = {"name": "margaret chen", "dob": "1985-03-15", "ssn_last4": "4472"}
        self.state = {"phase": "PROCESS_CASE", "verified_party_id": "P9"}

    def test_reads_all_six_fixtures_from_root_or_fixture_directory(self):
        self.assertEqual(len(self.repo.policyholders), 4)
        self.assertEqual(len(self.repo.claims), 5)
        self.assertEqual(len(self.repo.representatives), 1)
        self.assertIn("default", self.repo.consent_scenarios)
        self.assertIn("field_descriptions", self.repo.claim_schema)
        self.assertIn("document_guidance", self.repo.guidelines)
        self.assertEqual(FixtureRepository(ROOT / "fixtures").claims, self.repo.claims)

    def test_example_verifies_three_distinct_pii_fields(self):
        result = self.repo.verify_identity({**self.identity, "policy_number": "POL-9921"})
        self.assertTrue(result["verified"])
        self.assertEqual(result["party_id"], "P9")
        self.assertEqual(set(result["matched_fields"]), {"name", "dob", "ssn_last4"})

    def test_policy_is_not_a_third_identity_field(self):
        result = self.repo.verify_identity({"name": "margaret chen", "dob": "1985-03-15", "policy_number": "POL-9921"})
        self.assertFalse(result["verified"])
        self.assertEqual(result["reason"], "insufficient_fields")
        self.assertNotIn("policy_number", result["matched_fields"])

    def test_phone_and_email_can_replace_ssn(self):
        # Inputs arrive already standardized by the model and validated by the schema.
        result = self.repo.verify_identity({"name": "margaret chen", "phone": "+16505212836", "email": "margaret@email.com"})
        self.assertEqual(result["party_id"], "P9")

    def test_registered_aliases_count_as_one_field_each(self):
        result = self.repo.verify_identity({"name": "yaven li", "email": "yawen.li@example.com", "dob": "1989-12-03"})
        self.assertTrue(result["verified"])
        self.assertEqual(result["party_id"], "P13")
        self.assertEqual(len(result["matched_fields"]), 3)

    def test_national_id_is_not_ssn(self):
        result = self.repo.verify_identity({"name": "ma tian", "dob": "1964-09-10", "ssn_last4": "6688"})
        self.assertFalse(result["verified"])
        result = self.repo.verify_identity({"name": "ma tian", "dob": "1964-09-10", "email": "matian@example.com"})
        self.assertTrue(result["verified"])

    def test_a_conflicting_fourth_field_blocks_three_matches(self):
        result = self.repo.verify_identity({**self.identity, "email": "other@example.com"})
        self.assertFalse(result["verified"])
        self.assertEqual(result["reason"], "conflicting_fields")
        self.assertIsNone(result["party_id"])

    def test_conflicting_policy_number_blocks_verification(self):
        result = self.repo.verify_identity({**self.identity, "policy_number": "POL-1044"})
        self.assertFalse(result["verified"])

    def test_an_unresolved_field_is_not_dropped_from_matching(self):
        result = self.repo.verify_identity({**self.identity, "email": None})
        self.assertFalse(result["verified"])
        self.assertIsNone(result["party_id"])

    def test_fields_from_different_people_cannot_be_combined(self):
        result = self.repo.verify_identity({"name": "margaret chen", "dob": "1990-08-21", "ssn_last4": "9180"})
        self.assertFalse(result["verified"])

    def test_unique_customer_is_required(self):
        self.repo.policyholders.append({**self.repo.policyholders[0], "party_id": "DUPLICATE"})
        result = self.repo.verify_identity(self.identity)
        self.assertFalse(result["verified"])
        self.assertEqual(result["reason"], "ambiguous_identity")

    def test_empty_identity_never_verifies(self):
        self.assertFalse(self.repo.verify_identity({})["verified"])

    def test_unverified_access_is_blocked_by_every_read_tool(self):
        for state in [{"phase": "VERIFY_ID", "verified_party_id": None},
                      {"phase": "VERIFY_ID", "verified_party_id": "P9"},
                      {"phase": "PROCESS_CASE", "verified_party_id": None},
                      {"phase": "PROCESS_CASE", "verified_party_id": "P999"}]:
            with self.subTest(state=state):
                with self.assertRaises(PermissionError):
                    self.repo.guarded_claims(state, {})
                with self.assertRaises(PermissionError):
                    self.repo.guarded_claim(state, "CL-2048")
                with self.assertRaises(PermissionError):
                    self.repo.get_contact(state)

    def test_all_authorized_phases_can_access_their_own_claim(self):
        for phase in ["RESOLVE_INTENT", "PROCESS_CASE", "POST_PROCESS"]:
            self.assertEqual(self.repo.guarded_claim({**self.state, "phase": phase}, "CL-2048")["party_id"], "P9")

    def test_cross_customer_claim_access_and_nonexistent_ids_share_error(self):
        errors = []
        for case_id in ["CL-3001", "DOES-NOT-EXIST"]:
            with self.assertRaises(PermissionError) as caught:
                self.repo.guarded_claim(self.state, case_id)
            errors.append(str(caught.exception))
        self.assertEqual(errors[0], errors[1])
        self.assertEqual(self.repo.guarded_claims(self.state, {"case_id": "CL-3001"}), [])

    def test_claim_candidates_preserve_ownership_and_combined_hints(self):
        claims = self.repo.guarded_claims(self.state, {"case_type": "healthcare", "status": "denied", "month": 1, "date_kind": "unspecified"})
        self.assertEqual([item["case_id"] for item in claims], ["CL-2048"])
        self.assertEqual(len(self.repo.guarded_claims(self.state, {})), 4)

    def test_created_date_filters_and_service_date_is_not_assumed(self):
        created = self.repo.guarded_claims(self.state, {"date_kind": "created", "month": 1, "year": 2025})
        self.assertEqual([item["case_id"] for item in created], ["CL-2011"])
        for date_kind in ["unspecified", "service"]:
            self.assertEqual(len(self.repo.guarded_claims(self.state, {"date_kind": date_kind, "month": 1, "year": 2025})), 4)

    def test_no_claims_is_a_valid_verified_customer_result(self):
        self.assertEqual(self.repo.guarded_claims({**self.state, "verified_party_id": "P7"}, {}), [])

    def test_contact_exposes_only_email_and_name(self):
        contact = self.repo.get_contact(self.state)
        self.assertEqual(contact, {"name": "Margaret Chen", "email": "margaret@email.com"})

    def guidance(self, topic, today=date(2026, 9, 21), case_id="CL-2048"):
        return self.repo.get_guidance(self.repo.guarded_claim(self.state, case_id), topic, today)

    def test_expired_deadline_is_explicit_and_generic_week_rule_never_used(self):
        for topic in ["overview", "denial", "documents", "alternatives", "submission_method", "deadline", "processing_time"]:
            with self.subTest(topic=topic):
                facts = self.guidance(topic)
                text = " ".join(item["text"] for item in facts)
                self.assertIn("2026-03-18", text)
                self.assertIn("has passed", text)
                self.assertIn("late appeal", text)
                self.assertNotIn("please submit pathology report, office note within a week", text.lower())

    def test_future_and_same_day_deadline_are_not_marked_expired(self):
        for today in [date(2026, 3, 1), date(2026, 3, 18)]:
            facts = self.guidance("deadline", today)
            self.assertTrue(any("2026-03-18" in item["text"] for item in facts))
            self.assertFalse(any(item["id"] == "rule:expired_appeal_deadline" for item in facts))

    def test_document_aliases_find_guidance_without_requiring_an_original(self):
        facts = self.guidance("documents")
        sources = {item["id"] for item in facts}
        self.assertIn("guideline:document_guidance:original pathology report", sources)
        self.assertIn("guideline:document_guidance:treating provider office note", sources)
        text = " ".join(item["text"] for item in facts)
        self.assertIn("does not establish an original-only requirement", text)

    def test_alternatives_do_not_promise_acceptance(self):
        facts = self.guidance("alternatives")
        text = " ".join(item["text"] for item in facts)
        self.assertIn("replacement copy", text)
        self.assertIn("does not guarantee", text)
        self.assertIn("human claims representative", text)

    def test_no_documents_does_not_invent_requirements_or_reprocessing(self):
        facts = self.guidance("documents", case_id="CL-2102")
        self.assertIn("does not list", " ".join(item["text"] for item in facts))
        facts = self.guidance("processing_time", case_id="CL-2102")
        self.assertNotIn("review usually restarts", " ".join(item["text"] for item in facts))

    def test_receipt_is_not_fabricated(self):
        self.assertIn("actual receipt cannot be confirmed", " ".join(item["text"] for item in self.guidance("receipt")))

    def test_money_is_usd_and_not_a_payout_or_customer_balance_promise(self):
        facts = self.guidance("payment")
        text = " ".join(item["text"] for item in facts)
        self.assertIn("USD 1450.00", text)
        self.assertIn("USD 0.00", text)
        self.assertIn("not a promised payout", text)
        self.assertIn("does not establish what the customer owes", text)
        self.assertTrue(any(item["id"] == "claim_schema:net_fee" for item in facts))

    def test_guidance_ids_are_unique_and_no_template_remains(self):
        for topic in ["overview", "denial", "documents", "alternatives", "submission_method", "processing_time", "deadline", "payment", "receipt", "format", "unknown"]:
            with self.subTest(topic=topic):
                facts = self.guidance(topic)
                self.assertEqual(len(facts), len({item["id"] for item in facts}))
                self.assertFalse(any("{case_id}" in item["text"] or "{documents}" in item["text"] for item in facts))


if __name__ == "__main__":
    unittest.main()
