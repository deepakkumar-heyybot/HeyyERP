import { useEffect, useMemo, useRef, useState, type FormEvent, type KeyboardEvent } from "react";
import { ArrowLeft, PackageSearch, Plus, RefreshCw } from "lucide-react";
import { Link, useLocation, useNavigate } from "react-router-dom";
import {
  ApiError, type Item, type Me, type Reservation, type ReservationReferenceType, type StockRow, type Store, type Unit,
  createReservation, fetchAllItems, fetchAllOrganizationUnits, fetchAllStores, fetchStockLot, listReservations, releaseReservations,
} from "../api-client";
import { InventoryTabs } from "./InventoryTabs";
import { FormPageTemplate, RegisterPageTemplate } from "./PageTemplates";
import { RequestError } from "./RequestError";

const PATH = "/app/inventory/reservations";
const REFERENCE_TYPES: ReservationReferenceType[] = ["WORK_ORDER", "SALES_ORDER", "DISPATCH", "OTHER"];
type Load<T> = { status: "loading" } | { status: "ready"; data: T } | { status: "error"; error: Error };
type Enriched = { reservation: Reservation; lot: StockRow };
type Reference = { reference_type: ReservationReferenceType; reference: string };
const failure = (value: unknown, fallback: string) => value instanceof Error ? value : new Error(fallback);
const label = (value: string) => value.toLowerCase().replaceAll("_", " ").replace(/^./, letter => letter.toUpperCase());
const allowed = (me: Me, unitId: string) => me.all_plants || me.unit_ids.includes(unitId);
const validQuantity = (value: string) => {
  if (!/^\d{1,10}(?:\.\d{1,3})?$/.test(value)) return false;
  const [whole, fraction = ""] = value.split(".");
  const scaled = BigInt(whole) * 1000n + BigInt(fraction.padEnd(3, "0"));
  return scaled > 0n && scaled <= 1_000_000_000_000n;
};

function ErrorNotice({ error }: { error: Error }) {
  return <><RequestError error={error} />{error instanceof ApiError && error.status < 500 && error.requestId && <p>Request ID: {error.requestId}</p>}</>;
}

async function enrich(rows: Reservation[], signal: AbortSignal): Promise<Enriched[]> {
  const ids = [...new Set(rows.map(row => row.lot_id))];
  const byId = new Map<string, StockRow>();
  for (let offset = 0; offset < ids.length; offset += 6) {
    if (signal.aborted) throw new DOMException("Lot loading aborted", "AbortError");
    const lots = await Promise.all(ids.slice(offset, offset + 6).map(id => fetchStockLot(id, signal)));
    for (const lot of lots) byId.set(lot.lot_id, lot);
  }
  if (signal.aborted) throw new DOMException("Lot loading aborted", "AbortError");
  return rows.map(reservation => {
    const lot = byId.get(reservation.lot_id);
    if (!lot) throw new Error("A reservation lot could not be loaded.");
    return { reservation, lot };
  });
}

