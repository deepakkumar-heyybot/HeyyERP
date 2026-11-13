import { useEffect, useMemo, useState } from "react";
import { AlertCircle, ArrowUpRight, ChevronLeft, ChevronRight, ClipboardList, Plus, RefreshCw } from "lucide-react";
import { Link, useLocation, useNavigate, useSearchParams } from "react-router-dom";
import {
  ApiError, BusinessRule, Me, Po, PoLateLine, PoSummary, Vendor, fetchAllPos, fetchAllVendors, fetchPo, fetchPoHistory, fetchPoLateLines, listPosPage,
} from "../api-client";
import { useActivePlant } from "../plant-context";
import { ProcurementTabs } from "./ProcurementTabs";
import { PoCreation } from "./PoCreation";
import { PoDetail } from "./PoDetail";
import { RequestError } from "./RequestError";

const PATH = "/app/procurement/purchase-orders";
const PAGE_SIZE = 20;
const STATUSES = ["DRAFT", "PENDING_APPROVAL", "OPEN", "PARTIALLY_RECEIVED", "RECEIVED", "CLOSED", "REJECTED", "CANCELLED"];
type Load<T> = { status: "loading" } | { status: "ready"; data: T } | { status: "error"; error: Error };
type Register = { rows: PoSummary[]; total: number };
const failure = (value: unknown, fallback: string) => value instanceof Error ? value : new Error(fallback);
const label = (value: string) => value.toLowerCase().replaceAll("_", " ").replace(/^./, match => match.toUpperCase());
const nonZero = (value: string) => /[1-9]/.test(value);

