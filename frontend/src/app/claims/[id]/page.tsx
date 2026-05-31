'use client';

import { useEffect, useState } from 'react';
import { useParams, useRouter } from 'next/navigation';
import Link from 'next/link';
import { claimsApi } from '@/services/api';
import { ClaimDecisionOutput, AgentExecutionTrace } from '@/types/claims';
import styles from './claimDetail.module.css';

export default function ClaimDetail() {
  const params = useParams();
  const router = useRouter();
  const id = params.id as string;

  const [claim, setClaim] = useState<ClaimDecisionOutput | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [expandedAgent, setExpandedAgent] = useState<string | null>(null);
  const [showJsonMap, setShowJsonMap] = useState<Record<string, boolean>>({});

  useEffect(() => {
    let intervalId: any;
    
    async function loadClaimDetails(showLoading = true) {
      if (showLoading) setLoading(true);
      try {
        const data = await claimsApi.getClaim(id);
        setClaim(data);
        
        if (data.status === 'completed' || data.status === 'failed' || data.status === 'awaiting_review') {
          const firstActive = data.execution_trace.find(t => t.status !== 'skipped');
          if (firstActive) {
            setExpandedAgent(prev => prev || firstActive.agent_name);
          }
        }
      } catch (err: any) {
        setError(err.message || 'Failed to load claim details.');
      } finally {
        if (showLoading) setLoading(false);
      }
    }

    async function pollClaimDetails() {
      try {
        const data = await claimsApi.getClaim(id);
        setClaim(data);
        
        if (data.status === 'completed' || data.status === 'failed' || data.status === 'awaiting_review') {
          clearInterval(intervalId);
          const firstActive = data.execution_trace.find(t => t.status !== 'skipped');
          if (firstActive) {
            setExpandedAgent(firstActive.agent_name);
          }
        }
      } catch (err: any) {
        console.error('Polling error:', err);
      }
    }

    if (id) {
      loadClaimDetails(true);

      intervalId = setInterval(() => {
        setClaim(prev => {
          if (!prev) {
            return prev; // Initial fetch hasn't finished yet, don't clear interval
          }
          if (prev.status === 'pending' || prev.status === 'processing') {
            pollClaimDetails();
          } else {
            clearInterval(intervalId);
          }
          return prev;
        });
      }, 2000);
    }

    return () => {
      if (intervalId) clearInterval(intervalId);
    };
  }, [id]);

  const toggleAgent = (name: string) => {
    setExpandedAgent(expandedAgent === name ? null : name);
  };

  const toggleJson = (name: string, e: React.MouseEvent) => {
    e.stopPropagation();
    setShowJsonMap(prev => ({ ...prev, [name]: !prev[name] }));
  };

  const isProcessing = claim && (claim.status === 'pending' || claim.status === 'processing');

  if (loading || isProcessing) {
    return (
      <div className={styles.centered}>
        <div className={styles.spinner}></div>
        <p style={{ marginTop: '20px', color: 'var(--plum-berry)', fontWeight: 500 }}>
          {claim?.status === 'processing'
            ? 'Running multi-agent claim adjudication...'
            : 'Orchestrating agent traces...'}
        </p>
      </div>
    );
  }

  if (error || !claim) {
    return (
      <div className={`${styles.centered} ${styles.errorContainer}`}>
        <h2>Failed to load claim trace</h2>
        <p>{error || `Claim with ID "${id}" could not be found.`}</p>
        <Link href="/claims" className="btn-primary" style={{ marginTop: '20px' }}>
          Back to Registry
        </Link>
      </div>
    );
  }

  const breakdown = claim.amount_breakdown;

  return (
    <div className={`${styles.detailPage} animate-fade-in`}>
      <header className={styles.backHeader}>
        <Link href="/claims" className={styles.backLink}>
          &larr; Back to Claims Registry
        </Link>
      </header>

      {/* Hero Overview */}
      <section className={`card ${styles.heroSection}`}>
        <div className={styles.heroMain}>
          <div>
            <span className={styles.claimIdText}>Claim ID: {claim.claim_id}</span>
            <h1 className={styles.memberName}>{claim.member_name}</h1>
            <p className={styles.policyInfo}>
              Member ID: <strong>{claim.member_id}</strong> | Policy: <strong>{claim.policy_id}</strong> | Category: <strong>{claim.claim_category}</strong>
            </p>
          </div>
          <div className={styles.decisionArea}>
            <span className={`badge ${
              claim.is_document_error ? 'badge-rejected' :
              claim.status === 'awaiting_review' ? 'badge-awaiting' :
              claim.decision === 'APPROVED' ? 'badge-approved' :
              claim.decision === 'PARTIAL' ? 'badge-partial' :
              claim.decision === 'REJECTED' ? 'badge-rejected' : 'badge-review'
            } ${styles.heroBadge}`}>
              {claim.is_document_error ? 'Document Fault' :
               claim.status === 'awaiting_review' ? 'Awaiting Review' :
               claim.review_action ? `${claim.decision} (Human)` : claim.decision}
            </span>
            <span className={styles.speedText}>Latency: {claim.processing_time_ms}ms</span>
          </div>
        </div>

        {claim.is_document_error && (
          <div className={styles.docErrorBanner}>
            <h3>⚠️ Document Verification Failure</h3>
            <ul>
              {claim.document_issues.map((issue, idx) => (
                <li key={idx}>{issue}</li>
              ))}
            </ul>
            <p className={styles.bannerAction}>The pipeline halted early to prevent processing errors.</p>
          </div>
        )}

        {claim.rejection_reasons.length > 0 && (
          <div className={styles.rejectionBanner}>
            <h3>❌ Claim Rejected</h3>
            <ul>
              {claim.rejection_reasons.map((reason, idx) => (
                <li key={idx}>{reason}</li>
              ))}
            </ul>
          </div>
        )}

        {claim.status === 'awaiting_review' && (
          <div className={styles.awaitingReviewBanner}>
            <h3>⏳ Paused at Decision Gate</h3>
            <p>
              This claim recommended <strong>MANUAL_REVIEW</strong> and is currently waiting for human intervention.
            </p>
            <p style={{ marginTop: '12px' }}>
              <Link href="/review" className="btn-primary" style={{ display: 'inline-block', fontSize: '13px', padding: '6px 16px' }}>
                Go to Review Queue &rarr;
              </Link>
            </p>
          </div>
        )}

        {claim.review_action && (
          <div className={claim.review_action === 'approved' ? styles.reviewApprovedBanner : styles.reviewDeniedBanner}>
            <h3>
              {claim.review_action === 'approved' ? '✅ Human Approved' : '❌ Human Denied'}
            </h3>
            <p>
              This claim was reviewed by <strong>{claim.reviewed_by}</strong> on {claim.reviewed_at ? new Date(claim.reviewed_at).toLocaleDateString('en-IN', {
                day: 'numeric',
                month: 'short',
                year: 'numeric',
                hour: '2-digit',
                minute: '2-digit'
              }) : 'N/A'}.
            </p>
            <p className={styles.reviewNotes}>
              <strong>Reviewer Notes:</strong> {claim.review_notes}
            </p>
          </div>
        )}

        {claim.manual_review_recommended && !claim.review_action && claim.status !== 'awaiting_review' && (
          <div className={styles.reviewBanner}>
            <h3>🔍 Manual Review Recommended</h3>
            <p>
              The system flagged this claim for human auditor verification. Reasons:{' '}
              {claim.decision_reasons.filter(r => r.includes('review') || r.includes('Fraud')).join(', ') || 'Low overall confidence score.'}
            </p>
          </div>
        )}
      </section>

      {/* Grid: Receipts Adjudication vs Metadata */}
      <div className={styles.statsGrid}>
        {/* Receipt Math Card */}
        <div className={`card ${styles.receiptCard}`}>
          <h3 className={styles.cardTitle}>Adjudication Calculations</h3>
          
          {claim.is_document_error ? (
            <div className={styles.noMath}>
              <p>No calculation breakdown available because document checks failed.</p>
            </div>
          ) : breakdown ? (
            <div className={styles.receipt}>
              <div className={styles.receiptRow}>
                <span>Claimed Amount</span>
                <strong>₹{breakdown.claimed_amount.toLocaleString('en-IN')}</strong>
              </div>

              {breakdown.network_discount_amount !== undefined && breakdown.network_discount_amount > 0 && (
                <div className={`${styles.receiptRow} ${styles.deduction}`}>
                  <span>
                    Network Discount ({breakdown.network_discount_percent !== undefined ? breakdown.network_discount_percent : 0}%)
                    <span className={styles.receiptSubText}>Discount applied at network hospital</span>
                  </span>
                  <span>-₹{breakdown.network_discount_amount.toLocaleString('en-IN')}</span>
                </div>
              )}

              {breakdown.sub_limit_applied && breakdown.sub_limit !== undefined && (
                <div className={`${styles.receiptRow} ${styles.deduction}`}>
                  <span>
                    Sub-limit Deduction
                    <span className={styles.receiptSubText}>Capped at category max (₹{breakdown.sub_limit.toLocaleString('en-IN')})</span>
                  </span>
                  <span>-₹{(breakdown.amount_after_discount - breakdown.copay_amount - breakdown.approved_amount).toLocaleString('en-IN')}</span>
                </div>
              )}

              {breakdown.claimed_amount > breakdown.eligible_amount && (
                <div className={`${styles.receiptRow} ${styles.deduction}`}>
                  <span>
                    Policy Exclusion Deductions
                    <span className={styles.receiptSubText}>Excluded treatments / lines</span>
                  </span>
                  <span>-₹{(breakdown.claimed_amount - breakdown.eligible_amount).toLocaleString('en-IN')}</span>
                </div>
              )}

              {breakdown.copay_amount !== undefined && breakdown.copay_amount > 0 && (
                <div className={`${styles.receiptRow} ${styles.deduction}`}>
                  <span>
                    Co-Pay ({breakdown.copay_percent !== undefined ? breakdown.copay_percent : 0}%)
                    <span className={styles.receiptSubText}>Applied on post-discount amount</span>
                  </span>
                  <span>-₹{breakdown.copay_amount.toLocaleString('en-IN')}</span>
                </div>
              )}

              <div className={styles.receiptSeparator}></div>

              <div className={`${styles.receiptRow} ${styles.totalApproved}`}>
                <span>
                  {claim.status === 'awaiting_review' ? 'Calculated Amount (Pending)' : 'Adjudicated Amount'}
                </span>
                <span>
                  ₹{claim.status === 'awaiting_review' && claim.pre_review_approved_amount !== undefined
                    ? claim.pre_review_approved_amount.toLocaleString('en-IN')
                    : breakdown.approved_amount.toLocaleString('en-IN')}
                </span>
              </div>

              {breakdown.line_items && breakdown.line_items.length > 0 && (
                <div className={styles.calcLogs}>
                  <strong>Line Items Adjudication:</strong>
                  <ul style={{ listStyle: 'none', paddingLeft: 0, marginTop: '8px' }}>
                    {breakdown.line_items.map((item, idx) => (
                      <li key={idx} style={{ marginBottom: '8px', fontSize: '13px', display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start' }}>
                        <span style={{ color: 'var(--text-secondary)' }}>
                          {item.approved ? '✅' : '❌'} {item.description}
                          {!item.approved && item.reason && (
                            <span style={{ display: 'block', fontSize: '11px', color: 'var(--plum-rose)', marginLeft: '20px', fontStyle: 'italic' }}>
                              Reason: {item.reason}
                            </span>
                          )}
                        </span>
                        <strong style={{ color: item.approved ? 'var(--plum-berry)' : 'var(--text-muted)', marginLeft: '12px' }}>
                          ₹{item.amount.toLocaleString('en-IN')}
                        </strong>
                      </li>
                    ))}
                  </ul>
                </div>
              )}
            </div>
          ) : (
            <div className={styles.noMath}>
              <p>Adjudication calculation was bypassed or rejected outright.</p>
            </div>
          )}
        </div>

        {/* Security & Observability Details */}
        <div className={`card ${styles.metaCard}`}>
          <h3 className={styles.cardTitle}>Observations</h3>
          
          <div className={styles.metaBlock}>
            <div className={styles.metaRow}>
              <span>Confidence Index:</span>
              <strong style={{ color: claim.confidence_score >= 0.85 ? 'var(--plum-mint-dark)' : 'var(--plum-rose)' }}>
                {Math.round(claim.confidence_score * 100)}%
              </strong>
            </div>
            <div className={styles.metaRow}>
              <span>Fraud Risk Index:</span>
              <strong style={{ color: claim.fraud_score >= 40 ? 'var(--plum-rose)' : 'var(--text-secondary)' }}>
                {claim.fraud_score} / 100
              </strong>
            </div>
            <div className={styles.metaRow}>
              <span>Degraded Components:</span>
              <span className={styles.degradedText}>
                {claim.degraded_components.length > 0 ? claim.degraded_components.join(', ') : 'None (Full Fidelity)'}
              </span>
            </div>
          </div>

          <div className={styles.reasonsList}>
            <strong>Adjudication Assertions:</strong>
            <ul>
              {claim.decision_reasons.map((r, idx) => (
                <li key={idx}>✓ {r}</li>
              ))}
              {claim.fraud_signals.map((s, idx) => (
                <li key={idx} className={styles.fraudSignal}>⚠️ {s}</li>
              ))}
            </ul>
          </div>
        </div>
      </div>

      {/* 7-Agent Observability Trace Tree */}
      <section className={`card ${styles.traceSection}`}>
        <h2 className={styles.sectionTitle}>7-Agent Execution Trace</h2>
        <p className={styles.sectionDescription}>
          Deep audit logs tracking input formats, agent reasoning outputs, confidence ratings, and latencies.
        </p>

        <div className={styles.timeline}>
          {claim.execution_trace.map((trace, idx) => {
            const isExpanded = expandedAgent === trace.agent_name;
            const isJsonVisible = showJsonMap[trace.agent_name] || false;

            return (
              <div
                key={trace.agent_name}
                className={`${styles.timelineNode} ${isExpanded ? styles.nodeExpanded : ''} ${
                  trace.status === 'skipped' ? styles.nodeSkipped :
                  trace.status === 'failed' ? styles.nodeFailed :
                  trace.status === 'degraded' ? styles.nodeDegraded : styles.nodeSuccess
                }`}
              >
                {/* Node Line connector */}
                <div className={styles.timelineLine}></div>
                
                {/* Node Bullet icon */}
                <div className={styles.timelineMarker}>
                  {trace.status === 'skipped' ? '○' :
                   trace.status === 'failed' ? '✗' :
                   trace.status === 'degraded' ? '⚠' : '✓'}
                </div>

                <div className={styles.nodeCard} onClick={() => toggleAgent(trace.agent_name)}>
                  <div className={styles.nodeHeader}>
                    <div>
                      <span className={styles.nodeAgentIndex}>Agent {idx + 1}</span>
                      <h4 className={styles.nodeName}>{trace.agent_name}</h4>
                    </div>
                    <div className={styles.nodeMeta}>
                      <span className={styles.nodeLatency}>{trace.duration_ms}ms</span>
                      <span className={`badge ${
                        trace.status === 'success' ? 'badge-approved' :
                        trace.status === 'degraded' ? 'badge-partial' :
                        trace.status === 'failed' ? 'badge-rejected' : 'badge-review'
                      }`}>
                        {trace.status}
                      </span>
                    </div>
                  </div>

                  {isExpanded && (
                    <div className={styles.nodeBody}>
                      <div className={styles.nodeGradients}>
                        <div className={styles.gradientBar}>
                          <span>Agent Confidence Score:</span>
                          <strong>{Math.round(trace.confidence * 100)}%</strong>
                          <div className={styles.meterContainer}>
                            <div
                              className={styles.meterFill}
                              style={{
                                width: `${trace.confidence * 100}%`,
                                background: trace.confidence >= 0.85 ? 'var(--plum-mint)' : 'var(--plum-rose)'
                              }}
                            ></div>
                          </div>
                        </div>
                      </div>

                      {trace.error && (
                        <div className={styles.agentErrorBlock}>
                          <strong>Error Details:</strong>
                          <p>{trace.error}</p>
                        </div>
                      )}

                      <div className={styles.checksList}>
                        <h5>Evaluations Checklist:</h5>
                        {trace.checks.length === 0 ? (
                          <p className={styles.noChecksText}>No assertions evaluated by this agent.</p>
                        ) : (
                          <ul>
                            {trace.checks.map((check, cIdx) => (
                              <li key={cIdx} className={check.passed ? styles.checkPass : styles.checkFail}>
                                <span className={styles.checkMarker}>{check.passed ? '✓' : '✗'}</span>
                                <div className={styles.checkContent}>
                                  <strong>{check.check_name}</strong>
                                  <span>{check.reason}</span>
                                </div>
                              </li>
                            ))}
                          </ul>
                        )}
                      </div>

                      <div className={styles.jsonControlArea}>
                        <button
                          type="button"
                          className={styles.jsonToggleBtn}
                          onClick={(e) => toggleJson(trace.agent_name, e)}
                        >
                          {isJsonVisible ? 'Hide Raw Payloads' : 'Show Raw Inputs / Outputs (JSON)'}
                        </button>

                        {isJsonVisible && (
                          <div className={styles.jsonBlock} onClick={(e) => e.stopPropagation()}>
                            <div className={styles.jsonSubBlock}>
                              <h6>Inputs Payload:</h6>
                              <pre>{JSON.stringify(trace.input_data, null, 2)}</pre>
                            </div>
                            <div className={styles.jsonSubBlock}>
                              <h6>Outputs Payload:</h6>
                              <pre>{JSON.stringify(trace.output_data, null, 2)}</pre>
                            </div>
                          </div>
                        )}
                      </div>
                    </div>
                  )}
                </div>
              </div>
            );
          })}
        </div>
      </section>
    </div>
  );
}
