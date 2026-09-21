"""Deterministic fixture access, identity checks, and grounded business facts.

The model cannot grant access through these functions. ``state`` must be the
server-owned session state, never a dictionary accepted from the client/model.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import date
from decimal import Decimal, InvalidOperation
import json
from pathlib import Path
import re


PII_FIELDS = ("name", "dob", "phone", "email", "ssn_last4")
IDENTITY_FIELDS = (*PII_FIELDS, "policy_number")
AUTHORIZED_PHASES = frozenset({"RESOLVE_INTENT", "PROCESS_CASE", "POST_PROCESS"})
DOCUMENT_ALIASES = {
    "pathology report": "original pathology report",
    "office note": "treating provider office note",
}


def _normalize(field: str, value: object) -> str | None:
    """Normalize presentation, without fuzzy matching or repairing identities."""
    if not isinstance(value, str):
        return None
    value = value.strip()
    if not value:
        return None
    if field == "dob":
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            return None
        try:
            return date.fromisoformat(value).isoformat()
        except ValueError:
            return None
    if field == "ssn_last4":
        return value if re.fullmatch(r"[0-9]{4}", value) else None
    if field == "phone":
        if not re.fullmatch(r"\+?[0-9() .-]+", value):
            return None
        digits = re.sub(r"[^0-9]", "", value)
        # This US demo permits domestic formatting of an existing +1 number.
        if len(digits) == 10 and not value.startswith("+"):
            digits = "1" + digits
        return digits if 11 <= len(digits) <= 15 else None
    if field == "name":
        return " ".join(value.split()).casefold()
    if field == "email":
        if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", value):
            return None
        return value.casefold()
    return value.casefold()


class FixtureRepository:
    """Read the six starter fixtures from a project root or fixtures directory."""

    def __init__(self, root: Path):
        root = Path(root)
        self.root = root / "fixtures" if (root / "fixtures").is_dir() else root
        files = {
            "policyholders": ("policyholders.json", list),
            "claims": ("claims.json", list),
            "representatives": ("representatives.json", list),
            "consent_scenarios": ("consent_scenarios.json", dict),
            "claim_schema": ("claim_schema.json", dict),
            "required_document_guideline": ("required_document_guideline.json", dict),
        }
        for attribute, (filename, expected_type) in files.items():
            with (self.root / filename).open(encoding="utf-8") as stream:
                content = json.load(stream)
            if not isinstance(content, expected_type):
                raise ValueError(f"{filename} must contain a {expected_type.__name__}")
            setattr(self, attribute, content)
        self.guidelines = self.required_document_guideline
        self._people = self._index(self.policyholders, "party_id")
        self._cases = self._index(self.claims, "case_id")
        if any(claim.get("party_id") not in self._people for claim in self.claims):
            raise ValueError("Each claim must belong to an existing policyholder")

    @staticmethod
    def _index(records: list, key: str) -> dict:
        index = {}
        for record in records:
            if not isinstance(record, dict) or not isinstance(record.get(key), str):
                raise ValueError(f"Each record must contain a string {key}")
            if record[key] in index:
                raise ValueError(f"Duplicate {key} in fixtures")
            index[record[key]] = record
        return index

    @staticmethod
    def _identity_result(reason: str, matched: list | None = None, party_id: str | None = None) -> dict:
        return {
            "verified": reason == "verified",
            "party_id": party_id if reason == "verified" else None,
            "matched_fields": matched or [],
            "reason": reason,
        }

    def verify_identity(self, fields: dict) -> dict:
        """Require three distinct PII categories and no supplied-field conflict.

        Policy number may narrow a customer but never contributes to the count.
        All supplied identity values, including policy number, must agree with
        the same unique customer. Registered aliases still count as one field.
        """
        if not isinstance(fields, dict) or set(fields) - set(IDENTITY_FIELDS):
            return self._identity_result("invalid_fields")
        supplied = {}
        for key, value in fields.items():
            if value is None or (isinstance(value, str) and not value.strip()):
                continue
            normalized = _normalize(key, value)
            if normalized is None:
                return self._identity_result("invalid_fields")
            supplied[key] = normalized
        if not supplied:
            return self._identity_result("insufficient_fields")

        candidates = []
        partial = []
        for person in self.policyholders:
            matched = []
            conflicts = []
            for field, provided in supplied.items():
                if field == "ssn_last4":
                    values = [person.get("id_last4")] if person.get("id_type") == "ssn_last4" else []
                else:
                    values = [person.get(field), *person.get(f"{field}_aliases", [])]
                matches = any(_normalize(field, item) == provided for item in values if item is not None)
                if matches and field in PII_FIELDS:
                    matched.append(field)
                if not matches:
                    conflicts.append(field)
            entry = (person, sorted(matched), conflicts)
            partial.append(entry)
            if not conflicts:
                candidates.append(entry)

        if len(candidates) > 1:
            return self._identity_result("ambiguous_identity")
        if len(candidates) == 1:
            person, matched, _ = candidates[0]
            if len(matched) >= 3:
                return self._identity_result("verified", matched, person["party_id"])
            return self._identity_result("insufficient_fields", matched)
        # Do not reveal which stored field differed or which account matched.
        if any(matched for _, matched, _ in partial):
            return self._identity_result("conflicting_fields")
        return self._identity_result("no_match")

    def _authorized_party(self, state: dict) -> str:
        party_id = state.get("verified_party_id")
        if state.get("phase") not in AUTHORIZED_PHASES or not isinstance(party_id, str) or party_id not in self._people:
            raise PermissionError("Verified identity and an authorized workflow phase are required")
        return party_id

    def guarded_claims(self, state: dict, hints: dict) -> list[dict]:
        """Only return the verified customer's cases, filtered by trusted schema.

        The fixtures contain creation dates, not service dates. An unspecified
        or service-date hint must be clarified by the harness; it is not silently
        reinterpreted as a creation-date filter here.
        """
        party_id = self._authorized_party(state)
        matches = [claim for claim in self.claims if claim["party_id"] == party_id]
        for key in ("case_id", "case_type", "status"):
            if hints.get(key) is not None:
                expected = str(hints[key]).strip().casefold()
                matches = [claim for claim in matches if str(claim.get(key, "")).casefold() == expected]
        if hints.get("date_kind") == "created":
            month, year = hints.get("month"), hints.get("year")
            if month is not None and (type(month) is not int or not 1 <= month <= 12):
                raise ValueError("month must be an integer from 1 through 12")
            if year is not None and (type(year) is not int or not 1900 <= year <= 2100):
                raise ValueError("year must be an integer from 1900 through 2100")
            matches = [
                claim for claim in matches
                if (month is None or date.fromisoformat(claim["created_at"]).month == month)
                and (year is None or date.fromisoformat(claim["created_at"]).year == year)
            ]
        return deepcopy(matches)

    def guarded_claim(self, state: dict, case_id: str) -> dict:
        party_id = self._authorized_party(state)
        claim = self._cases.get(case_id)
        if claim is None or claim["party_id"] != party_id:
            # Same failure for a nonexistent and another customer's case.
            raise PermissionError("Claim is not available for the verified customer")
        return deepcopy(claim)

    def get_contact(self, state: dict) -> dict:
        person = self._people[self._authorized_party(state)]
        return {"name": person["name"], "email": person["email"]}

    def get_guidance(self, claim: dict, topic: str, today: date) -> list[dict]:
        """Build source-labelled facts, without inventing business capabilities."""
        case_id = claim["case_id"]
        output = []

        def add(source: str, text: str | None):
            if text and not any(item["id"] == source for item in output):
                output.append({"id": source, "text": text})

        def claim_fact(field: str, text: str):
            add(f"claim:{case_id}:{field}", text)

        def guide(section: str, key: str):
            content = self.guidelines.get(section, {}).get(key, {})
            add(f"guideline:{section}:{key}", content.get("en"))

        documents = claim.get("documents_needed") or []
        documents_text = ", ".join(documents)
        claim_fact("status", f"Claim {case_id} is a {claim['case_type']} claim with status {claim['status']}.")
        claim_fact("created_at", f"Claim {case_id} was created on {claim['created_at']}; this is not a recorded service date.")

        if topic in {"overview", "denial", "unknown"}:
            if claim.get("denial_reason"):
                claim_fact("denial_reason", f"The recorded denial reason is: {claim['denial_reason']}.")
            elif claim.get("summary"):
                claim_fact("summary", claim["summary"])

        document_topics = {"overview", "denial", "documents", "alternatives", "submission_method", "format", "deadline", "unknown"}
        if topic in document_topics:
            if documents:
                claim_fact("documents_needed", f"The claim requests these documents: {documents_text}.")
            elif topic in {"documents", "alternatives", "submission_method", "format"}:
                claim_fact("documents_needed", "This demo claim does not list any requested documents; no additional requirement can be inferred.")

        if topic in {"documents", "format", "submission_method"} and documents:
            add("guideline:default_guidance", self.guidelines.get("default_guidance", {}).get("en"))
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
                guide("document_alternative_guidance", key if key in self.guidelines.get("document_alternative_guidance", {}) else "default")
            guide("claim_followup_settings", "human_review_after_document_alternatives_exhausted")
            add("rule:alternative_acceptance", "Providing a substitute does not guarantee its acceptance or a claim approval.")

        followup_topic = {
            "processing_time": "processing_time_after_submission",
            "receipt": "receipt_confirmation",
            "submission_method": "submission_method",
        }.get(topic)
        if followup_topic and documents:
            for entry in self.guidelines.get("claim_followup_guidance", []):
                if entry.get("topic") == followup_topic:
                    rendered = entry["en"].format(
                        case_id=case_id,
                        documents=documents_text,
                        average_processing_time_after_submission=self.guidelines.get("claim_followup_settings", {}).get("average_processing_time_after_submission", {}).get("en", "not recorded"),
                    )
                    add(f"guideline:claim_followup_guidance:{followup_topic}", rendered)
        if topic == "processing_time":
            add("rule:processing_time_not_guaranteed", "The fixtures provide no case-specific completion date. General processing estimates are not a guaranteed deadline or a promise of approval.")
        if topic == "receipt":
            add("rule:no_receipt_tool", "These fixtures do not record document-upload receipts, so actual receipt cannot be confirmed here.")

        # Never use the generic 'within a week' submission template to override
        # a case deadline. Past deadlines cannot be repaired by the model.
        deadline = claim.get("appeal_deadline")
        if deadline and topic in document_topics | {"processing_time", "receipt"}:
            recorded = date.fromisoformat(deadline)
            claim_fact("appeal_deadline", f"The recorded appeal deadline is {deadline}.")
            if recorded < today:
                add("rule:expired_appeal_deadline", f"That recorded deadline has passed as of {today.isoformat()}. The fixture does not establish whether a late appeal is available. A human claims representative must confirm available options; no extension or reconsideration is guaranteed.")
        elif topic == "deadline":
            claim_fact("appeal_deadline", "No appeal deadline is recorded for this demo claim; this does not mean no deadline applies.")

        if topic == "payment":
            labels = {
                "expected_reimbursement_amount": "Recorded expected insurer payment",
                "allowed_max_amount": "Recorded maximum allowed amount",
                "net_pay": "Recorded finalized insurer payment",
                "net_fee": "Recorded adjusted fee schedule amount",
            }
            for field, label in labels.items():
                if field not in claim:
                    continue
                try:
                    amount = Decimal(claim[field])
                    if not amount.is_finite():
                        raise InvalidOperation
                except (InvalidOperation, TypeError, ValueError):
                    raise ValueError(f"Invalid monetary value for {field}") from None
                claim_fact(field, f"{label}: USD {amount:.2f}.")
                description = self.claim_schema.get("field_descriptions", {}).get(field, {}).get("description")
                add(f"claim_schema:{field}", description)
            add("rule:payment_limits", "The maximum allowed amount is not a promised payout. The adjusted fee schedule amount does not establish what the customer owes.")
        return output
