import { FormEvent, useEffect, useMemo, useRef, useState } from "react";
import { AlertCircle, ArrowLeft, ArrowUpRight, Pencil, Plus, RefreshCw, Warehouse, X } from "lucide-react";
import { Link, useLocation, useNavigationType, useSearchParams } from "react-router-dom";
import {
  ApiError,
  Me,
  Store,
  StoreCreate,
  StoreType,
  StoreUpdate,
  Unit,
  createStore,
  fetchAllOrganizationUnits,
  fetchAllStores,
  fetchStore,
  updateStore,
} from "../api-client";
import { OrganizationTabs } from "./OrganizationTabs";

type ListState = { status: "loading" } | { status: "ready"; items: Store[] } | { status: "error"; error: Error };
type DetailState = { status: "loading" } | { status: "ready"; item: Store } | { status: "error"; error: Error };

const STORE_TYPES: Array<{ value: StoreType; label: string }> = [
  { value: "RAW_MATERIAL", label: "Raw material" },
  { value: "WIP", label: "Work in progress" },
  { value: "FINISHED_GOODS", label: "Finished goods" },
  { value: "SPARES", label: "Spares" },
  { value: "OTHER", label: "Other" },
];

function typeLabel(value: StoreType) {
  return STORE_TYPES.find(item => item.value === value)?.label ?? value;
}

function StatusBadge({ active }: { active: boolean }) {
  return <span className={`status-badge status-badge--${active ? "approved" : "cancelled"}`}>{active ? "Active" : "Inactive"}</span>;
}

function RequestError({ error, remainsActive = false }: { error: Error; remainsActive?: boolean }) {
  return <div className="decision-error" role="alert"><AlertCircle size={17} /><div><span>{error.message}</span>{remainsActive && error instanceof ApiError && error.status === 409 && <span>No changes were saved. The store remains active.</span>}{error instanceof ApiError && error.status >= 500 && error.requestId && <span>Request ID: <code>{error.requestId}</code></span>}</div></div>;
}

function StoreForm({
  mode, item, plants, busy, error, onCancel, onCreate, onUpdate,
}: {
  mode: "create" | "edit";
  item?: Store;
  plants: Unit[];
  busy: boolean;
  error: Error | null;
  onCancel: () => void;
  onCreate?: (body: StoreCreate) => Promise<void>;
  onUpdate?: (body: StoreUpdate) => Promise<void>;
}) {
  const [code, setCode] = useState(item?.code ?? "");
  const [name, setName] = useState(item?.name ?? "");
  const [unitId, setUnitId] = useState(item?.unit_id ?? "");
  const [storeType, setStoreType] = useState<StoreType>(item?.store_type ?? "RAW_MATERIAL");
  const [active, setActive] = useState(item?.active ?? true);

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (mode === "create") await onCreate?.({ code: code.trim().toUpperCase(), name: name.trim(), unit_id: unitId, store_type: storeType });
    else await onUpdate?.({ name: name.trim(), store_type: storeType, active });
  }

  const blockedDeactivation = Boolean(item?.active && !active);
  return (
    <form className="record-form" onSubmit={submit}>
      <div className="record-form__heading"><div><p className="eyebrow">{mode === "create" ? "New store" : item?.code}</p><h3>{mode === "create" ? "Create store" : "Edit store"}</h3></div><button type="button" aria-label="Close store form" onClick={onCancel} disabled={busy}><X size={20} /></button></div>
      <div className="record-form__grid">
        {mode === "create" && <div className="field"><label htmlFor="create-store-code">Store code</label><input id="create-store-code" required maxLength={50} value={code} onChange={event => setCode(event.target.value)} /></div>}
        <div className="field"><label htmlFor={`${mode}-store-name`}>Store name</label><input id={`${mode}-store-name`} required maxLength={120} value={name} onChange={event => setName(event.target.value)} /></div>
        {mode === "create" ? <div className="field"><label htmlFor="create-store-plant">Plant</label><select id="create-store-plant" required value={unitId} onChange={event => setUnitId(event.target.value)}><option value="">Select a plant</option>{plants.filter(plant => plant.active).map(plant => <option key={plant.unit_id} value={plant.unit_id}>{plant.name}</option>)}</select></div> : <div className="field"><span className="field__label">Plant</span><p className="read-only-value">{plants.find(plant => plant.unit_id === item?.unit_id)?.name ?? "Unknown plant"}</p></div>}
        <div className="field"><label htmlFor={`${mode}-store-type`}>Store type</label><select id={`${mode}-store-type`} value={storeType} onChange={event => setStoreType(event.target.value as StoreType)}>{STORE_TYPES.map(type => <option key={type.value} value={type.value}>{type.label}</option>)}</select></div>
        {mode === "edit" && <label className="checkbox-field"><input type="checkbox" checked={active} onChange={event => setActive(event.target.checked)} />Active store</label>}
      </div>
      {error && <RequestError error={error} remainsActive={blockedDeactivation} />}
      <div className="form-actions"><button className="button button--secondary" type="button" onClick={onCancel} disabled={busy}>Cancel</button><button className="button button--primary" type="submit" disabled={busy || (mode === "create" && !unitId)}>{busy ? "Saving…" : mode === "create" ? "Save store" : "Save changes"}</button></div>
    </form>
  );
}

