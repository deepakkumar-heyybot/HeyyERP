import { FormEvent, useEffect, useRef, useState } from "react";
import { AlertCircle, ArrowLeft, ArrowUpRight, Boxes, ChevronLeft, ChevronRight, Plus, RefreshCw } from "lucide-react";
import { Link, useLocation, useNavigate, useNavigationType, useSearchParams } from "react-router-dom";
import {
  ApiError, Item, ItemCategory, ItemCreate, ItemType, Me, Page, UnitOfMeasure,
  createItem, fetchAllItemCategories, fetchUnitsOfMeasure, listItems,
} from "../api-client";
import { ItemDetail, ItemSection } from "./ItemDetail";
import { EngineeringTabs } from "./EngineeringTabs";

const PAGE_SIZE = 20;
const TYPES: Array<{ value: ItemType; label: string }> = [
  { value: "RM", label: "Raw material" }, { value: "WIP", label: "Work in progress" },
  { value: "FG", label: "Finished goods" }, { value: "SPARE", label: "Spare" },
  { value: "CONSUMABLE", label: "Consumable" }, { value: "CAPITAL", label: "Capital" },
  { value: "OTHER", label: "Other" },
];
type ListState = { status: "loading" } | { status: "ready"; page: Page<Item>; categories: ItemCategory[] } | { status: "error"; error: Error };
type FormState = { status: "loading" } | { status: "ready"; categories: ItemCategory[]; uoms: UnitOfMeasure[] } | { status: "error"; error: Error };

function typeLabel(type: ItemType) { return TYPES.find(option => option.value === type)?.label ?? type; }
function StatusBadge({ active }: { active: boolean }) { return <span className={`status-badge status-badge--${active ? "approved" : "cancelled"}`}>{active ? "Active" : "Inactive"}</span>; }
function RequestError({ error }: { error: Error }) { return <div className="decision-error" role="alert"><AlertCircle size={17} /><div><span>{error.message}</span>{error instanceof ApiError && error.status >= 500 && error.requestId && <span>Request ID: {error.requestId}</span>}</div></div>; }