function ReservationCreatePage({ me }: { me: Me }) {
  const navigate = useNavigate();
  const location = useLocation();
  const [sources, setSources] = useState<Load<{ stores: Store[]; items: Item[]; plants: Unit[] }>>({ status: "loading" });
  const [attempt, setAttempt] = useState(0);
  const [store, setStore] = useState("");
  const [item, setItem] = useState("");
  const [quantity, setQuantity] = useState("");
  const [referenceType, setReferenceType] = useState<ReservationReferenceType>("WORK_ORDER");
  const [reference, setReference] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<Error | null>(null);
  useEffect(() => {
    const controller = new AbortController();
    setSources({ status: "loading" });
    void Promise.all([fetchAllStores(true, null, null, controller.signal), fetchAllItems(controller.signal), fetchAllOrganizationUnits(controller.signal)])
      .then(([stores, items, plants]) => { if (!controller.signal.aborted) setSources({ status: "ready", data: { stores: stores.filter(value => value.active && allowed(me, value.unit_id)), items: items.filter(value => value.active), plants } }); })
      .catch(value => { if (!controller.signal.aborted) setSources({ status: "error", error: failure(value, "Reservation options could not be loaded.") }); });
    return () => controller.abort();
  }, [attempt, me.all_plants, me.unit_ids]);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (busy) return;
    setError(null);
    if (sources.status !== "ready" || !sources.data.stores.some(value => value.store_id === store) || !sources.data.items.some(value => value.item_id === item)) { setError(new Error("Select an active store in an authorized plant and an active item.")); return; }
    if (!validQuantity(quantity)) { setError(new Error("Enter a positive quantity with up to three decimal places.")); return; }
    if (!reference.trim()) { setError(new Error("Enter a reference.")); return; }
    setBusy(true);
    try {
      await createReservation({ store_id: store, item_id: item, quantity, reference_type: referenceType, reference: reference.trim() });
      navigate(`${PATH}${location.search}`);
    } catch (value) { setError(failure(value, "Reservation could not be created.")); }
    finally { setBusy(false); }
  }

  const selectedStore = sources.status === "ready" ? sources.data.stores.find(value => value.store_id === store) : undefined;
  const selectedPlant = sources.status === "ready" && selectedStore ? sources.data.plants.find(plant => plant.unit_id === selectedStore.unit_id) : undefined;

  return <FormPageTemplate className="reservation-create" title="New reservation" headingId="reservation-create-title" description="Reserve stock for a reference. The backend selects eligible lots." navigation={<InventoryTabs active="reservations" canIssue />} back={<Link to={`${PATH}${location.search}`}><ArrowLeft size={16} />Back to reservations</Link>} formTitle="Reservation details" onSubmit={submit} formActions={<><Link className="button button--secondary" to={`${PATH}${location.search}`}>Cancel</Link><button className="button button--primary" type="submit" disabled={busy || sources.status !== "ready"}>{busy ? "Creating…" : "Create reservation"}</button></>}>
    {sources.status === "loading" && <p role="status">Loading reservation options…</p>}
    {sources.status === "error" && <><ErrorNotice error={sources.error} /><button className="button button--secondary" type="button" onClick={() => setAttempt(value => value + 1)}>Try again</button></>}
    <fieldset disabled={busy || sources.status !== "ready"} className="issue-planner__fieldset"><div className="record-form__grid">
      <div className="field"><label htmlFor="reservation-create-store">Store</label><select id="reservation-create-store" required value={store} aria-describedby={selectedStore ? "reservation-selected-store" : undefined} onChange={event => setStore(event.target.value)}><option value="">Select active store</option>{sources.status === "ready" && sources.data.stores.map(value => <option key={value.store_id} value={value.store_id}>{value.code} — {value.name} ({sources.data.plants.find(plant => plant.unit_id === value.unit_id)?.name ?? value.unit_id})</option>)}</select>{selectedStore && <small id="reservation-selected-store" style={{ overflowWrap: "anywhere" }}>Selected store: {selectedStore.code} — {selectedStore.name} ({selectedPlant?.name ?? selectedStore.unit_id})</small>}</div>
      <div className="field"><label htmlFor="reservation-create-item">Item</label><select id="reservation-create-item" required value={item} onChange={event => setItem(event.target.value)}><option value="">Select active item</option>{sources.status === "ready" && sources.data.items.map(value => <option key={value.item_id} value={value.item_id}>{value.code} — {value.name}</option>)}</select></div>
      <div className="field"><label htmlFor="reservation-create-quantity">Quantity</label><input id="reservation-create-quantity" inputMode="decimal" required value={quantity} onChange={event => setQuantity(event.target.value)} /><small>Up to three decimal places.</small></div>
      <div className="field"><label htmlFor="reservation-create-reference-type">Reference type</label><select id="reservation-create-reference-type" value={referenceType} onChange={event => setReferenceType(event.target.value as ReservationReferenceType)}>{REFERENCE_TYPES.map(value => <option key={value} value={value}>{label(value)}</option>)}</select></div>
      <div className="field"><label htmlFor="reservation-create-reference">Reference</label><input id="reservation-create-reference" required maxLength={60} value={reference} onChange={event => setReference(event.target.value)} /></div>
    </div></fieldset>{error && <ErrorNotice error={error} />}
  </FormPageTemplate>;
}

