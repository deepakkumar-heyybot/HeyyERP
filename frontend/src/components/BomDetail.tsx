import { FormEvent, KeyboardEvent, useEffect, useRef, useState } from "react";
import { AlertCircle, ArrowLeft, Boxes, ChevronRight, FileClock, Plus, X } from "lucide-react";
import { Link, useLocation, useNavigate } from "react-router-dom";
import {
  ApiError, Item, ItemBom, Me, fetchAllBomComponents, fetchAllBoms, fetchBom,
  reviseBom, submitBom, updateBomLines,
} from "../api-client";

type DetailState = { status: "loading" } | { status: "error"; error: Error } | { status: "ready"; bom: ItemBom };
type HistoryState = { status: "loading" } | { status: "error"; error: Error } | { status: "ready"; boms: ItemBom[] };
type ComponentsState = { status: "idle" | "loading" } | { status: "error"; error: Error } | { status: "ready"; items: Item[] };
type LineInput = { component_item_id: string; quantity_per: string; scrap_factor: string };
const problem = (error: unknown, fallback: string) => error instanceof Error ? error : new Error(fallback);
const statusLabel = (status: string) => ({ DRAFT: "Draft", PENDING_APPROVAL: "Pending approval", APPROVED: "Approved", REJECTED: "Rejected", SUPERSEDED: "Superseded" })[status as "DRAFT"] ?? status;
const statusTone = (status: string) => status === "APPROVED" ? "approved" : status === "PENDING_APPROVAL" ? "pending" : status === "REJECTED" ? "rejected" : "cancelled";

function RequestError({ error }: { error: Error }) {
  return <div className="decision-error" role="alert"><AlertCircle size={17} /><div><span>{error.message}</span>{error instanceof ApiError && error.status >= 500 && error.requestId && <span>Request ID: {error.requestId}</span>}</div></div>;
}

