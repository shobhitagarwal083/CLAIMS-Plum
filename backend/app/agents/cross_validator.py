"""
Agent 4: Cross-Document Validator.

Validates consistency across all uploaded documents AND cross-references
extracted data against the form submission fields.

Checks performed:
1. Patient Name Consistency — names across documents match each other
2. Member Name Verification — extracted names match the policy roster member
3. Date Consistency — dates across documents are consistent
4. Treatment Date Verification — extracted dates match the form treatment_date
5. Hospital Name Verification — extracted hospital matches the form hospital_name
6. Category Content Verification — extracted content aligns with claim category

Handles TC003: "Prescription for Rajesh Kumar, bill for Arjun Mehta."

Design: Uses fuzzy string matching (not LLM) for name comparison
because this is a deterministic check that should be fast and reliable.
"""

from __future__ import annotations

import logging
from difflib import SequenceMatcher
from typing import Any

from app.models.claim import DocumentType
from app.models.trace import CheckResult, CheckSeverity
from app.pipeline.claim_context import ClaimContext

from .base import BaseAgent

logger = logging.getLogger(__name__)

# Minimum similarity ratio for names to be considered matching
NAME_MATCH_THRESHOLD = 0.75

# ── Category-specific keywords for content verification ──────────────
# These are used to detect obvious mismatches between claimed category
# and document content. Only strong indicators are listed to avoid
# false positives.
CATEGORY_KEYWORDS: dict[str, list[str]] = {
    "CONSULTATION": [
        "fever", "cough", "cold", "viral", "infection", "headache",
        "stomach", "pain", "consultation", "opd", "clinic visit",
        "bp", "blood pressure", "diabetes", "hypertension",
        "doctor fee", "physician", "pediatric", "orthopedic"
    ],
    "DENTAL": [
        "dental", "tooth", "teeth", "root canal", "orthodontic", "gum",
        "cavity", "crown", "bridge", "denture", "oral surgery", "extraction",
        "filling", "periodontal", "endodontic", "braces", "implant",
        "whitening", "scaling", "polish",
    ],
    "DIAGNOSTIC": [
        "mri", "ct scan", "pet scan", "x-ray", "xray", "ultrasound",
        "biopsy", "ecg", "eeg", "pathology",
        "radiology", "sonography", "mammography", "colonoscopy",
        "endoscopy", "angiography",
    ],
    "PHARMACY": [
        "tablet", "capsule", "syrup", "injection", "ointment", "drops",
        "pharmacy", "drug store", "chemist", "medicine refill",
    ],
    "ALTERNATIVE_MEDICINE": [
        "ayurved", "homeopath", "unani", "siddha", "naturopath",
        "panchakarma", "acupuncture", "chiropractic",
    ],
}


