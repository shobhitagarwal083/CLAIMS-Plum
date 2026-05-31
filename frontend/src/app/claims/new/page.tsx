'use client';

import { useEffect, useState } from 'react';
import { useRouter } from 'next/navigation';
import { claimsApi } from '@/services/api';
import { Member, TestCase, DocumentInput, ClaimSubmissionRequest } from '@/types/claims';
import styles from './newClaim.module.css';

const calculateAge = (dobString?: string) => {
  if (!dobString) return 'N/A';
  const today = new Date();
  const birthDate = new Date(dobString);
  let age = today.getFullYear() - birthDate.getFullYear();
  const m = today.getMonth() - birthDate.getMonth();
  if (m < 0 || (m === 0 && today.getDate() < birthDate.getDate())) {
    age--;
  }
  return age;
};

const DOCUMENT_REQUIREMENTS: Record<string, { required: string[]; optional: string[] }> = {
  CONSULTATION: {
    required: ['PRESCRIPTION', 'HOSPITAL_BILL'],
    optional: ['LAB_REPORT', 'DIAGNOSTIC_REPORT'],
  },
  DIAGNOSTIC: {
    required: ['PRESCRIPTION', 'LAB_REPORT', 'HOSPITAL_BILL'],
    optional: ['DISCHARGE_SUMMARY'],
  },
  PHARMACY: {
    required: ['PRESCRIPTION', 'PHARMACY_BILL'],
    optional: [],
  },
  DENTAL: {
    required: ['HOSPITAL_BILL'],
    optional: ['PRESCRIPTION', 'DENTAL_REPORT'],
  },
  VISION: {
    required: ['PRESCRIPTION', 'HOSPITAL_BILL'],
    optional: [],
  },
  ALTERNATIVE_MEDICINE: {
    required: ['PRESCRIPTION', 'HOSPITAL_BILL'],
    optional: [],
  },
};