export function BomDetail({ bomId, me }: { bomId: string; me: Me }) {
  const navigate = useNavigate();
  const location = useLocation();
  const [state, setState] = useState<DetailState>({ status: "loading" });
  const [history, setHistory] = useState<HistoryState>({ status: "loading" });
  const [reload, setReload] = useState(0);
  const [editorOpen, setEditorOpen] = useState(false);
  const [lines, setLines] = useState<LineInput[]>([]);
  const [components, setComponents] = useState<ComponentsState>({ status: "idle" });
  const [submitting, setSubmitting] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<Error | null>(null);
  const [notice, setNotice] = useState("");
  const editRef = useRef<HTMLButtonElement>(null);
  const closeRef = useRef<HTMLButtonElement>(null);
  const canManage = me.permissions.includes("bom.manage");
  const back = `/app/master-data/engineering/boms${location.search}`;

  useEffect(() => {
    const controller = new AbortController();
    setState({ status: "loading" }); setHistory({ status: "loading" }); setNotice(""); setError(null);
    void fetchBom(bomId, controller.signal).then(bom => {
      if (controller.signal.aborted) return;
      setState({ status: "ready", bom });
      void fetchAllBoms(bom.item_id, null, controller.signal)
        .then(boms => { if (!controller.signal.aborted) setHistory({ status: "ready", boms }); })
        .catch(error => { if (!controller.signal.aborted) setHistory({ status: "error", error: problem(error, "Revision history could not be loaded.") }); });
    }).catch(error => { if (!controller.signal.aborted) setState({ status: "error", error: problem(error, "BOM detail could not be loaded.") }); });
    return () => controller.abort();
  }, [bomId, reload]);

  useEffect(() => {
    if (!editorOpen) return;
    const controller = new AbortController();
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    closeRef.current?.focus();
    setComponents({ status: "loading" });
    void fetchAllBomComponents(controller.signal)
      .then(items => { if (!controller.signal.aborted) setComponents({ status: "ready", items }); })
      .catch(error => { if (!controller.signal.aborted) setComponents({ status: "error", error: problem(error, "Component items could not be loaded.") }); });
    return () => { controller.abort(); document.body.style.overflow = previousOverflow; };
  }, [editorOpen]);

  function openEditor() {
    if (state.status !== "ready") return;
    setLines(state.bom.lines.map(line => ({ component_item_id: line.component_item_id, quantity_per: line.quantity_per, scrap_factor: line.scrap_factor })));
    setError(null); setEditorOpen(true);
  }
  function closeEditor() { setEditorOpen(false); setError(null); editRef.current?.focus(); }
  function editorKeys(event: KeyboardEvent<HTMLElement>) {
    if (event.key === "Escape") { event.preventDefault(); closeEditor(); return; }
    if (event.key !== "Tab") return;
    const focusable = Array.from(event.currentTarget.querySelectorAll<HTMLElement>('button:not(:disabled), input:not(:disabled), select:not(:disabled)'));
    if (!focusable.length) return;
    const first = focusable[0], last = focusable[focusable.length - 1];
    if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
    if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
  }
  function changeLine(index: number, key: keyof LineInput, value: string) {
    setLines(current => current.map((line, row) => row === index ? { ...line, [key]: value } : line));
  }

  async function saveLines(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setSaving(true); setError(null);
    try {
      const bom = await updateBomLines(bomId, lines);
      setState({ status: "ready", bom });
      setHistory(current => current.status === "ready" ? { status: "ready", boms: current.boms.map(revision => revision.bom_id === bomId ? bom : revision) } : current);
      setEditorOpen(false); editRef.current?.focus(); setNotice("Draft lines saved.");
    } catch (error) { setError(problem(error, "Draft lines could not be saved.")); }
    finally { setSaving(false); }
  }

  async function sendForApproval(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    setSaving(true); setError(null);
    try {
      const bom = await submitBom(bomId, { reason: String(data.get("reason") ?? "").trim(), effective_from: String(data.get("effective_from") ?? "") });
      setState({ status: "ready", bom });
      setHistory(current => current.status === "ready" ? { status: "ready", boms: current.boms.map(revision => revision.bom_id === bomId ? bom : revision) } : current);
      setSubmitting(false); setNotice("BOM submitted for approval.");
    } catch (error) { setError(problem(error, "BOM could not be submitted.")); }
    finally { setSaving(false); }
  }

  async function revise() {
    setSaving(true); setError(null);
    try { const bom = await reviseBom(bomId); navigate(`/app/master-data/engineering/boms/${bom.bom_id}${location.search}`); }
    catch (error) { setError(problem(error, "BOM could not be revised.")); }
    finally { setSaving(false); }
  }

  if (state.status === "loading") return <section className="master-detail"><div className="master-state"><span className="spinner" /><p>Loading BOM detail…</p></div></section>;
  if (state.status === "error") return <section className="master-detail"><div className="inline-error" role="alert"><AlertCircle size={20} /><div><h3>BOM detail unavailable</h3><p>{state.error.message}</p>{state.error instanceof ApiError && state.error.status >= 500 && state.error.requestId && <p>Request ID: {state.error.requestId}</p>}</div><button className="button button--secondary" type="button" onClick={() => setReload(value => value + 1)}>Try again</button></div></section>;

  const bom = state.bom;
  const isDraft = bom.status === "DRAFT";
  const mayRevise = bom.status === "APPROVED" || bom.status === "SUPERSEDED";
  const candidateItems = components.status === "ready" ? components.items.filter(item => item.item_id !== bom.item_id) : [];

  return <section className="master-detail bom-detail" aria-labelledby="bom-detail-title">
    <div className="master-detail__heading"><div><Link to={back}><ArrowLeft size={16} />Back to BOMs</Link><p className="eyebrow">{bom.item_code} · Revision {bom.version}</p><h2 id="bom-detail-title">{bom.item_name}</h2><span className={`status-badge status-badge--${statusTone(bom.status)}`}>{statusLabel(bom.status)}</span></div>{canManage && <div>{isDraft && <><button className="button button--secondary" type="button" ref={editRef} onClick={openEditor}>Edit lines</button><button className="button button--primary" type="button" disabled={bom.lines.length === 0} onClick={() => { setSubmitting(true); setError(null); }}>Submit for approval</button></>}{mayRevise && <button className="button button--primary" type="button" disabled={saving} onClick={revise}>{saving ? "Revising…" : "Revise BOM"}</button>}</div>}</div>
    {notice && <p className="success-banner" role="status">{notice}</p>}
    {error && !editorOpen && !submitting && <RequestError error={error} />}
    <section className="record-panel bom-detail__summary"><div className="record-panel__heading"><div><h3>Revision summary</h3><p>Dates and approval state returned by the backend.</p></div><FileClock size={20} /></div><dl className="record-facts bom-detail__facts"><div><dt>Item code</dt><dd>{bom.item_code}</dd></div><div><dt>Revision</dt><dd>Revision {bom.version}</dd></div><div><dt>Status</dt><dd>{statusLabel(bom.status)}</dd></div><div><dt>Valid from</dt><dd>{bom.valid_from}</dd></div><div><dt>Valid to</dt><dd>{bom.valid_to ?? "No end date"}</dd></div><div><dt>Approval</dt><dd>{bom.approval_id ? "Approval linked" : "Not linked"}</dd></div></dl></section>
    <section className="record-panel bom-detail__components"><div className="record-panel__heading"><div><h3>Components</h3><p>{isDraft ? "Draft lines can be replaced before submission." : "This revision is read-only."}</p></div></div>{bom.lines.length === 0 ? <div className="master-state bom-detail__empty"><Boxes size={25} /><h3>No components</h3><p>Add draft lines before submitting for approval.</p></div> : <div className="bom-detail__lines">{bom.lines.map(line => <div key={line.line_no} className="bom-detail__line"><span className="bom-detail__line-number">{line.line_no}</span><div><strong>{line.component_code}</strong><span>{line.component_name}</span></div><dl><div><dt>Quantity per</dt><dd>{line.quantity_per} {line.uom_code}</dd></div><div><dt>Scrap factor</dt><dd>{line.scrap_factor}</dd></div></dl></div>)}</div>}</section>
    {submitting && isDraft && <section className="form-panel"><form className="record-form" onSubmit={sendForApproval}><div className="record-form__heading"><div><h3>Submit for approval</h3><p>Explain the engineering change and choose its requested effective date.</p></div></div><div className="record-form__grid"><div className="field"><label htmlFor="bom-reason">Change reason</label><textarea id="bom-reason" name="reason" required minLength={5} maxLength={500} rows={4} /></div><div className="field"><label htmlFor="bom-effective">Effective from</label><input id="bom-effective" name="effective_from" type="date" required /></div></div>{error && <RequestError error={error} />}<div className="record-form__actions"><button className="button button--secondary" type="button" onClick={() => { setSubmitting(false); setError(null); }}>Cancel</button><button className="button button--primary" type="submit" disabled={saving}>{saving ? "Submitting…" : "Confirm submission"}</button></div></form></section>}
    <section className="record-panel bom-detail__history"><div className="record-panel__heading"><div><h3>Revision history</h3><p>Open earlier and later versions of this item.</p></div></div>{history.status === "loading" && <p>Loading revision history…</p>}{history.status === "error" && <RequestError error={history.error} />}{history.status === "ready" && <div className="item-detail__rows">{history.boms.map(revision => <Link className="bom-detail__history-link" key={revision.bom_id} to={`/app/master-data/engineering/boms/${revision.bom_id}${location.search}`} aria-current={revision.bom_id === bomId ? "page" : undefined}><strong>Revision {revision.version}</strong><span>{statusLabel(revision.status)}</span><span>{revision.valid_from}</span><ChevronRight size={17} /></Link>)}</div>}</section>
    {editorOpen && isDraft && <div className="bom-editor-backdrop"><section className="bom-editor" role="dialog" aria-modal="true" aria-labelledby="bom-editor-title" onKeyDown={editorKeys}><div className="bom-editor__header"><div><p className="eyebrow">{bom.item_code} · Revision {bom.version}</p><h3 id="bom-editor-title">Edit draft lines</h3><p>Replace the component list for this draft. Quantity and scrap values are sent exactly as entered.</p></div><button className="button button--secondary" type="button" ref={closeRef} onClick={closeEditor} aria-label="Close line editor"><X size={18} /></button></div><form onSubmit={saveLines}><div className="bom-editor__body">{components.status === "loading" && <p>Loading component choices…</p>}{components.status === "error" && <RequestError error={components.error} />}{lines.map((line, index) => <fieldset key={index} className="bom-editor__line"><legend>Component {index + 1}</legend><div className="record-form__grid"><div className="field"><label htmlFor={`bom-component-${index}`}>Component item</label><select id={`bom-component-${index}`} required value={line.component_item_id} onChange={event => changeLine(index, "component_item_id", event.target.value)}><option value="" disabled>Select component</option>{candidateItems.filter(item => item.item_id === line.component_item_id || !lines.some((other, row) => row !== index && other.component_item_id === item.item_id)).map(item => <option key={item.item_id} value={item.item_id}>{item.code} — {item.name}</option>)}{line.component_item_id && !candidateItems.some(item => item.item_id === line.component_item_id) && <option value={line.component_item_id}>{bom.lines.find(row => row.component_item_id === line.component_item_id)?.component_code ?? "Existing component"}</option>}</select></div><div className="field"><label htmlFor={`bom-qty-${index}`}>Quantity per</label><input id={`bom-qty-${index}`} inputMode="decimal" required pattern="\d+(\.\d{1,6})?" value={line.quantity_per} onChange={event => changeLine(index, "quantity_per", event.target.value)} /></div><div className="field"><label htmlFor={`bom-scrap-${index}`}>Scrap factor</label><input id={`bom-scrap-${index}`} inputMode="decimal" required pattern="0(\.\d{1,6})?" value={line.scrap_factor} onChange={event => changeLine(index, "scrap_factor", event.target.value)} /></div></div><button className="button button--secondary" type="button" onClick={() => setLines(current => current.filter((_, row) => row !== index))}>Remove row</button></fieldset>)}<button className="button button--secondary" type="button" disabled={lines.length >= 200} onClick={() => setLines(current => [...current, { component_item_id: "", quantity_per: "", scrap_factor: "0" }])}><Plus size={16} />Add component</button>{error && <RequestError error={error} />}</div><div className="bom-editor__actions"><button className="button button--secondary" type="button" onClick={closeEditor}>Cancel</button><button className="button button--primary" type="submit" disabled={saving || components.status === "loading"}>{saving ? "Saving…" : "Save lines"}</button></div></form></section></div>}
  </section>;
}
