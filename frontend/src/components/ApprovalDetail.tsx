import { FormEvent, useEffect, useMemo, useRef, useState } from "react";
import {
  AlertCircle,
  ArrowLeft,
  CalendarClock,
  Check,
  CheckCircle2,
  Circle,
  Clock3,
  RefreshCw,
  ShieldAlert,
  X,
  XCircle,
} from "lucide-react";
import { Link, useNavigate } from "react-router-dom";
import {
  ApiError,
  ApprovalDetail as ApprovalDetailData,
  ApprovalStep,
  Me,
  decideApproval,
  fetchApprovalDetail,
} from "../api-client";
import { authClient } from "../auth-client";

type DetailState =
  | { status: "loading" }
  | { status: "error"; error: ApiError | Error }
  | { status: "ready"; detail: ApprovalDetailData };

type Decision = "APPROVE" | "REJECT";

function humanize(value: string) {
  return value.toLowerCase().replaceAll("_", " ").replace(/\b\w/g, letter => letter.toUpperCase());
}

function formatDate(value: string | null) {
  if (!value) return "Not set";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "Not set";
  return new Intl.DateTimeFormat("en-IN", {
    day: "2-digit",
    month: "short",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  }).format(date);
}

function statusClass(value: string) {
  return value.toLowerCase().replaceAll("_", "-");
}

function StepIcon({ step }: { step: ApprovalStep }) {
  if (step.state === "APPROVED" || step.state === "SKIPPED") return <CheckCircle2 size={19} aria-hidden="true" />;
  if (step.state === "REJECTED") return <XCircle size={19} aria-hidden="true" />;
  if (step.state === "CURRENT") return <Clock3 size={19} aria-hidden="true" />;
  return <Circle size={19} aria-hidden="true" />;
}

function newIdempotencyKey() {
  return typeof crypto.randomUUID === "function"
    ? `approval:${crypto.randomUUID()}`
    : `approval:${Date.now()}:${Math.random().toString(36).slice(2)}`;
}