function ItemList({ me }: { me: Me }) {
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const navigationType = useNavigationType();
  const pendingParams = useRef(new URLSearchParams(searchParams));
  useEffect(() => { if (navigationType !== "REPLACE") pendingParams.current = new URLSearchParams(searchParams); }, [navigationType, searchParams]);
  const query = searchParams.get("q") ?? "";
  const typeParam = searchParams.get("type") ?? "";
  const itemType = TYPES.some(option => option.value === typeParam) ? typeParam as ItemType : null;
  const categoryId = searchParams.get("category") || null;
  const activeParam = searchParams.get("active") ?? "";
  const active = activeParam === "true" ? true : activeParam === "false" ? false : null;
  const currentPage = Math.max(1, Number(searchParams.get("page") ?? "1") || 1);
  const [state, setState] = useState<ListState>({ status: "loading" });
  const [attempt, setAttempt] = useState(0);
  const canManage = me.permissions.includes("item.manage");

  useEffect(() => {
    const controller = new AbortController(); setState({ status: "loading" });
    void Promise.resolve().then(async () => {
      if (controller.signal.aborted) return;
      try {
        const [page, categories] = await Promise.all([
          listItems({ q: query.trim(), itemType, categoryId, active, limit: PAGE_SIZE, offset: (currentPage - 1) * PAGE_SIZE }, controller.signal),
          fetchAllItemCategories(controller.signal),
        ]);
        setState({ status: "ready", page, categories });
      } catch (problem) { if (!controller.signal.aborted) setState({ status: "error", error: problem instanceof Error ? problem : new Error("Items could not be loaded.") }); }
    });
    return () => controller.abort();
  }, [active, attempt, categoryId, currentPage, itemType, query]);

  function setParam(name: string, value: string, resetPage = true) {
    const next = new URLSearchParams(pendingParams.current);
    if (value) next.set(name, value); else next.delete(name);
    if (resetPage) next.delete("page");
    pendingParams.current = next;
    setSearchParams(next, { replace: true });
  }
  const items = state.status === "ready" ? state.page.items : [];
  const total = state.status === "ready" ? state.page.total : 0;
  const categories = state.status === "ready" ? state.categories : [];
  const categoryName = (id: string | null) => id ? categories.find(category => category.category_id === id)?.name ?? "Unknown category" : "Uncategorized";
  const start = total ? (currentPage - 1) * PAGE_SIZE + 1 : 0;
  const end = Math.min(currentPage * PAGE_SIZE, total);
  const maxPage = Math.max(1, Math.ceil(total / PAGE_SIZE));

  return <section className="master-list" aria-labelledby="items-title">
    <EngineeringTabs active="items" />
    <div className="page-heading-row"><div><h2 id="items-title">Items</h2><p>Material and product identities, classification, units, and operating status.</p></div>{canManage && <button className="button button--primary" type="button" onClick={() => navigate("/app/master-data/engineering/items/new")}><Plus size={17} />Create item</button>}</div>
    <div className="master-filters item-filters"><div className="field"><label htmlFor="item-search">Search items</label><input id="item-search" value={query} onChange={event => setParam("q", event.target.value)} placeholder="Code or item name" /></div><div className="field"><label htmlFor="item-type-filter">Item type</label><select id="item-type-filter" value={typeParam} onChange={event => setParam("type", event.target.value)}><option value="">All types</option>{TYPES.map(option => <option key={option.value} value={option.value}>{option.label}</option>)}</select></div><div className="field"><label htmlFor="item-category-filter">Category filter</label><select id="item-category-filter" value={categoryId ?? ""} onChange={event => setParam("category", event.target.value)}><option value="">All categories</option>{categories.map(category => <option key={category.category_id} value={category.category_id}>{category.name}</option>)}</select></div><div className="field"><label htmlFor="item-status-filter">Status filter</label><select id="item-status-filter" value={activeParam} onChange={event => setParam("active", event.target.value)}><option value="">All statuses</option><option value="true">Active</option><option value="false">Inactive</option></select></div><button className="button button--secondary" type="button" disabled={state.status === "loading"} onClick={() => setAttempt(value => value + 1)}><RefreshCw size={16} />Refresh</button></div>
    {state.status === "loading" && <div className="master-state"><span className="spinner" /><p>Loading items…</p></div>}
    {state.status === "error" && <div className="inline-error" role="alert"><AlertCircle size={20} /><div><h3>Items unavailable</h3><p>{state.error.message}</p>{state.error instanceof ApiError && state.error.status >= 500 && state.error.requestId && <p>Request ID: {state.error.requestId}</p>}</div><button className="button button--secondary" type="button" onClick={() => setAttempt(value => value + 1)}>Try again</button></div>}
    {state.status === "ready" && items.length === 0 && <div className="master-state"><Boxes size={28} /><h3>No items found</h3><p>{query || itemType || categoryId || active !== null ? "Change the filters or search term." : "Create the first item if you have permission."}</p></div>}
    {state.status === "ready" && items.length > 0 && <><p className="item-scroll-hint">Scroll table horizontally to view more columns.</p><div className="master-table-wrap" role="region" aria-label="Scrollable item results" tabIndex={0}><table className="master-table item-table" aria-label="Item results"><thead><tr><th>Code</th><th>Item</th><th>Type</th><th>UoM</th><th>Category</th><th>HSN</th><th>Status</th><th><span className="sr-only">Actions</span></th></tr></thead><tbody>{items.map(item => <tr key={item.item_id}><td><strong>{item.code}</strong></td><td>{item.name}</td><td>{typeLabel(item.item_type)}</td><td>{item.uom_code}</td><td>{categoryName(item.category_id)}</td><td className="legal-id">{item.hsn_code ?? "Not supplied"}</td><td><StatusBadge active={item.active} /></td><td><Link className="icon-link" to={`/app/master-data/engineering/items/${item.item_id}`} aria-label={`Open item ${item.code}`}><ArrowUpRight size={17} /></Link></td></tr>)}</tbody></table></div><ul className="master-cards" aria-label="Item results">{items.map(item => <li key={item.item_id}><div><span>{item.code}</span><StatusBadge active={item.active} /></div><h3>{item.name}</h3><dl><div><dt>Type</dt><dd>{typeLabel(item.item_type)}</dd></div><div><dt>UoM</dt><dd>{item.uom_code}</dd></div><div><dt>Category</dt><dd>{categoryName(item.category_id)}</dd></div><div><dt>HSN</dt><dd className="legal-id">{item.hsn_code ?? "Not supplied"}</dd></div></dl><Link className="button button--secondary" to={`/app/master-data/engineering/items/${item.item_id}`} aria-label={`Open item ${item.code}`}>Open item<ArrowUpRight size={16} /></Link></li>)}</ul><div className="reference-pagination"><p className="result-summary" aria-live="polite">Showing {start}–{end} of {total} records</p>{total > PAGE_SIZE && <div><button className="button button--secondary" type="button" aria-label="Previous page" disabled={currentPage === 1} onClick={() => setParam("page", String(currentPage - 1), false)}><ChevronLeft size={16} /></button><span>Page {currentPage} of {maxPage}</span><button className="button button--secondary" type="button" aria-label="Next page" disabled={currentPage === maxPage} onClick={() => setParam("page", String(currentPage + 1), false)}><ChevronRight size={16} /></button></div>}</div></>}
  </section>;
}

