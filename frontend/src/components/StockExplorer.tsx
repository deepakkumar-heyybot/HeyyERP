import { useEffect, useMemo, useRef, useState } from "react";
import { ArrowUpRight, ChevronLeft, ChevronRight, PackageSearch, RefreshCw } from "lucide-react";
import { Link, useLocation, useNavigationType, useSearchParams } from "react-router-dom";
import { Item, Me, StockRow, Store, Unit, fetchAllItems, fetchAllOrganizationUnits, fetchAllStock, fetchAllStores, listStockPage } from "../api-client";
import { useActivePlant } from "../plant-context";
import { InventoryTabs } from "./InventoryTabs";
import { RegisterPageTemplate } from "./PageTemplates";
import { RequestError } from "./RequestError";
import { StockLotDetail } from "./StockLotDetail";

const PATH = "/app/inventory/stock";
const PAGE_SIZE = 20;
const LOT_STATUSES = ["QA_HOLD", "AVAILABLE", "QUARANTINE", "BLOCKED", "CONSUMED", "EXPIRED"];
type Load<T> = { status: "loading" } | { status: "ready"; data: T } | { status: "error"; error: Error };
type Register = { rows: StockRow[]; total: number };
const failure = (value: unknown, fallback: string) => value instanceof Error ? value : new Error(fallback);
const label = (value: string) => value.toLowerCase().replaceAll("_", " ").replace(/^./, match => match.toUpperCase());