export default function NewClaim() {
  const router = useRouter();

  // Generate a unique idempotency key for this form submission session
  const [idempotencyKey] = useState(() => Math.random().toString(36).substring(2) + Date.now());

  // Data states
  const [members, setMembers] = useState<Member[]>([]);
  const [testCases, setTestCases] = useState<TestCase[]>([]);
  const [loadingData, setLoadingData] = useState(true);

  // Form states
  const [selectedMember, setSelectedMember] = useState('');
  const [claimCategory, setClaimCategory] = useState('CONSULTATION');
  const [claimedAmount, setClaimedAmount] = useState('');
  const [treatmentDate, setTreatmentDate] = useState(new Date().toISOString().split('T')[0]);
  const [hospitalName, setHospitalName] = useState('');
  const [simulateFailure, setSimulateFailure] = useState(false);

  // Document states
  const [documents, setDocuments] = useState<Array<Partial<DocumentInput>>>([]);

  // Orchestrating state
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState('');
  const [activeStep, setActiveStep] = useState(1);

  useEffect(() => {
    async function loadData() {
      try {
        const [membersList, casesData] = await Promise.all([
          claimsApi.listMembers(),
          claimsApi.getTestCases(),
        ]);
        setMembers(membersList);
        setTestCases(casesData.test_cases);

        // Prepopulate first member
        if (membersList.length > 0) {
          setSelectedMember(membersList[0].member_id);
        }
      } catch (err) {
        console.error('Failed to load form prerequisites:', err);
      } finally {
        setLoadingData(false);
      }
    }
    loadData();
  }, []);

  // Pre-fill fields from Test Case Preset
  const handleLoadPreset = (tc: TestCase) => {
    const input = tc.input;
    setSelectedMember(input.member_id);
    setClaimCategory(input.claim_category);
    setClaimedAmount(input.claimed_amount.toString());
    setTreatmentDate(input.treatment_date);
    setHospitalName(input.hospital_name || '');
    setSimulateFailure(input.simulate_component_failure || false);

    // Load mock documents from preset input
    const mockDocs = input.documents.map((doc, idx) => ({
      file_id: doc.file_id || `file_${idx}`,
      file_name: doc.file_name || 'mock_doc.pdf',
      actual_type: doc.actual_type || 'PRESCRIPTION',
      quality: doc.quality || 'GOOD',
      patient_name_on_doc: doc.patient_name_on_doc || '',
      content: typeof doc.content === 'object' ? JSON.stringify(doc.content, null, 2) : doc.content || '',
    }));
    setDocuments(mockDocs);
    setActiveStep(2); // Jump directly to parameters review step
  };

  const handleAddDocument = () => {
    const newDoc: Partial<DocumentInput> = {
      file_id: `file_${Date.now()}`,
      file_name: 'document.pdf',
      actual_type: 'PRESCRIPTION',
      quality: 'GOOD',
      patient_name_on_doc: '',
      content: 'Dr. Sameer Roy prescribes Consultation for fever and cold.',
    };
    setDocuments([...documents, newDoc]);
  };

  const handleRemoveDocument = (idx: number) => {
    setDocuments(documents.filter((_, i) => i !== idx));
  };

  const handleUpdateDocument = (idx: number, key: keyof DocumentInput, value: string) => {
    const updated = [...documents];
    updated[idx] = { ...updated[idx], [key]: value };
    setDocuments(updated);
  };

  const handleMultipleFilesUpload = (e: React.ChangeEvent<HTMLInputElement>) => {
    const files = e.target.files;
    if (!files) return;

    Array.from(files).forEach((file, idx) => {
      const reader = new FileReader();
      reader.onload = () => {
        const result = reader.result as string;
        const base64Str = result.split(',')[1];
        
        setDocuments(prev => [
          ...prev,
          {
            file_id: `file_${Date.now()}_${idx}`,
            file_name: file.name,
            mime_type: file.type,
            base64_data: base64Str,
          }
        ]);
      };
      reader.readAsDataURL(file);
    });
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (submitting) return;
    if (!selectedMember || !claimedAmount || !claimCategory) {
      setSubmitError('Please complete all required fields.');
      return;
    }

    setSubmitting(true);
    setSubmitError('');

    try {
      // Build full request body
      const req: ClaimSubmissionRequest = {
        member_id: selectedMember,
        policy_id: 'PLUM_GHI_2024',
        claim_category: claimCategory,
        treatment_date: treatmentDate,
        claimed_amount: parseFloat(claimedAmount),
        hospital_name: hospitalName || undefined,
        simulate_component_failure: simulateFailure,
        documents: documents.map((d, idx) => {
          if (d.base64_data) {
            // Live Mode document
            return {
              file_id: d.file_id || `doc_${idx}`,
              file_name: d.file_name || 'document.pdf',
              base64_data: d.base64_data,
              mime_type: d.mime_type,
            };
          }

          // Sandbox Mode document
          let parsedContent: any = null;
          if (typeof d.content === 'string' && d.content.trim()) {
            try {
              parsedContent = JSON.parse(d.content);
            } catch (err) {
              parsedContent = { text: d.content };
            }
          } else if (typeof d.content === 'object') {
            parsedContent = d.content;
          }
          return {
            file_id: d.file_id || `doc_${idx}`,
            file_name: d.file_name || 'document.pdf',
            actual_type: d.actual_type || 'PRESCRIPTION',
            quality: d.quality || 'GOOD',
            patient_name_on_doc: d.patient_name_on_doc || '',
            content: parsedContent,
          };
        }),
      };

      const result = await claimsApi.submitClaim(req, idempotencyKey);
      
      // Redirect to the newly created claim details page to see the agent execution trace!
      router.push(`/claims/${result.claim_id}`);
    } catch (err: any) {
      setSubmitError(err.message || 'An error occurred during submission.');
      setSubmitting(false);
    }
  };

  const currentMemberObj = members.find(m => m.member_id === selectedMember);

  return (
    <div className={`${styles.newClaim} animate-fade-in`}>
      <header className={styles.header}>
        <span className={styles.badge}>Adjudication Entry</span>
        <h1 className={styles.title}>Submit Claim for Processing</h1>
        <p className={styles.subtitle}>
          Execute the 7-Agent policy engine against either custom inputs or one of the 12 assignment test case presets.
        </p>
      </header>

      {/* Preset Selector */}
      <section className={`card ${styles.presetSection}`}>
        <h3 className={styles.sectionTitle}>Load Test Case Presets</h3>
        <p className={styles.sectionDescription}>
          Instantly fill the claim parameters with one of the standard test cases from the specification.
        </p>
        {loadingData ? (
          <div className={styles.loadingSmall}>Loading presets...</div>
        ) : (
          <div className={styles.presetsGrid}>
            {testCases.map(tc => (
              <button
                key={tc.case_id}
                type="button"
                onClick={() => handleLoadPreset(tc)}
                className={styles.presetBtn}
              >
                <span className={styles.presetId}>{tc.case_id}</span>
                <span className={styles.presetName}>{tc.case_name}</span>
              </button>
            ))}
          </div>
        )}
      </section>

      {/* Steps indicator */}
      <div className={styles.stepsIndicator}>
        <div
          className={`${styles.stepNode} ${activeStep >= 1 ? styles.stepActive : ''}`}
          onClick={() => setActiveStep(1)}
        >
          1. Basic Info
        </div>
        <div className={styles.stepConnector}></div>
        <div
          className={`${styles.stepNode} ${activeStep >= 2 ? styles.stepActive : ''}`}
          onClick={() => setActiveStep(2)}
        >
          2. Documents Config
        </div>
      </div>

      {/* Main Submission Form */}
      <form onSubmit={handleSubmit} className={`card ${styles.form}`}>
        {submitError && <div className={styles.errorBanner}>❌ {submitError}</div>}

        {activeStep === 1 && (
          <div className={styles.stepContent}>
            <h3 className={styles.stepTitle}>Claim Information</h3>
            <div className={styles.formGrid}>
              <div className={styles.inputGroup}>
                <label>Active Policy Member *</label>
                {loadingData ? (
                  <select disabled className={styles.select}><option>Loading...</option></select>
                ) : (
                  <select
                    value={selectedMember}
                    onChange={e => setSelectedMember(e.target.value)}
                    className={styles.select}
                  >
                    {members.map(m => (
                      <option key={m.member_id} value={m.member_id}>
                        {m.name} ({m.member_id}) - Joined: {m.join_date}
                      </option>
                    ))}
                  </select>
                )}
                {currentMemberObj && (
                  <span className={styles.memberMeta}>
                    Age: {calculateAge(currentMemberObj.date_of_birth)} | Gender: {currentMemberObj.gender} | Relationship: {currentMemberObj.relationship}
                  </span>
                )}
              </div>

              <div className={styles.inputGroup}>
                <label>Treatment Category *</label>
                <select
                  value={claimCategory}
                  onChange={e => setClaimCategory(e.target.value)}
                  className={styles.select}
                >
                  <option value="CONSULTATION">Consultation (OPD)</option>
                  <option value="DIAGNOSTIC">Diagnostic</option>
                  <option value="PHARMACY">Pharmacy</option>
                  <option value="DENTAL">Dental</option>
                  <option value="VISION">Vision</option>
                  <option value="ALTERNATIVE_MEDICINE">Alternative Medicine</option>
                </select>
              </div>

              <div className={styles.inputGroup}>
                <label>Claimed Amount (INR) *</label>
                <input
                  type="number"
                  placeholder="e.g. 1500"
                  value={claimedAmount}
                  onChange={e => setClaimedAmount(e.target.value)}
                  className={styles.input}
                  required
                />
              </div>

              <div className={styles.inputGroup}>
                <label>Treatment Date *</label>
                <input
                  type="date"
                  value={treatmentDate}
                  onChange={e => setTreatmentDate(e.target.value)}
                  className={styles.input}
                  required
                />
              </div>

              <div className={styles.inputGroup}>
                <label>Hospital Name</label>
                <input
                  type="text"
                  placeholder="e.g. Apollo Hospitals (Network discounts check)"
                  value={hospitalName}
                  onChange={e => setHospitalName(e.target.value)}
                  className={styles.input}
                />
              </div>

            </div>

            <div className={styles.actions}>
              <button
                type="button"
                className="btn-primary"
                onClick={() => setActiveStep(2)}
              >
                Configure Documents &rarr;
              </button>
            </div>
          </div>
        )}

        {activeStep === 2 && (
          <div className={styles.stepContent}>
            <div className={styles.docHeader}>
              <h3 className={styles.stepTitle}>Document Upload Configuration</h3>
            </div>

            {/* Document Requirements Highlight */}
            {(() => {
              const reqs = DOCUMENT_REQUIREMENTS[claimCategory];
              if (!reqs) return null;
              return (
                <div className={styles.requirementsBox}>
                  <div className={styles.requirementsTitle}>
                    Document Requirements for <span className={styles.categoryHighlight}>{claimCategory.replace('_', ' ').toLowerCase()}</span> claims:
                  </div>
                  <div className={styles.requirementsGrid}>
                    <div className={styles.requirementsCol}>
                      <span className={styles.reqBadgeRequired}>Required</span>
                      <ul className={styles.reqList}>
                        {reqs.required.map(docType => (
                          <li key={docType} className={styles.reqListItem}>
                            <span className={styles.bulletRequired}>●</span> {docType.replace('_', ' ')}
                          </li>
                        ))}
                      </ul>
                    </div>
                    {reqs.optional.length > 0 && (
                      <div className={styles.requirementsCol}>
                        <span className={styles.reqBadgeOptional}>Optional</span>
                        <ul className={styles.reqList}>
                          {reqs.optional.map(docType => (
                            <li key={docType} className={styles.reqListItem}>
                              <span className={styles.bulletOptional}>○</span> {docType.replace('_', ' ')}
                            </li>
                          ))}
                        </ul>
                      </div>
                    )}
                  </div>
                </div>
              );
            })()}

            <p className={styles.sectionDescription} style={{ marginBottom: '20px' }}>
              Attach documents (e.g. prescriptions or bills) to process with this claim submission. Real scans will be verified by Gemini Vision OCR.
            </p>

            <div className={styles.uploadArea}>
              <label className={styles.uploadBox}>
                <span className={styles.uploadIcon}>📁</span>
                <strong>Choose medical scan files (PDF, PNG, JPG)</strong>
                <span className={styles.uploadSubtext}>Select one or more documents to attach to this claim</span>
                <input
                  type="file"
                  multiple
                  accept="image/*,application/pdf"
                  onChange={handleMultipleFilesUpload}
                  className={styles.hiddenFileInput}
                />
              </label>
            </div>

            {documents.length > 0 && (
              <div className={styles.attachedDocsList}>
                <h4>Attached Documents ({documents.length})</h4>
                <div className={styles.docsGrid}>
                  {documents.map((doc, idx) => (
                    <div key={doc.file_id || idx} className={styles.attachedDocCard}>
                      <span className={styles.docIcon}>📄</span>
                      <div className={styles.docDetails}>
                        <span className={styles.attachedDocName}>{doc.file_name}</span>
                        <span className={styles.attachedDocMeta}>
                          {doc.base64_data ? 'Real Scan (Gemini OCR)' : 'Preset Sandbox Mock'}
                        </span>
                      </div>
                      <button
                        type="button"
                        onClick={() => handleRemoveDocument(idx)}
                        className={styles.deleteDocBtn}
                        title="Remove Document"
                      >
                        ✕
                      </button>
                    </div>
                  ))}
                </div>
              </div>
            )}

            <div className={styles.actions}>
              <button
                type="button"
                className="btn-secondary"
                onClick={() => setActiveStep(1)}
                disabled={submitting}
              >
                &larr; Back
              </button>
              <button
                type="submit"
                className="btn-primary pulse-button"
                disabled={submitting}
              >
                {submitting ? 'Adjudicating Claims Pipeline...' : 'Submit Claim to Agents'}
              </button>
            </div>
          </div>
        )}
      </form>
    </div>
  );
}
