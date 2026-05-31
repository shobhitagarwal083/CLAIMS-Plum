'use client';

import { useEffect, useState } from 'react';
import Link from 'next/link';
import { claimsApi } from '@/services/api';
import { ClaimRegistrySummary } from '@/types/claims';
import styles from './claims.module.css';

export default function ClaimsRegistry() {
  const [claims, setClaims] = useState<ClaimRegistrySummary[]>([]);
  const [filteredClaims, setFilteredClaims] = useState<ClaimRegistrySummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [search, setSearch] = useState('');
  const [filterDecision, setFilterDecision] = useState('ALL');

  useEffect(() => {
    async function loadClaims() {
      try {
        const data = await claimsApi.listClaims();
        setClaims(data);
        setFilteredClaims(data);
      } catch (err) {
        console.error('Failed to load claims registry:', err);
      } finally {
        setLoading(false);
      }
    }
    loadClaims();
  }, []);

  useEffect(() => {
    let result = claims;

    // Filter by search (name or ID)
    if (search.trim()) {
      const q = search.toLowerCase();
      result = result.filter(
        c =>
          c.member_name.toLowerCase().includes(q) ||
          c.member_id.toLowerCase().includes(q) ||
          c.claim_id.toLowerCase().includes(q)
      );
    }

    // Filter by status decision
    if (filterDecision !== 'ALL') {
      if (filterDecision === 'DOC_ERROR') {
        result = result.filter(c => c.is_document_error);
      } else if (filterDecision === 'AWAITING_REVIEW') {
        result = result.filter(c => c.status === 'awaiting_review');
      } else if (filterDecision === 'MANUAL_REVIEW') {
        result = result.filter(c => c.status === 'awaiting_review' || !!c.review_action);
      } else {
        result = result.filter(c => c.decision === filterDecision && !c.is_document_error && c.status !== 'awaiting_review');
      }
    }

    setFilteredClaims(result);
  }, [search, filterDecision, claims]);

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

  return (
    <div className={`${styles.registry} animate-fade-in`}>
      <header className={styles.header}>
        <div className={styles.titleArea}>
          <span className={styles.badge}>Security & Logs</span>
          <h1 className={styles.title}>Claims Registry</h1>
          <p className={styles.subtitle}>
            A permanent record of all health insurance claims evaluated by the orchestration system.
          </p>
        </div>
        <Link href="/claims/new" className="btn-primary">
          New Submission
        </Link>
      </header>

      <section className={`card ${styles.filterBar}`}>
        <div className={styles.searchWrapper}>
          <span className={styles.searchIcon}>🔍</span>
          <input
            type="text"
            placeholder="Search by Patient Name, Member ID, or Claim ID..."
            value={search}
            onChange={e => setSearch(e.target.value)}
            className={styles.searchInput}
          />
        </div>
        <div className={styles.filterWrapper}>
          <span className={styles.filterLabel}>Decision:</span>
          <select
            value={filterDecision}
            onChange={e => setFilterDecision(e.target.value)}
            className={styles.filterSelect}
          >
            <option value="ALL">All Outcomes</option>
            <option value="AWAITING_REVIEW">Awaiting Review</option>
            <option value="APPROVED">Approved</option>
            <option value="PARTIAL">Partial</option>
            <option value="REJECTED">Rejected</option>
            <option value="MANUAL_REVIEW">Manual Review</option>
            <option value="DOC_ERROR">Document Faults</option>
          </select>
        </div>
      </section>

      <section className={`card ${styles.tableCard}`}>
        {loading ? (
          <div className={styles.loading}>Loading registry records...</div>
        ) : filteredClaims.length === 0 ? (
          <div className={styles.empty}>
            <p>No processed claims match the specified criteria.</p>
            {claims.length === 0 && (
              <Link href="/claims/new" className="btn-primary" style={{ marginTop: '16px' }}>
                Submit Claim
              </Link>
            )}
          </div>
        ) : (
          <div className={styles.tableResponsive}>
            <table className={styles.table}>
              <thead>
                <tr>
                  <th>Submission Timestamp</th>
                  <th>Patient Name (Member ID)</th>
                  <th>Category</th>
                  <th>Claimed Amount</th>
                  <th>Approved Amount</th>
                  <th>Decision Outcome</th>
                  <th>Latency</th>
                  <th>Action</th>
                </tr>
              </thead>
              <tbody>
                {filteredClaims.map(c => (
                  <tr key={c.claim_id} className={styles.row}>
                    <td>
                      <span className={styles.timestamp}>{formatDate(c.created_at)}</span>
                    </td>
                    <td>
                      <div className={styles.memberCell}>
                        <span className={styles.memberName}>{c.member_name}</span>
                        <span className={styles.memberId}>{c.member_id}</span>
                      </div>
                    </td>
                    <td>
                      <span className={styles.categoryBadge}>{c.claim_category}</span>
                    </td>
                    <td>
                      <span className={styles.amount}>₹{c.claimed_amount.toLocaleString('en-IN')}</span>
                    </td>
                    <td>
                      <span className={`${styles.amount} ${c.approved_amount !== null && c.approved_amount > 0 && (c.decision === 'APPROVED' || c.decision === 'PARTIAL') ? styles.approvedAmt : ''}`}>
                        {c.approved_amount === null || c.is_document_error ? '—' : `₹${c.approved_amount.toLocaleString('en-IN')}`}
                      </span>
                    </td>
                    <td>
                      <span className={`badge ${
                        c.status === 'failed' ? 'badge-rejected' :
                        c.status === 'pending' ? 'badge-review' :
                        c.status === 'processing' ? 'badge-partial' :
                        c.status === 'awaiting_review' ? 'badge-awaiting' :
                        c.is_document_error ? 'badge-rejected' :
                        c.decision === 'APPROVED' ? 'badge-approved' :
                        c.decision === 'PARTIAL' ? 'badge-partial' :
                        c.decision === 'REJECTED' ? 'badge-rejected' : 'badge-review'
                      }`}>
                        {c.status === 'failed' ? 'Failed' :
                         c.status === 'pending' ? 'Pending' :
                         c.status === 'processing' ? 'Processing' :
                         c.status === 'awaiting_review' ? 'Awaiting Review' :
                         c.is_document_error ? 'Doc Fault' :
                         c.review_action ? `${c.decision} (Human)` : c.decision}
                      </span>
                    </td>
                    <td>
                      <span className={styles.latency}>{c.processing_time_ms}ms</span>
                    </td>
                    <td>
                      <Link href={`/claims/${c.claim_id}`} className={styles.viewLink}>
                        View Trace &rarr;
                      </Link>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  );
}
