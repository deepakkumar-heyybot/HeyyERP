import { useEffect, useRef, useState, type KeyboardEvent, type MouseEvent } from "react";
import { ArrowLeft, RefreshCw } from "lucide-react";
import { Link } from "react-router-dom";
import {
  ApiError, fetchBatchUsedIn, fetchCompoundBatch, transitionBatch,
  type CompoundBatch, type Me, type ProductionLotSummary,
} from "../api-client";
import { ManufacturingTabs } from "./ManufacturingTabs";
import { DetailPageTemplate } from "./PageTemplates";
import { RequestError } from "./RequestError";

type Load<T> = { status: "loading" } | { status: "ready"; data: T } | { status: "error"; error: Error };
type Action = "start-mixing" | "mixed" | "qa";
type QaResult = "PASSED" | "FAILED";
const failure = (value: unknown, fallback: string) => value instanceof Error ? value : new Error(fallback);
const label = (value: string) => value.toLowerCase().replaceAll("_", " ").replace(/^./, char => char.toUpperCase()).replace("qa", "QA");
const date = (value: string | null) => value ? <time dateTime={value}>{value}</time> : "Not recorded";
const hardnessValid = (value: string) => /^(?:100(?:\.0{1,2})?|\d{1,2}(?:\.\d{1,2})?)$/.test(value);
const actionName: Record<Action, string> = {
  "start-mixing": "Start mixing", mixed: "Finish mixing", qa: "Record QA",
};

function ErrorNotice({ error }: { error: Error }) {
  return <><RequestError error={error} />{error instanceof ApiError && error.status < 500 && error.requestId && <p>Request ID: {error.requestId}</p>}</>;
}

