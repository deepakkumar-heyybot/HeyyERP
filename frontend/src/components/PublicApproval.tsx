import { FormEvent, useEffect, useRef, useState } from "react";
import { AlertCircle, Check, CheckCircle2, Clock3, FileText, ShieldCheck, X, XCircle } from "lucide-react";
import { Link, useParams } from "react-router-dom";
import {
  ApiError,
  decidePublicApproval,
  fetchPublicApproval,
  PublicApprovalDecision,
  PublicApprovalLink,
} from "../api-client";
import { BrandMark } from "./Brand";

type LoadState =
  | { status: "loading" }
  | { status: "ready"; approval: PublicApprovalLink }
  | { status: "terminal"; kind: "invalid" | "expired" | "decided" }
  | { status: "error"; error: Error };

type Decision = "APPROVE" | "REJECT";
type TerminalKind = "invalid" | "expired" | "decided";

function humanize(value: string) {
  const words = value.toLowerCase().replaceAll("_", " ");
  return `${words.charAt(0).toUpperCase()}${words.slice(1)}`;
}

function formatDate(value: string) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "Expiry unavailable";
  return new Intl.DateTimeFormat("en-IN", { dateStyle: "medium", timeStyle: "short" }).format(date);
}

function terminalKind(error: ApiError): TerminalKind | null {
  if (error.status === 404) return "invalid";
  if (error.status !== 410) return null;
  return /expired/i.test(error.message) ? "expired" : "decided";
}

const terminalCopy = {
  invalid: {
    title: "Approval link not found",
    message: "This approval link is not valid.",
    help: "Check that you opened the complete link from the approval message.",
  },
  expired: {
    title: "Approval link expired",
    message: "This approval link is no longer active.",
    help: "Ask the requester for a new approval link.",
  },
  decided: {
    title: "Approval already decided",
    message: "This approval step is already complete.",
    help: "No further action is available from this link.",
  },
} as const;

function PublicFrame({ children }: { children: React.ReactNode }) {
  return (
    <main className="public-approval-shell">
      <header className="public-approval-header"><BrandMark /></header>
      <div className="public-approval-stage">{children}</div>
      <footer className="public-approval-footer"><span>Secure approval link</span><span>heyyERP · V-Rub Suspensions</span></footer>
    </main>
  );
}

function PublicApprovalState({ kind }: { kind: "invalid" | "expired" | "decided" }) {
  const copy = terminalCopy[kind];
  return (
    <section className="public-approval-card public-approval-state" aria-labelledby="public-approval-state-title">
      <span className={`public-approval-state__icon public-approval-state__icon--${kind}`} aria-hidden="true">
        {kind === "expired" ? <Clock3 size={25} /> : kind === "decided" ? <CheckCircle2 size={25} /> : <AlertCircle size={25} />}
      </span>
      <p className="eyebrow">Approval link</p>
      <h1 id="public-approval-state-title" tabIndex={-1}>{copy.title}</h1>
      <p>{copy.message}</p>
      <p className="public-approval-state__help">{copy.help}</p>
      <Link className="button button--secondary" to="/">Go to sign in</Link>
    </section>
  );
}