function ItemCreatePage() {
  const navigate = useNavigate();
  const [state, setState] = useState<FormState>({ status: "loading" });
  const [itemType, setItemType] = useState<ItemType>("RM");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<Error | null>(null);
  useEffect(() => { const controller = new AbortController(); void Promise.all([fetchAllItemCategories(controller.signal), fetchUnitsOfMeasure(controller.signal)]).then(([categories, uoms]) => setState({ status: "ready", categories, uoms })).catch(problem => { if (!controller.signal.aborted) setState({ status: "error", error: problem instanceof Error ? problem : new Error("Item references could not be loaded.") }); }); return () => controller.abort(); }, []);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); const data = new FormData(event.currentTarget);
    const shelfLife = String(data.get("shelf_life_days") ?? "").trim();
    const body: ItemCreate = {
      code: String(data.get("code") ?? "").trim().toUpperCase(), name: String(data.get("name") ?? "").trim(), item_type: itemType,
      uom_code: String(data.get("uom_code") ?? ""), category_id: String(data.get("category_id") ?? "") || null,
      hsn_code: String(data.get("hsn_code") ?? "").trim() || null, shelf_life_days: shelfLife ? Number(shelfLife) : null,
      route_code: itemType === "WIP" || itemType === "FG" ? String(data.get("route_code") ?? "").trim().toUpperCase() || null : null,
    };
    setSaving(true); setError(null);
    try { const item = await createItem(body); navigate(`/app/master-data/engineering/items/${item.item_id}`); }
    catch (problem) { setError(problem instanceof Error ? problem : new Error("The item could not be created.")); }
    finally { setSaving(false); }
  }

  return <section className="master-detail item-create" aria-labelledby="item-create-title"><div className="master-detail__heading"><div><Link to="/app/master-data/engineering"><ArrowLeft size={16} />Back to items</Link><h2 id="item-create-title">Create item</h2></div></div>{state.status === "loading" && <div className="master-state"><span className="spinner" /><p>Loading item form…</p></div>}{state.status === "error" && <div className="inline-error" role="alert"><AlertCircle size={20} /><div><h3>Item form unavailable</h3><p>{state.error.message}</p></div></div>}{state.status === "ready" && <div className="form-panel"><form className="record-form" onSubmit={submit}><div className="record-form__heading"><div><h3>Item master</h3><p>Choose a registered unit of measure and category. Type and UoM cannot change after creation.</p></div></div><div className="record-form__grid"><div className="field"><label htmlFor="new-item-code">Item code</label><input id="new-item-code" name="code" required maxLength={50} /></div><div className="field"><label htmlFor="new-item-name">Item name</label><input id="new-item-name" name="name" required maxLength={160} /></div><div className="field"><label htmlFor="new-item-type">Item type</label><select id="new-item-type" name="item_type" value={itemType} onChange={event => setItemType(event.target.value as ItemType)}>{TYPES.map(option => <option key={option.value} value={option.value}>{option.label}</option>)}</select></div><div className="field"><label htmlFor="new-item-uom">Unit of measure</label><select id="new-item-uom" name="uom_code" required defaultValue=""><option value="" disabled>Select unit of measure</option>{state.uoms.map(uom => <option key={uom.uom_id} value={uom.code}>{uom.code} — {uom.name}</option>)}</select></div><div className="field"><label htmlFor="new-item-category">Category</label><select id="new-item-category" name="category_id" defaultValue=""><option value="">Uncategorized</option>{state.categories.map(category => <option key={category.category_id} value={category.category_id}>{category.code} — {category.name}</option>)}</select></div><div className="field"><label htmlFor="new-item-hsn">HSN code</label><input id="new-item-hsn" name="hsn_code" maxLength={8} inputMode="numeric" /></div><div className="field"><label htmlFor="new-item-shelf-life">Shelf life (days)</label><input id="new-item-shelf-life" name="shelf_life_days" type="number" min={0} max={36500} step={1} /></div>{(itemType === "WIP" || itemType === "FG") && <div className="field"><label htmlFor="new-item-route">Production route code</label><input id="new-item-route" name="route_code" maxLength={50} aria-describedby="route-help" /><small id="route-help">Optional; the backend requires an active route with this code.</small></div>}</div>{error && <RequestError error={error} />}<div className="record-form__actions"><Link className="button button--secondary" to="/app/master-data/engineering">Cancel</Link><button className="button button--primary" type="submit" disabled={saving}>{saving ? "Saving…" : "Save item"}</button></div></form></div>}</section>;
}

export function Items({ me }: { me: Me }) {
  const location = useLocation(); const prefix = "/app/master-data/engineering/items/";
  if (location.pathname === `${prefix}new`) return me.permissions.includes("item.manage") ? <ItemCreatePage /> : <ItemList me={me} />;
  if (location.pathname.startsWith(prefix)) {
    const [itemId, sectionName] = location.pathname.slice(prefix.length).split("/");
    const sections: ItemSection[] = ["master", "policies", "specs", "effective-bom", "engineering-changes", "where-used", "customer-parts"];
    if (itemId && (!sectionName || sections.includes(sectionName as ItemSection))) return <ItemDetail itemId={itemId} section={sectionName as ItemSection || "master"} me={me} />;
  }
  return <ItemList me={me} />;
}