class CrossDocumentValidatorAgent(BaseAgent):
    """
    Validates consistency across documents and against form data.

    Input (from context):
        - context.extracted_patient_names (from Agent 3)
        - context.parsed_documents (from Agent 3)
        - context.documents (original inputs with patient_name_on_doc for TC003)
        - context.member_name (from roster, set by executor)
        - context.treatment_date (from form)
        - context.hospital_name (from form)
        - context.claim_category (from form)

    Output (written to context):
        - context.should_halt = True if patient name mismatch (cross-doc)

    Checks produced:
        - patient_name_consistency: Do all documents reference the same patient?
        - member_name_verification: Does extracted name match the roster member?
        - date_consistency: Are treatment dates consistent across documents?
        - treatment_date_verification: Do document dates match the form date?
        - hospital_name_verification: Does document hospital match the form?
        - category_content_verification: Does content align with claimed category?
    """

    @property
    def agent_name(self) -> str:
        return "Cross-Document Validator"

    @property
    def agent_type(self) -> str:
        return "cross_document_validator"

    async def _execute(
        self,
        context: ClaimContext,
    ) -> tuple[list[CheckResult], float, dict[str, Any]]:
        checks: list[CheckResult] = []
        confidence = 1.0

        # ── Collect patient names from all sources ───────────────
        patient_names = list(context.extracted_patient_names)

        # Also check test-mode patient_name_on_doc fields
        for doc in context.documents:
            if doc.patient_name_on_doc:
                patient_names.append(doc.patient_name_on_doc)

        # Deduplicate while preserving order
        seen: set[str] = set()
        unique_names: list[str] = []
        for name in patient_names:
            normalized = name.strip().lower()
            if normalized and normalized not in seen:
                seen.add(normalized)
                unique_names.append(name.strip())

        # ═══════════════════════════════════════════════════════════
        # Check 1: Patient Name Consistency (across documents)
        # ═══════════════════════════════════════════════════════════
        if len(unique_names) <= 1:
            checks.append(CheckResult(
                check_name="patient_name_consistency",
                passed=True,
                reason=(
                    f"All documents reference the same patient: "
                    f"'{unique_names[0]}'." if unique_names else "No patient names found."
                ),
                severity=CheckSeverity.INFO,
                details={"patient_names": unique_names},
            ))
        else:
            # Check if names are similar enough (fuzzy match)
            mismatches = self._find_name_mismatches(unique_names)

            if mismatches:
                # Build specific error message with exact names
                name_list = " and ".join(f"'{n}'" for n in unique_names)
                doc_details = self._build_name_source_details(context, unique_names)

                reason = (
                    f"The uploaded documents appear to belong to different patients. "
                    f"Found different patient names: {name_list}. "
                    f"{doc_details} "
                    f"All documents in a claim must belong to the same patient. "
                    f"Please verify and resubmit with documents for a single patient."
                )

                checks.append(CheckResult(
                    check_name="patient_name_consistency",
                    passed=False,
                    reason=reason,
                    severity=CheckSeverity.BLOCK,
                    details={
                        "patient_names": unique_names,
                        "mismatches": mismatches,
                    },
                ))

                context.halt(reason, is_doc_error=True)
                confidence = 0.0
            else:
                # Names are similar enough (e.g., "Rajesh Kumar" vs "R. Kumar")
                checks.append(CheckResult(
                    check_name="patient_name_consistency",
                    passed=True,
                    reason=(
                        f"Patient names across documents are consistent: "
                        f"{', '.join(f'{repr(n)}' for n in unique_names)}."
                    ),
                    severity=CheckSeverity.INFO,
                    details={"patient_names": unique_names, "fuzzy_match": True},
                ))
                confidence = 0.9  # Slight reduction for fuzzy match
                context.reduce_confidence(0.9, "Patient names across documents have slight fuzzy mismatch")

        # ═══════════════════════════════════════════════════════════
        # Check 2: Member Name Verification (form/roster vs extracted)
        # ═══════════════════════════════════════════════════════════
        member_name_check = self._check_member_name(context, unique_names)
        if member_name_check:
            checks.append(member_name_check)
            if not member_name_check.passed:
                confidence *= 0.6
                context.reduce_confidence(0.6, f"Extracted patient name does not match policy member '{context.member_name}'")
            elif member_name_check.severity == CheckSeverity.WARN:
                confidence *= 0.85
                context.reduce_confidence(0.85, "Unable to extract patient name from documents to verify member identity")

        # ── Collect dates from parsed documents ──────────────────
        dates_found: list[str] = []
        for doc in context.parsed_documents:
            doc_date = doc.extracted_data.get("date")
            if doc_date and isinstance(doc_date, str):
                dates_found.append(doc_date)

        # ═══════════════════════════════════════════════════════════
        # Check 3: Date Consistency (across documents)
        # ═══════════════════════════════════════════════════════════
        if len(set(dates_found)) > 1:
            checks.append(CheckResult(
                check_name="date_consistency",
                passed=False,  # Warn but don't block
                reason=(
                    f"Multiple dates found across documents: {', '.join(dates_found)}. "
                    f"This may indicate different visits."
                ),
                severity=CheckSeverity.WARN,
                details={"dates": dates_found},
            ))
            confidence *= 0.95
            context.reduce_confidence(0.95, "Multiple different dates found across uploaded documents")
        elif dates_found:
            checks.append(CheckResult(
                check_name="date_consistency",
                passed=True,
                reason=f"Consistent date across documents: {dates_found[0]}.",
                severity=CheckSeverity.INFO,
            ))

        # ═══════════════════════════════════════════════════════════
        # Check 4: Treatment Date Verification (form vs extracted)
        # ═══════════════════════════════════════════════════════════
        date_verification = self._check_treatment_date(context, dates_found)
        if date_verification:
            checks.append(date_verification)
            if date_verification.severity == CheckSeverity.WARN:
                confidence *= 0.85
                context.reduce_confidence(0.85, f"Treatment date on form ({context.treatment_date}) does not match document dates")

        # ═══════════════════════════════════════════════════════════
        # Check 5: Hospital Name Verification (form vs extracted)
        # ═══════════════════════════════════════════════════════════
        hospital_check = self._check_hospital_name(context)
        if hospital_check:
            checks.append(hospital_check)
            if hospital_check.severity == CheckSeverity.WARN:
                confidence *= 0.9
                context.reduce_confidence(0.9, f"Hospital name on form ({context.hospital_name}) does not match document hospital")

        # ═══════════════════════════════════════════════════════════
        # Check 6: Category Content Verification
        # ═══════════════════════════════════════════════════════════
        category_check = self._check_category_content(context)
        if category_check:
            checks.append(category_check)
            if category_check.severity == CheckSeverity.WARN and not category_check.passed:
                confidence *= 0.8
                context.reduce_confidence(0.8, f"Document content suggests a different treatment category than '{context.claim_category}'")

        # ═══════════════════════════════════════════════════════════
        # Check 7: Claimed Amount Verification (form vs bill total)
        # ═══════════════════════════════════════════════════════════
        amount_check = self._check_claimed_amount(context)
        if amount_check:
            checks.append(amount_check)
            if not amount_check.passed:
                confidence *= 0.8
                context.reduce_confidence(0.8, f"Claimed amount (₹{context.claimed_amount:,.2f}) does not match total bill amount")

        output_summary = {
            "patient_names_found": unique_names,
            "names_consistent": not context.should_halt,
            "member_name_verified": any(
                c.check_name == "member_name_verification" and c.passed
                for c in checks
            ),
            "dates_found": dates_found,
            "treatment_date_verified": any(
                c.check_name == "treatment_date_verification" and c.passed
                for c in checks
            ),
            "hospital_verified": any(
                c.check_name == "hospital_name_verification" and c.passed
                for c in checks
            ),
            "category_content_verified": any(
                c.check_name == "category_content_verification" and c.passed
                for c in checks
            ),
            "claimed_amount_verified": any(
                c.check_name == "claimed_amount_verification" and c.passed
                for c in checks
            ),
        }

        return checks, round(confidence, 3), output_summary

    # ─────────────────────────────────────────────────────────────
    # NEW: Claimed Amount Verification
    # ─────────────────────────────────────────────────────────────

    def _check_claimed_amount(self, context: ClaimContext) -> CheckResult | None:
        """
        Compare the claimed amount against the total of all uploaded bills.
        """
        from decimal import Decimal
        bill_totals = []
        for doc in context.parsed_documents:
            if doc.document_type in (DocumentType.HOSPITAL_BILL, DocumentType.PHARMACY_BILL):
                val = doc.extracted_data.get("total")
                if val is not None:
                    try:
                        bill_totals.append(Decimal(str(val)))
                    except (ValueError, TypeError):
                        pass

        if not bill_totals:
            return None

        total_bill_amount = sum(bill_totals)
        claimed_amount = Decimal(str(context.claimed_amount))
        
        # Check if they match within 0.01 margin to allow float variations
        if abs(total_bill_amount - claimed_amount) < Decimal('0.01'):
            return CheckResult(
                check_name="claimed_amount_verification",
                passed=True,
                reason=(
                    f"Claimed amount (₹{claimed_amount:,.2f}) matches the total "
                    f"bill amount (₹{total_bill_amount:,.2f}) from the uploaded documents."
                ),
                severity=CheckSeverity.INFO,
                details={
                    "claimed_amount": float(claimed_amount),
                    "total_bill_amount": float(total_bill_amount),
                    "bill_totals": [float(b) for b in bill_totals],
                },
            )
        else:
            return CheckResult(
                check_name="claimed_amount_verification",
                passed=False,
                reason=(
                    f"Discrepancy detected: The claimed amount entered on the form (₹{claimed_amount:,.2f}) "
                    f"does not match the total bill amount (₹{total_bill_amount:,.2f}) found on the uploaded documents."
                ),
                severity=CheckSeverity.WARN,
                details={
                    "claimed_amount": float(claimed_amount),
                    "total_bill_amount": float(total_bill_amount),
                    "bill_totals": [float(b) for b in bill_totals],
                },
            )

    # ─────────────────────────────────────────────────────────────
    # NEW: Member Name Verification
    # ─────────────────────────────────────────────────────────────

    def _check_member_name(
        self,
        context: ClaimContext,
        unique_names: list[str],
    ) -> CheckResult | None:
        """
        Compare OCR-extracted patient names against the member roster name.

        If no name is extracted from documents, warn about unverifiable identity.
        If extracted name clearly differs from member name, flag mismatch.
        """
        if not context.member_name:
            return None  # No member name available — skip

        if not unique_names:
            return CheckResult(
                check_name="member_name_verification",
                passed=False,  # Can't verify — warn but don't block
                reason=(
                    f"No patient name could be extracted from the uploaded documents. "
                    f"Unable to verify identity against policy member "
                    f"'{context.member_name}'. This may indicate poor document quality "
                    f"or missing patient information on the documents."
                ),
                severity=CheckSeverity.WARN,
                details={"member_name": context.member_name, "extracted_names": []},
            )

        # Find best match among extracted names
        similarities = [
            (name, self._name_similarity(name, context.member_name))
            for name in unique_names
        ]
        best_name, best_score = max(similarities, key=lambda x: x[1])

        if best_score >= NAME_MATCH_THRESHOLD:
            return CheckResult(
                check_name="member_name_verification",
                passed=True,
                reason=(
                    f"Extracted patient name '{best_name}' matches policy member "
                    f"'{context.member_name}' (similarity: {best_score:.0%})."
                ),
                severity=CheckSeverity.INFO,
                details={
                    "member_name": context.member_name,
                    "best_match": best_name,
                    "similarity": round(best_score, 3),
                },
            )
        else:
            return CheckResult(
                check_name="member_name_verification",
                passed=False,
                reason=(
                    f"Patient name(s) extracted from documents "
                    f"({', '.join(repr(n) for n in unique_names)}) do not match "
                    f"the policy member '{context.member_name}' "
                    f"(best similarity: {best_score:.0%}). "
                    f"Please verify the documents belong to the claimed member."
                ),
                severity=CheckSeverity.WARN,
                details={
                    "member_name": context.member_name,
                    "extracted_names": unique_names,
                    "best_match": best_name,
                    "similarity": round(best_score, 3),
                },
            )

    # ─────────────────────────────────────────────────────────────
    # NEW: Treatment Date Verification
    # ─────────────────────────────────────────────────────────────

    def _check_treatment_date(
        self,
        context: ClaimContext,
        dates_found: list[str],
    ) -> CheckResult | None:
        """
        Compare dates extracted from documents against the form treatment_date.

        Mismatches may indicate a data entry error or fraudulent date manipulation.
        """
        if not context.treatment_date:
            return None

        if not dates_found:
            return CheckResult(
                check_name="treatment_date_verification",
                passed=True,
                reason=(
                    f"No dates could be extracted from the uploaded documents. "
                    f"Unable to verify against the form treatment date "
                    f"({context.treatment_date})."
                ),
                severity=CheckSeverity.WARN,
                details={
                    "form_date": context.treatment_date,
                    "document_dates": [],
                },
            )

        form_date = context.treatment_date.strip()
        matching_dates = [d for d in dates_found if d.strip() == form_date]

        if matching_dates:
            return CheckResult(
                check_name="treatment_date_verification",
                passed=True,
                reason=(
                    f"Treatment date on form ({form_date}) matches "
                    f"date(s) found in documents."
                ),
                severity=CheckSeverity.INFO,
                details={
                    "form_date": form_date,
                    "document_dates": dates_found,
                    "matched": True,
                },
            )
        else:
            return CheckResult(
                check_name="treatment_date_verification",
                passed=False,  # Warn but don't block — could be multi-visit
                reason=(
                    f"The treatment date entered in the form ({form_date}) does not "
                    f"match the date(s) found in documents ({', '.join(dates_found)}). "
                    f"This may indicate a data entry error or a multi-visit claim. "
                    f"Please verify the treatment date is correct."
                ),
                severity=CheckSeverity.WARN,
                details={
                    "form_date": form_date,
                    "document_dates": dates_found,
                    "matched": False,
                },
            )

    # ─────────────────────────────────────────────────────────────
    # NEW: Hospital Name Verification
    # ─────────────────────────────────────────────────────────────

    def _check_hospital_name(self, context: ClaimContext) -> CheckResult | None:
        """
        Compare hospital name entered in the form against hospital names
        extracted from documents.
        """
        if not context.hospital_name:
            return None  # No hospital specified in form — skip

        # Collect hospital names from all parsed documents
        extracted_hospitals: list[str] = []
        for doc in context.parsed_documents:
            h = doc.extracted_data.get("hospital_name")
            if h and isinstance(h, str) and h.strip():
                extracted_hospitals.append(h.strip())

        if not extracted_hospitals:
            return CheckResult(
                check_name="hospital_name_verification",
                passed=True,
                reason=(
                    f"No hospital name could be extracted from documents. "
                    f"Unable to verify against the form value "
                    f"'{context.hospital_name}'."
                ),
                severity=CheckSeverity.WARN,
                details={
                    "form_hospital": context.hospital_name,
                    "extracted_hospitals": [],
                },
            )

        # Fuzzy match against extracted hospitals
        similarities = [
            (h, self._name_similarity(h, context.hospital_name))
            for h in extracted_hospitals
        ]
        best_hospital, best_score = max(similarities, key=lambda x: x[1])

        if best_score >= NAME_MATCH_THRESHOLD:
            return CheckResult(
                check_name="hospital_name_verification",
                passed=True,
                reason=(
                    f"Hospital name on form ('{context.hospital_name}') matches "
                    f"document ('{best_hospital}', similarity: {best_score:.0%})."
                ),
                severity=CheckSeverity.INFO,
                details={
                    "form_hospital": context.hospital_name,
                    "document_hospital": best_hospital,
                    "similarity": round(best_score, 3),
                },
            )
        else:
            return CheckResult(
                check_name="hospital_name_verification",
                passed=False,  # Warn but don't block
                reason=(
                    f"Hospital name on form ('{context.hospital_name}') does not "
                    f"closely match hospital name(s) in documents "
                    f"({', '.join(repr(h) for h in extracted_hospitals)}). "
                    f"Best match similarity: {best_score:.0%}. "
                    f"Please verify the hospital name."
                ),
                severity=CheckSeverity.WARN,
                details={
                    "form_hospital": context.hospital_name,
                    "extracted_hospitals": extracted_hospitals,
                    "best_match": best_hospital,
                    "similarity": round(best_score, 3),
                },
            )

    # ─────────────────────────────────────────────────────────────
    # NEW: Category Content Verification
    # ─────────────────────────────────────────────────────────────

    def _check_category_content(self, context: ClaimContext) -> CheckResult | None:
        """
        Verify that extracted document content aligns with the claimed
        treatment category.

        Detects obvious mismatches like dental procedures in a consultation
        claim, or consultation terms in a dental claim.
        """
        claim_category = context.claim_category.upper()

        # Collect all text content from extracted data
        all_text_parts: list[str] = []
        if context.extracted_diagnosis:
            all_text_parts.append(context.extracted_diagnosis)
        if context.extracted_treatment:
            all_text_parts.append(context.extracted_treatment)
        for item in context.extracted_line_items:
            desc = item.get("description", "")
            if desc:
                all_text_parts.append(desc)

        if not all_text_parts:
            return None  # No content to verify

        combined_text = " ".join(all_text_parts).lower()

        # Check: Does the content match the selected category?
        matched_categories: dict[str, list[str]] = {}
        for category, keywords in CATEGORY_KEYWORDS.items():
            matched_kw = [kw for kw in keywords if kw in combined_text]
            if matched_kw:
                matched_categories[category] = matched_kw

        # Case 1: Content matches a DIFFERENT category than selected
        mismatched_categories = {
            cat: kws for cat, kws in matched_categories.items()
            if cat != claim_category
        }

        # Case 2: Content matches the selected category
        selected_match = matched_categories.get(claim_category, [])

        # Only flag if we detect strong indicators of a DIFFERENT category
        # and NO indicators of the selected category
        if mismatched_categories and not selected_match:
            mismatch_details = "; ".join(
                f"{cat}: [{', '.join(kws[:3])}]"
                for cat, kws in mismatched_categories.items()
            )
            return CheckResult(
                check_name="category_content_verification",
                passed=False,
                reason=(
                    f"The document content suggests a different treatment category "
                    f"than '{claim_category}'. Detected indicators for: "
                    f"{mismatch_details}. "
                    f"Please verify the correct treatment category was selected. "
                    f"Using the wrong category may result in incorrect sub-limits "
                    f"and co-pay calculations."
                ),
                severity=CheckSeverity.WARN,
                details={
                    "selected_category": claim_category,
                    "detected_categories": matched_categories,
                    "content_summary": combined_text[:200],
                },
            )
        elif selected_match:
            return CheckResult(
                check_name="category_content_verification",
                passed=True,
                reason=(
                    f"Document content aligns with selected category "
                    f"'{claim_category}' (matched: {', '.join(selected_match[:3])})."
                ),
                severity=CheckSeverity.INFO,
                details={
                    "selected_category": claim_category,
                    "matched_keywords": selected_match[:5],
                },
            )
        else:
            # No strong indicators either way — pass without comment
            # (e.g., CONSULTATION is broad and may not have specific keywords)
            return CheckResult(
                check_name="category_content_verification",
                passed=True,
                reason=(
                    f"No specific category indicators found in document content. "
                    f"Category '{claim_category}' accepted as declared."
                ),
                severity=CheckSeverity.INFO,
                details={"selected_category": claim_category},
            )

    # ─────────────────────────────────────────────────────────────
    # Existing helpers
    # ─────────────────────────────────────────────────────────────

    def _find_name_mismatches(self, names: list[str]) -> list[dict[str, Any]]:
        """
        Compare all pairs of names and find mismatches.

        Uses SequenceMatcher for fuzzy string comparison.
        Returns list of mismatched pairs with similarity scores.
        """
        mismatches = []
        for i in range(len(names)):
            for j in range(i + 1, len(names)):
                similarity = self._name_similarity(names[i], names[j])
                if similarity < NAME_MATCH_THRESHOLD:
                    mismatches.append({
                        "name_a": names[i],
                        "name_b": names[j],
                        "similarity": round(similarity, 3),
                        "threshold": NAME_MATCH_THRESHOLD,
                    })
        return mismatches

    @staticmethod
    def _name_similarity(name_a: str, name_b: str) -> float:
        """
        Calculate similarity between two names.

        Handles common Indian name variations:
        - Initials: "R. Kumar" vs "Rajesh Kumar"
        - Missing middle names
        - Case differences
        """
        a = name_a.strip().lower()
        b = name_b.strip().lower()

        if a == b:
            return 1.0

        # Direct SequenceMatcher ratio
        ratio = SequenceMatcher(None, a, b).ratio()

        # Also check if one name contains the other's last name
        a_parts = a.split()
        b_parts = b.split()
        if a_parts and b_parts:
            # Check last name match (most reliable in Indian names)
            if a_parts[-1] == b_parts[-1]:
                ratio = max(ratio, 0.8)

        return ratio

    def _build_name_source_details(
        self,
        context: ClaimContext,
        names: list[str],
    ) -> str:
        """Build details about which document each name came from."""
        parts = []
        for doc in context.documents:
            name_on_doc = doc.patient_name_on_doc
            if name_on_doc and name_on_doc.strip() in names:
                doc_type = doc.actual_type or "document"
                parts.append(
                    f"'{name_on_doc}' was found on {doc.file_name or doc_type}"
                )

        # Also check parsed documents
        for parsed in context.parsed_documents:
            name = parsed.extracted_data.get("patient_name", "")
            if name and name.strip() in names:
                parts.append(
                    f"'{name}' was found on {parsed.document_type.value.lower()}"
                )

        if parts:
            return "Specifically: " + "; ".join(parts) + "."
        return ""