export function PublicApproval() {
  const { token = "" } = useParams();
  const [attempt, setAttempt] = useState(0);
  const [state, setState] = useState<LoadState>({ status: "loading" });
  const [decision, setDecision] = useState<Decision | null>(null);
  const [note, setNote] = useState("");
  const [validationError, setValidationError] = useState("");
  const [decisionError, setDecisionError] = useState<Error | null>(null);
  const [result, setResult] = useState<{ decision: Decision; response: PublicApprovalDecision } | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const submittingRef = useRef(false);
  const headingRef = useRef<HTMLHeadingElement>(null);

  useEffect(() => {
    const controller = new AbortController();
    setState({ status: "loading" });
    setDecision(null);
    setDecisionError(null);
    fetchPublicApproval(token, controller.signal).then(approval => {
      setState({ status: "ready", approval });
    }).catch(error => {
      if (controller.signal.aborted) return;
      if (error instanceof ApiError) {
        const kind = terminalKind(error);
        if (kind) {
          setState({ status: "terminal", kind });
          return;
        }
      }
      setState({ status: "error", error: error instanceof Error ? error : new Error("The approval link could not be opened.") });
    });
    return () => controller.abort();
  }, [attempt, token]);

  useEffect(() => {
    if (state.status !== "loading" || result) headingRef.current?.focus();
  }, [result, state.status]);

  function openDecision(nextDecision: Decision) {
    setDecision(nextDecision);
    setValidationError("");
    setDecisionError(null);
  }

  async function submitDecision(event?: FormEvent) {
    event?.preventDefault();
    if (!decision || submittingRef.current) return;
    const cleanNote = note.trim();
    if (decision === "REJECT" && !cleanNote) {
      setValidationError("Enter a reason for rejecting this request.");
      return;
    }
    submittingRef.current = true;
    setSubmitting(true);
    setValidationError("");
    setDecisionError(null);
    try {
      const response = await decidePublicApproval(token, decision, decision === "REJECT" ? cleanNote : null);
      setResult({ decision, response });
    } catch (error) {
      if (error instanceof ApiError) {
        const kind = terminalKind(error);
        if (kind) {
          setState({ status: "terminal", kind });
          return;
        }
      }
      setDecisionError(error instanceof Error ? error : new Error("The decision could not be submitted."));
    } finally {
      submittingRef.current = false;
      setSubmitting(false);
    }
  }

  if (state.status === "loading") {
    return <PublicFrame><section className="public-approval-card public-approval-loading" aria-live="polite"><span className="spinner" /><h1>Checking approval link</h1><p>Loading the request without making a decision.</p></section></PublicFrame>;
  }

  if (state.status === "terminal") return <PublicFrame><PublicApprovalState kind={state.kind} /></PublicFrame>;

  if (state.status === "error") {
    return (
      <PublicFrame>
        <section className="public-approval-card public-approval-state" aria-labelledby="public-approval-error-title">
          <span className="public-approval-state__icon public-approval-state__icon--invalid" aria-hidden="true"><AlertCircle size={25} /></span>
          <p className="eyebrow">Approval link</p>
          <h1 id="public-approval-error-title" ref={headingRef} tabIndex={-1}>Approval link unavailable</h1>
          <p role="alert">{state.error.message}</p>
          {state.error instanceof ApiError && state.error.status >= 500 && state.error.requestId && <p className="public-approval-request-id">Request ID: <code>{state.error.requestId}</code></p>}
          <button className="button button--primary" type="button" onClick={() => setAttempt(value => value + 1)}>Try again</button>
        </section>
      </PublicFrame>
    );
  }

  const approval = state.approval;
  if (result) {
    const rejected = result.decision === "REJECT";
    return (
      <PublicFrame>
        <section className="public-approval-card public-approval-state public-approval-state--success" aria-labelledby="public-approval-result-title">
          <span className="public-approval-state__icon public-approval-state__icon--decided" aria-hidden="true"><CheckCircle2 size={25} /></span>
          <p className="eyebrow">Decision recorded</p>
          <h1 id="public-approval-result-title" ref={headingRef} tabIndex={-1}>{rejected ? "Request rejected" : "Approval submitted"}</h1>
          <p>{rejected ? "Your rejection and reason were recorded." : "Your approval was recorded."}</p>
          <p className="public-approval-state__help">Current workflow status: {humanize(result.response.status)}</p>
          <Link className="button button--secondary" to="/">Go to sign in</Link>
        </section>
      </PublicFrame>
    );
  }

  return (
    <PublicFrame>
      <section className="public-approval-card" aria-labelledby="public-approval-title">
        <div className="public-approval-card__intro">
          <span aria-hidden="true"><FileText size={22} /></span>
          <div><p className="eyebrow">Approval requested</p><h1 id="public-approval-title" ref={headingRef} tabIndex={-1}>{approval.title?.trim() || "Approval request"}</h1></div>
        </div>
        <p className="public-approval-card__lead">Review the details below. Opening this page does not record a decision.</p>

        <dl className="public-approval-summary">
          <div><dt>Document</dt><dd>{humanize(approval.document_type)}</dd></div>
          <div><dt>Amount</dt><dd>{approval.amount === null ? "Not provided" : `₹${approval.amount}`}</dd></div>
          <div><dt>Requested by</dt><dd>{approval.requested_by?.trim() || "Not provided"}</dd></div>
          <div><dt>Approval step</dt><dd>{approval.step_label?.trim() || "Current approval"}</dd></div>
        </dl>

        <div className="public-approval-expiry"><Clock3 size={17} aria-hidden="true" /><span>This secure link expires <time dateTime={approval.expires_at}>{formatDate(approval.expires_at)}</time>.</span></div>

        {decision ? (
          <form className="public-approval-decision" onSubmit={submitDecision}>
            <div className="public-approval-decision__heading">
              <div><p className="eyebrow">Final confirmation</p><h2>{decision === "REJECT" ? "Reject request" : "Confirm approval"}</h2></div>
              <button type="button" aria-label="Cancel decision" onClick={() => setDecision(null)} disabled={submitting}><X size={20} /></button>
            </div>
            {decision === "REJECT" && <div className="field"><label htmlFor="public-approval-note">Reason for rejection</label><textarea id="public-approval-note" value={note} onChange={event => { setNote(event.target.value); setValidationError(""); }} maxLength={500} aria-invalid={Boolean(validationError)} aria-describedby={validationError ? "public-approval-note-error" : undefined} />{validationError && <span id="public-approval-note-error" className="field-error" role="alert">{validationError}</span>}<span className="field-hint">Required · Maximum 500 characters</span></div>}
            {decisionError && <div className="decision-error" role="alert"><AlertCircle size={17} aria-hidden="true" /><div><span>{decisionError.message}</span>{decisionError instanceof ApiError && decisionError.status >= 500 && decisionError.requestId && <span className="public-approval-request-id">Request ID: <code>{decisionError.requestId}</code></span>}</div></div>}
            <div className="public-approval-decision__actions">
              <button className="button button--secondary" type="button" onClick={() => setDecision(null)} disabled={submitting}>Cancel</button>
              <button className={`button ${decision === "REJECT" ? "button--danger" : "button--primary"}`} type="submit" disabled={submitting}>{submitting ? "Submitting…" : decisionError && decision === "APPROVE" ? "Try approval again" : decision === "REJECT" ? "Confirm rejection" : "Confirm approval"}</button>
            </div>
          </form>
        ) : (
          <div className="public-approval-actions" aria-label="Approval actions">
            <button className="button button--secondary button--reject" type="button" onClick={() => openDecision("REJECT")}><XCircle size={17} aria-hidden="true" />Reject request</button>
            <button className="button button--primary" type="button" onClick={() => openDecision("APPROVE")}><Check size={17} aria-hidden="true" />Approve request</button>
          </div>
        )}
        <p className="public-approval-security"><ShieldCheck size={16} aria-hidden="true" />This link is private, works once, and does not require a heyyERP sign-in.</p>
      </section>
    </PublicFrame>
  );
}
