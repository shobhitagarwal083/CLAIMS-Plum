# Component Contracts — Plum Claims Orchestration Platform

This document defines the interface contracts for all core components of the Plum Claims Orchestration Platform. These contracts are specified with sufficient precision so that any component could be refactored or reimplemented without needing to read its original code.

---

## 📋 Table of Contents
1. [Core Pipeline state: `ClaimContext`](#1-core-pipeline-state-claimcontext)
2. [Base Agent Interface: `BaseAgent`](#2-base-agent-interface-baseagent)
3. [Agent 1: Document Classifier (`DocumentClassifierAgent`)](#agent-1-document-classifier-documentclassifieragent)
4. [Agent 2: Document Validator (`DocumentValidatorAgent`)](#agent-2-document-validator-documentvalidatoragent)
5. [Agent 3: Document Parser (`DocumentParserAgent`)](#agent-3-document-parser-documentparseragent)
6. [Agent 4: Cross-Document Validator (`CrossDocumentValidatorAgent`)](#agent-4-cross-document-validator-crossdocumentvalidatoragent)
7. [Agent 5: Policy Evaluator (`PolicyEvaluatorAgent`)](#agent-5-policy-evaluator-policyevaluatoragent)
8. [Agent 6: Claim Adjudicator (`ClaimAdjudicatorAgent`)](#agent-6-claim-adjudicator-claimadjudicatoragent)
9. [Agent 7: Fraud Detector (`FraudDetectorAgent`)](#agent-7-fraud-detector-frauddetectoragent)
10. [Core Policy Rules Engine (`PolicyRulesEngine`)](#core-policy-rules-engine-policyrulesengine)
11. [Pipeline Orchestrator (`PipelineExecutor`)](#pipeline-orchestrator-pipelineexecutor)
12. [Asynchronous Celery Workers (`tasks/worker.py`)](#asynchronous-celery-workers-tasksworkerpy)
13. [API Layer Contracts (`routes/claims.py` & `routes/review.py`)](#api-layer-contracts-routesclaimspy--routesreviewpy)

---

## 1. Core Pipeline State: `ClaimContext`

The `ClaimContext` is the shared, mutable execution context passed between all agents in the pipeline sequence.

### Interface Specification (Python)
```python
@dataclass
class ClaimContext:
    # Identity
    claim_id: str
    
    # Input Data
    member_id: str
    member_name: Optional[str]
    policy_id: str
    claim_category: str  # Matches ClaimCategory Enum
    claimed_amount: Decimal
    treatment_date: str  # YYYY-MM-DD
    hospital_name: Optional[str]
    documents: list[DocumentInput]
    ytd_claims_amount: Decimal
    claims_history: list[dict[str, Any]]
    simulate_component_failure: bool

    # Progressive Intermediate State
    classified_documents: list[ClassifiedDocument]
    parsed_documents: list[ParsedDocument]
    extracted_diagnosis: Optional[str]
    extracted_treatment: Optional[str]
    extracted_line_items: list[dict[str, Any]]
    extracted_patient_names: list[str]

    # Pipeline Control & Resilience
    overall_confidence: float  # Scale 0.0 to 1.0
    degraded_components: list[str]
    confidence_reductions: list[dict[str, Any]]
    should_halt: bool
    halt_reason: Optional[str]
    is_document_error: bool

    # Execution Observability Traces
    agent_traces: list[AgentTraceEntry]
    started_at: datetime
    finished_at: Optional[datetime]
```

### Mutability Rules
- **Input Data**: Read-only once initialized.
- **Intermediate State**: Populated progressively by specific agents (e.g., Agent 1 populates `classified_documents`, Agent 3 populates `parsed_documents` and aggregates extracted terms).
- **Control Flags**: Set by validator or cross-validator to trigger early exits using `context.halt(reason, is_doc_error=True)`.

---

## 2. Base Agent Interface: `BaseAgent`

All pipeline agents inherit from the `BaseAgent` abstract base class.

### Contract
- **Input**: `ClaimContext` (mutable)
- **Output**: Returns an immutable `AgentTraceEntry` recording execution results, status, latencies, and assertions checks.
- **Exception Boundary**: The public `execute()` method catches all internal exceptions. If an exception occurs, it:
  1. Records `status = AgentStatus.FAILED` in the trace.
  2. Marks the component as degraded on `ClaimContext` via `context.mark_degraded(agent_name, error)`.
  3. Lowers the overall pipeline confidence score by a factor of `0.6`.
  4. Returns the trace entry safely **without** interrupting the pipeline run.

### Interface Specification (Python)
```python
class BaseAgent(ABC):
    @property
    @abstractmethod
    def agent_name(self) -> str: ...

    @property
    @abstractmethod
    def agent_type(self) -> str: ...

    async def execute(self, context: ClaimContext) -> AgentTraceEntry: ...

    @abstractmethod
    async def _execute(
        self, context: ClaimContext
    ) -> tuple[list[CheckResult], float, dict[str, Any]]:
        """
        Subclasses implement their core logic here.
        Returns:
            - checks: List of validation checks run by the agent.
            - confidence: Agent-specific confidence rating (0.0 to 1.0).
            - output_summary: Key-value outputs formatted for display.
        """
        ...
```

---

## Agent 1: Document Classifier (`DocumentClassifierAgent`)

Responsible for analyzing all uploaded files to determine their document types and visual readability.

- **Class Identifier**: `document_classifier`
- **Pipeline Order**: 1

### Input Contract
- Read from `ClaimContext`:
  - `context.documents`: List of uploaded documents (`DocumentInput`), each optionally containing `base64_data` or `file_path`.

### Output Contract
- Mutates `ClaimContext`:
  - Populates `context.classified_documents` with classified models.
- Returns from `_execute()`:
  - `checks`: List of `CheckResult` verifying readability (`passed=False` if quality is `UNREADABLE`).
  - `confidence`: Average of document classification confidences.
  - `output_summary`:
    ```json
    {
      "documents_classified": 2,
      "types_found": ["PRESCRIPTION", "HOSPITAL_BILL"],
      "qualities": ["GOOD", "GOOD"]
    }
    ```

### Classified Types & Readability Enums
- **DocumentType**: `PRESCRIPTION`, `HOSPITAL_BILL`, `LAB_REPORT`, `PHARMACY_BILL`, `DENTAL_REPORT`, `DIAGNOSTIC_REPORT`, `DISCHARGE_SUMMARY`, `UNKNOWN`
- **DocumentQuality**: `GOOD` (readable), `POOR` (faded/blurry but salvageable), `UNREADABLE` (halt indicator)

---

## Agent 2: Document Validator (`DocumentValidatorAgent`)

Validates if all required documents are present for the selected treatment category.

- **Class Identifier**: `document_validator`
- **Pipeline Order**: 2

### Input Contract
- Read from `ClaimContext`:
  - `context.claim_category` (e.g. `DENTAL`, `PHARMACY`)
  - `context.classified_documents` (types and quality)

### Output Contract
- Mutates `ClaimContext`:
  - Sets `context.should_halt = True`, `context.is_document_error = True`, and `context.halt_reason` to a specific error message if requirements fail or any document is `UNREADABLE`.
- Returns from `_execute()`:
  - `checks`:
    - `document_quality`: Verified readability.
    - `required_documents`: Verified presence of required types from `policy_terms.json`.
  - `confidence`: `1.0` if fully valid, reduced by `0.85` factor if any `POOR` quality document exists, `0.0` if blocked/halted.

### Actionable Early Stop Message Format
If wrong or missing files are provided, the validator halts with a message specifying:
> *"Your [Category] claim requires the following documents: [Required Types]. You uploaded: [Uploaded Types]. Missing: [Missing Types]. Please upload the missing document(s) and resubmit."*

---

## Agent 3: Document Parser (`DocumentParserAgent`)

Performs OCR extraction and parses medical documents into structured key-value schemas.

- **Class Identifier**: `document_parser`
- **Pipeline Order**: 3

### Input Contract
- Read from `ClaimContext`:
  - `context.classified_documents`
  - `context.documents` (real bytes or sandbox pre-extracted mock contents)

### Output Contract
- Mutates `ClaimContext`:
  - Populates `context.parsed_documents` with extracted keys.
  - Aggregates terms: `context.extracted_patient_names`, `context.extracted_diagnosis`, `context.extracted_treatment`, and `context.extracted_line_items`.
- Returns from `_execute()`:
  - `checks`: `parse_[file_id]` checks tracking field counts and OCR validity.
  - `confidence`: Mean extraction confidence score.
  - `output_summary`: Aggregated diagnostic terms, line items counts, and patient names.

---

## Agent 4: Cross-Document Validator (`CrossDocumentValidatorAgent`)

Performs deterministic cross-checks between document fields and the user-entered form data.

- **Class Identifier**: `cross_document_validator`
- **Pipeline Order**: 4

### Input Contract
- Read from `ClaimContext`:
  - `context.extracted_patient_names`, `context.member_name` (from roster)
  - `context.parsed_documents` (dates, hospital, item totals)
  - `context.treatment_date`, `context.hospital_name`, `context.claimed_amount`

### Output Contract
- Mutates `ClaimContext`:
  - If a patient name mismatch occurs between documents (e.g. prescription for Person A but bill for Person B), sets `context.should_halt = True`, `context.is_document_error = True`, and `context.halt_reason`.
- Returns from `_execute()`:
  - `checks`:
    - `patient_name_consistency`: Verify names match across documents (fuzzy threshold `>= 0.75`).
    - `member_name_verification`: Verify extracted names match the policy member.
    - `date_consistency`: Verify dates match across bills/prescriptions.
    - `treatment_date_verification`: Verify document dates match the form `treatment_date`.
    - `hospital_name_verification`: Verify document hospital name matches form input.
    - `category_content_verification`: Verify keywords in procedures align with selected claim category.
    - `claimed_amount_verification`: Verify that the sum of line items in bills matches `claimed_amount` on the form.
  - `confidence`: Degraded by factors (`0.9`, `0.85`, `0.80`, `0.60`) for warnings/mismatches.

---

## Agent 5: Policy Evaluator (`PolicyEvaluatorAgent`)

Assesses compliance with policy rules, waiting periods, sub-limits, exclusions, and pre-authorization.

- **Class Identifier**: `policy_evaluator`
- **Pipeline Order**: 5

### Input Contract
- Read from `ClaimContext`:
  - `context.member_id`, `claim_category`, `claimed_amount`, `treatment_date`
  - `context.extracted_diagnosis`, `context.extracted_treatment`, `context.extracted_line_items`
  - `context.ytd_claims_amount`

### Output Contract
- Returns from `_execute()`:
  - `checks`: Individual rule outcomes evaluated through `PolicyRulesEngine`.
    - `member_eligibility` (`block`)
    - `minimum_claim_amount` (`block`)
    - `per_claim_limit` (`block` or `info` if overridden by a higher category sub-limit)
    - `annual_limit` (`block`)
    - `submission_deadline` (`block`)
    - `waiting_period` (`block`)
    - `exclusion_check` (`block` or `warn` for line-item partial exclusions)
    - `pre_authorization` (`block` if required procedure is above pre-auth limit)
    - `relationship_coverage` (`block` if dependent relationship is ineligible)
    - `covered_procedures` (`warn` if dental/vision item is not whitelisted)
    - `dental_report_requirement` (`warn` if missing report for dental category)
    - `pharmacy_generic_mandatory` (`warn` if branded drug or unitemized bill detected)
    - `alternative_medicine_system` (`warn` if system is not in whitelisted systems)
    - `alternative_medicine_practitioner` (`warn` if registration lacks `AYUR/` prefix)
    - `alternative_medicine_sessions` (`warn` if sessions count exceeds 20 sessions)
  - `confidence`: Based on passed check ratios. Boosted to `0.85` if no blockers.

---

## Agent 6: Claim Adjudicator (`ClaimAdjudicatorAgent`)

Calculates approved payment payouts using high-precision Decimal arithmetic and determines initial decisions.

- **Class Identifier**: `claim_adjudicator`
- **Pipeline Order**: 6

### Input Contract
- Read from `ClaimContext`:
  - Traces of previous agents (retrieving exclusions, network status, co-pays, sub-limits).
  - `context.claimed_amount`, `context.claim_category`, `context.hospital_name`.
  - `context.extracted_line_items`.

### Output Contract
- Returns from `_execute()`:
  - `checks`: `adjudication_decision`, `network_discount`, `copay_applied`, `sub_limit_cap`, and `final_decision`.
  - `confidence`: Propagated from context overall confidence.
  - `output_summary`:
    ```python
    {
        "decision": "APPROVED" | "PARTIAL" | "REJECTED",
        "approved_amount": Decimal,  # High precision
        "rejection_reasons": list[str],
        "decision_reasons": list[str],
        "amount_breakdown": dict,    # Matches AmountBreakdown model
        "line_items": list[dict],    # Matches LineItemDecision list
    }
    ```

### Payout Calculation Order (Decimal)
1. **Base Eligible Amount**: Total bill amount (or claimed amount, capped at bill totals if a mismatch exists). If partial exclusions apply, deduct excluded item costs.
2. **Network Discount**: Apply first if hospital is in network:
   $$\text{Discounted Amount} = \text{Base Eligible Amount} \times (1 - \text{Discount}\%)$$
3. **Co-pay**: Deduct after network discount:
   $$\text{Post-Copay Amount} = \text{Discounted Amount} \times (1 - \text{Copay}\%)$$
4. **Category Sub-limit**: Cap final approved amount at category sub-limit:
   $$\text{Final Approved Payout} = \min(\text{Post-Copay Amount}, \text{Category Sub-limit})$$

---

## Agent 7: Fraud Detector (`FraudDetectorAgent`)

Evaluates rate-limiting counters, high-value limits, duplicate records, and computes final risk indices.

- **Class Identifier**: `fraud_detector`
- **Pipeline Order**: 7

### Input Contract
- Read from `ClaimContext`:
  - `context.claims_history` (persisted claims from database + request payload)
  - `context.claimed_amount`, `context.treatment_date`, `context.claim_id`

### Output Contract
- Returns from `_execute()`:
  - `checks`: `same_day_claims`, `monthly_frequency`, `high_value_flag`, `duplicate_claim`, and `fraud_assessment`.
  - `confidence`: `1.0 - fraud_score`.
  - `output_summary`:
    ```json
    {
      "fraud_score": 0.40,
      "signals_triggered": 1,
      "signals": ["SAME_DAY_CLAIMS: 3 claims on 2026-05-31 (including this one)."],
      "recommend_review": true
    }
    ```

### Fraud Signals & Score Mappings
- **Same-Day Frequency**: $\ge 2$ claims on same treatment date $\rightarrow +0.40$
- **Monthly Frequency**: $\ge 6$ claims in same calendar month $\rightarrow +0.20$
- **High-Value**: Claimed amount $> \text{₹}25,000 \rightarrow +0.15$
- **Low Document Quality**: Quality score $< 0.50 \rightarrow +0.10$
- **Duplicate Claim**: Identical date + amount + category for same member (not rejected) $\rightarrow +0.80$

### Resolution Rules
- If `fraud_score >= 0.80` (or `claimed_amount > ₹25,000` auto-review threshold), forces `recommend_review = True`.
- If `recommend_review = True` and the claim was otherwise `APPROVED` or `PARTIAL`, the pipeline executor overrides the decision to `MANUAL_REVIEW` and resets the active payout to `0.0`.

---

## Core Policy Rules Engine (`PolicyRulesEngine`)

A stateless engine that reads and evaluates policy logic dynamically from `policy_terms.json`.

### Public Methods Contract
```python
class PolicyRulesEngine:
    def __init__(self, policy_path: str | Path): ...
    
    def check_member_eligibility(self, member_id: str) -> RuleResult: ...
    
    def check_waiting_period(
        self, member_id: str, diagnosis: str, treatment_date: str
    ) -> RuleResult: ...
    
    def check_exclusions(
        self, diagnosis: str, treatment: str, line_items: Optional[list[dict]]
    ) -> RuleResult: ...
    
    def check_pre_authorization(
        self, claim_category: str, line_items: Optional[list[dict]], claimed_amount: Decimal
    ) -> RuleResult: ...
    
    def check_per_claim_limit(self, claimed_amount: Decimal, claim_category: str) -> RuleResult: ...
    
    def check_annual_limit(self, claimed_amount: Decimal, ytd_amount: Decimal) -> RuleResult: ...
    
    def check_minimum_claim(self, claimed_amount: Decimal) -> RuleResult: ...
    
    def check_submission_deadline(self, treatment_date: str, submission_date: Optional[str]) -> RuleResult: ...
    
    def check_covered_procedures(self, claim_category: str, line_items: Optional[list[dict]]) -> RuleResult: ...
    
    def check_relationship(self, member_id: str) -> RuleResult: ...
    
    def check_dental_report(self, has_dental_report: bool) -> RuleResult: ...
    
    def check_pharmacy_generic_status(self, line_items: Optional[list[dict]], medicines: Optional[list[str]]) -> RuleResult: ...
    
    def check_alternative_medicine_system(self, diagnosis: str, treatment: str, line_items: Optional[list[dict]], hospital_name: Optional[str]) -> RuleResult: ...
    
    def check_alternative_medicine_practitioner(self, doctor_registration: Optional[str]) -> RuleResult: ...
    
    def check_alternative_medicine_sessions(self, line_items: Optional[list[dict]]) -> RuleResult: ...
    
    def check_sub_limit(self, claim_category: str, amount: Decimal) -> RuleResult: ...
```

---

## Pipeline Orchestrator (`PipelineExecutor`)

Sequential orchestrator managing the pipeline lifecycle, handling exceptions, overrides, and generating final outputs.

### Public Interface
```python
class PipelineExecutor:
    def __init__(self, policy_engine: PolicyRulesEngine, ai_client: Optional[ModelClient] = None): ...

    async def execute(self, request: ClaimSubmissionRequest) -> ClaimDecisionOutput: ...
```

### Escalation Override Logic
If any of the following occurrences happen during a pipeline run, the final decision is overridden to `MANUAL_REVIEW`, and the active `approved_amount` is set to `0.0` (while preserving the pre-review decision and amount inside `pre_review_decision` and `pre_review_approved_amount`):
1. Any critical cross-validation checks fail (`passed=False` on patient name match, category content mismatch, treatment date mismatch, hospital mismatch, or bill total mismatch).
2. Any warnings are triggered on policy compliance rules (such as missing dental reports, branded medicines in pharmacy bills, invalid alternative practitioner prefixes, or unrecognized procedures).
3. Fraud risk triggers recommendation for manual review.
4. Any component fails during processing, putting the pipeline in a degraded state.

---

## Asynchronous Celery Workers (`tasks/worker.py`)

Handles background execution of the multi-agent pipeline using Celery and Redis.

### Task Signature
```python
@celery_app.task(name="app.tasks.worker.process_claim_task")
def process_claim_task(claim_id: str, request_data: dict[str, Any]) -> None: ...
```

### Key Execution Contracts
1. **Distributed Concurrency Lock**: Prior to processing, the worker attempts to acquire a lock in Redis using the key:
   `claim_lock:{member_id}:{treatment_date}:{claim_category}`
   - Timeout: `120s`, blocking timeout: `5s`.
   - If the lock is not acquired, the claim is rejected with `status = "failed"` and the reason is recorded as concurrent submission.
2. **Dynamic Database History Merging**: The worker queries the `claims` table for any past claims matching `member_id` (excluding the current `claim_id`) and combines them with `claims_history` in the request payload to ensure accurate rate-limit checks.
3. **Payload Optimization (Disk Loading)**: Inspects document records. If `file_path` is present and `base64_data` is null, reads the file from local disk and encodes it back to base64 before executing the pipeline.
4. **Lifecycle Loop Resolution**: Runs inside `asyncio.run()`. Connection pools and async engines are disposed at completion by calling `await close_db()` in a `finally` block to prevent loop mismatch crashes.

---

## API Layer Contracts (`routes/claims.py` & `routes/review.py`)

### 1. Claims Routes (`/api/claims`)
- **POST `/api/claims`** (Submit Claim):
  - **Headers**: Accepts optional `X-Idempotency-Key` string.
  - **Idempotency Check**: If key is provided:
    1. Check Redis for `idempotency:{key}`.
    2. If key exists, retrieve the claim ID, fetch the record from the database, and return it.
    3. If key doesn't exist, store the key-value pair in Redis with a 24-hour expiration (`ex=86400`) and proceed.
  - **Offloading**: Extracts base64 document contents and writes them to local folders `uploads/{claim_id}/{file_id}_{file_name}`, clearing `base64_data` and populating `file_path` before dispatching Celery tasks.
  - **Status Code**: `202 Accepted`.
  - **Response**: Returns a shell of the record in `pending` state.
- **GET `/api/claims`**: Returns a list of all claim records in the database.
- **GET `/api/claims/{claim_id}`**: Returns full details and trace summary logs for a specific claim.

### 2. Review Routes (`/api/reviews`)
- **GET `/api/reviews`**: Lists all claims currently in `awaiting_review` state.
- **POST `/api/reviews/{claim_id}/action`**: Performs manual override.
  - **Request Body**:
    ```json
    {
      "action": "approve" | "deny",
      "reviewed_by": "Auditor Name",
      "notes": "Auditor review notes detailing decisions",
      "approved_amount": 1500.00 // Optional override amount (not to exceed claimed amount)
    }
    ```
  - **Validation**: If `action == "approve"`, overrides approved amount if provided (verifies it does not exceed `claimed_amount`), restores decision to `pre_review_decision` (or default `APPROVED`), and sets status to `completed`. If `action == "deny"`, sets decision to `REJECTED`, approved amount to `0.0`, and sets status to `completed`.
