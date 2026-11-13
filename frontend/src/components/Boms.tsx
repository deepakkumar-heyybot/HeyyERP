import { FormEvent, useEffect, useState } from "react";
import { AlertCircle, ArrowLeft, ArrowUpRight, Boxes, ChevronLeft, ChevronRight, Plus, RefreshCw } from "lucide-react";
import { Link, useLocation, useNavigate, useSearchParams } from "react-router-dom";
import { ApiError, Item, ItemBom, Me, Page, createBom, fetchAllBoms, fetchAllMadeItems, listBomsPage } from "../api-client";
import { EngineeringTabs } from "./EngineeringTabs";
import { BomDetail } from "./BomDetail";

const PAGE_SIZE = 20;
const STATUSES = [
  { value: "DRAFT", label: "Draft" }, { value: "PENDING_APPROVAL", label: "Pending approval" },
  { value: "APPROVED", label: "Approved" }, { value: "REJECTED", label: "Rejected" },
  { value: "SUPERSEDED", label: "Superseded" },
];
type ListState = { status: "loading" } | { status: "error"; error: Error } | { status: "ready"; boms: ItemBom[]; total: number };
type ItemsState = { status: "loading" } | { status: "error"; error: Error } | { status: "ready"; items: Item[] };
const errorOf = (error: unknown, fallback: string) => error instanceof Error ? error : new Error(fallback);
const statusLabel = (value: string) => STATUSES.find(status => status.value === value)?.label ?? value;
const statusTone = (value: string) => value === "APPROVED" ? "approved" : value === "PENDING_APPROVAL" ? "pending" : value === "REJECTED" ? "rejected" : "cancelled";
function RequestError({ error }: { error: Error }) {
  return <div className="decision-error" role="alert"><AlertCircle size={17} /><div><span>{error.message}</span>{error instanceof ApiError && error.status >= 500 && error.requestId && <span>Request ID: {error.requestId}</span>}</div></div>;
}