function PoList({ me }: { me: Me }) {
  const navigate = useNavigate();
  const { activePlantId } = useActivePlant();
  const unitId = activePlantId === "all" ? null : activePlantId;
  const [params, setParams] = useSearchParams();
  const status = STATUSES.includes(params.get("status") ?? "") ? params.get("status") : null;
  const vendorId = params.get("vendor") || null;
  const from = params.get("from") ?? "";
  const through = params.get("through") ?? "";
  const hasDates = Boolean(from || through);
  const pageNo = Math.max(1, Number(params.get("page") ?? "1") || 1);
  const [register, setRegister] = useState<Load<Register>>({ status: "loading" });
  const [vendors, setVendors] = useState<Load<Vendor[]>>({ status: "loading" });
  const [details, setDetails] = useState<Record<string, Load<Po>>>({});
  const [approvals, setApprovals] = useState<Record<string, Load<string | null>>>({});
  const [late, setLate] = useState<Load<PoLateLine[]>>({ status: "loading" });
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    void fetchAllVendors(controller.signal)
      .then(data => { if (!controller.signal.aborted) setVendors({ status: "ready", data }); })
      .catch(value => { if (!controller.signal.aborted) setVendors({ status: "error", error: failure(value, "Vendors could not be loaded.") }); });
    return () => controller.abort();
  }, []);
  useEffect(() => {
    const controller = new AbortController();
    setRegister({ status: "loading" });
    const request = hasDates
      ? fetchAllPos(status, vendorId, unitId, controller.signal).then(rows => ({ rows, total: rows.length }))
      : listPosPage({ status, vendorId, unitId, limit: PAGE_SIZE, offset: (pageNo - 1) * PAGE_SIZE }, controller.signal).then(page => ({ rows: page.items, total: page.total }));
    void request.then(data => { if (!controller.signal.aborted) setRegister({ status: "ready", data }); })
      .catch(value => { if (!controller.signal.aborted) setRegister({ status: "error", error: failure(value, "Purchase orders could not be loaded.") }); });
    return () => controller.abort();
  }, [attempt, hasDates, pageNo, status, unitId, vendorId]);
  useEffect(() => {
    const controller = new AbortController();
    setLate({ status: "loading" });
    void fetchPoLateLines(controller.signal)
      .then(data => { if (!controller.signal.aborted) setLate({ status: "ready", data }); })
      .catch(value => { if (!controller.signal.aborted) setLate({ status: "error", error: failure(value, "Late-line status could not be loaded.") }); });
    return () => controller.abort();
  }, [attempt]);

  const filtered = useMemo(() => register.status !== "ready" ? [] : hasDates ? register.data.rows.filter(row =>
    (!from || row.created_at.slice(0, 10) >= from) && (!through || row.created_at.slice(0, 10) <= through)) : register.data.rows,
  [from, hasDates, register, through]);
  const total = register.status === "ready" ? hasDates ? filtered.length : register.data.total : 0;
  const visible = hasDates ? filtered.slice((pageNo - 1) * PAGE_SIZE, pageNo * PAGE_SIZE) : filtered;
  const visibleIds = visible.map(row => row.po_id).join("|");
  useEffect(() => {
    if (!visibleIds) return;
    const controller = new AbortController();
    const ids = visibleIds.split("|");
    setDetails(current => ({ ...current, ...Object.fromEntries(ids.map(id => [id, { status: "loading" }])) }));
    void Promise.all(ids.map(async id => {
      try { return [id, { status: "ready", data: await fetchPo(id, controller.signal) }] as const; }
      catch (value) { return [id, { status: "error", error: failure(value, "Purchase order detail could not be loaded.") }] as const; }
    })).then(entries => { if (!controller.signal.aborted) setDetails(current => ({ ...current, ...Object.fromEntries(entries) })); });
    return () => controller.abort();
  }, [attempt, visibleIds]);
  const approvalIds = visible.filter(row => row.approval_id).map(row => row.po_id).join("|");
  useEffect(() => {
    if (!approvalIds) return;
    const controller = new AbortController();
    const ids = approvalIds.split("|");
    setApprovals(current => ({ ...current, ...Object.fromEntries(ids.map(id => [id, { status: "loading" }])) }));
    void Promise.all(ids.map(async id => {
      try { const history = await fetchPoHistory(id, controller.signal); return [id, { status: "ready", data: history.approval?.status ?? null }] as const; }
      catch (value) { return [id, { status: "error", error: failure(value, "Approval state could not be loaded.") }] as const; }
    })).then(entries => { if (!controller.signal.aborted) setApprovals(current => ({ ...current, ...Object.fromEntries(entries) })); });
    return () => controller.abort();
  }, [approvalIds, attempt]);

  function setParam(key: string, value: string, resetPage = true) {
    const next = new URLSearchParams(window.location.search);
    if (value) next.set(key, value); else next.delete(key);
    if (resetPage) next.delete("page");
    setParams(next, { replace: true });
  }
  const maxPage = Math.max(1, Math.ceil(total / PAGE_SIZE));
  useEffect(() => {
    if (register.status !== "ready" || pageNo <= maxPage) return;
    const next = new URLSearchParams(params); next.set("page", String(maxPage)); setParams(next, { replace: true });
  }, [maxPage, pageNo, params, register.status, setParams]);
  const query = params.toString();
  const href = (id: string) => `${PATH}/${id}${query ? `?${query}` : ""}`;
  const value = (id: string, currency: string) => details[id]?.status === "ready" ? `${currency} ${details[id].data.gross_total}` : details[id]?.status === "error" ? "Value unavailable" : "Loading value…";
  const receipts = (id: string) => {
    const current = details[id];
    if (current?.status === "error") return "Receiving unavailable";
    if (current?.status !== "ready") return "Loading receipts…";
    const lines = current.data.lines;
    return `${lines.filter(line => nonZero(line.received_qty)).length} of ${lines.length} lines with receipts`;
  };
  const lateLabel = (id: string) => {
    if (late.status === "error") return "Late status unavailable";
    if (late.status === "loading") return "Loading late lines…";
    const count = late.data.filter(line => line.po_id === id).length;
    return `${count} late ${count === 1 ? "line" : "lines"}`;
  };
  const detailError = visible.map(row => details[row.po_id]).find(result => result?.status === "error");
  const approvalError = visible.map(row => approvals[row.po_id]).find(result => result?.status === "error");
  const approvalLabel = (row: PoSummary) => {
    if (!row.approval_id) return "No approval linked";
    const state = approvals[row.po_id];
    return state?.status === "ready" ? state.data ? label(state.data) : "Approval unavailable" : state?.status === "error" ? "Approval unavailable" : "Loading approval…";
  };

  return <section className="master-list po-list" aria-labelledby="pos-title"><ProcurementTabs active="orders" />
    <div className="page-heading-row"><div><h2 id="pos-title">Purchase orders</h2><p>Review supplier orders, receiving progress, approvals, and overdue deliveries.</p></div>
      {me.permissions.includes("po.create") && <div className="po-list__actions"><button className="button button--secondary" type="button" onClick={() => navigate(`${PATH}/from-indents`)}>From approved indents</button><button className="button button--primary" type="button" onClick={() => navigate(`${PATH}/new`)}><Plus size={16} />New purchase order</button></div>}</div>
    <div className="master-filters po-filters"><div className="field"><label htmlFor="po-status">PO status</label><select id="po-status" value={status ?? ""} onChange={event => setParam("status", event.target.value)}><option value="">All statuses</option>{STATUSES.map(item => <option key={item} value={item}>{label(item)}</option>)}</select></div><div className="field"><label htmlFor="po-vendor">Vendor</label><select id="po-vendor" value={vendorId ?? ""} onChange={event => setParam("vendor", event.target.value)}><option value="">All vendors</option>{vendors.status === "ready" && vendors.data.map(vendor => <option key={vendor.vendor_id} value={vendor.vendor_id}>{vendor.code} — {vendor.name}</option>)}{vendorId && vendors.status === "ready" && !vendors.data.some(vendor => vendor.vendor_id === vendorId) && <option value={vendorId}>Selected vendor</option>}</select></div><div className="field"><label htmlFor="po-from">Created from</label><input id="po-from" type="date" value={from} onChange={event => setParam("from", event.target.value)} /></div><div className="field"><label htmlFor="po-through">Created through</label><input id="po-through" type="date" min={from || undefined} value={through} onChange={event => setParam("through", event.target.value)} /></div><button className="button button--secondary" type="button" disabled={register.status === "loading"} onClick={() => setAttempt(value => value + 1)}><RefreshCw size={16} />Refresh</button></div>
    {hasDates && <p className="indent-list__filter-note">Creation dates are filtered across all matching backend pages.</p>}
    {vendors.status === "error" && <RequestError error={vendors.error} />}{late.status === "error" && <RequestError error={late.error} />}{detailError?.status === "error" && <RequestError error={detailError.error} />}{approvalError?.status === "error" && <RequestError error={approvalError.error} />}
    {register.status === "loading" && <div className="master-state"><span className="spinner" /><p>Loading purchase orders…</p></div>}
    {register.status === "error" && <div className="inline-error" role="alert"><AlertCircle size={20} /><div><h3>Purchase orders unavailable</h3><p>{register.error.message}</p>{register.error instanceof ApiError && register.error.status >= 500 && register.error.requestId && <p>Request ID: {register.error.requestId}</p>}</div><button className="button button--secondary" type="button" onClick={() => setAttempt(value => value + 1)}>Try again</button></div>}
    {register.status === "ready" && visible.length === 0 && <div className="master-state"><ClipboardList size={28} /><h3>No purchase orders found</h3><p>{status || vendorId || hasDates ? "Adjust the filters to see other orders." : "No purchase orders were returned."}</p></div>}
    {register.status === "ready" && visible.length > 0 && <><p className="po-list__scroll-hint">Scroll table horizontally to view more columns.</p><div className="master-table-wrap po-list__table-wrap" role="region" aria-label="Scrollable purchase order results" tabIndex={0}><table className="master-table po-list__table" aria-label="Purchase order results"><thead><tr><th>PO no.</th><th>Vendor</th><th>Plant</th><th>Created</th><th>Value</th><th>Receiving</th><th>Approval</th><th>Late lines</th><th>Status</th><th><span className="sr-only">Actions</span></th></tr></thead><tbody>{visible.map(row => <tr key={row.po_id}><td><strong>{row.po_no}</strong></td><td>{row.vendor_code} — {row.vendor_name}</td><td>{row.unit_code}</td><td>{row.created_at.slice(0, 10)}</td><td>{value(row.po_id, row.currency_code)}</td><td>{receipts(row.po_id)}</td><td>{row.approval_id && me.modules.includes("approvals") ? <Link to={`/app/approvals/${row.approval_id}`}>{approvalLabel(row)}</Link> : approvalLabel(row)}</td><td>{lateLabel(row.po_id)}</td><td><span className="status-badge">{label(row.status)}</span></td><td><Link className="icon-link" to={href(row.po_id)} aria-label={`Open purchase order ${row.po_no}`}><ArrowUpRight size={17} /></Link></td></tr>)}</tbody></table></div>
      <ul className="master-cards po-list__cards" aria-label="Purchase order results">{visible.map(row => <li key={row.po_id}><div><strong>{row.po_no}</strong><span className="status-badge">{label(row.status)}</span></div><h3>{row.vendor_code} — {row.vendor_name}</h3><dl><div><dt>Plant</dt><dd>{row.unit_code}</dd></div><div><dt>Created</dt><dd>{row.created_at.slice(0, 10)}</dd></div><div><dt>Value</dt><dd>{value(row.po_id, row.currency_code)}</dd></div><div><dt>Receiving</dt><dd>{receipts(row.po_id)}</dd></div><div><dt>Approval</dt><dd>{approvalLabel(row)}</dd></div><div><dt>Late lines</dt><dd>{lateLabel(row.po_id)}</dd></div></dl><Link className="button button--secondary" to={href(row.po_id)}>Open purchase order<ArrowUpRight size={16} /></Link></li>)}</ul>
      <div className="reference-pagination"><p className="result-summary" aria-live="polite">Showing {(pageNo - 1) * PAGE_SIZE + 1}–{Math.min(pageNo * PAGE_SIZE, total)} of {total} records</p>{total > PAGE_SIZE && <div><button className="button button--secondary" type="button" aria-label="Previous page" disabled={pageNo === 1} onClick={() => setParam("page", String(pageNo - 1), false)}><ChevronLeft size={16} /></button><span>Page {pageNo} of {maxPage}</span><button className="button button--secondary" type="button" aria-label="Next page" disabled={pageNo >= maxPage} onClick={() => setParam("page", String(pageNo + 1), false)}><ChevronRight size={16} /></button></div>}</div></>}
  </section>;
}

export function PurchaseOrders({ me, settings }: { me: Me; settings: BusinessRule[] }) {
  const location = useLocation();
  if (!me.modules.includes("procurement")) return <section className="module-pending"><h2>Purchase order access unavailable</h2><p>The procurement module is required to read purchase orders.</p></section>;
  if (location.pathname === `${PATH}/new` || location.pathname === `${PATH}/from-indents`) return me.permissions.includes("po.create") ? <PoCreation mode={location.pathname.endsWith("from-indents") ? "indents" : "direct"} me={me} settings={settings} /> : <PoList me={me} />;
  if (location.pathname.startsWith(`${PATH}/`)) return <PoDetail id={location.pathname.slice(PATH.length + 1)} me={me} settings={settings} />;
  return <PoList me={me} />;
}