export function ApprovalDetail({ me, approvalId }: { me: Me; approvalId: string }) {
  const navigate = useNavigate();
  const [attempt, setAttempt] = useState(0);
  const [detailState, setDetailState] = useState<DetailState>({ status: "loading" });
  const [decision, setDecision] = useState<Decision | null>(null);
  const [note, setNote] = useState("");
  const [noteError, setNoteError] = useState("");
  const [decisionError, setDecisionError] = useState<ApiError | Error | null>(null);
  const [outcome, setOutcome] = useState<"success" | "stale" | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const idempotencyKey = useRef<string | null>(null);
  const headingRef = useRef<HTMLHeadingElement>(null);

  useEffect(() => {
    const controller = new AbortController();
    setDetailState({ status: "loading" });
    fetchApprovalDetail(approvalId, controller.signal)
      .then(detail => setDetailState({ status: "ready", detail }))
      .catch(error => {
        if (controller.signal.aborted) return;
        setDetailState({ status: "error", error: error instanceof Error ? error : new Error("Approval detail could not be loaded.") });
      });
    return () => controller.abort();
  }, [approvalId, attempt]);

  useEffect(() => {
    if (detailState.status !== "error" || !(detailState.error instanceof ApiError) || detailState.error.status !== 401) return;
    void authClient.signOut().finally(() => navigate("/", { replace: true, state: { sessionExpired: true } }));
  }, [detailState, navigate]);

  useEffect(() => { if (detailState.status === "ready") headingRef.current?.focus(); }, [detailState.status]);

  const detail = detailState.status === "ready" ? detailState.detail : null;
  const title = detail?.title || (detail ? `${humanize(detail.document_type)} approval` : "Approval detail");
  const ownSubmission = useMemo(() => Boolean(
    detail?.submitted_by_name
    && detail.submitted_by_name.trim().localeCompare(me.display_name.trim(), undefined, { sensitivity: "accent" }) === 0
  ), [detail?.submitted_by_name, me.display_name]);
  const canDecide = Boolean(detail && detail.status === "PENDING" && !ownSubmission);

  const openDecision = (nextDecision: Decision) => {
    setDecision(nextDecision);
    setNote("");
    setNoteError("");
    setDecisionError(null);
    setOutcome(null);
    idempotencyKey.current = null;
  };

  const submitDecision = async (event: FormEvent) => {
    event.preventDefault();
    if (!detail || !decision || submitting) return;
    const cleanNote = note.trim();
    if (decision === "REJECT" && !cleanNote) {
      setNoteError("Explain why this request is being rejected.");
      return;
    }
    setNoteError("");
    setDecisionError(null);
    setOutcome(null);
    setSubmitting(true);
    const key = idempotencyKey.current ?? newIdempotencyKey();
    idempotencyKey.current = key;
    try {
      const updated = await decideApproval(approvalId, {
        decision,
        note: cleanNote || null,
        step: detail.current_step,
      }, key);
      setDetailState({ status: "ready", detail: updated });
      setDecision(null);
      setNote("");
      idempotencyKey.current = null;
      setOutcome("success");
    } catch (error) {
      const safeError = error instanceof Error ? error : new Error("The decision could not be recorded.");
      if (safeError instanceof ApiError && safeError.status === 409) {
        setOutcome("stale");
        setDecision(null);
        idempotencyKey.current = null;
        setDecisionError(safeError);
        try {
          const refreshed = await fetchApprovalDetail(approvalId);
          setDetailState({ status: "ready", detail: refreshed });
        } catch {
          // The preserved conflict remains actionable even if the refresh also fails.
        }
      } else {
        setDecisionError(safeError);
      }
    } finally {
      setSubmitting(false);
    }
  };

  if (detailState.status === "loading") {
    return <section className="approval-detail approval-detail--loading" aria-busy="true"><span className="skeleton skeleton--title" /><span className="skeleton skeleton--line" /><span className="skeleton skeleton--line" /><span>Loading approval…</span></section>;
  }

  if (detailState.status === "error") {
    const error = detailState.error;
    return (
      <section className="approval-detail-state" role="alert">
        <span><AlertCircle size={26} aria-hidden="true" /></span>
        <h2>{error instanceof ApiError && error.status === 404 ? "Approval not found" : "Approval unavailable"}</h2>
        <p>{error.message}</p>
        {error instanceof ApiError && error.status >= 500 && error.requestId && <p>Request ID: <code>{error.requestId}</code></p>}
        <div><button className="button button--secondary" type="button" onClick={() => setAttempt(value => value + 1)}><RefreshCw size={16} />Try again</button><Link className="button button--primary" to="/app/approvals">Back to approvals</Link></div>
      </section>
    );
  }

  if (!detail) return null;

  return (
    <article className="approval-detail">
      <Link className="back-link" to="/app/approvals"><ArrowLeft size={16} aria-hidden="true" />Back to approvals</Link>
      <div className="approval-detail__heading">
        <div>
          <p className="eyebrow">{humanize(detail.document_type)}</p>
          <h2 ref={headingRef} tabIndex={-1}>{title}</h2>
          <p>Submitted by {detail.submitted_by_name || "Unknown submitter"} on {formatDate(detail.submitted_at)}</p>
        </div>
        <span className={`detail-status detail-status--${statusClass(detail.status)}`}>{humanize(detail.status)}</span>
      </div>

      {outcome === "success" && <div className="detail-notice detail-notice--success" role="status"><CheckCircle2 size={19} aria-hidden="true" /><div><strong>Approval recorded</strong><span>The workflow has been refreshed with the backend result.</span></div></div>}
      {outcome === "stale" && <div className="detail-notice detail-notice--warning" role="alert"><AlertCircle size={19} aria-hidden="true" /><div><h3>Approval changed</h3><span>{decisionError?.message || "Another decision changed this approval. The latest route is shown below."}</span></div></div>}

      <section className="approval-detail__summary" aria-label="Approval summary">
        <div><span>Amount</span><strong>{detail.amount === null ? "Not applicable" : `₹${detail.amount}`}</strong></div>
        <div><span>Current step</span><strong>{detail.step_label || (detail.step_role ? humanize(detail.step_role) : `Step ${detail.current_step}`)}</strong></div>
        <div><span>Due</span><strong>{formatDate(detail.due_at)}</strong>{detail.overdue && <small><AlertCircle size={13} aria-hidden="true" />Overdue</small>}</div>
        <div><span>Approval rule</span><strong>{detail.rule}</strong></div>
      </section>

      <section className="approval-route-panel" aria-labelledby="approval-route-title">
        <div className="approval-route-panel__heading"><div><p className="eyebrow">Workflow</p><h3 id="approval-route-title">Approval route and history</h3></div><span>{detail.steps.length} {detail.steps.length === 1 ? "step" : "steps"}</span></div>
        <ol className="approval-route" aria-label="Approval route">
          {detail.steps.map(step => (
            <li key={step.seq} className={`approval-step approval-step--${statusClass(step.state)}`}>
              <span className="approval-step__icon"><StepIcon step={step} /></span>
              <div className="approval-step__content">
                <div><strong>{step.label}</strong><span className={`step-state step-state--${statusClass(step.state)}`}>{humanize(step.state)}</span></div>
                <p>{step.role ? humanize(step.role) : "Named approver"}</p>
                {step.decided_by && <p>Decided by {step.decided_by}{step.decided_at ? ` · ${formatDate(step.decided_at)}` : ""}</p>}
                {step.note && <blockquote>{step.note}</blockquote>}
              </div>
            </li>
          ))}
        </ol>
      </section>

      {detail.status === "PENDING" && ownSubmission && (
        <div className="self-approval-notice"><ShieldAlert size={20} aria-hidden="true" /><div><strong>Decision unavailable</strong><span>You cannot decide your own submission.</span></div></div>
      )}

      {canDecide && (
        <section className="approval-actions" aria-label="Approval actions">
          {decision ? (
            <form onSubmit={submitDecision} noValidate>
              <div className="approval-actions__form-heading"><div><h3>{decision === "APPROVE" ? "Approve this request" : "Reject this request"}</h3><p>{decision === "REJECT" ? "A clear rejection note is required." : "You can add an optional note for the workflow history."}</p></div><button type="button" aria-label="Close decision form" onClick={() => setDecision(null)} disabled={submitting}><X size={18} /></button></div>
              {decisionError && outcome !== "stale" && <div className="decision-error" role="alert"><AlertCircle size={17} /><span>{decisionError.message}</span></div>}
              <div className="field"><label htmlFor="decision-note">Decision note{decision === "REJECT" ? " *" : " (optional)"}</label><textarea id="decision-note" value={note} maxLength={500} rows={3} aria-describedby={noteError ? "decision-note-error" : "decision-note-help"} aria-invalid={Boolean(noteError)} onChange={event => { setNote(event.target.value); if (noteError && event.target.value.trim()) setNoteError(""); }} /><span id="decision-note-help" className="field-hint">Maximum 500 characters. The note becomes part of approval history.</span>{noteError && <span id="decision-note-error" className="field-error">{noteError}</span>}</div>
              <div className="approval-actions__buttons"><button className="button button--secondary" type="button" onClick={() => setDecision(null)} disabled={submitting}>Cancel</button><button className={`button ${decision === "REJECT" ? "button--danger" : "button--primary"}`} type="submit" disabled={submitting}>{submitting ? "Recording…" : decision === "REJECT" ? "Confirm rejection" : "Confirm approval"}</button></div>
            </form>
          ) : (
            <div className="approval-actions__bar"><div><strong>Decision required</strong><span>Acting on step {detail.current_step}: {detail.step_label || humanize(detail.step_role || "Current approval")}</span></div><div><button className="button button--secondary button--reject" type="button" onClick={() => openDecision("REJECT")}><XCircle size={17} />Reject request</button><button className="button button--primary" type="button" onClick={() => openDecision("APPROVE")}><Check size={17} />Approve request</button></div></div>
          )}
        </section>
      )}

      <p className="approval-footnote"><CalendarClock size={15} aria-hidden="true" />The backend validates approver identity, current step, stale state, and final document outcome.</p>
    </article>
  );
}
