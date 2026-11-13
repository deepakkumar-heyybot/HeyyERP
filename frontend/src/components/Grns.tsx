import { useEffect, useMemo, useRef, useState } from "react";
import { ArrowUpRight, ChevronLeft, ChevronRight, ClipboardList, Plus, RefreshCw } from "lucide-react";
import { Link, useLocation, useNavigationType, useNavigate, useSearchParams } from "react-router-dom";
import { GrnSummary, Me, Unit, Vendor, fetchAllGrns, fetchAllOrganizationUnits, fetchAllVendors, listGrnsPage } from "../api-client";
import { useActivePlant } from "../plant-context";
import { GrnCreation } from "./GrnCreation";
import { GrnDetail } from "./GrnDetail";
import { InventoryTabs } from "./InventoryTabs";
import { RequestError } from "./RequestError";

const PATH = "/app/inventory/grns";
const PAGE_SIZE = 20;
const STATUSES = ["DRAFT", "POSTED", "CANCELLED"];
type Load<T> = { status: "loading" } | { status: "ready"; data: T } | { status: "error"; error: Error };
type Register = { rows: GrnSummary[]; total: number };
const failure = (value: unknown, fallback: string) => value instanceof Error ? value : new Error(fallback);
const label = (value: string) => value.toLowerCase().replaceAll("_", " ").replace(/^./, match => match.toUpperCase());