function BomList({ me }: { me: Me }) {
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const itemId = searchParams.get("item") || null;
  const rawStatus = searchParams.get("status") || null;
  const bomStatus = STATUSES.some(status => status.value === rawStatus) ? rawStatus : null;
  const from = searchParams.get("from") ?? "";
  const through = searchParams.get("through") ?? "";
  const currentPage = Math.max(1, Number(searchParams.get("page") ?? "1") || 1);
  const [state, setState] = useState<ListState>({ status: "loading" });
  const [itemsState, setItemsState] = useState<ItemsState>({ status: "loading" });
  const [attempt, setAttempt] = useState(0);
  const canManage = me.permissions.includes("bom.manage");

  useEffect(() => {
    const controller = new AbortController();
    void fetchAllMadeItems(controller.signal)
      .then(items => { if (!controller.signal.aborted) setItemsState({ status: "ready", items }); })
      .catch(error => { if (!controller.signal.aborted) setItemsState({ status: "error", error: errorOf(error, "BOM items could not be loaded.") }); });
    return () => controller.abort();
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    setState({ status: "loading" });
    void Promise.resolve().then(async () => {
      if (controller.signal.aborted) return;
      try {
        if (from || through) {
          const all = await fetchAllBoms(itemId, bomStatus, controller.signal);
          const filtered = all.filter(bom => (!from || bom.valid_from >= from) && (!through || bom.valid_from <= through));
          if (!controller.signal.aborted) setState({ status: "ready", boms: filtered.slice((currentPage - 1) * PAGE_SIZE, currentPage * PAGE_SIZE), total: filtered.length });
        } else {
          const page: Page<ItemBom> = await listBomsPage({ itemId, status: bomStatus, limit: PAGE_SIZE, offset: (currentPage - 1) * PAGE_SIZE }, controller.signal);
          if (!controller.signal.aborted) setState({ status: "ready", boms: page.items, total: page.total });
        }
      } catch (error) { if (!controller.signal.aborted) setState({ status: "error", error: errorOf(error, "BOMs could not be loaded.") }); }
    });
    return () => controller.abort();
  }, [attempt, bomStatus, currentPage, from, itemId, through]);

  function setParam(name: string, value: string, resetPage = true) {
    const next = new URLSearchParams(window.location.search);
    if (value) next.set(name, value); else next.delete(name);
    if (resetPage) next.delete("page");
    setSearchParams(next, { replace: true });
  }
  const boms = state.status === "ready" ? state.boms : [];
  const total = state.status === "ready" ? state.total : 0;
  const first = total ? (currentPage - 1) * PAGE_SIZE + 1 : 0;
  const last = Math.min(currentPage * PAGE_SIZE, total);
  const maxPage = Math.max(1, Math.ceil(total / PAGE_SIZE));
  const backQuery = searchParams.toString();
  const detailHref = (id: string) => `/app/master-data/engineering/boms/${id}${backQuery ? `?${backQuery}` : ""}`;

  return <section className="master-list bom-list" aria-labelledby="boms-title">
    <EngineeringTabs active="boms" />
    <div className="page-heading-row"><div><h2 id="boms-title">Bills of material</h2><p>Revisions, approval states, and validity dates for manufactured items.</p></div>{canManage && <button className="button button--primary" type="button" onClick={() => navigate(`/app/master-data/engineering/boms/new${backQuery ? `?${backQuery}` : ""}`)}><Plus size={17} />Create BOM</button>}</div>
    <div className="master-filters bom-filters"><div className="field"><label htmlFor="bom-item">BOM item</label><select id="bom-item" value={itemId ?? ""} onChange={event => setParam("item", event.target.value)}><option value="">All made items</option>{itemsState.status === "ready" && itemsState.items.map(item => <option key={item.item_id} value={item.item_id}>{item.code} — {item.name}</option>)}</select></div><div className="field"><label htmlFor="bom-status">BOM status</label><select id="bom-status" value={bomStatus ?? ""} onChange={event => setParam("status", event.target.value)}><option value="">All statuses</option>{STATUSES.map(status => <option key={status.value} value={status.value}>{status.label}</option>)}</select></div><div className="field"><label htmlFor="bom-from">Valid from start</label><input id="bom-from" type="date" value={from} onChange={event => setParam("from", event.target.value)} /></div><div className="field"><label htmlFor="bom-through">Valid from end</label><input id="bom-through" type="date" value={through} min={from || undefined} onChange={event => setParam("through", event.target.value)} /></div><button className="button button--secondary" type="button" disabled={state.status === "loading"} onClick={() => setAttempt(value => value + 1)}><RefreshCw size={16} />Refresh</button></div>
    {itemsState.status === "error" && <RequestError error={itemsState.error} />}
    {(from || through) && <p className="bom-list__date-note">Valid-from dates are filtered across all matching backend pages.</p>}
    {state.status === "loading" && <div className="master-state"><span className="spinner" /><p>Loading BOMs…</p></div>}
    {state.status === "error" && <div className="inline-error" role="alert"><AlertCircle size={20} /><div><h3>BOM register unavailable</h3><p>{state.error.message}</p>{state.error instanceof ApiError && state.error.status >= 500 && state.error.requestId && <p>Request ID: {state.error.requestId}</p>}</div><button className="button button--secondary" type="button" onClick={() => setAttempt(value => value + 1)}>Try again</button></div>}
    {state.status === "ready" && boms.length === 0 && <div className="master-state"><Boxes size={28} /><h3>No BOMs found</h3><p>{itemId || bomStatus || from || through ? "Adjust the filters to see other revisions." : "No bills of material were returned."}</p></div>}
    {state.status === "ready" && boms.length > 0 && <><p className="bom-scroll-hint">Scroll table horizontally to view more columns.</p><div className="master-table-wrap bom-table-wrap" role="region" aria-label="Scrollable BOM results" tabIndex={0}><table className="master-table bom-table" aria-label="BOM results"><thead><tr><th>Item code</th><th>Item</th><th>Revision</th><th>Status</th><th>Valid from</th><th>Valid to</th><th><span className="sr-only">Actions</span></th></tr></thead><tbody>{boms.map(bom => <tr key={bom.bom_id}><td><strong>{bom.item_code}</strong></td><td>{bom.item_name}</td><td>Revision {bom.version}</td><td><span className={`status-badge status-badge--${statusTone(bom.status)}`}>{statusLabel(bom.status)}</span></td><td>{bom.valid_from}</td><td>{bom.valid_to ?? "No end date"}</td><td><Link className="icon-link" to={detailHref(bom.bom_id)} aria-label={`Open BOM ${bom.item_code} revision ${bom.version}`}><ArrowUpRight size={17} /></Link></td></tr>)}</tbody></table></div><ul className="master-cards" aria-label="BOM results">{boms.map(bom => <li key={bom.bom_id}><div><span>{bom.item_code}</span><span className={`status-badge status-badge--${statusTone(bom.status)}`}>{statusLabel(bom.status)}</span></div><h3>{bom.item_name}</h3><dl><div><dt>Revision</dt><dd>Revision {bom.version}</dd></div><div><dt>Valid from</dt><dd>{bom.valid_from}</dd></div><div><dt>Valid to</dt><dd>{bom.valid_to ?? "No end date"}</dd></div></dl><Link className="button button--secondary" to={detailHref(bom.bom_id)} aria-label={`Open BOM ${bom.item_code} revision ${bom.version}`}>Open BOM<ArrowUpRight size={16} /></Link></li>)}</ul><div className="reference-pagination"><p className="result-summary" aria-live="polite">Showing {first}–{last} of {total} records</p>{total > PAGE_SIZE && <div><button className="button button--secondary" type="button" aria-label="Previous page" disabled={currentPage === 1} onClick={() => setParam("page", String(currentPage - 1), false)}><ChevronLeft size={16} /></button><span>Page {currentPage} of {maxPage}</span><button className="button button--secondary" type="button" aria-label="Next page" disabled={currentPage === maxPage} onClick={() => setParam("page", String(currentPage + 1), false)}><ChevronRight size={16} /></button></div>}</div></>}
  </section>;
}