export function BatchDetail({ id, me }: { id: string; me: Me }) {
  const [batch, setBatch] = useState<Load<CompoundBatch>>({ status: "loading" });
  const [usedIn, setUsedIn] = useState<Load<ProductionLotSummary[]>>({ status: "loading" });
  const [batchAttempt, setBatchAttempt] = useState(0);
  const [usedInAttempt, setUsedInAttempt] = useState(0);
  const [action, setAction] = useState<Action | null>(null);
  const [result, setResult] = useState<QaResult>("PASSED");
  const [hardness, setHardness] = useState("");
  const [note, setNote] = useState("");
  const [formError, setFormError] = useState<Error | null>(null);
  const [actionError, setActionError] = useState<Error | null>(null);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState("");
  const headingRef = useRef<HTMLHeadingElement>(null);
  const triggerRef = useRef<HTMLButtonElement | null>(null);
  const cancelRef = useRef<HTMLButtonElement>(null);
  const dialogRef = useRef<HTMLElement>(null);
  const inFlight = useRef(false);
  const canRead = me.modules.includes("manufacturing");

  useEffect(() => {
    if (!canRead) return;
    const controller = new AbortController();
    setBatch({ status: "loading" });
    void fetchCompoundBatch(id, controller.signal)
      .then(data => { if (!controller.signal.aborted) setBatch({ status: "ready", data }); })
      .catch(value => { if (!controller.signal.aborted) setBatch({ status: "error", error: failure(value, "Batch detail could not be loaded.") }); });
    return () => controller.abort();
  }, [id, batchAttempt, canRead]);

  useEffect(() => {
    if (!canRead) return;
    const controller = new AbortController();
    setUsedIn({ status: "loading" });
    void fetchBatchUsedIn(id, controller.signal)
      .then(data => { if (!controller.signal.aborted) setUsedIn({ status: "ready", data }); })
      .catch(value => { if (!controller.signal.aborted) setUsedIn({ status: "error", error: failure(value, "Production use could not be loaded.") }); });
    return () => controller.abort();
  }, [id, usedInAttempt, canRead]);

  useEffect(() => {
    if (!action) return;
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    cancelRef.current?.focus();
    return () => { document.body.style.overflow = previousOverflow; };
  }, [action]);

  useEffect(() => { if (busy) dialogRef.current?.focus(); }, [busy]);

  const item = batch.status === "ready" ? batch.data : null;
  const inScope = item ? me.all_plants || me.unit_ids.includes(item.unit_id) : false;
  const canMove = canRead && inScope && me.permissions.includes("route.move");
  const canQa = canRead && inScope && me.permissions.includes("qa.approve");
  const canStart = canMove && item?.status === "WEIGHED";
  const canFinish = canMove && item?.status === "MIXING";
  const canDecide = canQa && item?.status === "AWAITING_QA";
  const allowed = (selected: Action) => selected === "start-mixing" ? canStart : selected === "mixed" ? canFinish : canDecide;

  function open(selected: Action, event: MouseEvent<HTMLButtonElement>) {
    if (inFlight.current || !allowed(selected)) return;
    if (selected === "qa") {
      const enteredHardness = hardness.trim();
      const enteredNote = note.trim();
      if (result === "PASSED" && !enteredHardness) {
        setFormError(new Error("Enter the measured hardness to approve a batch."));
        return;
      }
      if (result === "PASSED" && !hardnessValid(enteredHardness)) {
        setFormError(new Error("Hardness must be between 0 and 100 with at most two decimal places."));
        return;
      }
      if (result === "FAILED" && !enteredNote) {
        setFormError(new Error("A failed batch needs a note explaining why."));
        return;
      }
    }
    triggerRef.current = event.currentTarget;
    setFormError(null);
    setActionError(null);
    setNotice("");
    setAction(selected);
  }

  function close() {
    if (inFlight.current) return;
    setAction(null);
    setActionError(null);
    requestAnimationFrame(() => {
      if (triggerRef.current?.isConnected) triggerRef.current.focus();
      else headingRef.current?.focus();
    });
  }

  function dialogKeys(event: KeyboardEvent<HTMLElement>) {
    if (event.key === "Escape" && !inFlight.current) { event.preventDefault(); close(); return; }
    if (event.key !== "Tab") return;
    const buttons = Array.from(event.currentTarget.querySelectorAll<HTMLButtonElement>("button:not(:disabled)"));
    if (!buttons.length) { event.preventDefault(); return; }
    if (event.shiftKey && document.activeElement === buttons[0]) {
      event.preventDefault(); buttons[buttons.length - 1].focus();
    } else if (!event.shiftKey && document.activeElement === buttons[buttons.length - 1]) {
      event.preventDefault(); buttons[0].focus();
    }
  }

  async function confirm() {
    if (!item || !action || !allowed(action) || inFlight.current) return;
    const selected = action;
    inFlight.current = true;
    setBusy(true);
    setActionError(null);
    try {
      const body = selected === "qa" ? {
        result,
        hardness: hardness.trim() || null,
        note: note.trim() || null,
      } : undefined;
      const updated = await transitionBatch(id, selected, body);
      setBatch({ status: "ready", data: updated });
      setAction(null);
      setNotice(`Batch updated: ${label(updated.status)}.`);
      requestAnimationFrame(() => headingRef.current?.focus());
    } catch (value) {
      setActionError(failure(value, "Batch action could not be completed."));
    } finally {
      inFlight.current = false;
      setBusy(false);
    }
  }

  if (!canRead) return <div className="master-state" role="alert"><p>This route is outside your current module access.</p></div>;

  return <DetailPageTemplate
    className="compound-detail"
    navigation={<ManufacturingTabs active="batches" />}
    title={item?.batch_no ?? "Batch detail"}
    headingId="batch-detail-title"
    headingRef={headingRef}
    description="Weighing, mixing and quality status returned by the backend."
    back={<Link to="/app/manufacturing/batches"><ArrowLeft size={16} />Back to batches</Link>}
    status={item && <span className="status-badge">{label(item.status)}</span>}
    actions={item && <button className="button button--secondary" type="button" disabled={busy} onClick={() => setBatchAttempt(value => value + 1)}><RefreshCw size={16} />Refresh batch</button>}
  >
    {batch.status === "loading" && <div className="master-state" role="status"><span className="spinner" /><p>Loading batch detail…</p></div>}
    {batch.status === "error" && <section className="record-panel" style={{ minHeight: 0 }}><ErrorNotice error={batch.error} /><button className="button button--secondary" type="button" onClick={() => setBatchAttempt(value => value + 1)}>Try again</button></section>}
    {item && <>
      {notice && <p className="success-banner" role="status">{notice}</p>}
      <section className="record-panel" style={{ minHeight: 0 }} aria-label="Batch summary">
        <div className="record-panel__heading"><h3>Batch progress</h3><span className="status-badge">{label(item.status)}</span></div>
        <dl className="record-facts">
          <div><dt>Compound</dt><dd>{item.compound_code} — {item.compound_name}</dd></div>
          <div><dt>QA status</dt><dd>{label(item.qa_status)}</dd></div>
          <div><dt>Total weighed (kg)</dt><dd>{item.total_kg}</dd></div>
          <div><dt>Remaining (kg)</dt><dd>{item.remaining_kg}</dd></div>
          <div><dt>Weighed at</dt><dd>{date(item.weighed_at)}</dd></div>
          <div><dt>Mixed at</dt><dd>{date(item.mixed_at)}</dd></div>
          <div><dt>Age (hours)</dt><dd>{item.age_hours ?? "Not started"}</dd></div>
          <div><dt>Minimum maturation (hours)</dt><dd>{item.maturation_min_hours}</dd></div>
          <div><dt>Maximum age (hours)</dt><dd>{item.maximum_age_hours}</dd></div>
          <div><dt>Measured hardness</dt><dd>{item.hardness ?? "Not recorded"}</dd></div>
          <div><dt>QA decision at</dt><dd>{date(item.qa_at)}</dd></div>
          <div><dt>QA note</dt><dd>{item.qa_note ?? "Not recorded"}</dd></div>
        </dl>
      </section>

      <section className="record-panel" style={{ minHeight: 0, minWidth: 0 }} aria-label="Weighed ingredients">
        <div className="record-panel__heading"><h3>Weighed ingredients</h3></div>
        {item.weighing.length === 0 ? <p>No weighing lines were returned for this batch.</p> : <>
          <p>Target, actual and variance are shown for each source lot, exactly as recorded.</p>
          <div className="master-table-wrap" role="region" aria-label="Scrollable batch weighing lines" tabIndex={0}>
            <table className="master-table" aria-label="Batch weighing lines" style={{ minWidth: 680, tableLayout: "auto" }}>
              <thead><tr><th>Ingredient</th><th>Source lot</th><th>Target (kg)</th><th>Actual (kg)</th><th>Variance (kg)</th></tr></thead>
              <tbody>{item.weighing.map((line, index) => <tr key={`${line.item_code}-${line.lot_no}-${index}`}>
                <td>{line.item_code} — {line.item_name}</td><td>{line.lot_no}</td><td>{line.target_qty}</td><td>{line.actual_qty}</td><td>{line.variance}</td>
              </tr>)}</tbody>
            </table>
          </div>
          <ul className="master-cards" aria-label="Batch weighing lines on small screens">{item.weighing.map((line, index) => <li key={`${line.item_code}-${line.lot_no}-${index}`}>
            <div><strong>{line.item_code} — {line.item_name}</strong></div>
            <dl><div><dt>Source lot</dt><dd>{line.lot_no}</dd></div><div><dt>Target (kg)</dt><dd>{line.target_qty}</dd></div><div><dt>Actual (kg)</dt><dd>{line.actual_qty}</dd></div><div><dt>Variance (kg)</dt><dd>{line.variance}</dd></div></dl>
          </li>)}</ul>
        </>}
      </section>

      {(canStart || canFinish || canDecide) && <section className="record-panel" style={{ minHeight: 0 }} aria-label="Batch actions">
        <div className="record-panel__heading"><h3>Next action</h3></div>
        {canStart && <><p>Begin mixing the weighed batch.</p><button className="button button--primary" style={{ minHeight: 44 }} type="button" disabled={busy} onClick={event => open("start-mixing", event)}>Start mixing</button></>}
        {canFinish && <><p>Finish mixing to start the maturation clock.</p><button className="button button--primary" style={{ minHeight: 44 }} type="button" disabled={busy} onClick={event => open("mixed", event)}>Finish mixing</button></>}
        {canDecide && <>
          <p>Record a QA decision after maturation. If an in-process inspection plan applies, a manual passing inspection is required before approval.</p>
          <div className="record-form__grid">
            <div className="field"><label htmlFor="batch-qa-result">QA result</label><select id="batch-qa-result" value={result} onChange={event => { setResult(event.target.value as QaResult); setFormError(null); }}><option value="PASSED">Passed</option><option value="FAILED">Failed</option></select></div>
            <div className="field"><label htmlFor="batch-hardness">Measured hardness</label><input id="batch-hardness" inputMode="decimal" value={hardness} onChange={event => { setHardness(event.target.value); setFormError(null); }} aria-describedby="batch-hardness-hint" /><small id="batch-hardness-hint">Required for a pass; 0–100, at most two decimal places.</small></div>
            <div className="field"><label htmlFor="batch-qa-note">QA note</label><textarea id="batch-qa-note" maxLength={500} value={note} onChange={event => { setNote(event.target.value); setFormError(null); }} /><small>Required for a failed batch.</small></div>
          </div>
          {formError && <ErrorNotice error={formError} />}
          <button className="button button--primary" style={{ minHeight: 44 }} type="button" disabled={busy} onClick={event => open("qa", event)}>Record QA</button>
        </>}
      </section>}

      <section className="record-panel" style={{ minHeight: 0 }} aria-label="Production use">
        <div className="record-panel__heading"><h3>Used in production</h3><button className="button button--secondary" type="button" onClick={() => setUsedInAttempt(value => value + 1)}>Refresh use</button></div>
        {usedIn.status === "loading" && <p role="status">Loading production use…</p>}
        {usedIn.status === "error" && <><ErrorNotice error={usedIn.error} /><button className="button button--secondary" type="button" onClick={() => setUsedInAttempt(value => value + 1)}>Retry production use</button></>}
        {usedIn.status === "ready" && (usedIn.data.length === 0 ? <p>No production lots were returned for this batch.</p>
          : <ul aria-label="Production lots using this batch">{usedIn.data.map(lot => <li key={lot.prod_lot_id}>
            <strong>{lot.lot_no}</strong><dl className="record-facts"><div><dt>Work order</dt><dd>{lot.work_order_no}</dd></div><div><dt>Item</dt><dd>{lot.item_code}</dd></div><div><dt>Quantity</dt><dd>{lot.quantity}</dd></div><div><dt>Stage</dt><dd>{lot.stage_code} — {lot.stage_name}</dd></div><div><dt>Status</dt><dd>{label(lot.status)}</dd></div></dl>
          </li>)}</ul>)}
      </section>
    </>}

    {action && item && <div className="bom-editor-backdrop">
      <section ref={dialogRef} className="bom-editor" role="dialog" aria-modal="true" aria-labelledby="batch-action-title" aria-describedby="batch-action-description" tabIndex={-1} onKeyDown={dialogKeys} style={{ width: "min(520px, 100%)" }}>
        <div className="bom-editor__header"><div><p className="eyebrow">{item.batch_no}</p><h3 id="batch-action-title">{actionName[action]}</h3><p id="batch-action-description">{action === "start-mixing" ? "Record the start of mixing for this weighed batch?" : action === "mixed" ? "Finish mixing and start maturation?" : `Record ${result.toLowerCase()} QA${result === "PASSED" ? ` with measured hardness ${hardness.trim()}` : `: ${note.trim()}`}?`}</p></div></div>
        <div className="bom-editor__body">{actionError && <ErrorNotice error={actionError} />}</div>
        <div className="bom-editor__actions"><button ref={cancelRef} className="button button--secondary" type="button" disabled={busy} onClick={close}>Cancel</button><button className="button button--primary" type="button" disabled={busy} onClick={() => void confirm()}>{busy ? "Working…" : `Confirm ${actionName[action]}`}</button></div>
      </section>
    </div>}
  </DetailPageTemplate>;
}