function GrnList({ me }: { me: Me }) {
  const navigate = useNavigate();
  const { activePlantId } = useActivePlant();
  const [params, setParams] = useSearchParams();
  const navigationType = useNavigationType();
  const pendingParams = useRef(new URLSearchParams(params));
  useEffect(() => { if (navigationType !== "REPLACE") pendingParams.current = new URLSearchParams(params); }, [navigationType, params]);
  const status = STATUSES.includes(params.get("status") ?? "") ? params.get("status") : null;
  const poId = params.get("po_id") || null;
  const plantId = params.get("plant") || (activePlantId === "all" ? "" : activePlantId);
  const vendorCode = params.get("vendor") || "";
  const from = params.get("from") || "";
  const through = params.get("through") || "";
  const hasClientFilters = Boolean(plantId || vendorCode || from || through);
  const pageNo = Math.max(1, Number(params.get("page") ?? "1") || 1);
  const [register, setRegister] = useState<Load<Register>>({ status: "loading" });
  const [vendors, setVendors] = useState<Load<Vendor[]>>({ status: "loading" });
  const [plants, setPlants] = useState<Load<Unit[]>>({ status: "loading" });
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    void fetchAllVendors(controller.signal).then(data => { if (!controller.signal.aborted) setVendors({ status: "ready", data }); }).catch(value => { if (!controller.signal.aborted) setVendors({ status: "error", error: failure(value, "Vendors could not be loaded.") }); });
    void fetchAllOrganizationUnits(controller.signal).then(data => { if (!controller.signal.aborted) setPlants({ status: "ready", data: data.filter(unit => me.all_plants || me.unit_ids.includes(unit.unit_id)) }); }).catch(value => { if (!controller.signal.aborted) setPlants({ status: "error", error: failure(value, "Plants could not be loaded.") }); });
    return () => controller.abort();
  }, [me.all_plants, me.unit_ids]);
  useEffect(() => {
    const controller = new AbortController();
    setRegister({ status: "loading" });
    const request = hasClientFilters ? fetchAllGrns(status, poId, controller.signal).then(rows => ({ rows, total: rows.length })) : listGrnsPage({ status, poId, limit: PAGE_SIZE, offset: (pageNo - 1) * PAGE_SIZE }, controller.signal).then(data => ({ rows: data.items, total: data.total }));
    void request.then(data => { if (!controller.signal.aborted) setRegister({ status: "ready", data }); }).catch(value => { if (!controller.signal.aborted) setRegister({ status: "error", error: failure(value, "Goods receipts could not be loaded.") }); });
    return () => controller.abort();
  }, [attempt, hasClientFilters, pageNo, poId, status]);
  const filtered = useMemo(() => register.status !== "ready" ? [] : hasClientFilters ? register.data.rows.filter(row => (!plantId || row.unit_id === plantId) && (!vendorCode || row.vendor_code === vendorCode) && (!from || row.received_at.slice(0, 10) >= from) && (!through || row.received_at.slice(0, 10) <= through)) : register.data.rows, [from, hasClientFilters, plantId, register, through, vendorCode]);
  const total = register.status === "ready" ? hasClientFilters ? filtered.length : register.data.total : 0;
  const visible = hasClientFilters ? filtered.slice((pageNo - 1) * PAGE_SIZE, pageNo * PAGE_SIZE) : filtered;
  const maxPage = Math.max(1, Math.ceil(total / PAGE_SIZE));
  useEffect(() => { if (register.status !== "ready" || pageNo <= maxPage) return; const next = new URLSearchParams(params); next.set("page", String(maxPage)); setParams(next, { replace: true }); }, [maxPage, pageNo, params, register.status, setParams]);
  function setParam(name: string, value: string, resetPage = true) {
    const next = new URLSearchParams(pendingParams.current);
    if (value) next.set(name, value); else next.delete(name);
    if (resetPage) next.delete("page");
    pendingParams.current = next; setParams(next, { replace: true });
  }
  const query = params.toString();
  const href = (id: string) => `${PATH}/${id}${query ? `?${query}` : ""}`;
  return <section className="master-list grn-list" aria-labelledby="grns-title"><InventoryTabs canIssue={me.permissions.includes("inv.issue")} /><div className="page-heading-row"><div><h2 id="grns-title">Goods receipts</h2><p>Track supplier deliveries against purchase orders and their posting state.</p></div>{me.permissions.includes("grn.post") && <button className="button button--primary" type="button" onClick={() => navigate(`${PATH}/new${poId ? `?po_id=${encodeURIComponent(poId)}` : ""}`)}><Plus size={16} />New goods receipt</button>}</div>
    <div className="master-filters grn-filters"><div className="field"><label htmlFor="grn-status">GRN status</label><select id="grn-status" value={status ?? ""} onChange={event => setParam("status", event.target.value)}><option value="">All statuses</option>{STATUSES.map(item => <option key={item} value={item}>{label(item)}</option>)}</select></div><div className="field"><label htmlFor="grn-plant">Plant filter</label><select id="grn-plant" value={plantId} onChange={event => setParam("plant", event.target.value)}><option value="">All authorized plants</option>{plants.status === "ready" && plants.data.map(unit => <option key={unit.unit_id} value={unit.unit_id}>{unit.code} — {unit.name}</option>)}</select></div><div className="field"><label htmlFor="grn-vendor">Vendor filter</label><select id="grn-vendor" value={vendorCode} onChange={event => setParam("vendor", event.target.value)}><option value="">All vendors</option>{vendors.status === "ready" && vendors.data.map(vendor => <option key={vendor.vendor_id} value={vendor.code}>{vendor.code} — {vendor.name}</option>)}</select></div><div className="field"><label htmlFor="grn-from">Received from</label><input id="grn-from" type="date" value={from} onChange={event => setParam("from", event.target.value)} /></div><div className="field"><label htmlFor="grn-through">Received through</label><input id="grn-through" type="date" min={from || undefined} value={through} onChange={event => setParam("through", event.target.value)} /></div><button className="button button--secondary" type="button" disabled={register.status === "loading"} onClick={() => setAttempt(value => value + 1)}><RefreshCw size={16} />Refresh</button></div>
    {hasClientFilters && <p className="indent-list__filter-note">Plant, vendor, and receipt dates apply to the full result set.</p>}
    {plants.status === "error" && <RequestError error={plants.error} />}{vendors.status === "error" && <RequestError error={vendors.error} />}
    {register.status === "loading" && <div className="master-state" role="status"><span className="spinner" /><p>Loading goods receipts…</p></div>}
    {register.status === "error" && <><RequestError error={register.error} /><button className="button button--secondary" type="button" onClick={() => setAttempt(value => value + 1)}>Try again</button></>}
    {register.status === "ready" && visible.length === 0 && <div className="master-state"><ClipboardList size={28} /><h3>No goods receipts found</h3><p>Adjust the filters or post a receipt against an open purchase order.</p></div>}
    {register.status === "ready" && visible.length > 0 && <><p className="grn-list__scroll-hint">Scroll table horizontally to view more columns.</p><div className="master-table-wrap grn-list__table-wrap" role="region" aria-label="Scrollable goods receipt results" tabIndex={0}><table className="master-table grn-list__table" aria-label="Goods receipt results"><thead><tr><th>GRN no.</th><th>PO no.</th><th>Vendor</th><th>Plant</th><th>Received</th><th>Status</th><th><span className="sr-only">Actions</span></th></tr></thead><tbody>{visible.map(row => <tr key={row.grn_id}><td><strong>{row.grn_no}</strong></td><td><Link to={`/app/procurement/purchase-orders/${row.po_id}`}>{row.po_no}</Link></td><td>{row.vendor_code}</td><td>{row.unit_code}</td><td>{row.received_at.slice(0, 10)}</td><td><span className="status-badge">{label(row.status)}</span></td><td><Link className="icon-link" to={href(row.grn_id)} aria-label={`Open goods receipt ${row.grn_no}`}><ArrowUpRight size={17} /></Link></td></tr>)}</tbody></table></div><ul className="master-cards grn-list__cards" aria-label="Goods receipt results">{visible.map(row => <li key={row.grn_id}><div><strong>{row.grn_no}</strong><span className="status-badge">{label(row.status)}</span></div><h3>{row.po_no} · {row.vendor_code}</h3><dl><div><dt>Plant</dt><dd>{row.unit_code}</dd></div><div><dt>Received</dt><dd>{row.received_at.slice(0, 10)}</dd></div><div><dt>Delivery challan</dt><dd>{row.supplier_dc_no ?? "Not provided"}</dd></div></dl><Link className="button button--secondary" to={href(row.grn_id)}>Open goods receipt<ArrowUpRight size={16} /></Link></li>)}</ul><div className="reference-pagination"><p className="result-summary" aria-live="polite">Showing {(pageNo - 1) * PAGE_SIZE + 1}–{Math.min(pageNo * PAGE_SIZE, total)} of {total} records</p>{total > PAGE_SIZE && <div><button className="button button--secondary" type="button" aria-label="Previous page" disabled={pageNo === 1} onClick={() => setParam("page", String(pageNo - 1), false)}><ChevronLeft size={16} /></button><span>Page {pageNo} of {maxPage}</span><button className="button button--secondary" type="button" aria-label="Next page" disabled={pageNo >= maxPage} onClick={() => setParam("page", String(pageNo + 1), false)}><ChevronRight size={16} /></button></div>}</div></>}
  </section>;
}

export function Grns({ me }: { me: Me }) {
  const location = useLocation();
  if (!me.modules.includes("inventory")) return <section className="module-pending"><h2>Inventory access unavailable</h2><p>The inventory module is required to read goods receipts.</p></section>;
  if (location.pathname === `${PATH}/new`) return me.permissions.includes("grn.post") && me.modules.includes("procurement") ? <GrnCreation canIssue={me.permissions.includes("inv.issue")} /> : <section className="module-pending"><h2>Goods receipt creation unavailable</h2><p>Receiving needs GRN posting permission and purchase-order read access.</p></section>;
  if (location.pathname.startsWith(`${PATH}/`)) return <GrnDetail id={location.pathname.slice(PATH.length + 1)} me={me} />;
  return <GrnList me={me} />;
}