function BomCreate() {
  const navigate = useNavigate();
  const location = useLocation();
  const [itemsState, setItemsState] = useState<ItemsState>({ status: "loading" });
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<Error | null>(null);
  const back = `/app/master-data/engineering/boms${location.search}`;
  useEffect(() => {
    const controller = new AbortController();
    void fetchAllMadeItems(controller.signal)
      .then(items => { if (!controller.signal.aborted) setItemsState({ status: "ready", items }); })
      .catch(problem => { if (!controller.signal.aborted) setItemsState({ status: "error", error: errorOf(problem, "Made items could not be loaded.") }); });
    return () => controller.abort();
  }, []);
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    setSaving(true); setError(null);
    try { const bom = await createBom({ item_id: String(data.get("item_id") ?? ""), lines: [] }); navigate(`/app/master-data/engineering/boms/${bom.bom_id}${location.search}`); }
    catch (problem) { setError(errorOf(problem, "The BOM draft could not be created.")); }
    finally { setSaving(false); }
  }
  return <section className="master-detail bom-create" aria-labelledby="bom-create-title"><div className="master-detail__heading"><div><Link to={back}><ArrowLeft size={16} />Back to BOMs</Link><h2 id="bom-create-title">Create BOM</h2></div></div>{itemsState.status === "loading" && <div className="master-state"><span className="spinner" /><p>Loading made items…</p></div>}{itemsState.status === "error" && <RequestError error={itemsState.error} />}{itemsState.status === "ready" && <div className="form-panel"><form className="record-form" onSubmit={submit}><div className="record-form__heading"><div><h3>New draft</h3><p>Only an active work-in-progress or finished item can have a BOM. Add components on the draft detail page.</p></div></div>{itemsState.items.length === 0 ? <div className="master-state"><Boxes size={26} /><h3>No eligible items</h3><p>Create an active WIP or finished item first.</p></div> : <div className="field"><label htmlFor="new-bom-item">BOM item</label><select id="new-bom-item" name="item_id" required defaultValue=""><option value="" disabled>Select made item</option>{itemsState.items.map(item => <option key={item.item_id} value={item.item_id}>{item.code} — {item.name}</option>)}</select></div>}{error && <RequestError error={error} />}<div className="record-form__actions"><Link className="button button--secondary" to={back}>Cancel</Link><button className="button button--primary" type="submit" disabled={saving || itemsState.items.length === 0}>{saving ? "Creating…" : "Create draft"}</button></div></form></div>}</section>;
}

export function Boms({ me }: { me: Me }) {
  const location = useLocation();
  const prefix = "/app/master-data/engineering/boms";
  if (location.pathname === `${prefix}/new`) return me.permissions.includes("bom.manage") ? <BomCreate /> : <BomList me={me} />;
  if (location.pathname.startsWith(`${prefix}/`)) return <BomDetail bomId={location.pathname.slice(prefix.length + 1)} me={me} />;
  return <BomList me={me} />;
}
