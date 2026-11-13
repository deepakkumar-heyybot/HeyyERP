import { useEffect, useMemo, useRef, useState } from "react";
import {
  AlertCircle,
  ArrowUpRight,
  CalendarClock,
  ChevronLeft,
  ChevronRight,
  Inbox,
  RefreshCw,
} from "lucide-react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import {
  ApiError,
  ApprovalScope,
  ApprovalState,
  ApprovalSummary,
  Me,
  Page,
  fetchApprovals,
} from "../api-client";
import { authClient } from "../auth-client";

const PAGE_SIZE = 20;
const approvalStates: Array<{ value: ApprovalState; label: string }> = [
  { value: "PENDING", label: "Pending" },
  { value: "APPROVED", label: "Approved" },
  { value: "REJECTED", label: "Rejected" },
  { value: "CANCELLED", label: "Cancelled" },
];

type InboxState =
  | { status: "loading" }
  | { status: "error"; error: ApiError | Error }
  | { status: "ready"; page: Page<ApprovalSummary> };

function isApprovalState(value: string | null): value is ApprovalState {
  return approvalStates.some(state => state.value === value);
}

function normalizedOffset(value: string | null) {
  const parsed = Number(value);
  return Number.isInteger(parsed) && parsed >= 0 && parsed % PAGE_SIZE === 0 ? parsed : 0;
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

function humanize(value: string) {
  return value.toLowerCase().replaceAll("_", " ").replace(/\b\w/g, letter => letter.toUpperCase());
}

function approvalTitle(approval: ApprovalSummary) {
  return approval.title || `${humanize(approval.document_type)} approval`;
}

function ApprovalBadge({ approval }: { approval: ApprovalSummary }) {
  if (approval.overdue) {
    return <span className="status-badge status-badge--overdue"><AlertCircle size={13} aria-hidden="true" />Overdue</span>;
  }
  return <span className={`status-badge status-badge--${approval.status.toLowerCase()}`}>{humanize(approval.status)}</span>;
}

function ApprovalActions({ approval }: { approval: ApprovalSummary }) {
  const title = approvalTitle(approval);
  return (
    <Link className="icon-link" to={`/app/approvals/${approval.approval_id}`} aria-label={`Open approval ${title}`}>
      <ArrowUpRight size={17} aria-hidden="true" />
    </Link>
  );
}

function ApprovalTable({ approvals }: { approvals: ApprovalSummary[] }) {
  return (
    <div className="approval-table-wrap">
      <table className="approval-table" aria-label="Approval results">
        <thead><tr><th>Approval</th><th>Submitted by</th><th>Amount</th><th>Current step</th><th>Due</th><th>Status</th><th><span className="sr-only">Actions</span></th></tr></thead>
        <tbody>
          {approvals.map(approval => (
            <tr key={approval.approval_id}>
              <td><strong>{approvalTitle(approval)}</strong><span>{humanize(approval.document_type)}</span></td>
              <td>{approval.submitted_by_name || "Unknown submitter"}</td>
              <td className="numeric-value">{approval.amount === null ? "—" : `₹${approval.amount}`}</td>
              <td><strong>{approval.step_label || humanize(approval.step_role || "Current approval")}</strong><span>Step {approval.current_step}</span></td>
              <td>{formatDate(approval.due_at)}</td>
              <td><ApprovalBadge approval={approval} /></td>
              <td><ApprovalActions approval={approval} /></td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function ApprovalCards({ approvals }: { approvals: ApprovalSummary[] }) {
  return (
    <ul className="approval-cards" aria-label="Approval results">
      {approvals.map(approval => (
        <li key={approval.approval_id}>
          <div className="approval-card__heading"><div><strong>{approvalTitle(approval)}</strong><span>{humanize(approval.document_type)}</span></div><ApprovalBadge approval={approval} /></div>
          <dl>
            <div><dt>Submitted by</dt><dd>{approval.submitted_by_name || "Unknown submitter"}</dd></div>
            <div><dt>Amount</dt><dd>{approval.amount === null ? "—" : `₹${approval.amount}`}</dd></div>
            <div><dt>Current step</dt><dd>{approval.step_label || humanize(approval.step_role || "Current approval")} · Step {approval.current_step}</dd></div>
            <div><dt>Due</dt><dd>{formatDate(approval.due_at)}</dd></div>
          </dl>
          <Link className="button button--secondary" to={`/app/approvals/${approval.approval_id}`} aria-label={`Open approval ${approvalTitle(approval)}`}>Open approval<ArrowUpRight size={16} aria-hidden="true" /></Link>
        </li>
      ))}
    </ul>
  );
}

export function ApprovalInbox({ me }: { me: Me }) {
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const [attempt, setAttempt] = useState(0);
  const [inboxState, setInboxState] = useState<InboxState>({ status: "loading" });
  const headingRef = useRef<HTMLHeadingElement>(null);
  const canViewAll = me.permissions.includes("approval.view_all");
  const scope: ApprovalScope = searchParams.get("scope") === "all" && canViewAll ? "all" : "mine";
  const state: ApprovalState = isApprovalState(searchParams.get("state")) ? searchParams.get("state") as ApprovalState : "PENDING";
  const offset = normalizedOffset(searchParams.get("offset"));

  const canonicalParams = useMemo(() => new URLSearchParams({
    scope,
    state,
    offset: String(offset),
  }), [offset, scope, state]);

  useEffect(() => {
    if (searchParams.toString() !== canonicalParams.toString()) setSearchParams(canonicalParams, { replace: true });
  }, [canonicalParams, searchParams, setSearchParams]);

  useEffect(() => {
    const controller = new AbortController();
    setInboxState({ status: "loading" });
    fetchApprovals({ scope, state, limit: PAGE_SIZE, offset }, controller.signal)
      .then(page => setInboxState({ status: "ready", page }))
      .catch(error => {
        if (controller.signal.aborted) return;
        setInboxState({ status: "error", error: error instanceof Error ? error : new Error("Approval inbox could not be loaded.") });
      });
    return () => controller.abort();
  }, [attempt, offset, scope, state]);

  useEffect(() => {
    if (inboxState.status !== "error" || !(inboxState.error instanceof ApiError) || inboxState.error.status !== 401) return;
    void authClient.signOut().finally(() => navigate("/", { replace: true, state: { sessionExpired: true } }));
  }, [inboxState, navigate]);

  useEffect(() => { headingRef.current?.focus(); }, []);

  const updateQuery = (changes: Partial<{ scope: ApprovalScope; state: ApprovalState; offset: number }>) => {
    const next = new URLSearchParams({
      scope: changes.scope ?? scope,
      state: changes.state ?? state,
      offset: String(changes.offset ?? 0),
    });
    setSearchParams(next);
  };

  const page = inboxState.status === "ready" ? inboxState.page : null;
  const firstRecord = page && page.total > 0 ? page.offset + 1 : 0;
  const lastRecord = page ? page.offset + page.items.length : 0;
  const selectedStateLabel = approvalStates.find(option => option.value === state)?.label.toLowerCase() ?? "selected";

  return (
    <section className="approval-inbox" aria-labelledby="approval-inbox-title">
      <div className="page-heading-row">
        <div>
          <p className="eyebrow">Approvals</p>
          <h2 id="approval-inbox-title" ref={headingRef} tabIndex={-1}>Approval inbox</h2>
          <p>Review workflow items assigned to you and track their current decision state.</p>
        </div>
        <button className="button button--secondary" type="button" onClick={() => setAttempt(value => value + 1)} disabled={inboxState.status === "loading"}>
          <RefreshCw size={16} aria-hidden="true" />Refresh
        </button>
      </div>

      <div className="approval-controls">
        <div className="segmented-control" aria-label="Approval scope">
          <button type="button" aria-pressed={scope === "mine"} onClick={() => updateQuery({ scope: "mine" })}>My approvals</button>
          {canViewAll && <button type="button" aria-pressed={scope === "all"} onClick={() => updateQuery({ scope: "all" })}>All approvals</button>}
        </div>
        <div className="state-tabs" aria-label="Approval state">
          {approvalStates.map(option => <button key={option.value} type="button" aria-pressed={state === option.value} onClick={() => updateQuery({ state: option.value })}>{option.label}</button>)}
        </div>
      </div>

      <div className="approval-results" aria-live="polite" aria-busy={inboxState.status === "loading"}>
        {inboxState.status === "loading" ? (
          <div className="results-loading" role="status"><span className="skeleton skeleton--line" /><span className="skeleton skeleton--line" /><span>Loading approvals…</span></div>
        ) : inboxState.status === "error" ? (
          <div className="inline-error" role="alert">
            <AlertCircle size={22} aria-hidden="true" />
            <div><h3>Approval inbox unavailable</h3><p>{inboxState.error.message}</p>{inboxState.error instanceof ApiError && inboxState.error.status >= 500 && inboxState.error.requestId && <p className="inline-error__request">Request ID: <code>{inboxState.error.requestId}</code></p>}</div>
            <button className="button button--secondary" type="button" onClick={() => setAttempt(value => value + 1)}>Try again</button>
          </div>
        ) : page && page.items.length === 0 ? (
          <div className="filtered-empty">
            <span><Inbox size={26} aria-hidden="true" /></span>
            <h3>No {selectedStateLabel} approvals</h3>
            <p>Change the state filter or refresh the inbox.</p>
          </div>
        ) : page ? (
          <>
            <ApprovalTable approvals={page.items} />
            <ApprovalCards approvals={page.items} />
          </>
        ) : null}
      </div>

      {page && page.total > 0 && (
        <div className="results-pagination">
          <p>Showing {firstRecord}–{lastRecord} of {page.total} approvals</p>
          <div>
            <button type="button" aria-label="Previous page" disabled={offset === 0} onClick={() => updateQuery({ offset: Math.max(0, offset - PAGE_SIZE) })}><ChevronLeft size={18} aria-hidden="true" /></button>
            <span>Page {Math.floor(offset / PAGE_SIZE) + 1} of {Math.ceil(page.total / PAGE_SIZE)}</span>
            <button type="button" aria-label="Next page" disabled={offset + page.items.length >= page.total} onClick={() => updateQuery({ offset: offset + PAGE_SIZE })}><ChevronRight size={18} aria-hidden="true" /></button>
          </div>
        </div>
      )}

      <p className="approval-footnote"><CalendarClock size={15} aria-hidden="true" />Due and overdue labels come from the configured backend approval workflow.</p>
    </section>
  );
}