function StoreList({ me }: { me: Me }) {
  const [searchParams, setSearchParams] = useSearchParams();
  const navigationType = useNavigationType();
  const pendingParams = useRef(new URLSearchParams(searchParams));
  useEffect(() => { if (navigationType !== "REPLACE") pendingParams.current = new URLSearchParams(searchParams); }, [navigationType, searchParams]);
  const query = searchParams.get("q") ?? "";
  const unitId = searchParams.get("unit_id") ?? "";
  const activeParam = searchParams.get("active") ?? "";
  const typeParam = searchParams.get("store_type") ?? "";
  const active = activeParam === "true" ? true : activeParam === "false" ? false : null;
  const storeType = STORE_TYPES.some(type => type.value === typeParam) ? typeParam as StoreType : null;
  const [state, setState] = useState<ListState>({ status: "loading" });
  const [plants, setPlants] = useState<Unit[]>([]);
  const [attempt, setAttempt] = useState(0);
  const [creating, setCreating] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<Error | null>(null);
  const [notice, setNotice] = useState("");
  const canManage = me.permissions.includes("org.manage");

  useEffect(() => {
    const controller = new AbortController();
    setState({ status: "loading" });
    Promise.all([fetchAllStores(active, unitId || null, storeType, controller.signal), fetchAllOrganizationUnits(controller.signal)]).then(([items, allPlants]) => {
      setPlants(allPlants); setState({ status: "ready", items });
    }).catch(problem => { if (!controller.signal.aborted) setState({ status: "error", error: problem instanceof Error ? problem : new Error("Stores could not be loaded.") }); });
    return () => controller.abort();
  }, [active, attempt, storeType, unitId]);

  function setFilter(name: string, value: string) {
    const next = new URLSearchParams(pendingParams.current);
    if (value) next.set(name, value); else next.delete(name);
    pendingParams.current = next;
    setSearchParams(next, { replace: true });
  }
  const items = state.status === "ready" ? state.items : [];
  const filtered = useMemo(() => {
    const normalized = query.trim().toLowerCase();
    return items.filter(item => {
      if (unitId && item.unit_id !== unitId) return false;
      if (active !== null && item.active !== active) return false;
      if (storeType && item.store_type !== storeType) return false;
      return !normalized || `${item.code} ${item.name}`.toLowerCase().includes(normalized);
    });
  }, [active, items, query, storeType, unitId]);
  const plantName = (id: string) => plants.find(plant => plant.unit_id === id)?.name ?? "Unknown plant";

  async function submitCreate(body: StoreCreate) {
    setSaving(true); setError(null);
    try { await createStore(body); setCreating(false); setNotice("Store created."); setAttempt(value => value + 1); }
    catch (problem) { setError(problem instanceof Error ? problem : new Error("The store could not be created.")); }
    finally { setSaving(false); }
  }

  return (
    <section className="master-list" aria-labelledby="stores-title">
      <OrganizationTabs active="stores" />
      <div className="page-heading-row"><div><p className="eyebrow">Organization master</p><h2 id="stores-title">Stores</h2><p>Plant inventory locations, material purpose, and operating status.</p></div>{canManage && <button className="button button--primary" type="button" onClick={() => { setCreating(true); setError(null); }}><Plus size={17} />Create store</button>}</div>
      {notice && <p className="success-banner" role="status">{notice}</p>}
      {creating && <div className="form-panel"><StoreForm mode="create" plants={plants} busy={saving} error={error} onCancel={() => setCreating(false)} onCreate={submitCreate} /></div>}
      <div className="master-filters master-filters--stores">
        <div className="field"><label htmlFor="store-search">Search stores</label><input id="store-search" value={query} onChange={event => setFilter("q", event.target.value)} placeholder="Code or name" /></div>
        <div className="field"><label htmlFor="store-type-filter">Store type filter</label><select id="store-type-filter" value={typeParam} onChange={event => setFilter("store_type", event.target.value)}><option value="">All types</option>{STORE_TYPES.map(type => <option key={type.value} value={type.value}>{type.label}</option>)}</select></div>
        <div className="field"><label htmlFor="store-plant-filter">Plant filter</label><select id="store-plant-filter" value={unitId} onChange={event => setFilter("unit_id", event.target.value)}><option value="">All plants</option>{plants.map(plant => <option key={plant.unit_id} value={plant.unit_id}>{plant.name}</option>)}</select></div>
        <div className="field"><label htmlFor="store-status-filter">Status filter</label><select id="store-status-filter" value={activeParam} onChange={event => setFilter("active", event.target.value)}><option value="">All statuses</option><option value="true">Active</option><option value="false">Inactive</option></select></div>
        <button className="button button--secondary" type="button" onClick={() => setAttempt(value => value + 1)} disabled={state.status === "loading"}><RefreshCw size={16} />Refresh</button>
      </div>
      {state.status === "loading" && <div className="master-state"><span className="spinner" /><p>Loading stores…</p></div>}
      {state.status === "error" && <div className="inline-error"><AlertCircle size={20} /><div><h3>Stores unavailable</h3><p>{state.error.message}</p></div><button className="button button--secondary" onClick={() => setAttempt(value => value + 1)}>Try again</button></div>}
      {state.status === "ready" && filtered.length === 0 && <div className="master-state"><Warehouse size={28} /><h3>No stores found</h3><p>Change the filters or search term.</p></div>}
      {state.status === "ready" && filtered.length > 0 && <>
        <div className="master-table-wrap"><table className="master-table store-table" aria-label="Store results"><thead><tr><th>Code</th><th>Store</th><th>Plant</th><th>Type</th><th>Status</th><th><span className="sr-only">Actions</span></th></tr></thead><tbody>{filtered.map(item => <tr key={item.store_id}><td><strong>{item.code}</strong></td><td>{item.name}</td><td>{plantName(item.unit_id)}</td><td>{typeLabel(item.store_type)}</td><td><StatusBadge active={item.active} /></td><td><Link className="icon-link" to={`/app/master-data/organization/stores/${item.store_id}`} aria-label={`Open store ${item.name}`}><ArrowUpRight size={17} /></Link></td></tr>)}</tbody></table></div>
        <ul className="master-cards" aria-label="Store results">{filtered.map(item => <li key={item.store_id}><div><span>{item.code}</span><StatusBadge active={item.active} /></div><h3>{item.name}</h3><dl><div><dt>Plant</dt><dd>{plantName(item.unit_id)}</dd></div><div><dt>Type</dt><dd>{typeLabel(item.store_type)}</dd></div></dl><Link className="button button--secondary" to={`/app/master-data/organization/stores/${item.store_id}`} aria-label={`Open store ${item.name}`}>Open store<ArrowUpRight size={16} /></Link></li>)}</ul>
        <p className="result-summary" aria-live="polite">Showing {filtered.length} of {items.length} stores</p>
      </>}
    </section>
  );
}

