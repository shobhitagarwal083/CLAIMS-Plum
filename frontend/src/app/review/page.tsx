'use client';

import { useEffect, useState, useCallback } from 'react';
import Link from 'next/link';
import { claimsApi } from '@/services/api';
import styles from './review.module.css';

interface ReviewQueueItem {
  claim_id: string;
  member_id: string;
  member_name: string;
  claim_category: string;
  claimed_amount: number;
  decision: string;
  pre_review_decision?: string;
  pre_review_approved_amount?: number;
  confidence_score: number;
  fraud_score: number;
  fraud_signals: string[];
  decision_reasons: string[];
  degraded_components: string[];
  status: string;
  is_document_error: boolean;
  processing_time_ms: number;
  created_at?: string;
}

export default function ReviewQueue() {
  const [queue, setQueue] = useState<ReviewQueueItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [reviewerName, setReviewerName] = useState('');
  const [notes, setNotes] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [actionError, setActionError] = useState('');
  const [successMsg, setSuccessMsg] = useState('');
  const [customApprovedAmount, setCustomApprovedAmount] = useState<string>('');
  const [confirmModal, setConfirmModal] = useState<{
    claimId: string;
    action: 'approve' | 'deny';
    patientName: string;
  } | null>(null);

  const loadQueue = useCallback(async (showLoading = true) => {
    if (showLoading) setLoading(true);
    try {
      const data = await claimsApi.listReviews();
      setQueue(data);
    } catch (err) {
      console.error('Failed to load review queue:', err);
    } finally {
      if (showLoading) setLoading(false);
    }
  }, []);

  useEffect(() => {
    loadQueue(true);
    const interval = setInterval(() => loadQueue(false), 5000);
    return () => clearInterval(interval);
  }, [loadQueue]);

  const toggleExpand = (id: string) => {
    const isExpanding = expandedId !== id;
    setExpandedId(isExpanding ? id : null);
    setNotes('');
    setReviewerName('');
    setActionError('');
    setSuccessMsg('');
    if (isExpanding) {
      const item = queue.find(q => q.claim_id === id);
      setCustomApprovedAmount(item && item.pre_review_approved_amount !== undefined ? item.pre_review_approved_amount.toString() : '0');
    } else {
      setCustomApprovedAmount('');
    }
  };

  const openConfirmation = (claimId: string, action: 'approve' | 'deny', patientName: string) => {
    if (!reviewerName.trim()) {
      setActionError('Please enter your name as the reviewer.');
      return;
    }
    if (!notes.trim()) {
      setActionError('Review notes are required for all actions.');
      return;
    }
    if (action === 'approve') {
      const amt = parseFloat(customApprovedAmount);
      if (isNaN(amt) || amt < 0) {
        setActionError('Please enter a valid positive approved amount.');
        return;
      }
      const item = queue.find(q => q.claim_id === claimId);
      if (item && amt > item.claimed_amount) {
        setActionError(`Approved amount cannot exceed the claimed amount of ₹${item.claimed_amount.toLocaleString('en-IN')}.`);
        return;
      }
    }
    setActionError('');
    setConfirmModal({ claimId, action, patientName });
  };

  const submitAction = async () => {
    if (!confirmModal) return;
    setSubmitting(true);
    setActionError('');
    try {
      const payload: any = {
        action: confirmModal.action,
        reviewed_by: reviewerName.trim(),
        notes: notes.trim(),
      };
      if (confirmModal.action === 'approve') {
        payload.approved_amount = parseFloat(customApprovedAmount);
      }
      await claimsApi.submitReviewAction(confirmModal.claimId, payload);
      setSuccessMsg(
        confirmModal.action === 'approve'
          ? `✅ Claim approved successfully for ₹${parseFloat(customApprovedAmount).toLocaleString('en-IN')} by ${reviewerName.trim()}.`
          : `❌ Claim denied by ${reviewerName.trim()}.`
      );
      setConfirmModal(null);
      // Remove from queue after a short delay
      setTimeout(() => {
        setQueue(prev => prev.filter(q => q.claim_id !== confirmModal.claimId));
        setExpandedId(null);
        setSuccessMsg('');
        setNotes('');
        setReviewerName('');
        setCustomApprovedAmount('');
      }, 2000);
    } catch (err: any) {
      setActionError(err.message || 'Failed to submit review action.');
      setConfirmModal(null);
    } finally {
      setSubmitting(false);
    }
  };

  const formatDate = (isoStr?: string) => {
    if (!isoStr) return 'N/A';
    const date = new Date(isoStr);
    return date.toLocaleDateString('en-IN', {
      day: 'numeric',
      month: 'short',
      year: 'numeric',
      hour: '2-digit',
      minute: '2-digit',
    });
  };

  const getTimeAgo = (isoStr?: string) => {
    if (!isoStr) return '';
    const now = new Date();
    const created = new Date(isoStr);
    const diffMs = now.getTime() - created.getTime();
    const diffMin = Math.floor(diffMs / 60000);
    if (diffMin < 1) return 'Just now';
    if (diffMin < 60) return `${diffMin}m ago`;
    const diffHrs = Math.floor(diffMin / 60);
    if (diffHrs < 24) return `${diffHrs}h ${diffMin % 60}m ago`;
    const diffDays = Math.floor(diffHrs / 24);
    return `${diffDays}d ${diffHrs % 24}h ago`;
  };

  return (
    <div className={`${styles.reviewPage} animate-fade-in`}>
      <header className={styles.header}>
        <div className={styles.titleArea}>
          <span className={styles.gateBadge}>
            <span className={styles.gateIcon}>⏸</span>
            Decision Gate
          </span>
          <h1 className={styles.title}>Human Review Queue</h1>
          <p className={styles.subtitle}>
            Claims flagged for manual intervention by the multi-agent pipeline.
            Each case requires a human decision before processing completes.
          </p>
        </div>
        {queue.length > 0 && (
          <span className={styles.countBadge}>
            🔔 {queue.length} awaiting review
          </span>
        )}
      </header>

      {loading ? (
        <div className={`card ${styles.loading}`}>Loading review queue...</div>
      ) : queue.length === 0 ? (
        <div className={`card ${styles.emptyState}`}>
          <span className={styles.emptyIcon}>✅</span>
          <h3 className={styles.emptyTitle}>All Clear</h3>
          <p className={styles.emptyText}>
            No claims are currently awaiting human review. The pipeline is processing
            all claims automatically.
          </p>
          <Link href="/claims" className="btn-secondary" style={{ marginTop: '8px' }}>
            View Claims Registry
          </Link>
        </div>
      ) : (
        <div className={styles.queueList}>
          {queue.map(item => {
            const isExpanded = expandedId === item.claim_id;
            return (
              <div key={item.claim_id} className={styles.queueCard}>
                {/* Header — clickable to expand */}
                <div
                  className={styles.queueCardHeader}
                  onClick={() => toggleExpand(item.claim_id)}
                >
                  <div className={styles.headerLeft}>
                    <div className={styles.patientRow}>
                      <span className={styles.patientName}>{item.member_name}</span>
                      <span className={styles.memberId}>{item.member_id}</span>
                      <span className="badge badge-awaiting">Awaiting Review</span>
                    </div>
                    <div className={styles.metaRow}>
                      <span className={styles.metaItem}>
                        Category: <strong>{item.claim_category}</strong>
                      </span>
                      <span className={styles.metaItem}>
                        Submitted: <strong>{formatDate(item.created_at)}</strong>
                      </span>
                      <span className={styles.metaItem}>
                        Pipeline Latency: <strong>{item.processing_time_ms}ms</strong>
                      </span>
                    </div>
                  </div>
                  <div className={styles.headerRight}>
                    <span className={styles.amountPill}>
                      ₹{item.claimed_amount.toLocaleString('en-IN')}
                    </span>
                    <span className={styles.timeInQueue}>
                      ⏱ {getTimeAgo(item.created_at)}
                    </span>
                  </div>
                  <span className={`${styles.expandIcon} ${isExpanded ? styles.expandIconOpen : ''}`}>
                    ▼
                  </span>
                </div>

                {/* Expanded Review Panel */}
                {isExpanded && (
                  <div className={styles.expandedPanel}>
                    <div className={styles.panelGrid}>
                      {/* Escalation Reasons */}
                      <div className={styles.panelBlock}>
                        <h4 className={styles.panelBlockTitle}>Escalation Reasons</h4>
                        {item.decision_reasons.length > 0 ? (
                          <ul className={styles.reasonsList}>
                            {item.decision_reasons.map((r, idx) => (
                              <li key={idx}>{r}</li>
                            ))}
                          </ul>
                        ) : (
                          <p style={{ color: 'var(--text-muted)', fontSize: '14px' }}>
                            No specific escalation reasons recorded.
                          </p>
                        )}
                        {item.fraud_signals.length > 0 && (
                          <ul className={styles.reasonsList} style={{ marginTop: '8px' }}>
                            {item.fraud_signals.map((s, idx) => (
                              <li key={idx} className={styles.fraudSignal}>⚠️ {s}</li>
                            ))}
                          </ul>
                        )}
                      </div>

                      {/* Scores & Assessment */}
                      <div className={styles.panelBlock}>
                        <h4 className={styles.panelBlockTitle}>Pipeline Assessment</h4>
                        <div className={styles.scoreRow}>
                          <span className={styles.scoreLabel}>Confidence Score</span>
                          <span className={`${styles.scoreValue} ${item.confidence_score >= 0.85 ? styles.scoreGood : styles.scoreBad}`}>
                            {Math.round(item.confidence_score * 100)}%
                          </span>
                        </div>
                        <div className={styles.scoreRow}>
                          <span className={styles.scoreLabel}>Fraud Risk Score</span>
                          <span className={`${styles.scoreValue} ${item.fraud_score >= 40 ? styles.scoreBad : styles.scoreGood}`}>
                            {item.fraud_score ?? 'N/A'} / 100
                          </span>
                        </div>
                        {item.pre_review_decision && (
                          <div className={styles.scoreRow}>
                            <span className={styles.scoreLabel}>Original Decision</span>
                            <span className={styles.scoreValue} style={{ color: 'var(--plum-berry)' }}>
                              {item.pre_review_decision}
                            </span>
                          </div>
                        )}
                        {item.pre_review_approved_amount !== undefined && item.pre_review_approved_amount !== null && (
                          <div className={styles.scoreRow}>
                            <span className={styles.scoreLabel}>Calculated Amount</span>
                            <span className={styles.scoreValue} style={{ color: 'var(--plum-mint-dark)' }}>
                              ₹{item.pre_review_approved_amount.toLocaleString('en-IN')}
                            </span>
                          </div>
                        )}
                        {item.degraded_components.length > 0 && (
                          <div className={styles.scoreRow}>
                            <span className={styles.scoreLabel}>Degraded Components</span>
                            <span className={styles.scoreValue} style={{ color: 'var(--plum-rose)' }}>
                              {item.degraded_components.join(', ')}
                            </span>
                          </div>
                        )}
                        <Link
                          href={`/claims/${item.claim_id}`}
                          className={styles.traceLink}
                          onClick={e => e.stopPropagation()}
                        >
                          View Full Execution Trace →
                        </Link>
                      </div>
                    </div>

                    {/* Action Section */}
                    {successMsg && expandedId === item.claim_id ? (
                      <div className={styles.actionSuccess}>{successMsg}</div>
                    ) : (
                      <div className={styles.actionSection}>
                        <h4 className={styles.actionTitle}>Submit Review Decision</h4>

                        <div className={styles.notesLabel}>
                          Reviewer Name <span className={styles.required}>*</span>
                        </div>
                        <input
                          type="text"
                          className={styles.reviewerField}
                          placeholder="Your name (e.g., Dr. Patel)"
                          value={reviewerName}
                          onChange={e => {
                            setReviewerName(e.target.value);
                            setActionError('');
                          }}
                        />

                        <div className={styles.notesLabel}>
                          Approved Amount Override (₹) <span className={styles.required}>*</span>
                        </div>
                        <input
                          type="number"
                          step="0.01"
                          className={styles.reviewerField}
                          placeholder="Amount to approve"
                          value={customApprovedAmount}
                          onChange={e => {
                            setCustomApprovedAmount(e.target.value);
                            setActionError('');
                          }}
                        />

                        <div className={styles.notesLabel}>
                          Review Notes <span className={styles.required}>*</span>
                        </div>
                        <textarea
                          className={styles.notesField}
                          placeholder="Explain your decision — why are you approving or denying this claim?"
                          value={notes}
                          onChange={e => {
                            setNotes(e.target.value);
                            setActionError('');
                          }}
                        />

                        <div className={styles.actionButtons}>
                          <button
                            className={styles.btnApprove}
                            disabled={submitting}
                            onClick={() => openConfirmation(item.claim_id, 'approve', item.member_name)}
                          >
                            ✅ Approve Claim
                          </button>
                          <button
                            className={styles.btnDeny}
                            disabled={submitting}
                            onClick={() => openConfirmation(item.claim_id, 'deny', item.member_name)}
                          >
                            ❌ Deny Claim
                          </button>
                        </div>

                        {actionError && (
                          <p className={styles.actionError}>{actionError}</p>
                        )}
                      </div>
                    )}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}

      {/* Confirmation Modal */}
      {confirmModal && (
        <div className={styles.modalOverlay} onClick={() => setConfirmModal(null)}>
          <div className={styles.modalCard} onClick={e => e.stopPropagation()}>
            <h3 className={styles.modalTitle}>
              {confirmModal.action === 'approve' ? '✅ Confirm Approval' : '❌ Confirm Denial'}
            </h3>
            <p className={styles.modalText}>
              {confirmModal.action === 'approve' ? (
                <>
                  You are about to <strong>approve</strong> the claim for <strong>{confirmModal.patientName}</strong>.
                  The approved amount will be set to <strong>₹{parseFloat(customApprovedAmount).toLocaleString('en-IN')}</strong> and the claim will be marked as completed.
                </>
              ) : (
                <>
                  You are about to <strong>deny</strong> the claim for <strong>{confirmModal.patientName}</strong>.
                  The claim will be rejected with an approved amount of ₹0.
                </>
              )}
            </p>
            <p style={{ fontSize: '13px', color: 'var(--text-muted)', marginBottom: '20px' }}>
              <strong>Reviewer:</strong> {reviewerName} <br />
              <strong>Notes:</strong> {notes} <br />
              {confirmModal.action === 'approve' && (
                <>
                  <strong>Approved Amount:</strong> ₹{parseFloat(customApprovedAmount).toLocaleString('en-IN')}
                </>
              )}
            </p>
            <div className={styles.modalActions}>
              <button className={styles.btnCancel} onClick={() => setConfirmModal(null)}>
                Cancel
              </button>
              {confirmModal.action === 'approve' ? (
                <button className={styles.btnApprove} onClick={submitAction} disabled={submitting}>
                  {submitting ? 'Processing...' : 'Confirm Approve'}
                </button>
              ) : (
                <button className={styles.btnDeny} onClick={submitAction} disabled={submitting}>
                  {submitting ? 'Processing...' : 'Confirm Deny'}
                </button>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
