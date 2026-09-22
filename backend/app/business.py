"""Deterministic fixture access, identity checks, and grounded business facts.

The model cannot grant access through these functions. ``state`` must be the
server-owned session state, never a dictionary accepted from the client/model.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
import json
from pathlib import Path

from .constants import (
    AUTHORIZED_PHASES, DOCUMENT_ALIASES, DOCUMENT_TOPICS, FIXTURE_FILES,
    FOLLOWUP_TOPICS, PAYMENT_LABELS, PII_FIELDS,
)
from .schemas import canonical_name


class FixtureRepository:
    """Read the six starter fixtures from a project root or fixtures directory."""

    def __init__(self, root: Path):
        self.root = root / "fixtures" if (root / "fixtures").is_dir() else root
        for attribute, filename in FIXTURE_FILES.items():
            with (self.root / filename).open(encoding="utf-8") as stream:
                setattr(self, attribute, json.load(stream))
        self.guidelines = self.required_document_guideline
        self.people = {person["party_id"]: person for person in self.policyholders}
        self.cases = {claim["case_id"]: claim for claim in self.claims}
        self.records: dict[str, dict[str, set[str]]] = {}

    @staticmethod
    def identity_result(reason: str, matched: list | None = None, party_id: str | None = None) -> dict:
        return {
            "verified": reason == "verified",
            "party_id": party_id if reason == "verified" else None,
            "matched_fields": matched or [],
            "reason": reason,
        }

    def record(self, person: dict) -> dict[str, set[str]]:
        """Prepare one customer's match values once. Names use the match key."""
        record = self.records.get(person["party_id"])
        if record is None:
            record = {}
            for field in ("name", "dob", "phone", "email", "policy_number"):
                if field not in person:
                    continue
                values = [person[field], *person.get(f"{field}_aliases", [])]
                match_key = canonical_name if field == "name" else str
                record[field] = {match_key(value) for value in values}
            if person.get("id_type") == "ssn_last4":
                record["ssn_last4"] = {person["id_last4"]}
            self.records[person["party_id"]] = record
        return record

    def verify_identity(self, fields: dict[str, str | None]) -> dict:
        """Require three distinct PII categories and no supplied-field conflict.

        Values arrive standardized by the model and validated by the schema; this
        method only matches them against the fixtures. Policy number may narrow a
        customer but never contributes to the count. Registered aliases still
        count as one field each.
        """
        if not fields:
            return self.identity_result("insufficient_fields")

        candidates = []
        partial = []
        for person in self.policyholders:
            record = self.record(person)
            matched = []
            conflicts = []
            for field, provided in fields.items():
                if provided in record.get(field, set()):
                    if field in PII_FIELDS:
                        matched.append(field)
                else:
                    conflicts.append(field)
            entry = (person, sorted(matched), conflicts)
            partial.append(entry)
            if not conflicts:
                candidates.append(entry)

        if len(candidates) > 1:
            return self.identity_result("ambiguous_identity")
        if len(candidates) == 1:
            person, matched, _ = candidates[0]
            if len(matched) >= 3:
                return self.identity_result("verified", matched, person["party_id"])
            return self.identity_result("insufficient_fields", matched)
        # Do not reveal which stored field differed or which account matched.
        if any(matched for _, matched, _ in partial):
            return self.identity_result("conflicting_fields")
        return self.identity_result("no_match")

    def authorized_party(self, state: dict) -> str:
        party_id = state["verified_party_id"]
        if state["phase"] not in AUTHORIZED_PHASES or party_id not in self.people:
            raise PermissionError("Verified identity and an authorized workflow phase are required")
        return party_id

    def guarded_claims(self, state: dict, hints: dict) -> list[dict]:
        """Only return the verified customer's cases, filtered by trusted schema.

        The fixtures contain creation dates, not service dates. An unspecified
        or service-date hint must be clarified by the harness; it is not silently
        reinterpreted as a creation-date filter here.
        """
        party_id = self.authorized_party(state)
        matches = [claim for claim in self.claims if claim["party_id"] == party_id]
        for key in ("case_id", "case_type", "status"):
            if hints.get(key) is not None:
                expected = hints[key].strip().casefold()
                matches = [claim for claim in matches if claim[key].casefold() == expected]
        if hints.get("date_kind") == "created":
            month, year = hints.get("month"), hints.get("year")
            matches = [
                claim for claim in matches
                if (month is None or date.fromisoformat(claim["created_at"]).month == month)
                and (year is None or date.fromisoformat(claim["created_at"]).year == year)
            ]
        return matches

    def guarded_claim(self, state: dict, case_id: str) -> dict:
        party_id = self.authorized_party(state)
        claim = self.cases.get(case_id)
        if claim is None or claim["party_id"] != party_id:
            # Same failure for a nonexistent and another customer's case.
            raise PermissionError("Claim is not available for the verified customer")
        return claim

    def get_contact(self, state: dict) -> dict:
        person = self.people[self.authorized_party(state)]
        return {"name": person["name"], "email": person["email"]}

    def get_guidance(self, claim: dict, topic: str, today: date) -> list[dict]:
        """Build source-labelled facts, without inventing business capabilities."""
        case_id = claim["case_id"]
        output = []

        def add(source: str, text: str):
            if not any(item["id"] == source for item in output):
                output.append({"id": source, "text": text})

        def claim_fact(field: str, text: str):
            add(f"claim:{case_id}:{field}", text)

        def guide(section: str, key: str):
            content = self.guidelines[section].get(key)
            if content is not None:
                add(f"guideline:{section}:{key}", content["en"])

        documents = claim.get("documents_needed", [])
        documents_text = ", ".join(documents)
        claim_fact("status", f"Claim {case_id} is a {claim['case_type']} claim with status {claim['status']}.")
        claim_fact("created_at", f"Claim {case_id} was created on {claim['created_at']}; this is not a recorded service date.")

        if topic in {"overview", "denial", "unknown"}:
            if claim.get("denial_reason"):
                claim_fact("denial_reason", f"The recorded denial reason is: {claim['denial_reason']}.")
            else:
                claim_fact("summary", claim["summary"])

        if topic in DOCUMENT_TOPICS:
            if documents:
                claim_fact("documents_needed", f"The claim requests these documents: {documents_text}.")
            elif topic in {"documents", "alternatives", "submission_method", "format"}:
                claim_fact("documents_needed", "This demo claim does not list any requested documents; no additional requirement can be inferred.")

        if topic in {"documents", "format", "submission_method"} and documents:
            add("guideline:default_guidance", self.guidelines["default_guidance"]["en"])
            guide("case_type_guidance", claim["case_type"])
        if topic in {"documents", "format"}:
            for document in documents:
                key = DOCUMENT_ALIASES.get(document.casefold(), document.casefold())
                guide("document_guidance", key)
                if document.casefold() == "pathology report":
                    add("rule:pathology_original_not_required", "The claim requests a pathology report. The guideline discusses originals conditionally; this claim does not establish an original-only requirement.")
        if topic == "alternatives" and documents:
            for document in documents:
                key = DOCUMENT_ALIASES.get(document.casefold(), document.casefold())
                guide("document_alternative_guidance", key if key in self.guidelines["document_alternative_guidance"] else "default")
            guide("claim_followup_settings", "human_review_after_document_alternatives_exhausted")
            add("rule:alternative_acceptance", "Providing a substitute does not guarantee its acceptance or a claim approval.")

        followup_topic = FOLLOWUP_TOPICS.get(topic)
        if followup_topic and documents:
            for entry in self.guidelines["claim_followup_guidance"]:
                if entry["topic"] == followup_topic:
                    rendered = entry["en"].format(
                        case_id=case_id,
                        documents=documents_text,
                        average_processing_time_after_submission=self.guidelines["claim_followup_settings"]["average_processing_time_after_submission"]["en"],
                    )
                    add(f"guideline:claim_followup_guidance:{followup_topic}", rendered)
        if topic == "processing_time":
            add("rule:processing_time_not_guaranteed", "The fixtures provide no case-specific completion date. General processing estimates are not a guaranteed deadline or a promise of approval.")
        if topic == "receipt":
            add("rule:no_receipt_tool", "These fixtures do not record document-upload receipts, so actual receipt cannot be confirmed here.")

        # Never use the generic 'within a week' submission template to override
        # a case deadline. Past deadlines cannot be repaired by the model.
        deadline = claim.get("appeal_deadline")
        if deadline and topic in DOCUMENT_TOPICS | {"processing_time", "receipt"}:
            recorded = date.fromisoformat(deadline)
            claim_fact("appeal_deadline", f"The recorded appeal deadline is {deadline}.")
            if recorded < today:
                add("rule:expired_appeal_deadline", f"That recorded deadline has passed as of {today.isoformat()}. The fixture does not establish whether a late appeal is available. A human claims representative must confirm available options; no extension or reconsideration is guaranteed.")
        elif topic == "deadline":
            claim_fact("appeal_deadline", "No appeal deadline is recorded for this demo claim; this does not mean no deadline applies.")

        if topic == "payment":
            for field, label in PAYMENT_LABELS.items():
                if field not in claim:
                    continue
                amount = Decimal(claim[field])
                claim_fact(field, f"{label}: USD {amount:.2f}.")
                description = self.claim_schema["field_descriptions"][field]["description"]
                add(f"claim_schema:{field}", description)
            add("rule:payment_limits", "The maximum allowed amount is not a promised payout. The adjusted fee schedule amount does not establish what the customer owes.")
        return output
