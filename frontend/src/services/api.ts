import {
  ClaimDecisionOutput,
  ClaimRegistrySummary,
  ClaimSubmissionRequest,
  PolicyTerms,
  Member,
  TestCase,
  TestSuiteResult,
  ReviewActionPayload
} from '@/types/claims';

const API_BASE = process.env.NEXT_PUBLIC_API_URL || '';

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const url = `${API_BASE}${path}`;
  const response = await fetch(url, {
    headers: {
      'Content-Type': 'application/json',
    },
    cache: 'no-store',
    ...options,
  });

  if (!response.ok) {
    const errorBody = await response.json().catch(() => ({}));
    throw new Error(errorBody?.detail || `HTTP Error ${response.status} on ${path}`);
  }

  return response.json() as Promise<T>;
}

export const claimsApi = {
  // Claims Endpoints
  async listClaims(): Promise<ClaimRegistrySummary[]> {
    return request<ClaimRegistrySummary[]>('/api/claims');
  },

  async getClaim(id: string): Promise<ClaimDecisionOutput> {
    return request<ClaimDecisionOutput>(`/api/claims/${id}?_t=${Date.now()}`);
  },

  async submitClaim(claim: ClaimSubmissionRequest, idempotencyKey?: string): Promise<ClaimDecisionOutput> {
    const headers: Record<string, string> = {
      'Content-Type': 'application/json',
    };
    if (idempotencyKey) {
      headers['X-Idempotency-Key'] = idempotencyKey;
    }
    return request<ClaimDecisionOutput>('/api/claims', {
      method: 'POST',
      headers,
      body: JSON.stringify(claim),
    });
  },

  // Policy Endpoints
  async getPolicy(): Promise<PolicyTerms> {
    return request<PolicyTerms>('/api/policy');
  },

  async listMembers(): Promise<Member[]> {
    return request<Member[]>('/api/policy/members');
  },

  async getMember(memberId: string): Promise<Member> {
    return request<Member>(`/api/policy/members/${memberId}`);
  },

  // Eval Endpoints
  async getTestCases(): Promise<{ test_cases: TestCase[] }> {
    return request<{ test_cases: TestCase[] }>('/api/eval/test-cases');
  },

  async runAllTestCases(): Promise<TestSuiteResult> {
    return request<TestSuiteResult>('/api/eval/run-all', {
      method: 'POST',
    });
  },

  async runSingleTestCase(caseId: string): Promise<any> {
    return request<any>(`/api/eval/run/${caseId}`, {
      method: 'POST',
    });
  },

  // Review Queue Endpoints
  async listReviews(): Promise<any[]> {
    return request<any[]>('/api/reviews');
  },

  async getReview(id: string): Promise<ClaimDecisionOutput> {
    return request<ClaimDecisionOutput>(`/api/reviews/${id}`);
  },

  async submitReviewAction(id: string, payload: ReviewActionPayload): Promise<ClaimDecisionOutput> {
    return request<ClaimDecisionOutput>(`/api/reviews/${id}/action`, {
      method: 'POST',
      body: JSON.stringify(payload),
    });
  }
};
