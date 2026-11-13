import { FormEvent, useEffect, useMemo, useState } from "react";
import { AlertCircle, ArrowLeft, ArrowUpRight, ChevronLeft, ChevronRight, ClipboardList, Plus, RefreshCw } from "lucide-react";
import { Link, useLocation, useNavigate, useSearchParams } from "react-router-dom";
import {
  ApiError, BusinessRule, Item, ItemType, Me, Rfq, RfqComparison, RfqCreate as RfqCreateBody, RfqSummary, Unit,
  createRfq, fetchAllActiveItemsOfTypes, fetchAllOrganizationUnits, fetchAllRfqs, fetchRfq, fetchRfqComparison, listRfqsPage,
} from "../api-client";
import { useActivePlant } from "../plant-context";
import { ProcurementTabs } from "./ProcurementTabs";
import { RfqActions } from "./RfqActions";
import { RequestError } from "./RequestError";

const PATH = "/app/procurement/rfqs";
const PAGE_SIZE = 20;
const STATUSES = ["DRAFT", "SENT", "AWARDED", "CLOSED", "CANCELLED"];
const ALL_ITEM_TYPES: ItemType[] = ["RM", "WIP", "FG", "SPARE", "CONSUMABLE", "CAPITAL", "OTHER"];
type Resource<T> = { status: "loading" } | { status: "error"; error: Error } | { status: "ready"; data: T };
type RfqListState = { status: "loading" } | { status: "error"; error: Error } | { status: "ready"; rows: RfqSummary[]; total: number };
type LineDraft = { key: number; itemId: string; quantity: string; requiredBy: string };
const blankLine = (key: number): LineDraft => ({ key, itemId: "", quantity: "", requiredBy: "" });
const problem = (error: unknown, fallback: string) => error instanceof Error ? error : new Error(fallback);
const labelStatus = (value: string) => value.toLowerCase().replaceAll("_", " ").replace(/^./, letter => letter.toUpperCase());
const statusTone = (value: string) => value === "AWARDED" ? "approved" : value === "SENT" ? "pending" : "cancelled";
const validQuantity = (value: string) => /^\d{1,9}(?:\.\d{1,3})?$/.test(value) && !/^0+(?:\.0+)?$/.test(value);
const localToday = () => { const date = new Date(); return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}-${String(date.getDate()).padStart(2, "0")}`; };
const dueLabel = (rfq: RfqSummary) => !rfq.response_due_at ? "No response due date" : !["DRAFT", "SENT"].includes(rfq.status) ? "No active response deadline" : rfq.status === "SENT" && new Date(rfq.response_due_at).getTime() < Date.now() ? "Response overdue" : rfq.status === "DRAFT" ? "Planned response due" : "Response due";

function RfqList({ me }: { me: Me }) {
  const navigate = useNavigate();
  const { activePlantId } = useActivePlant();
  const unitId = activePlantId === "all" ? null : activePlantId;
  const [searchParams, setSearchParams] = useSearchParams();
  const rawStatus = searchParams.get("status") ?? "";
  const status = STATUSES.includes(rawStatus) ? rawStatus : null;
  const from = searchParams.get("from") ?? "";
  const through = searchParams.get("through") ?? "";
  const hasDateFilter = Boolean(from || through);
  const currentPage = Math.max(1, Number(searchParams.get("page") ?? "1") || 1);
  const [state, setState] = useState<RfqListState>({ status: "loading" });
  const [counts, setCounts] = useState<Record<string, Resource<number>>>({});
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    setState({ status: "loading" });
    const request = hasDateFilter
      ? fetchAllRfqs(status, unitId, controller.signal).then(rows => ({ rows, total: rows.length }))
      : listRfqsPage({ status, unitId, limit: PAGE_SIZE, offset: (currentPage - 1) * PAGE_SIZE }, controller.signal).then(page => ({ rows: page.items, total: page.total }));
    void request.then(result => { if (!controller.signal.aborted) setState({ status: "ready", ...result }); })
      .catch(error => { if (!controller.signal.aborted) setState({ status: "error", error: problem(error, "RFQs could not be loaded.") }); });
    return () => controller.abort();
  }, [attempt, currentPage, hasDateFilter, status, unitId]);

  const filtered = useMemo(() => state.status !== "ready" ? [] : hasDateFilter ? state.rows.filter(row =>
    row.response_due_at && (!from || row.response_due_at.slice(0, 10) >= from) && (!through || row.response_due_at.slice(0, 10) <= through)) : state.rows,
  [from, hasDateFilter, state, through]);
  const total = state.status === "ready" ? hasDateFilter ? filtered.length : state.total : 0;
  const visible = hasDateFilter ? filtered.slice((currentPage - 1) * PAGE_SIZE, currentPage * PAGE_SIZE) : filtered;
  const visibleIds = visible.map(row => row.rfq_id).join("|");
  useEffect(() => {
    if (!visibleIds) return;
    const controller = new AbortController();
    const ids = visibleIds.split("|");
    setCounts(current => ({ ...current, ...Object.fromEntries(ids.map(id => [id, { status: "loading" }])) }));
    void Promise.all(ids.map(async id => {
      try { const rfq = await fetchRfq(id, controller.signal); return [id, { status: "ready", data: rfq.invitations.length }] as const; }
      catch (error) { return [id, { status: "error", error: problem(error, "Vendor count unavailable.") }] as const; }
    })).then(entries => { if (!controller.signal.aborted) setCounts(current => ({ ...current, ...Object.fromEntries(entries) })); });
    return () => controller.abort();
  }, [attempt, visibleIds]);

  function setParam(key: string, value: string, resetPage = true) {
    const next = new URLSearchParams(window.location.search);
    if (value) next.set(key, value); else next.delete(key);
    if (resetPage) next.delete("page");
    setSearchParams(next, { replace: true });
  }
  const backQuery = searchParams.toString();
  const href = (id: string) => `${PATH}/${id}${backQuery ? `?${backQuery}` : ""}`;
  const first = total ? (currentPage - 1) * PAGE_SIZE + 1 : 0;
  const last = Math.min(currentPage * PAGE_SIZE, total);
  const maxPage = Math.max(1, Math.ceil(total / PAGE_SIZE));
  useEffect(() => {
    if (state.status !== "ready" || currentPage <= maxPage) return;
    const next = new URLSearchParams(searchParams);
    next.set("page", String(maxPage));
    setSearchParams(next, { replace: true });
  }, [currentPage, maxPage, searchParams, setSearchParams, state.status]);
  const vendorCount = (id: string) => counts[id]?.status === "ready" ? `${counts[id].data} ${counts[id].data === 1 ? "vendor" : "vendors"}` : counts[id]?.status === "error" ? "Count unavailable" : "Loading count…";
  const vendorError = visible.map(row => counts[row.rfq_id]).find(value => value?.status === "error");

  return <section className="master-list rfq-list" aria-labelledby="rfqs-title"><ProcurementTabs active="rfqs" />
    <div className="page-heading-row"><div><h2 id="rfqs-title">Requests for quotation</h2><p>Track vendor invitations, response deadlines, and quotation status.</p></div>{me.permissions.includes("po.create") && <button className="button button--primary" type="button" onClick={() => navigate(`${PATH}/new${backQuery ? `?${backQuery}` : ""}`)}><Plus size={17} />New RFQ</button>}</div>
    <div className="master-filters rfq-filters"><div className="field"><label htmlFor="rfq-status">RFQ status</label><select id="rfq-status" value={status ?? ""} onChange={event => setParam("status", event.target.value)}><option value="">All statuses</option>{STATUSES.map(value => <option key={value} value={value}>{labelStatus(value)}</option>)}</select></div><div className="field"><label htmlFor="rfq-from">Due from</label><input id="rfq-from" type="date" value={from} onChange={event => setParam("from", event.target.value)} /></div><div className="field"><label htmlFor="rfq-through">Due through</label><input id="rfq-through" type="date" value={through} min={from || undefined} onChange={event => setParam("through", event.target.value)} /></div><button className="button button--secondary" type="button" disabled={state.status === "loading"} onClick={() => setAttempt(value => value + 1)}><RefreshCw size={16} />Refresh</button></div>
    {hasDateFilter && <p className="indent-list__filter-note">Response due dates are filtered across all matching backend pages.</p>}
    {state.status === "loading" && <div className="master-state"><span className="spinner" /><p>Loading RFQs…</p></div>}
    {state.status === "error" && <div className="inline-error" role="alert"><AlertCircle size={20} /><div><h3>RFQ register unavailable</h3><p>{state.error.message}</p>{state.error instanceof ApiError && state.error.status >= 500 && state.error.requestId && <p>Request ID: {state.error.requestId}</p>}</div><button className="button button--secondary" type="button" onClick={() => setAttempt(value => value + 1)}>Try again</button></div>}
    {vendorError?.status === "error" && <RequestError error={vendorError.error} />}
    {state.status === "ready" && visible.length === 0 && <div className="master-state"><ClipboardList size={28} /><h3>No RFQs found</h3><p>{status || hasDateFilter ? "Adjust the filters to see other RFQs." : "No requests for quotation were returned."}</p></div>}
    {state.status === "ready" && visible.length > 0 && <><p className="rfq-scroll-hint">Scroll table horizontally to view more columns.</p><div className="master-table-wrap rfq-table-wrap" role="region" aria-label="Scrollable RFQ results" tabIndex={0}><table className="master-table rfq-table" aria-label="RFQ results"><thead><tr><th>RFQ no.</th><th>Plant</th><th>Created</th><th>Response due</th><th>Vendors</th><th>Status</th><th><span className="sr-only">Actions</span></th></tr></thead><tbody>{visible.map(row => <tr key={row.rfq_id}><td><strong>{row.rfq_no}</strong></td><td>{row.unit_code}</td><td>{row.created_at.slice(0, 10)}</td><td>{row.response_due_at ? <><time dateTime={row.response_due_at}>{row.response_due_at.slice(0, 10)}</time><span className={dueLabel(row) === "Response overdue" ? "rfq-due--overdue" : "rfq-due"}>{dueLabel(row)}</span></> : dueLabel(row)}</td><td>{vendorCount(row.rfq_id)}</td><td><span className={`status-badge status-badge--${statusTone(row.status)}`}>{labelStatus(row.status)}</span></td><td><Link className="icon-link" to={href(row.rfq_id)} aria-label={`Open RFQ ${row.rfq_no}`}><ArrowUpRight size={17} /></Link></td></tr>)}</tbody></table></div><ul className="master-cards rfq-cards" aria-label="RFQ results">{visible.map(row => <li key={row.rfq_id}><div><span>{row.rfq_no}</span><span className={`status-badge status-badge--${statusTone(row.status)}`}>{labelStatus(row.status)}</span></div><h3>{row.unit_code} · {vendorCount(row.rfq_id)}</h3><dl><div><dt>Created</dt><dd>{row.created_at.slice(0, 10)}</dd></div><div><dt>Due</dt><dd>{row.response_due_at?.slice(0, 10) ?? "Not set"} · {dueLabel(row)}</dd></div></dl><Link className="button button--secondary" to={href(row.rfq_id)} aria-label={`Open RFQ ${row.rfq_no}`}>Open RFQ<ArrowUpRight size={16} /></Link></li>)}</ul><div className="reference-pagination"><p className="result-summary" aria-live="polite">Showing {first}–{last} of {total} records</p>{total > PAGE_SIZE && <div><button className="button button--secondary" type="button" aria-label="Previous page" disabled={currentPage === 1} onClick={() => setParam("page", String(currentPage - 1), false)}><ChevronLeft size={16} /></button><span>Page {currentPage} of {maxPage}</span><button className="button button--secondary" type="button" aria-label="Next page" disabled={currentPage >= maxPage} onClick={() => setParam("page", String(currentPage + 1), false)}><ChevronRight size={16} /></button></div>}</div></>}
  </section>;
}

function RfqCreate({ me }: { me: Me }) {
  const navigate = useNavigate();
  const location = useLocation();
  const { activeUnit } = useActivePlant();
  const [units, setUnits] = useState<Resource<Unit[]>>({ status: "loading" });
  const [items, setItems] = useState<Resource<Item[]>>({ status: "loading" });
  const [unitId, setUnitId] = useState(activeUnit?.unit_id ?? "");
  const [responseDue, setResponseDue] = useState("");
  const [lines, setLines] = useState<LineDraft[]>([blankLine(1)]);
  const [error, setError] = useState<Error | null>(null);
  const [saving, setSaving] = useState(false);
  const back = `${PATH}${location.search}`;

  useEffect(() => {
    const controller = new AbortController();
    void fetchAllOrganizationUnits(controller.signal)
      .then(data => { if (!controller.signal.aborted) setUnits({ status: "ready", data: data.filter(unit => unit.active && (me.all_plants || me.unit_ids.includes(unit.unit_id))) }); })
      .catch(failure => { if (!controller.signal.aborted) setUnits({ status: "error", error: problem(failure, "Plants could not be loaded.") }); });
    void fetchAllActiveItemsOfTypes(ALL_ITEM_TYPES, controller.signal)
      .then(data => { if (!controller.signal.aborted) setItems({ status: "ready", data: [...new Map(data.map(item => [item.item_id, item])).values()] }); })
      .catch(failure => { if (!controller.signal.aborted) setItems({ status: "error", error: problem(failure, "Items could not be loaded.") }); });
    return () => controller.abort();
  }, [me.all_plants, me.unit_ids]);

  function updateLine(key: number, patch: Partial<LineDraft>) { setLines(current => current.map(line => line.key === key ? { ...line, ...patch } : line)); }
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); setError(null);
    if (!unitId || lines.length < 1 || lines.length > 100 || lines.some(line => !line.itemId || !validQuantity(line.quantity) || (line.requiredBy && line.requiredBy < localToday())) || new Set(lines.map(line => line.itemId)).size !== lines.length) {
      setError(new Error("Select distinct active items, positive quantities, and valid required-by dates.")); return;
    }
    const body: RfqCreateBody = { unit_id: unitId, response_due_at: responseDue ? new Date(responseDue).toISOString() : null,
      lines: lines.map(line => ({ item_id: line.itemId, quantity: line.quantity, required_by: line.requiredBy || null })), vendor_ids: [] };
    setSaving(true);
    try { const rfq = await createRfq(body); navigate(`${PATH}/${rfq.rfq_id}${location.search}`); }
    catch (failure) { setError(problem(failure, "RFQ draft could not be created.")); }
    finally { setSaving(false); }
  }

  return <section className="master-detail rfq-create" aria-labelledby="rfq-create-title"><ProcurementTabs active="rfqs" /><div className="master-detail__heading"><div><Link to={back}><ArrowLeft size={16} />Back to RFQs</Link><h2 id="rfq-create-title">New RFQ</h2><p>Create a draft with one or more active items. No vendor is invited by this form.</p></div></div><div className="form-panel"><form className="record-form" onSubmit={submit}><div className="record-form__heading"><h3>Request details</h3></div><div className="record-form__grid"><div className="field"><label htmlFor="new-rfq-plant">Plant</label><select id="new-rfq-plant" value={unitId} required onChange={event => setUnitId(event.target.value)}><option value="" disabled>Select plant</option>{units.status === "ready" && units.data.map(unit => <option key={unit.unit_id} value={unit.unit_id}>{unit.code} — {unit.name}</option>)}</select></div><div className="field"><label htmlFor="new-rfq-response-due">Response due</label><input id="new-rfq-response-due" type="datetime-local" value={responseDue} onChange={event => setResponseDue(event.target.value)} /></div></div><div className="indent-create__lines"><div className="record-form__heading"><div><h3>Requested items</h3><p>One to 100 distinct items. Quantities use up to three decimal places.</p></div></div>{lines.map((line, index) => <div className="record-panel indent-create__line" key={line.key}><div className="record-panel__heading"><h4>Line {index + 1}</h4>{lines.length > 1 && <button className="button button--secondary" type="button" onClick={() => setLines(current => current.filter(item => item.key !== line.key))}>Remove line</button>}</div><div className="record-form__grid"><div className="field"><label htmlFor={`rfq-item-${line.key}`}>Item</label><select id={`rfq-item-${line.key}`} value={line.itemId} required onChange={event => updateLine(line.key, { itemId: event.target.value })}><option value="" disabled>Select item</option>{items.status === "ready" && items.data.map(item => <option key={item.item_id} value={item.item_id}>{item.code} — {item.name} ({item.uom_code})</option>)}</select></div><div className="field"><label htmlFor={`rfq-quantity-${line.key}`}>Quantity</label><input id={`rfq-quantity-${line.key}`} inputMode="decimal" required value={line.quantity} onChange={event => updateLine(line.key, { quantity: event.target.value })} /></div><div className="field"><label htmlFor={`rfq-required-${line.key}`}>Required by</label><input id={`rfq-required-${line.key}`} type="date" min={localToday()} value={line.requiredBy} onChange={event => updateLine(line.key, { requiredBy: event.target.value })} /></div></div></div>)}<button className="button button--secondary" type="button" disabled={lines.length >= 100} onClick={() => setLines(current => [...current, blankLine(Math.max(...current.map(line => line.key)) + 1)])}><Plus size={16} />Add line</button></div>{items.status === "loading" && <p role="status">Loading active items…</p>}{units.status === "error" && <RequestError error={units.error} />}{items.status === "error" && <RequestError error={items.error} />}{items.status === "ready" && items.data.length === 0 && <div className="master-state"><ClipboardList size={24} /><h3>No active items</h3><p>Create an active item before starting an RFQ.</p></div>}{error && <RequestError error={error} />}<div className="record-form__actions"><Link className="button button--secondary" to={back}>Cancel</Link><button className="button button--primary" type="submit" disabled={saving || units.status !== "ready" || items.status !== "ready" || items.data.length === 0}>{saving ? "Creating…" : "Create draft RFQ"}</button></div></form></div></section>;
}

function RfqComparisonSection({ id, revision }: { id: string; revision: number }) {
  const [state, setState] = useState<Resource<RfqComparison>>({ status: "loading" });
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    setState({ status: "loading" });
    void fetchRfqComparison(id, controller.signal)
      .then(data => { if (!controller.signal.aborted) setState({ status: "ready", data }); })
      .catch(error => { if (!controller.signal.aborted) setState({ status: "error", error: problem(error, "RFQ comparison could not be loaded.") }); });
    return () => controller.abort();
  }, [attempt, id, revision]);

  return <section className="record-panel rfq-comparison" aria-labelledby="rfq-comparison-title">
    <div className="record-panel__heading"><h3 id="rfq-comparison-title">Vendor comparison</h3></div>
    {state.status === "loading" && <p role="status">Loading comparison…</p>}
    {state.status === "error" && <><RequestError error={state.error} /><button className="button button--secondary" type="button" onClick={() => setAttempt(value => value + 1)}>Retry comparison</button></>}
    {state.status === "ready" && state.data.vendors.length === 0 && <p>No vendor quotations recorded yet.</p>}
    {state.status === "ready" && state.data.vendors.length > 0 && <div className="rfq-comparison__scroll" role="region" aria-label="Scrollable RFQ comparison" tabIndex={0}>
      <table className="rfq-comparison__table" aria-label="RFQ comparison">
        <thead><tr><th scope="col">Requested item</th>{state.data.vendors.map(vendor => <th scope="col" key={vendor.vendor_id}>{vendor.vendor_code}<span>{vendor.vendor_name}</span></th>)}</tr></thead>
        <tbody>{state.data.lines.map(line => <tr key={line.rfq_line_id}><th scope="row">{line.item_code}<span>Required: {line.quantity}</span></th>{state.data.vendors.map(vendor => {
          const offer = line.offers.find(value => value.vendor_id === vendor.vendor_id);
          return <td key={vendor.vendor_id}>{offer ? <div className="rfq-comparison__offer">
            <strong>Unit price: {offer.unit_price}</strong><span>Quoted: {offer.quoted_qty}</span><span>Line total: {offer.line_total}</span>
            <span>Lead: {offer.lead_days === null ? "Not given" : `${offer.lead_days} days`}</span>
            <span>Valid until: {offer.valid_until ?? "Not given"}</span>
            {offer.expired && <span className="rfq-comparison__warning">Expired</span>}
            {!offer.covers_quantity && <span className="rfq-comparison__warning">Quantity shortfall</span>}
            {offer.is_lowest && <span className="rfq-comparison__best">Lowest eligible</span>}
          </div> : <span>No quote for this line</span>}</td>;
        })}</tr>)}
        <tr className="rfq-comparison__totals"><th scope="row">Vendor total</th>{state.data.vendors.map(vendor => <td key={vendor.vendor_id}><strong>{vendor.total}</strong><span>{vendor.lines_quoted} of {state.data.lines.length} lines quoted</span><span>{vendor.covers_all_lines ? "Covers all lines" : "Incomplete coverage"}</span>{vendor.has_expired_offer && <span className="rfq-comparison__warning">Has expired offer</span>}{state.data.cheapest_vendor_id === vendor.vendor_id && <span className="rfq-comparison__best">Lowest complete total</span>}</td>)}</tr>
        </tbody>
      </table>
    </div>}
    {state.status === "ready" && state.data.vendors.length > 0 && <ul className="rfq-comparison__cards" aria-label="Vendor comparison cards">
      {state.data.vendors.map(vendor => <li key={vendor.vendor_id}>
        <div className="rfq-comparison__card-head"><h4>{vendor.vendor_code} — {vendor.vendor_name}</h4>{state.data.cheapest_vendor_id === vendor.vendor_id && <span className="rfq-comparison__best">Lowest complete total</span>}</div>
        <p><strong>Total: {vendor.total}</strong> · {vendor.lines_quoted} of {state.data.lines.length} lines quoted</p>
        <p>{vendor.covers_all_lines ? "Covers all lines" : "Incomplete coverage"}{vendor.has_expired_offer ? " · Has expired offer" : ""}</p>
        {state.data.lines.map(line => {
          const offer = line.offers.find(value => value.vendor_id === vendor.vendor_id);
          return <div className="rfq-comparison__card-line" key={line.rfq_line_id}><h5>{line.item_code} · Required: {line.quantity}</h5>
            {offer ? <><p>Unit price: {offer.unit_price} · Quoted: {offer.quoted_qty}</p><p>Line total: {offer.line_total} · Lead: {offer.lead_days === null ? "Not given" : `${offer.lead_days} days`}</p><p>Valid until: {offer.valid_until ?? "Not given"}</p>{offer.expired && <span className="rfq-comparison__warning">Expired</span>}{!offer.covers_quantity && <span className="rfq-comparison__warning">Quantity shortfall</span>}{offer.is_lowest && <span className="rfq-comparison__best">Lowest eligible</span>}</> : <p>No quote for this line</p>}
          </div>;
        })}
      </li>)}
    </ul>}
  </section>;
}

function RfqDetail({ id, me, settings }: { id: string; me: Me; settings: BusinessRule[] }) {
  const location = useLocation();
  const [state, setState] = useState<Resource<Rfq>>({ status: "loading" });
  const [attempt, setAttempt] = useState(0);
  const [comparisonRevision, setComparisonRevision] = useState(0);
  const [awardPo, setAwardPo] = useState("");
  useEffect(() => {
    const controller = new AbortController(); setState({ status: "loading" });
    void fetchRfq(id, controller.signal).then(data => { if (!controller.signal.aborted) setState({ status: "ready", data }); })
      .catch(error => { if (!controller.signal.aborted) setState({ status: "error", error: problem(error, "RFQ detail could not be loaded.") }); });
    return () => controller.abort();
  }, [attempt, id]);
  const rfq = state.status === "ready" ? state.data : null;
  return <section className="master-detail rfq-detail" aria-labelledby="rfq-detail-title"><ProcurementTabs active="rfqs" />
    <div className="master-detail__heading"><div><Link to={`${PATH}${location.search}`}><ArrowLeft size={16} />Back to RFQs</Link><h2 id="rfq-detail-title" tabIndex={-1}>{rfq?.rfq_no ?? "RFQ detail"}</h2></div></div>
    {state.status === "loading" && <div className="master-state"><span className="spinner" /><p>Loading RFQ…</p></div>}
    {state.status === "error" && <div className="inline-error" role="alert"><AlertCircle size={20} /><div><h3>RFQ unavailable</h3><p>{state.error.message}</p>{state.error instanceof ApiError && state.error.status >= 500 && state.error.requestId && <p>Request ID: {state.error.requestId}</p>}</div><button className="button button--secondary" type="button" onClick={() => setAttempt(value => value + 1)}>Try again</button></div>}
    {awardPo && <p className="success-banner" role="status">Purchase order {awardPo} created from this RFQ.</p>}
    {rfq && <>
      {me.permissions.includes("po.create") && <RfqActions rfq={rfq} settings={settings}
        onUpdated={data => { setState({ status: "ready", data }); setComparisonRevision(value => value + 1); }}
        onQuote={data => { setState({ status: "ready", data }); setComparisonRevision(value => value + 1); }}
        onAward={poNo => { setAwardPo(poNo); setState({ status: "ready", data: { ...rfq, status: "AWARDED" } }); }} />}
      <section className="record-panel"><div className="record-panel__heading"><h3>RFQ summary</h3><span className={`status-badge status-badge--${statusTone(rfq.status)}`}>{labelStatus(rfq.status)}</span></div><dl className="record-facts"><div><dt>Plant</dt><dd>{rfq.unit_code}</dd></div><div><dt>Created by</dt><dd>{rfq.created_by_name}</dd></div><div><dt>Created</dt><dd>{rfq.created_at.slice(0, 10)}</dd></div><div><dt>Response due</dt><dd>{rfq.response_due_at?.slice(0, 10) ?? "Not set"}</dd></div></dl></section>
      <section className="record-panel"><div className="record-panel__heading"><h3>Requested items</h3></div><ul className="indent-detail__lines">{rfq.lines.map(line => <li key={line.rfq_line_id}><strong>{line.item_code} — {line.item_name}</strong><span>{line.quantity} {line.uom_code}</span></li>)}</ul></section>
      <section className="record-panel"><div className="record-panel__heading"><h3>Invited vendors</h3></div>{rfq.invitations.length ? <ul className="indent-detail__lines">{rfq.invitations.map(vendor => <li key={vendor.vendor_id}><strong>{vendor.vendor_code} — {vendor.vendor_name}</strong><span>{labelStatus(vendor.response_status)}</span></li>)}</ul> : <p>No vendors invited yet.</p>}</section>
      <RfqComparisonSection id={id} revision={comparisonRevision} />
    </>}
  </section>;
}

export function Rfqs({ me, settings }: { me: Me; settings: BusinessRule[] }) {
  const location = useLocation();
  if (!me.modules.includes("procurement")) return <section className="module-pending"><h2>RFQ access unavailable</h2><p>The procurement module is required to read RFQs.</p><Link className="button button--primary" to="/app/procurement/indents">Open indents</Link></section>;
  if (location.pathname === `${PATH}/new`) return me.permissions.includes("po.create") ? <RfqCreate me={me} /> : <RfqList me={me} />;
  if (location.pathname.startsWith(`${PATH}/`)) return <RfqDetail id={location.pathname.slice(PATH.length + 1)} me={me} settings={settings} />;
  return <RfqList me={me} />;
}
