'use client';

import { useEffect, useState } from 'react';
import Link from 'next/link';
import { claimsApi } from '@/services/api';
import { PolicyTerms, ClaimRegistrySummary } from '@/types/claims';
import styles from './page.module.css';

export default function Home() {
  const [policy, setPolicy] = useState<PolicyTerms | null>(null);
  const [claims, setClaims] = useState<ClaimRegistrySummary[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    async function loadDashboardData() {
      try {
        const [policyData, claimsData] = await Promise.all([
          claimsApi.getPolicy(),
          claimsApi.listClaims(),
        ]);
        setPolicy(policyData);
        setClaims(claimsData);
      } catch (err) {
        console.error('Failed to load dashboard data:', err);
      } finally {
        setLoading(false);
      }
    }
    loadDashboardData();
  }, []);

  // Compute stats
  const totalClaims = claims.length;
  const approvedClaims = claims.filter(c => c.decision === 'APPROVED' || c.decision === 'PARTIAL').length;
  const approvalRate = totalClaims > 0 ? Math.round((approvedClaims / totalClaims) * 100) : 0;
  const avgProcessingTime = totalClaims > 0 ? Math.round(claims.reduce((acc, c) => acc + c.processing_time_ms, 0) / totalClaims) : 0;
  const documentErrors = claims.filter(c => c.is_document_error).length;

  return (
    <div className={`${styles.dashboard} animate-fade-in`}>
      <header className={styles.hero}>
        <div className={styles.heroContent}>
          <h1 className={styles.title}>Claims Orchestration Engine</h1>
          <p className={styles.subtitle}>
            State-of-the-art multi-agent health insurance claims adjudication. Sub-second policy evaluation, OCR extraction, fraud checks, and full mathematical explainability.
          </p>
          <div className={styles.heroActions}>
            <Link href="/claims/new" className="btn-primary pulse-button">
              Submit New Claim
            </Link>
            <Link href="/eval" className="btn-secondary-light">
              Run Evaluation Suite
            </Link>
          </div>
        </div>
      </header>

      <section className={styles.statsGrid}>
        <div className="card card-hoverable">
          <div className={styles.statIcon} style={{ background: 'var(--plum-berry-light)' }}>📋</div>
          <h3 className={styles.statLabel}>Total Processed</h3>
          <p className={styles.statValue}>{loading ? '...' : totalClaims}</p>
          <span className={styles.statSubtext}>Claims submitted in registry</span>
        </div>
        <div className="card card-hoverable">
          <div className={styles.statIcon} style={{ background: 'var(--plum-mint-light)' }}>✓</div>
          <h3 className={styles.statLabel}>Approval Rate</h3>
          <p className={styles.statValue} style={{ color: 'var(--plum-mint)' }}>{loading ? '...' : `${approvalRate}%`}</p>
          <span className={styles.statSubtext}>Approved or Partially Approved</span>
        </div>
        <div className="card card-hoverable">
          <div className={styles.statIcon} style={{ background: '#FFF0F2' }}>⚠</div>
          <h3 className={styles.statLabel}>Document Faults</h3>
          <p className={styles.statValue} style={{ color: 'var(--plum-rose)' }}>{loading ? '...' : documentErrors}</p>
          <span className={styles.statSubtext}>Validation & OCR quality rejects</span>
        </div>
        <div className="card card-hoverable">
          <div className={styles.statIcon} style={{ background: '#F0F6FC' }}>⚡</div>
          <h3 className={styles.statLabel}>Avg Speed</h3>
          <p className={styles.statValue} style={{ color: '#1A73E8' }}>{loading ? '...' : `${avgProcessingTime}ms`}</p>
          <span className={styles.statSubtext}>Adjudication latency</span>
        </div>
      </section>

      <section className={styles.mainGrid}>
        <div className={`card ${styles.policyCard}`}>
          <h2 className={styles.sectionTitle}>Active Policy Guidelines</h2>
          <p className={styles.sectionDescription}>
            Adjudication parameters loaded dynamically from <code>policy_terms.json</code>.
          </p>

          {loading ? (
            <div className={styles.skeleton}>Loading policy parameters...</div>
          ) : (
            <div className={styles.policyParameters}>
              <div className={styles.policyGroup}>
                <h4>Co-Pay Deductibles</h4>
                <div className={styles.tags}>
                  {policy && Object.entries(policy.opd_categories).map(([category, config]) => (
                    <div key={category} className={styles.paramTag}>
                      <span className={styles.paramKey}>{category.toUpperCase()}</span>
                      <span className={styles.paramVal}>{config.copay_percent}% co-pay</span>
                    </div>
                  ))}
                </div>
              </div>

              <div className={styles.policyGroup}>
                <h4>Category Sub-Limits</h4>
                <div className={styles.tags}>
                  {policy && Object.entries(policy.opd_categories).map(([category, config]) => (
                    <div key={category} className={styles.paramTag}>
                      <span className={styles.paramKey}>{category.toUpperCase()}</span>
                      <span className={styles.paramVal}>₹{config.sub_limit?.toLocaleString('en-IN')} max</span>
                    </div>
                  ))}
                </div>
              </div>

              <div className={styles.policyGroup}>
                <h4>Network Hospitals & Discounts</h4>
                <div className={styles.tags}>
                  {policy && policy.network_hospitals.map((hospital) => (
                    <div key={hospital} className={styles.paramTag}>
                      <span className={styles.paramKey}>{hospital}</span>
                      <span className={styles.paramVal}>Network Partner (Discount Eligible)</span>
                    </div>
                  ))}
                </div>
              </div>

              <div className={styles.policyGroup}>
                <h4>Waiting Periods & Exclusions</h4>
                <div className={styles.conditions}>
                  <div className={styles.conditionBlock}>
                    <strong>Waiting Periods:</strong>
                    <ul>
                      {policy && (
                        <>
                          <li>Initial Waiting Period: {policy.waiting_periods.initial_waiting_period_days} days</li>
                          <li>Pre-Existing Conditions: {policy.waiting_periods.pre_existing_conditions_days} days</li>
                          {Object.entries(policy.waiting_periods.specific_conditions).map(([disease, days]) => (
                            <li key={disease} style={{ textTransform: 'capitalize' }}>
                              {disease}: {days} days
                            </li>
                          ))}
                        </>
                      )}
                    </ul>
                  </div>
                  <div className={styles.conditionBlock}>
                    <strong>Excluded Treatments:</strong>
                    <ul>
                      {policy && policy.exclusions.conditions.map(treatment => (
                        <li key={treatment}>{treatment}</li>
                      ))}
                    </ul>
                  </div>
                </div>
              </div>
            </div>
          )}
        </div>

        <div className={`card ${styles.recentClaimsCard}`}>
          <div className={styles.recentHeader}>
            <h2 className={styles.sectionTitle}>Recent Adjudications</h2>
            <Link href="/claims" className={styles.viewAll}>View All</Link>
          </div>
          {loading ? (
            <div className={styles.skeleton}>Loading claims registry...</div>
          ) : claims.length === 0 ? (
            <div className={styles.empty}>
              <p>No claims processed yet.</p>
              <Link href="/claims/new" className="btn-secondary" style={{ marginTop: '12px' }}>Submit Your First Claim</Link>
            </div>
          ) : (
            <div className={styles.claimsList}>
              {claims.slice(0, 5).map(claim => (
                <Link key={claim.claim_id} href={`/claims/${claim.claim_id}`} className={styles.claimRow}>
                  <div className={styles.claimInfo}>
                    <span className={styles.claimCategory}>{claim.claim_category}</span>
                    <span className={styles.claimMember}>{claim.member_name}</span>
                  </div>
                  <div className={styles.claimRight}>
                    <span className={styles.claimAmount}>₹{claim.claimed_amount.toLocaleString('en-IN')}</span>
                    <span className={`badge ${
                      claim.is_document_error ? 'badge-rejected' :
                      claim.decision === 'APPROVED' ? 'badge-approved' :
                      claim.decision === 'PARTIAL' ? 'badge-partial' :
                      claim.decision === 'REJECTED' ? 'badge-rejected' : 'badge-review'
                    }`}>
                      {claim.is_document_error ? 'Doc Fault' : claim.decision}
                    </span>
                  </div>
                </Link>
              ))}
            </div>
          )}
        </div>
      </section>
    </div>
  );
}
