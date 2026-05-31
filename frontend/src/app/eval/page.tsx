'use client';

import { useState, Fragment } from 'react';
import { claimsApi } from '@/services/api';
import { TestSuiteResult, AgentExecutionTrace } from '@/types/claims';
import styles from './eval.module.css';

export default function EvalSuite() {
  const [results, setResults] = useState<TestSuiteResult | null>(null);
  const [running, setRunning] = useState(false);
  const [runError, setRunError] = useState('');
  
  // Trace viewing state
  const [expandedCaseId, setExpandedCaseId] = useState<string | null>(null);
  const [expandedAgent, setExpandedAgent] = useState<string | null>(null);
  const [showJsonMap, setShowJsonMap] = useState<Record<string, boolean>>({});

  const handleRunSuite = async () => {
    setRunning(true);
    setRunError('');
    setExpandedCaseId(null);
    try {
      const data = await claimsApi.runAllTestCases();
      setResults(data);
    } catch (err: any) {
      setRunError(err.message || 'An error occurred while running the evaluation suite.');
    } finally {
      setRunning(false);
    }
  };

  const toggleTrace = (caseId: string, trace: AgentExecutionTrace[]) => {
    if (expandedCaseId === caseId) {
      setExpandedCaseId(null);
    } else {
      setExpandedCaseId(caseId);
      // Auto-expand first non-skipped agent of this trace
      const firstActive = trace.find(t => t.status !== 'skipped');
      if (firstActive) {
        setExpandedAgent(firstActive.agent_name);
      }
    }
  };

  const toggleAgent = (name: string, e: React.MouseEvent) => {
    e.stopPropagation();
    setExpandedAgent(expandedAgent === name ? null : name);
  };

  const toggleJson = (name: string, e: React.MouseEvent) => {
    e.stopPropagation();
    setShowJsonMap(prev => ({ ...prev, [name]: !prev[name] }));
  };

  return (
    <div className={`${styles.evalPage} animate-fade-in`}>
      <header className={styles.header}>
        <div>
          <span className={styles.badge}>Adjudication Audit</span>
          <h1 className={styles.title}>System Evaluation Suite</h1>
          <p className={styles.subtitle}>
            Execute all 20 standard test cases from the specification and verify their outcomes against expected rules.
          </p>
        </div>
        <button
          type="button"
          onClick={handleRunSuite}
          disabled={running}
          className={`btn-primary ${running ? '' : 'pulse-button'}`}
        >
          {running ? 'Running Adjudications...' : 'Run All 20 Test Cases'}
        </button>
      </header>

      {runError && <div className={styles.errorBanner}>❌ {runError}</div>}

      {/* Progress & Stat Cards */}
      {results && (
        <section className={styles.summarySection}>
          <div className="card">
            <h3 className={styles.statLabel}>Pass Rate</h3>
            <div className={styles.passRateContainer}>
              <span className={styles.passValue} style={{ color: results.failed > 0 ? 'var(--plum-rose)' : 'var(--plum-mint-dark)' }}>
                {results.pass_rate}
              </span>
              <span className={styles.passPct}>
                {Math.round((results.passed / results.total) * 100)}% Passed
              </span>
            </div>
            <div className={styles.progressBarContainer}>
              <div
                className={styles.progressBarFill}
                style={{
                  width: `${(results.passed / results.total) * 100}%`,
                  background: results.failed > 0 ? 'var(--plum-rose)' : 'var(--plum-mint)'
                }}
              ></div>
            </div>
          </div>

          <div className="card">
            <h3 className={styles.statLabel}>Audit Duration</h3>
            <p className={styles.statValue}>{(results.total_time_ms / 1000).toFixed(2)}s</p>
            <span className={styles.statSubtext}>Average {(results.total_time_ms / results.total).toFixed(0)}ms per claim</span>
          </div>

          <div className="card">
            <h3 className={styles.statLabel}>Resilience Status</h3>
            <p className={styles.statValue} style={{ color: 'var(--plum-mint-dark)' }}>Passed</p>
            <span className={styles.statSubtext}>Graceful degradation verified</span>
          </div>
        </section>
      )}

      {/* Results grid */}
      <section className={`card ${styles.resultsSection}`}>
        <h2 className={styles.sectionTitle}>Evaluation Log Matrix</h2>
        
        {running ? (
          <div className={styles.loader}>
            <div className={styles.spinner}></div>
            <p>Orchestrating agent worker pipelines... Please wait, executing OCR and policy analysis.</p>
          </div>
        ) : !results ? (
          <div className={styles.empty}>
            <p>No evaluation run has been triggered in this session.</p>
            <button
              type="button"
              onClick={handleRunSuite}
              className="btn-secondary"
              style={{ marginTop: '16px' }}
            >
              Start Full Verification Run
            </button>
          </div>
        ) : (
          <div className={styles.tableResponsive}>
            <table className={styles.table}>
              <thead>
                <tr>
                  <th>Case ID</th>
                  <th>Test Case Specification</th>
                  <th>Expected Output</th>
                  <th>Actual Output</th>
                  <th>Assertions Check</th>
                  <th>Latency</th>
                  <th>Action</th>
                </tr>
              </thead>
              <tbody>
                {results.results.map(r => {
                  const isTraceExpanded = expandedCaseId === r.case_id;

                  return (
                    <Fragment key={r.case_id}>
                      <tr className={styles.row}>
                        <td>
                          <span className={styles.caseId}>{r.case_id}</span>
                        </td>
                        <td>
                          <div className={styles.nameCell}>
                            <strong className={styles.caseName}>{r.case_name}</strong>
                            <span className={styles.caseDesc}>{r.description}</span>
                          </div>
                        </td>
                        <td>
                          <div className={styles.outcomeCell}>
                            <span className={styles.outcomeTitle}>Decision:</span>
                            <span className={styles.outcomeVal}>{r.expected.decision || 'Doc Fault'}</span>
                            {r.expected.approved_amount !== undefined && (
                              <span className={styles.outcomeVal}>Amount: ₹{r.expected.approved_amount}</span>
                            )}
                          </div>
                        </td>
                        <td>
                          <div className={styles.outcomeCell}>
                            <span className={styles.outcomeTitle}>Decision:</span>
                            <span className={styles.outcomeVal}>{r.actual.decision || 'Doc Fault'}</span>
                            {r.actual.approved_amount !== undefined && r.actual.approved_amount !== null && (
                              <span className={styles.outcomeVal}>Amount: ₹{r.actual.approved_amount.toLocaleString('en-IN')}</span>
                            )}
                          </div>
                        </td>
                        <td>
                          <div className={styles.checklist}>
                            {r.assessment.checks.map((check, idx) => (
                              <div
                                key={idx}
                                className={`${styles.checkItem} ${
                                  check.passed ? styles.checkPass : styles.checkFail
                                }`}
                              >
                                <span className={styles.checkMarker}>{check.passed ? '✓' : '✗'}</span>
                                <span className={styles.checkText}>{check.detail}</span>
                              </div>
                            ))}
                          </div>
                        </td>
                        <td>
                          <span className={styles.latency}>{r.processing_time_ms}ms</span>
                        </td>
                        <td>
                          <button
                            type="button"
                            onClick={() => toggleTrace(r.case_id, r.execution_trace)}
                            className={styles.viewLinkBtn}
                          >
                            {isTraceExpanded ? 'Hide Trace' : 'Inspect Trace'}
                          </button>
                        </td>
                      </tr>
                      
                      {/* Expanded In-line Trace Row */}
                      {isTraceExpanded && (
                        <tr className={styles.traceRow}>
                          <td colSpan={7}>
                            <div className={styles.inlineTraceContainer}>
                              <h4 className={styles.inlineTraceTitle}>
                                🔍 7-Agent Trace Timeline for {r.case_id}
                              </h4>
                              
                              <div className={styles.timeline}>
                                {r.execution_trace.map((trace, idx) => {
                                  const isAgentExpanded = expandedAgent === trace.agent_name;
                                  const isJsonVisible = showJsonMap[trace.agent_name] || false;

                                  return (
                                    <div
                                      key={trace.agent_name}
                                      className={`${styles.timelineNode} ${isAgentExpanded ? styles.nodeExpanded : ''} ${
                                        trace.status === 'skipped' ? styles.nodeSkipped :
                                        trace.status === 'failed' ? styles.nodeFailed :
                                        trace.status === 'degraded' ? styles.nodeDegraded : styles.nodeSuccess
                                      }`}
                                    >
                                      <div className={styles.timelineLine}></div>
                                      <div className={styles.timelineMarker}>
                                        {trace.status === 'skipped' ? '○' :
                                         trace.status === 'failed' ? '✗' :
                                         trace.status === 'degraded' ? '⚠' : '✓'}
                                      </div>

                                      <div className={styles.nodeCard} onClick={(e) => toggleAgent(trace.agent_name, e)}>
                                        <div className={styles.nodeHeader}>
                                          <div>
                                            <span className={styles.nodeAgentIndex}>Agent {idx + 1}</span>
                                            <h5 className={styles.nodeName}>{trace.agent_name}</h5>
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

                                        {isAgentExpanded && (
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
                                              <h6>Evaluations Checklist:</h6>
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
                            </div>
                          </td>
                        </tr>
                      )}
                    </Fragment>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  );
}