function ReservationList({ me }: { me: Me }) {
  const navigate = useNavigate();
  const canIssue = me.permissions.includes("inv.issue");
  const [status, setStatus] = useState("ACTIVE");
  const [referenceType, setReferenceType] = useState<ReservationReferenceType | "">("");
  const [reference, setReference] = useState("");
  const [plant, setPlant] = useState("");
  const [store, setStore] = useState("");
  const [item, setItem] = useState("");
  const [attempt, setAttempt] = useState(0);
  const [sources, setSources] = useState<Load<{ plants: Unit[]; stores: Store[]; items: Item[] }>>({ status: "loading" });
  const [register, setRegister] = useState<Load<Enriched[]>>({ status: "loading" });
  const [selected, setSelected] = useState<Reference | null>(null);
  const [preview, setPreview] = useState<Load<Enriched[]>>({ status: "loading" });
  const [actionError, setActionError] = useState<Error | null>(null);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState("");
  const triggerRef = useRef<HTMLButtonElement | null>(null);
  const cancelRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    const controller = new AbortController();
    setSources({ status: "loading" });
    void Promise.all([fetchAllOrganizationUnits(controller.signal), fetchAllStores(null, null, null, controller.signal), fetchAllItems(controller.signal)])
      .then(([plants, stores, items]) => { if (!controller.signal.aborted) setSources({ status: "ready", data: { plants: plants.filter(value => allowed(me, value.unit_id)), stores: stores.filter(value => allowed(me, value.unit_id)), items } }); })
      .catch(value => { if (!controller.signal.aborted) setSources({ status: "error", error: failure(value, "Filter options could not be loaded.") }); });
    return () => controller.abort();
  }, [attempt, me.all_plants, me.unit_ids]);
  useEffect(() => {
    const controller = new AbortController();
    setRegister({ status: "loading" });
    void listReservations({ status, referenceType: referenceType || null, reference: reference.trim() || null }, controller.signal)
      .then(rows => enrich(rows, controller.signal))
      .then(rows => { if (!controller.signal.aborted) setRegister({ status: "ready", data: rows.filter(row => allowed(me, row.lot.unit_id)) }); })
      .catch(value => { if (!controller.signal.aborted) setRegister({ status: "error", error: failure(value, "Reservations could not be loaded.") }); });
    return () => controller.abort();
  }, [attempt, status, referenceType, reference, me.all_plants, me.unit_ids]);
  useEffect(() => {
    if (!selected) return;
    const controller = new AbortController();
    setPreview({ status: "loading" });
    void listReservations({ status: "ACTIVE", referenceType: selected.reference_type, reference: selected.reference }, controller.signal)
      .then(rows => enrich(rows.filter(row => row.status === "ACTIVE" && row.reference_type === selected.reference_type && row.reference === selected.reference), controller.signal))
      .then(rows => { if (!controller.signal.aborted) setPreview({ status: "ready", data: rows.filter(row => allowed(me, row.lot.unit_id)) }); })
      .catch(value => { if (!controller.signal.aborted) setPreview({ status: "error", error: failure(value, "Active reservations could not be reviewed.") }); });
    return () => controller.abort();
  }, [selected, me.all_plants, me.unit_ids]);
  useEffect(() => { if (selected) cancelRef.current?.focus(); }, [selected]);

  const visible = useMemo(() => register.status === "ready" ? register.data.filter(({ lot }) => (!plant || lot.unit_id === plant) && (!store || lot.store_id === store) && (!item || lot.item_id === item)) : [], [register, plant, store, item]);
  function close() { if (busy) return; setSelected(null); setActionError(null); requestAnimationFrame(() => triggerRef.current?.focus()); }
  function keys(event: KeyboardEvent<HTMLElement>) {
    if (event.key === "Escape" && !busy) { event.preventDefault(); close(); return; }
    if (event.key !== "Tab") return;
    const controls = Array.from(event.currentTarget.querySelectorAll<HTMLElement>('button:not(:disabled), input:not(:disabled), select:not(:disabled), a[href]'));
    if (!controls.length) return;
    if (event.shiftKey && document.activeElement === controls[0]) { event.preventDefault(); controls[controls.length - 1].focus(); }
    else if (!event.shiftKey && document.activeElement === controls[controls.length - 1]) { event.preventDefault(); controls[0].focus(); }
  }
  async function release() {
    if (!selected || !canIssue || busy || preview.status !== "ready" || preview.data.length === 0) return;
    setBusy(true); setActionError(null);
    try {
      const result = await releaseReservations(selected);
      setSelected(null); setNotice(`${result.released} reservation${result.released === 1 ? "" : "s"} released.`); setAttempt(value => value + 1);
      requestAnimationFrame(() => triggerRef.current?.isConnected && triggerRef.current.focus());
    } catch (value) { setActionError(failure(value, "Reservations could not be released.")); }
    finally { setBusy(false); }
  }
  function releaseButton(row: Reservation) {
    return canIssue && row.status === "ACTIVE" ? <button className="button button--secondary" type="button" onClick={event => { triggerRef.current = event.currentTarget; setNotice(""); setActionError(null); setSelected({ reference_type: row.reference_type, reference: row.reference }); }}>Release reference</button> : null;
  }

  return <RegisterPageTemplate title="Reservations" headingId="reservations-title" className="stock-list" description="Review reserved quantities and their linked stock lots." navigation={<InventoryTabs active="reservations" canIssue={canIssue} />} actions={canIssue && <button className="button button--primary" type="button" onClick={() => navigate(`${PATH}/new`)}><Plus size={16} />New reservation</button>} filtersClassName="stock-filters" filters={<>
    <div className="field"><label htmlFor="reservation-plant">Plant filter</label><select id="reservation-plant" value={plant} onChange={event => { setPlant(event.target.value); setStore(""); }}><option value="">All authorized plants</option>{sources.status === "ready" && sources.data.plants.map(value => <option key={value.unit_id} value={value.unit_id}>{value.code} — {value.name}</option>)}</select></div>
    <div className="field"><label htmlFor="reservation-store">Store filter</label><select id="reservation-store" value={store} onChange={event => setStore(event.target.value)}><option value="">All stores</option>{sources.status === "ready" && sources.data.stores.filter(value => !plant || value.unit_id === plant).map(value => <option key={value.store_id} value={value.store_id}>{value.code} — {value.name} ({sources.data.plants.find(unit => unit.unit_id === value.unit_id)?.name ?? value.unit_id})</option>)}</select></div>
    <div className="field"><label htmlFor="reservation-item">Item filter</label><select id="reservation-item" value={item} onChange={event => setItem(event.target.value)}><option value="">All items</option>{sources.status === "ready" && sources.data.items.map(value => <option key={value.item_id} value={value.item_id}>{value.code} — {value.name}</option>)}</select></div>
    <div className="field"><label htmlFor="reservation-status">Reservation status</label><select id="reservation-status" value={status} onChange={event => setStatus(event.target.value)}><option value="ACTIVE">Active</option><option value="RELEASED">Released</option><option value="CONSUMED">Consumed</option></select></div>
    <div className="field"><label htmlFor="reservation-reference-type">Reference type filter</label><select id="reservation-reference-type" value={referenceType} onChange={event => setReferenceType(event.target.value as ReservationReferenceType | "")}><option value="">All reference types</option>{REFERENCE_TYPES.map(value => <option key={value} value={value}>{label(value)}</option>)}</select></div>
    <div className="field"><label htmlFor="reservation-reference">Reference filter</label><input id="reservation-reference" value={reference} onChange={event => setReference(event.target.value)} placeholder="Reference" /></div>
    <button className="button button--secondary" type="button" disabled={register.status === "loading"} onClick={() => setAttempt(value => value + 1)}><RefreshCw size={16} />Refresh</button>
  </>}>
    {notice && <p className="success-banner" role="status">{notice}</p>}
    {sources.status === "error" && <ErrorNotice error={sources.error} />}
    {register.status === "loading" && <div className="master-state" role="status"><span className="spinner" /><p>Loading reservations…</p></div>}
    {register.status === "error" && <><ErrorNotice error={register.error} /><button className="button button--secondary" type="button" onClick={() => setAttempt(value => value + 1)}>Try again</button></>}
    {register.status === "ready" && !visible.length && <div className="master-state"><PackageSearch size={27} /><h3>No reservations found</h3><p>Adjust the filters to see other reservations.</p></div>}
    {register.status === "ready" && visible.length > 0 && <>
      <div className="master-table-wrap" role="region" aria-label="Scrollable reservation results" tabIndex={0}><p style={{ margin: "12px 13px", fontSize: "12px" }}>Scroll the table horizontally to view all reservation fields.</p><table className="master-table" aria-label="Reservation results"><thead><tr><th>Reference</th><th>Item</th><th>Lot</th><th>Plant</th><th>Store</th><th>Quantity</th><th>Lot expiry</th><th>Status</th><th>Created</th>{canIssue && <th>Actions</th>}</tr></thead><tbody>{visible.map(({ reservation: row, lot }) => <tr key={row.reservation_id}><td>{label(row.reference_type)}<br /><strong>{row.reference}</strong></td><td>{row.item_code}</td><td>{row.lot_no}</td><td>{lot.unit_code}</td><td>{row.store_code}</td><td>{row.quantity} {lot.uom_code}</td><td>{lot.expiry_at?.slice(0, 10) ?? "Not set"}{lot.expired && " · Expired"}</td><td>{label(row.status)}</td><td>{row.created_at.slice(0, 10)}</td>{canIssue && <td>{releaseButton(row)}</td>}</tr>)}</tbody></table></div>
      <ul className="master-cards" aria-label="Reservation results">{visible.map(({ reservation: row, lot }) => <li key={row.reservation_id}><div><strong>{row.reference}</strong><span className="status-badge">{label(row.status)}</span></div><h3>{row.item_code} · {row.lot_no}</h3><dl><div><dt>Reference type</dt><dd>{label(row.reference_type)}</dd></div><div><dt>Plant</dt><dd>{lot.unit_code}</dd></div><div><dt>Store</dt><dd>{row.store_code}</dd></div><div><dt>Quantity</dt><dd>{row.quantity} {lot.uom_code}</dd></div><div><dt>Lot expiry</dt><dd>{lot.expiry_at?.slice(0, 10) ?? "Not set"}{lot.expired && " · Expired"}</dd></div><div><dt>Created</dt><dd>{row.created_at.slice(0, 10)}</dd></div></dl>{releaseButton(row)}</li>)}</ul>
      <p className="result-summary" aria-live="polite">Showing {visible.length} reservations</p>
    </>}
    {selected && <div className="bom-editor-backdrop"><section className="bom-editor" role="dialog" aria-modal="true" aria-labelledby="reservation-release-title" onKeyDown={keys}><div className="bom-editor__header"><div><h3 id="reservation-release-title">Release reservations</h3><p>Release all active reservations for {label(selected.reference_type)} <strong>{selected.reference}</strong> across authorized plants, including matches hidden by the current filters.</p><p>These are the current matches; the backend will release all active matches when confirmed.</p></div></div><div className="bom-editor__body">
      {preview.status === "loading" && <p role="status">Loading active matches…</p>}
      {preview.status === "error" && <ErrorNotice error={preview.error} />}
      {preview.status === "ready" && (preview.data.length ? <><p>{preview.data.length} active reservation{preview.data.length === 1 ? "" : "s"} found:</p><ul>{preview.data.map(({ reservation: row, lot }) => <li key={row.reservation_id}>{lot.unit_code} · {row.store_code} · {row.item_code} · {row.lot_no} · {row.quantity} {lot.uom_code}</li>)}</ul></> : <p>No active reservations found for this reference.</p>)}
      {actionError && <ErrorNotice error={actionError} />}
    </div><div className="bom-editor__actions"><button ref={cancelRef} className="button button--secondary" type="button" disabled={busy} onClick={close}>Cancel</button><button className="button button--primary" type="button" disabled={busy || preview.status !== "ready" || !preview.data.length} onClick={() => void release()}>{busy ? "Releasing…" : "Confirm release"}</button></div></section></div>}
  </RegisterPageTemplate>;
}

export function Reservations({ me }: { me: Me }) {
  const location = useLocation();
  if (!me.modules.includes("inventory")) return <section className="module-pending"><h2>Reservations unavailable</h2><p>The inventory module is required to read reservations.</p></section>;
  if (location.pathname === `${PATH}/new`) return me.permissions.includes("inv.issue") ? <ReservationCreatePage me={me} /> : <section className="module-pending"><h2>Reservation creation unavailable</h2><p>Creating reservations requires inv.issue permission.</p></section>;
  return <ReservationList me={me} />;
}
