export interface DocumentInput {
  file_id: string;
  file_name: string;
  actual_type?: string;
  quality?: 'GOOD' | 'POOR' | 'UNREADABLE';
  patient_name_on_doc?: string;
  content?: string;
  base64_data?: string;
  mime_type?: string;
}

export interface ClaimHistoryEntry {
  claim_id: string;
  member_id: string;
  claim_category: string;
  claimed_amount: number;
  treatment_date: string;
  decision: 'APPROVED' | 'PARTIAL' | 'REJECTED' | 'MANUAL_REVIEW';
  approved_amount: number;
}

export interface ClaimSubmissionRequest {
  member_id: string;
  policy_id: string;
  claim_category: string;
  treatment_date: string;
  claimed_amount: number;
  hospital_name?: string;
  documents: DocumentInput[];
  ytd_claims_amount?: number;
  claims_history?: ClaimHistoryEntry[];
  simulate_component_failure?: boolean;
}

export interface LineItemDecision {
  description: string;
  amount: number;
  approved: boolean;
  reason?: string;
}

export interface AmountBreakdown {
  claimed_amount: number;
  eligible_amount: number;
  network_discount_percent: number;
  network_discount_amount: number;
  amount_after_discount: number;
  copay_percent: number;
  copay_amount: number;
  sub_limit?: number;
  sub_limit_applied: boolean;
  approved_amount: number;
  rejection_deductions?: number;
  line_items?: LineItemDecision[];
}

export interface AgentCheckResult {
  check_name: string;
  passed: boolean;
  reason: string;
  details?: Record<string, any>;
  severity?: 'block' | 'warn' | 'info';
}

export interface AgentExecutionTrace {
  agent_name: string;
  agent_type: string;
  status: 'success' | 'failed' | 'skipped' | 'degraded';
  input_data: Record<string, any>;
  output_data: Record<string, any>;
  input_summary?: Record<string, any>;
  output_summary?: Record<string, any>;
  confidence: number;
  checks: AgentCheckResult[];
  duration_ms: number;
  error?: string;
  started_at: string;
  finished_at: string;
}

export interface ClaimDecisionOutput {
  claim_id: string;
  member_id: string;
  member_name: string;
  policy_id: string;
  claim_category: string;
  status: string;
  decision: 'APPROVED' | 'PARTIAL' | 'REJECTED' | 'MANUAL_REVIEW' | null;
  approved_amount: number;
  confidence_score: number;
  rejection_reasons: string[];
  decision_reasons: string[];
  amount_breakdown?: AmountBreakdown;
  document_issues: string[];
  is_document_error: boolean;
  fraud_signals: string[];
  fraud_score: number;
  degraded_components: string[];
  manual_review_recommended: boolean;
  review_action?: 'approved' | 'denied' | null;
  reviewed_by?: string;
  reviewed_at?: string;
  review_notes?: string;
  pre_review_decision?: string;
  pre_review_approved_amount?: number;
  processing_time_ms: number;
  processed_at?: string;
  execution_trace: AgentExecutionTrace[];
}

export interface ClaimRegistrySummary {
  claim_id: string;
  member_id: string;
  member_name: string;
  claim_category: string;
  claimed_amount: number;
  decision: 'APPROVED' | 'PARTIAL' | 'REJECTED' | 'MANUAL_REVIEW' | null;
  approved_amount: number;
  confidence_score: number;
  status: string;
  is_document_error: boolean;
  processing_time_ms: number;
  created_at?: string;
  review_action?: 'approved' | 'denied' | null;
  pre_review_decision?: string;
  pre_review_approved_amount?: number;
}

export interface ReviewActionPayload {
  action: 'approve' | 'deny';
  reviewed_by: string;
  notes: string;
  approved_amount?: number;
}

export interface Member {
  member_id: string;
  name: string;
  date_of_birth: string;
  gender: string;
  relationship: string;
  join_date: string;
  dependents?: string[];
  primary_member_id?: string;
}

export interface PolicyCategoryConfig {
  sub_limit: number;
  copay_percent: number;
  network_discount_percent: number;
  requires_prescription: boolean;
  requires_pre_auth: boolean;
  covered: boolean;
  covered_procedures?: string[];
  excluded_procedures?: string[];
  covered_items?: string[];
  excluded_items?: string[];
}

export interface PolicyTerms {
  policy_id: string;
  policy_name: string;
  insurer: string;
  policy_holder: {
    company_name: string;
    employee_count: number;
    policy_start_date: string;
    policy_end_date: string;
    renewal_status: string;
  };
  coverage: {
    sum_insured_per_employee: number;
    annual_opd_limit: number;
    per_claim_limit: number;
    family_floater: {
      enabled: boolean;
      combined_limit: number;
      covered_relationships: string[];
    };
  };
  opd_categories: Record<string, PolicyCategoryConfig>;
  waiting_periods: {
    initial_waiting_period_days: number;
    pre_existing_conditions_days: number;
    specific_conditions: Record<string, number>;
  };
  exclusions: {
    conditions: string[];
    dental_exclusions: string[];
    vision_exclusions: string[];
  };
  pre_authorization: {
    required_for: string[];
    validity_days: number;
  };
  network_hospitals: string[];
  submission_rules: {
    deadline_days_from_treatment: number;
    minimum_claim_amount: number;
    currency: string;
  };
  document_requirements: Record<string, {
    required: string[];
    optional: string[];
  }>;
  members: Member[];
}

export interface TestCase {
  case_id: string;
  case_name: string;
  description: string;
  input: {
    member_id: string;
    policy_id: string;
    claim_category: string;
    claimed_amount: number;
    treatment_date: string;
    hospital_name?: string;
    documents: Array<Partial<DocumentInput>>;
    ytd_claims_amount?: number;
    claims_history?: Array<Partial<ClaimHistoryEntry>>;
    simulate_component_failure?: boolean;
  };
  expected: {
    decision?: 'APPROVED' | 'PARTIAL' | 'REJECTED' | 'MANUAL_REVIEW';
    approved_amount?: number;
    confidence_score?: string;
    rejection_reasons?: string[];
    system_must?: string[];
  };
}

export interface TestSuiteResult {
  total: number;
  passed: number;
  failed: number;
  pass_rate: string;
  total_time_ms: number;
  results: Array<{
    case_id: string;
    case_name: string;
    description: string;
    passed: boolean;
    processing_time_ms: number;
    assessment: {
      passed: boolean;
      checks: Array<{
        check: string;
        passed: boolean;
        detail: string;
      }>;
    };
    expected: Record<string, any>;
    actual: Record<string, any>;
    execution_trace: AgentExecutionTrace[];
  }>;
}