function StockList({ me }: { me: Me }) {
  const { activePlantId } = useActivePlant();
  const [params, setParams] = useSearchParams();
  const navigationType = useNavigationType();
  const pendingParams = useRef(new URLSearchParams(params));
  useEffect(() => { if (navigationType !== "REPLACE") pendingParams.current = new URLSearchParams(params); }, [navigationType, params]);
  const unitId = params.get("plant") || (activePlantId === "all" ? "" : activePlantId);
  const storeId = params.get("store") || "";
  const itemId = params.get("item") || "";
  const status = LOT_STATUSES.includes(params.get("status") ?? "") ? params.get("status") : null;
  const inStockOnly = params.get("in_stock") !== "false";
  const lot = params.get("lot") || "";
  const pageNo = Math.max(1, Number(params.get("page") ?? "1") || 1);
  const [register, setRegister] = useState<Load<Register>>({ status: "loading" });
  const [plants, setPlants] = useState<Load<Unit[]>>({ status: "loading" });
  const [stores, setStores] = useState<Load<Store[]>>({ status: "loading" });
  const [items, setItems] = useState<Load<Item[]>>({ status: "loading" });
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    void fetchAllOrganizationUnits(controller.signal).then(data => { if (!controller.signal.aborted) setPlants({ status: "ready", data: data.filter(unit => me.all_plants || me.unit_ids.includes(unit.unit_id)) }); }).catch(value => { if (!controller.signal.aborted) setPlants({ status: "error", error: failure(value, "Plants could not be loaded.") }); });
    void fetchAllItems(controller.signal).then(data => { if (!controller.signal.aborted) setItems({ status: "ready", data }); }).catch(value => { if (!controller.signal.aborted) setItems({ status: "error", error: failure(value, "Items could not be loaded.") }); });
    return () => controller.abort();
  }, [me.all_plants, me.unit_ids]);
  useEffect(() => {
    const controller = new AbortController();
    setStores({ status: "loading" });
    void fetchAllStores(null, unitId || null, null, controller.signal).then(data => { if (!controller.signal.aborted) setStores({ status: "ready", data }); }).catch(value => { if (!controller.signal.aborted) setStores({ status: "error", error: failure(value, "Stores could not be loaded.") }); });
    return () => controller.abort();
  }, [unitId]);
  useEffect(() => {
    const controller = new AbortController();
    setRegister({ status: "loading" });
    const filters = { unitId: unitId || null, storeId: storeId || null, itemId: itemId || null, lotStatus: status, inStockOnly };
    const request = lot.trim() ? fetchAllStock(filters, controller.signal).then(rows => ({ rows, total: rows.length })) : listStockPage({ ...filters, limit: PAGE_SIZE, offset: (pageNo - 1) * PAGE_SIZE }, controller.signal).then(data => ({ rows: data.items, total: data.total }));
    void request.then(data => { if (!controller.signal.aborted) setRegister({ status: "ready", data }); }).catch(value => { if (!controller.signal.aborted) setRegister({ status: "error", error: failure(value, "Stock balances could not be loaded.") }); });
    return () => controller.abort();
  }, [attempt, inStockOnly, itemId, lot, pageNo, status, storeId, unitId]);
  const filtered = useMemo(() => register.status !== "ready" ? [] : lot.trim() ? register.data.rows.filter(row => row.lot_no.toLowerCase().includes(lot.trim().toLowerCase())) : register.data.rows, [lot, register]);
  const total = register.status === "ready" ? lot.trim() ? filtered.length : register.data.total : 0;
  const visible = lot.trim() ? filtered.slice((pageNo - 1) * PAGE_SIZE, pageNo * PAGE_SIZE) : filtered;
  const maxPage = Math.max(1, Math.ceil(total / PAGE_SIZE));
  useEffect(() => { if (register.status !== "ready" || pageNo <= maxPage) return; const next = new URLSearchParams(params); next.set("page", String(maxPage)); setParams(next, { replace: true }); }, [maxPage, pageNo, params, register.status, setParams]);
  function setParam(name: string, value: string, resetPage = true) {
    const next = new URLSearchParams(pendingParams.current);
    if (value) next.set(name, value); else next.delete(name);
    if (name === "plant") next.delete("store");
    if (resetPage) next.delete("page");
    pendingParams.current = next; setParams(next, { replace: true });
  }
  const query = params.toString();
  const href = (id: string) => `/app/inventory/lots/${id}${query ? `?${query}` : ""}`;
  return <RegisterPageTemplate className="stock-list" title="Stock explorer" headingId="stock-title" description="Inspect lot and store balances. Availability is on hand less reserved; lot status and expiry also affect use." navigation={<InventoryTabs active="stock" canIssue={me.permissions.includes("inv.issue")} />} filtersClassName="stock-filters" filters={<>
    <div className="field"><label htmlFor="stock-plant">Plant filter</label><select id="stock-plant" value={unitId} onChange={event => setParam("plant", event.target.value)}><option value="">All authorized plants</option>{plants.status === "ready" && plants.data.map(unit => <option key={unit.unit_id} value={unit.unit_id}>{unit.code} — {unit.name}</option>)}</select></div>
    <div className="field"><label htmlFor="stock-store">Store filter</label><select id="stock-store" value={storeId} onChange={event => setParam("store", event.target.value)}><option value="">All stores</option>{stores.status === "ready" && stores.data.map(store => <option key={store.store_id} value={store.store_id}>{store.code} — {store.name}</option>)}</select></div>
    <div className="field"><label htmlFor="stock-item">Item filter</label><select id="stock-item" value={itemId} onChange={event => setParam("item", event.target.value)}><option value="">All items</option>{items.status === "ready" && items.data.map(item => <option key={item.item_id} value={item.item_id}>{item.code} — {item.name}</option>)}</select></div>
    <div className="field"><label htmlFor="stock-lot">Lot filter</label><input id="stock-lot" value={lot} onChange={event => setParam("lot", event.target.value)} placeholder="Search lot number" /></div>
    <div className="field"><label htmlFor="stock-status">Lot status</label><select id="stock-status" value={status ?? ""} onChange={event => setParam("status", event.target.value)}><option value="">All statuses</option>{LOT_STATUSES.map(value => <option key={value} value={value}>{label(value)}</option>)}</select></div>
    <div className="field"><label htmlFor="stock-in-stock">Balance filter</label><select id="stock-in-stock" value={String(inStockOnly)} onChange={event => setParam("in_stock", event.target.value)}><option value="true">In stock only</option><option value="false">All balances</option></select></div>
    <button className="button button--secondary" type="button" disabled={register.status === "loading"} onClick={() => setAttempt(value => value + 1)}><RefreshCw size={16} />Refresh</button>
  </>}>
    {lot.trim() && <p className="indent-list__filter-note">Lot search applies to the full matching result set.</p>}
    {plants.status === "error" && <RequestError error={plants.error} />}{stores.status === "error" && <RequestError error={stores.error} />}{items.status === "error" && <RequestError error={items.error} />}
    {register.status === "loading" && <div className="master-state" role="status"><span className="spinner" /><p>Loading stock balances…</p></div>}
    {register.status === "error" && <><RequestError error={register.error} /><button className="button button--secondary" type="button" onClick={() => setAttempt(value => value + 1)}>Try again</button></>}
    {register.status === "ready" && !visible.length && <div className="master-state"><PackageSearch size={27} /><h3>No stock balances found</h3><p>Adjust the plant, store, item, lot, status, or balance filters.</p></div>}
    {register.status === "ready" && !!visible.length && <><p className="stock-list__scroll-hint">Scroll table horizontally to view all balance columns.</p><div className="master-table-wrap stock-list__table-wrap" role="region" aria-label="Scrollable stock balances" tabIndex={0}><table className="master-table stock-list__table" aria-label="Stock balances"><thead><tr><th>Item</th><th>Lot</th><th>Store</th><th>Plant</th><th>Status</th><th>On hand</th><th>Reserved</th><th>Available</th><th>Expiry</th><th><span className="sr-only">Actions</span></th></tr></thead><tbody>{visible.map(row => <tr key={`${row.lot_id}:${row.store_id}`}><td><strong>{row.item_code}</strong><br />{row.item_name}</td><td>{row.lot_no}</td><td>{row.store_code}</td><td>{row.unit_code}</td><td><span className="status-badge">{label(row.lot_status)}</span></td><td>{row.on_hand} {row.uom_code}</td><td>{row.reserved} {row.uom_code}</td><td>{row.available} {row.uom_code}</td><td>{row.expiry_at?.slice(0, 10) ?? "Not set"}{row.expired && <span className="stock-list__expired">Expired</span>}</td><td><Link className="icon-link" to={href(row.lot_id)} aria-label={`Open lot ${row.lot_no}`}><ArrowUpRight size={17} /></Link></td></tr>)}</tbody></table></div><ul className="master-cards stock-list__cards" aria-label="Stock balances">{visible.map(row => <li key={`${row.lot_id}:${row.store_id}`}><div><strong>{row.lot_no}</strong><span className="status-badge">{label(row.lot_status)}</span></div><h3>{row.item_code} — {row.item_name}</h3><dl><div><dt>Plant</dt><dd>{row.unit_code}</dd></div><div><dt>Store</dt><dd>{row.store_code}</dd></div><div><dt>On hand</dt><dd>{row.on_hand} {row.uom_code}</dd></div><div><dt>Reserved</dt><dd>{row.reserved} {row.uom_code}</dd></div><div><dt>Available</dt><dd>{row.available} {row.uom_code}</dd></div><div><dt>Expiry</dt><dd>{row.expiry_at?.slice(0, 10) ?? "Not set"}{row.expired && " · Expired"}</dd></div></dl><Link className="button button--secondary" to={href(row.lot_id)} aria-label={`Open lot ${row.lot_no}`}>Open lot<ArrowUpRight size={16} /></Link></li>)}</ul><div className="reference-pagination"><p className="result-summary" aria-live="polite">Showing {(pageNo - 1) * PAGE_SIZE + 1}–{Math.min(pageNo * PAGE_SIZE, total)} of {total} records</p>{total > PAGE_SIZE && <div><button className="button button--secondary" type="button" aria-label="Previous page" disabled={pageNo === 1} onClick={() => setParam("page", String(pageNo - 1), false)}><ChevronLeft size={16} /></button><span>Page {pageNo} of {maxPage}</span><button className="button button--secondary" type="button" aria-label="Next page" disabled={pageNo >= maxPage} onClick={() => setParam("page", String(pageNo + 1), false)}><ChevronRight size={16} /></button></div>}</div></>}
  </RegisterPageTemplate>;
}

export function StockExplorer({ me }: { me: Me }) {
  const location = useLocation();
  if (!me.modules.includes("inventory")) return <section className="module-pending"><h2>Stock access unavailable</h2><p>The inventory module is required to read stock.</p></section>;
  if (location.pathname.startsWith("/app/inventory/lots/")) return <StockLotDetail id={location.pathname.slice("/app/inventory/lots/".length)} me={me} />;
  return <StockList me={me} />;
}