function StoreDetail({ me, storeId }: { me: Me; storeId: string }) {
  const [state, setState] = useState<DetailState>({ status: "loading" });
  const [plants, setPlants] = useState<Unit[]>([]);
  const [attempt, setAttempt] = useState(0);
  const [editing, setEditing] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<Error | null>(null);
  const [notice, setNotice] = useState("");
  const canManage = me.permissions.includes("org.manage");
  const headingRef = useRef<HTMLHeadingElement>(null);
  useEffect(() => {
    const controller = new AbortController();
    setState({ status: "loading" });
    Promise.all([fetchStore(storeId, controller.signal), fetchAllOrganizationUnits(controller.signal)]).then(([item, allPlants]) => {
      setPlants(allPlants); setState({ status: "ready", item });
    }).catch(problem => { if (!controller.signal.aborted) setState({ status: "error", error: problem instanceof Error ? problem : new Error("The store could not be loaded.") }); });
    return () => controller.abort();
  }, [attempt, storeId]);
  useEffect(() => { if (state.status !== "loading") headingRef.current?.focus(); }, [state.status]);
  async function submitUpdate(body: StoreUpdate) {
    setSaving(true); setError(null);
    try { const item = await updateStore(storeId, body); setState({ status: "ready", item }); setEditing(false); setNotice("Store updated."); }
    catch (problem) { setError(problem instanceof Error ? problem : new Error("The store could not be updated.")); }
    finally { setSaving(false); }
  }
  if (state.status === "loading") return <div className="master-state"><span className="spinner" /><p>Loading store…</p></div>;
  if (state.status === "error") return <div className="master-state"><AlertCircle size={28} /><h2 ref={headingRef} tabIndex={-1}>Store unavailable</h2><p>{state.error.message}</p><button className="button button--secondary" onClick={() => setAttempt(value => value + 1)}>Try again</button></div>;
  const item = state.item;
  const plant = plants.find(candidate => candidate.unit_id === item.unit_id)?.name ?? "Unknown plant";
  return <section className="master-detail" aria-labelledby="store-detail-title"><OrganizationTabs active="stores" /><div className="master-detail__heading"><div><Link to="/app/master-data/organization/stores"><ArrowLeft size={16} />Back to stores</Link><p className="eyebrow">{item.code}</p><h2 id="store-detail-title" ref={headingRef} tabIndex={-1}>{item.name}</h2><StatusBadge active={item.active} /></div>{canManage && <div><button className="button button--secondary" type="button" onClick={() => { setEditing(true); setError(null); }}><Pencil size={16} />Edit store</button></div>}</div>{notice && <p className="success-banner" role="status">{notice}</p>}{editing && <div className="form-panel"><StoreForm mode="edit" item={item} plants={plants} busy={saving} error={error} onCancel={() => setEditing(false)} onUpdate={submitUpdate} /></div>}<section className="record-panel"><div className="record-panel__heading"><div><h3>Store details</h3><p>Plant scope, inventory purpose, and availability.</p></div><Warehouse size={20} /></div><dl className="record-facts"><div><dt>Code</dt><dd>{item.code}</dd></div><div><dt>Plant</dt><dd>{plant}</dd></div><div><dt>Store type</dt><dd>{typeLabel(item.store_type)}</dd></div><div><dt>Status</dt><dd>{item.active ? "Active" : "Inactive"}</dd></div></dl></section></section>;
}

export function OrganizationStores({ me }: { me: Me }) {
  const location = useLocation();
  const prefix = "/app/master-data/organization/stores/";
  const storeId = location.pathname.startsWith(prefix) ? location.pathname.slice(prefix.length) : "";
  return storeId ? <StoreDetail me={me} storeId={storeId} /> : <StoreList me={me} />;
}
