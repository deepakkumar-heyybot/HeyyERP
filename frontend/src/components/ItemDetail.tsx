import { FormEvent, KeyboardEvent, useEffect, useRef, useState } from "react";
import { AlertCircle, ArrowLeft, Boxes, Pencil, Plus, ShieldAlert } from "lucide-react";
import { Link } from "react-router-dom";
import {
  ApiError, Customer, Item, ItemBom, ItemCategory, ItemCustomerPart, ItemEngineeringChange,
  ItemPolicy, ItemPolicyUpdate, ItemSpecification, ItemSpecificationUpdate, ItemUpdate, Me,
  Store, Unit, addItemCustomerPart, fetchAllItemCategories, fetchAllOrganizationUnits,
  fetchAllStores, fetchItem, fetchItemCustomerParts, fetchItemEffectiveBom,
  fetchItemEngineeringChanges, fetchItemPolicies, fetchItemSpecifications, fetchItemWhereUsed,
  listCustomers, setItemPolicy, setItemSpecification, updateItem,
} from "../api-client";

export type ItemSection = "master" | "policies" | "specs" | "effective-bom" | "engineering-changes" | "where-used" | "customer-parts";
const SECTIONS: Array<{ id: ItemSection; label: string }> = [
  { id: "master", label: "Master details" }, { id: "policies", label: "Plant policies" },
  { id: "specs", label: "Specifications" }, { id: "effective-bom", label: "Effective BOM" },
  { id: "engineering-changes", label: "Engineering changes" }, { id: "where-used", label: "Where used" },
  { id: "customer-parts", label: "Customer parts" },
];
type MasterState = { status: "loading" } | { status: "error"; error: Error } | { status: "ready"; item: Item; categories: ItemCategory[] };
type SectionState = { status: "idle" | "loading" | "effective-empty" } | { status: "error"; error: Error } |
  { status: "policies"; policies: ItemPolicy[]; units: Unit[]; stores: Store[] } |
  { status: "specs"; specs: ItemSpecification[] } | { status: "effective-bom"; bom: ItemBom } |
  { status: "engineering-changes"; changes: ItemEngineeringChange[] } |
  { status: "where-used"; boms: ItemBom[] } | { status: "customer-parts"; parts: ItemCustomerPart[] };

function problem(error: unknown, fallback: string): Error { return error instanceof Error ? error : new Error(fallback); }
function RequestError({ error }: { error: Error }) {
  return <div className="decision-error" role="alert"><AlertCircle size={17} /><div><span>{error.message}</span>{error instanceof ApiError && error.status >= 500 && error.requestId && <span>Request ID: {error.requestId}</span>}</div></div>;
}
function EmptySection({ title, detail }: { title: string; detail: string }) {
  return <div className="master-state item-detail__empty"><Boxes size={26} /><h3>{title}</h3><p>{detail}</p></div>;
}
function Field({ label, value }: { label: string; value: string | number | null }) {
  return <div><dt>{label}</dt><dd>{value === null || value === "" ? "Not supplied" : value}</dd></div>;
}

export function ItemDetail({ itemId, section, me }: { itemId: string; section: ItemSection; me: Me }) {
  const [master, setMaster] = useState<MasterState>({ status: "loading" });
  const [sectionState, setSectionState] = useState<SectionState>({ status: "idle" });
  const [reload, setReload] = useState(0);
  const [sectionReload, setSectionReload] = useState(0);
  const [editingMaster, setEditingMaster] = useState(false);
  const [editingPolicy, setEditingPolicy] = useState(false);
  const [policyUnitId, setPolicyUnitId] = useState("");
  const [editingSpec, setEditingSpec] = useState<string | null>(null);
  const [editingPart, setEditingPart] = useState(false);
  const [customerSearch, setCustomerSearch] = useState("");
  const [customerResults, setCustomerResults] = useState<Customer[]>([]);
  const [customerError, setCustomerError] = useState<Error | null>(null);
  const [confirmingDeactivate, setConfirmingDeactivate] = useState(false);
  const [saving, setSaving] = useState(false);
  const [formError, setFormError] = useState<Error | null>(null);
  const [notice, setNotice] = useState("");
  const cancelRef = useRef<HTMLButtonElement>(null);
  const deactivateRef = useRef<HTMLButtonElement>(null);
  const canManage = me.permissions.includes("item.manage");
  const canAddPart = me.permissions.includes("customer.manage");

  useEffect(() => {
    const controller = new AbortController();
    setMaster({ status: "loading" });
    void Promise.all([fetchItem(itemId, controller.signal), fetchAllItemCategories(controller.signal)])
      .then(([item, categories]) => { if (!controller.signal.aborted) setMaster({ status: "ready", item, categories }); })
      .catch(error => { if (!controller.signal.aborted) setMaster({ status: "error", error: problem(error, "Item details could not be loaded.") }); });
    return () => controller.abort();
  }, [itemId, reload]);

  useEffect(() => {
    setEditingMaster(false); setEditingPolicy(false); setEditingSpec(null); setEditingPart(false);
    setFormError(null); setNotice("");
  }, [section, itemId]);

  useEffect(() => {
    if (section === "master") { setSectionState({ status: "idle" }); return; }
    const controller = new AbortController();
    setSectionState({ status: "loading" });
    const signal = controller.signal;
    async function load() {
      switch (section) {
        case "master": return { status: "idle" } as const;
        case "policies": {
          const [policies, units, stores] = await Promise.all([
            fetchItemPolicies(itemId, signal), fetchAllOrganizationUnits(signal), fetchAllStores(null, null, null, signal),
          ]);
          return { status: "policies", policies, units, stores } as const;
        }
        case "specs": return { status: "specs", specs: await fetchItemSpecifications(itemId, signal) } as const;
        case "effective-bom": {
          try { return { status: "effective-bom", bom: await fetchItemEffectiveBom(itemId, signal) } as const; }
          catch (error) { if (error instanceof ApiError && error.status === 404) return { status: "effective-empty" } as const; throw error; }
        }
        case "engineering-changes": return { status: "engineering-changes", changes: await fetchItemEngineeringChanges(itemId, signal) } as const;
        case "where-used": return { status: "where-used", boms: await fetchItemWhereUsed(itemId, signal) } as const;
        case "customer-parts": return { status: "customer-parts", parts: await fetchItemCustomerParts(itemId, signal) } as const;
      }
    }
    void load().then(state => { if (!signal.aborted) setSectionState(state); })
      .catch(error => { if (!signal.aborted) setSectionState({ status: "error", error: problem(error, "Item section could not be loaded.") }); });
    return () => controller.abort();
  }, [itemId, section, sectionReload]);

  useEffect(() => {
    if (!editingPart || section !== "customer-parts") return;
    const controller = new AbortController();
    setCustomerError(null);
    const timer = setTimeout(() => {
      void listCustomers({ q: customerSearch.trim(), active: true, limit: 200, offset: 0 }, controller.signal)
        .then(page => { if (!controller.signal.aborted) setCustomerResults(page.items); })
        .catch(error => { if (!controller.signal.aborted) setCustomerError(problem(error, "Customers could not be loaded.")); });
    }, 150);
    return () => { clearTimeout(timer); controller.abort(); };
  }, [customerSearch, editingPart, section]);

  useEffect(() => { if (confirmingDeactivate) cancelRef.current?.focus(); }, [confirmingDeactivate]);

  async function saveMaster(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); if (master.status !== "ready") return;
    const data = new FormData(event.currentTarget);
    const shelfLife = String(data.get("shelf_life_days") ?? "").trim();
    const body: ItemUpdate = {
      name: String(data.get("name") ?? "").trim(), category_id: String(data.get("category_id") ?? "") || null,
      hsn_code: String(data.get("hsn_code") ?? "").trim() || null,
      shelf_life_days: shelfLife ? Number(shelfLife) : null,
      route_code: ["FG", "WIP"].includes(master.item.item_type) ? String(data.get("route_code") ?? "").trim().toUpperCase() || null : null,
    };
    setSaving(true); setFormError(null);
    try { const item = await updateItem(itemId, body); setMaster({ ...master, item }); setEditingMaster(false); setNotice("Item updated."); }
    catch (error) { setFormError(problem(error, "Item could not be updated.")); }
    finally { setSaving(false); }
  }

  async function confirmDeactivate() {
    if (master.status !== "ready") return;
    setSaving(true); setFormError(null);
    try { const item = await updateItem(itemId, { active: false }); setMaster({ ...master, item }); setConfirmingDeactivate(false); setNotice("Item deactivated."); }
    catch (error) { setFormError(problem(error, "Item could not be deactivated.")); }
    finally { setSaving(false); }
  }

  function confirmationKeys(event: KeyboardEvent<HTMLElement>) {
    if (event.key === "Escape") { setConfirmingDeactivate(false); setFormError(null); deactivateRef.current?.focus(); }
    if (event.key !== "Tab") return;
    const buttons = Array.from(event.currentTarget.querySelectorAll<HTMLButtonElement>("button:not(:disabled)"));
    if (!buttons.length) return;
    const first = buttons[0], last = buttons[buttons.length - 1];
    if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
    if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
  }

  async function savePolicy(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); if (sectionState.status !== "policies" || !policyUnitId) return;
    const data = new FormData(event.currentTarget);
    const body: ItemPolicyUpdate = {
      reorder_point: String(data.get("reorder_point") ?? "").trim(),
      reorder_qty: String(data.get("reorder_qty") ?? "").trim(),
      default_store_id: String(data.get("default_store_id") ?? "") || null,
    };
    setSaving(true); setFormError(null);
    try {
      const policy = await setItemPolicy(itemId, policyUnitId, body);
      setSectionState({ ...sectionState, policies: [...sectionState.policies.filter(row => row.unit_id !== policy.unit_id), policy].sort((a, b) => a.unit_code.localeCompare(b.unit_code)) });
      setEditingPolicy(false); setNotice("Plant policy saved.");
    } catch (error) { setFormError(problem(error, "Plant policy could not be saved.")); }
    finally { setSaving(false); }
  }

  async function saveSpec(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); if (sectionState.status !== "specs") return;
    const data = new FormData(event.currentTarget);
    const name = String(data.get("name") ?? "").trim();
    const nullable = (key: string) => String(data.get(key) ?? "").trim() || null;
    const body: ItemSpecificationUpdate = {
      unit: nullable("unit"), target_value: nullable("target_value"), min_value: nullable("min_value"),
      max_value: nullable("max_value"), text_value: nullable("text_value"), note: nullable("note"),
    };
    setSaving(true); setFormError(null);
    try {
      const spec = await setItemSpecification(itemId, name, body);
      setSectionState({ status: "specs", specs: [...sectionState.specs.filter(row => row.name !== spec.name), spec].sort((a, b) => a.name.localeCompare(b.name)) });
      setEditingSpec(null); setNotice("Specification saved.");
    } catch (error) { setFormError(problem(error, "Specification could not be saved.")); }
    finally { setSaving(false); }
  }

  async function savePart(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); if (sectionState.status !== "customer-parts") return;
    const data = new FormData(event.currentTarget);
    const body = { customer_id: String(data.get("customer_id") ?? ""), customer_part_no: String(data.get("customer_part_no") ?? "").trim() };
    setSaving(true); setFormError(null);
    try { const part = await addItemCustomerPart(itemId, body); setSectionState({ status: "customer-parts", parts: [...sectionState.parts, part] }); setEditingPart(false); setNotice("Customer part added."); }
    catch (error) { setFormError(problem(error, "Customer part could not be added.")); }
    finally { setSaving(false); }
  }

  if (master.status === "loading") return <section className="master-detail"><div className="master-state"><span className="spinner" /><p>Loading item details…</p></div></section>;
  if (master.status === "error") return <section className="master-detail"><div className="inline-error" role="alert"><AlertCircle size={20} /><div><h3>Item unavailable</h3><p>{master.error.message}</p>{master.error instanceof ApiError && master.error.status >= 500 && master.error.requestId && <p>Request ID: {master.error.requestId}</p>}</div><button className="button button--secondary" onClick={() => setReload(value => value + 1)}>Try again</button></div></section>;

  const item = master.item;
  const category = master.categories.find(row => row.category_id === item.category_id)?.name ?? (item.category_id ? "Unknown category" : "Uncategorized");
  const itemBase = `/app/master-data/engineering/items/${itemId}`;
  const makeable = item.item_type === "FG" || item.item_type === "WIP";
  const selectedPolicy = sectionState.status === "policies" ? sectionState.policies.find(row => row.unit_id === policyUnitId) : undefined;
  const selectedSpec = sectionState.status === "specs" && editingSpec ? sectionState.specs.find(row => row.name === editingSpec) : undefined;

  return <section className="master-detail item-detail" aria-labelledby="item-detail-title">
    <nav className="item-detail__tabs" aria-label="Item sections">{SECTIONS.map(tab => <Link key={tab.id} to={tab.id === "master" ? itemBase : `${itemBase}/${tab.id}`} className={section === tab.id ? "active" : ""} aria-current={section === tab.id ? "page" : undefined}>{tab.label}</Link>)}</nav>
    <div className="master-detail__heading"><div><Link to="/app/master-data/engineering"><ArrowLeft size={16} />Back to items</Link><p className="eyebrow">{item.code}</p><h2 id="item-detail-title">{item.name}</h2><span className={`status-badge status-badge--${item.active ? "approved" : "cancelled"}`}>{item.active ? "Active" : "Inactive"}</span></div>{section === "master" && canManage && !editingMaster && <div><button className="button button--secondary" type="button" onClick={() => { setEditingMaster(true); setFormError(null); }}><Pencil size={16} />Edit item</button>{item.active && <button className="button button--danger" type="button" ref={deactivateRef} onClick={() => { setConfirmingDeactivate(true); setFormError(null); }}><ShieldAlert size={16} />Deactivate item</button>}</div>}</div>
    {notice && <p className="success-banner" role="status">{notice}</p>}
    {confirmingDeactivate && <div className="confirmation-backdrop"><section className="confirmation-panel" role="dialog" aria-modal="true" aria-labelledby="deactivate-item-title" onKeyDown={confirmationKeys}><div><ShieldAlert size={22} /><div><h3 id="deactivate-item-title">Deactivate this item?</h3><p>The item becomes unavailable for new activity. A live BOM component may block this action.</p></div></div>{formError && <RequestError error={formError} />}<div><button className="button button--secondary" ref={cancelRef} type="button" onClick={() => { setConfirmingDeactivate(false); setFormError(null); deactivateRef.current?.focus(); }}>Cancel</button><button className="button button--danger" type="button" disabled={saving} onClick={confirmDeactivate}>{saving ? "Deactivating…" : "Confirm deactivation"}</button></div></section></div>}
    {section === "master" && <>
      {editingMaster && <div className="form-panel"><form className="record-form" onSubmit={saveMaster}><div className="record-form__heading"><div><h3>Edit item</h3><p>Item type and unit of measure stay fixed after creation.</p></div></div><div className="record-form__grid"><div className="field"><label htmlFor="edit-item-name">Item name</label><input id="edit-item-name" name="name" required maxLength={160} defaultValue={item.name} /></div><div className="field"><label htmlFor="edit-item-category">Category</label><select id="edit-item-category" name="category_id" defaultValue={item.category_id ?? ""}><option value="">Uncategorized</option>{master.categories.map(row => <option key={row.category_id} value={row.category_id}>{row.code} — {row.name}</option>)}</select></div><div className="field"><label htmlFor="edit-item-hsn">HSN code</label><input id="edit-item-hsn" name="hsn_code" maxLength={8} inputMode="numeric" defaultValue={item.hsn_code ?? ""} /></div><div className="field"><label htmlFor="edit-item-shelf">Shelf life (days)</label><input id="edit-item-shelf" name="shelf_life_days" type="number" min={0} max={36500} step={1} defaultValue={item.shelf_life_days ?? ""} /></div>{makeable && <div className="field"><label htmlFor="edit-item-route">Production route code</label><input id="edit-item-route" name="route_code" maxLength={50} defaultValue={item.route_code ?? ""} /></div>}</div>{formError && <RequestError error={formError} />}<div className="record-form__actions"><button className="button button--secondary" type="button" onClick={() => { setEditingMaster(false); setFormError(null); }}>Cancel</button><button className="button button--primary" type="submit" disabled={saving}>{saving ? "Saving…" : "Save changes"}</button></div></form></div>}
      <section className="record-panel"><div className="record-panel__heading"><div><h3>Item master</h3><p>Product identity, classification, and routing.</p></div><Boxes size={20} /></div><dl className="record-facts item-detail__facts"><Field label="Item code" value={item.code} /><Field label="Type" value={item.item_type} /><Field label="Unit of measure" value={item.uom_code} /><Field label="Category" value={category} /><Field label="HSN code" value={item.hsn_code} /><Field label="Shelf life (days)" value={item.shelf_life_days} /><Field label="Production route" value={item.route_code} /><Field label="Status" value={item.active ? "Active" : "Inactive"} /></dl></section>
    </>}
    {section !== "master" && sectionState.status === "loading" && <div className="master-state"><span className="spinner" /><p>Loading {SECTIONS.find(tab => tab.id === section)?.label.toLowerCase()}…</p></div>}
    {section !== "master" && sectionState.status === "error" && <div className="inline-error" role="alert"><AlertCircle size={20} /><div><h3>Section unavailable</h3><p>{sectionState.error.message}</p>{sectionState.error instanceof ApiError && sectionState.error.status >= 500 && sectionState.error.requestId && <p>Request ID: {sectionState.error.requestId}</p>}</div><button className="button button--secondary" type="button" onClick={() => setSectionReload(value => value + 1)}>Try again</button></div>}
    {section === "policies" && sectionState.status === "policies" && <section className="record-panel"><div className="record-panel__heading"><div><h3>Plant policies</h3><p>Reorder levels and default stores for plants in your scope.</p></div>{canManage && item.active && <button className="button button--secondary" type="button" onClick={() => { setEditingPolicy(true); setPolicyUnitId(""); setFormError(null); }}><Plus size={16} />Set plant policy</button>}</div>{sectionState.policies.length === 0 ? <EmptySection title="No plant policies" detail="This item has not been assigned to a plant in your scope." /> : <div className="item-detail__rows">{sectionState.policies.map(policy => <div key={policy.unit_id} className="item-detail__row"><strong>{sectionState.units.find(unit => unit.unit_id === policy.unit_id)?.name ?? policy.unit_code}</strong><span>{policy.unit_code}</span><span>Reorder point: {policy.reorder_point}</span><span>Reorder quantity: {policy.reorder_qty}</span><span>Store: {sectionState.stores.find(store => store.store_id === policy.default_store_id)?.name ?? (policy.default_store_id ? "Store unavailable" : "Not assigned")}</span></div>)}</div>}{editingPolicy && <form className="record-form item-detail__form" onSubmit={savePolicy}><div className="field"><label htmlFor="policy-plant">Plant</label><select id="policy-plant" value={policyUnitId} onChange={event => setPolicyUnitId(event.target.value)} required><option value="" disabled>Select plant</option>{sectionState.units.filter(unit => unit.active && (me.all_plants || me.unit_ids.includes(unit.unit_id))).map(unit => <option key={unit.unit_id} value={unit.unit_id}>{unit.code} — {unit.name}</option>)}</select></div><div className="record-form__grid" key={policyUnitId}><div className="field"><label htmlFor="policy-point">Reorder point</label><input id="policy-point" name="reorder_point" inputMode="decimal" pattern="\d+(\.\d{1,3})?" required defaultValue={selectedPolicy?.reorder_point ?? "0"} /></div><div className="field"><label htmlFor="policy-qty">Reorder quantity</label><input id="policy-qty" name="reorder_qty" inputMode="decimal" pattern="\d+(\.\d{1,3})?" required defaultValue={selectedPolicy?.reorder_qty ?? "0"} /></div><div className="field"><label htmlFor="policy-store">Default store</label><select id="policy-store" name="default_store_id" defaultValue={selectedPolicy?.default_store_id ?? ""}><option value="">Not assigned</option>{sectionState.stores.filter(store => store.active && store.unit_id === policyUnitId).map(store => <option key={store.store_id} value={store.store_id}>{store.code} — {store.name}</option>)}</select></div></div>{formError && <RequestError error={formError} />}<div className="record-form__actions"><button className="button button--secondary" type="button" onClick={() => setEditingPolicy(false)}>Cancel</button><button className="button button--primary" type="submit" disabled={saving}>{saving ? "Saving…" : "Save policy"}</button></div></form>}</section>}
    {section === "specs" && sectionState.status === "specs" && <section className="record-panel"><div className="record-panel__heading"><div><h3>Specifications</h3><p>Technical values and tolerances returned by engineering.</p></div>{canManage && <button className="button button--secondary" type="button" onClick={() => { setEditingSpec(""); setFormError(null); }}><Plus size={16} />Add specification</button>}</div>{sectionState.specs.length === 0 ? <EmptySection title="No specifications" detail="No technical specifications were returned for this item." /> : <div className="item-detail__rows">{sectionState.specs.map(spec => <div key={spec.name} className="item-detail__row"><strong>{spec.name}</strong><span>{spec.text_value ?? spec.target_value ?? "Range only"}{spec.unit ? ` ${spec.unit}` : ""}</span><span>Minimum: {spec.min_value ?? "—"}</span><span>Maximum: {spec.max_value ?? "—"}</span><span>{spec.note ?? "No note"}</span>{canManage && <button className="button button--secondary" type="button" onClick={() => { setEditingSpec(spec.name); setFormError(null); }}>Edit {spec.name}</button>}</div>)}</div>}{editingSpec !== null && <form className="record-form item-detail__form" onSubmit={saveSpec} key={editingSpec}><div className="record-form__grid"><div className="field"><label htmlFor="spec-name">Specification name</label><input id="spec-name" name="name" required maxLength={80} readOnly={Boolean(editingSpec)} defaultValue={selectedSpec?.name ?? ""} /></div><div className="field"><label htmlFor="spec-unit">Unit</label><input id="spec-unit" name="unit" maxLength={20} defaultValue={selectedSpec?.unit ?? ""} /></div><div className="field"><label htmlFor="spec-target">Target value</label><input id="spec-target" name="target_value" inputMode="decimal" defaultValue={selectedSpec?.target_value ?? ""} /></div><div className="field"><label htmlFor="spec-min">Minimum value</label><input id="spec-min" name="min_value" inputMode="decimal" defaultValue={selectedSpec?.min_value ?? ""} /></div><div className="field"><label htmlFor="spec-max">Maximum value</label><input id="spec-max" name="max_value" inputMode="decimal" defaultValue={selectedSpec?.max_value ?? ""} /></div><div className="field"><label htmlFor="spec-text">Text value</label><input id="spec-text" name="text_value" maxLength={200} defaultValue={selectedSpec?.text_value ?? ""} /></div><div className="field"><label htmlFor="spec-note">Note</label><input id="spec-note" name="note" maxLength={500} defaultValue={selectedSpec?.note ?? ""} /></div></div>{formError && <RequestError error={formError} />}<div className="record-form__actions"><button className="button button--secondary" type="button" onClick={() => setEditingSpec(null)}>Cancel</button><button className="button button--primary" type="submit" disabled={saving}>{saving ? "Saving…" : "Save specification"}</button></div></form>}</section>}
    {section === "effective-bom" && sectionState.status === "effective-empty" && <EmptySection title="No effective BOM" detail="No approved BOM is in force for this item today." />}
    {section === "effective-bom" && sectionState.status === "effective-bom" && <section className="record-panel"><div className="record-panel__heading"><div><h3>Effective BOM</h3><p>Revision {sectionState.bom.version} · {sectionState.bom.status} · From {sectionState.bom.valid_from}</p></div></div><div className="item-detail__rows">{sectionState.bom.lines.map(line => <div key={line.line_no} className="item-detail__row"><strong>{line.component_code}</strong><span>{line.component_name}</span><span>Quantity: {line.quantity_per} {line.uom_code}</span><span>Scrap factor: {line.scrap_factor}</span></div>)}</div></section>}
    {section === "engineering-changes" && sectionState.status === "engineering-changes" && <section className="record-panel"><div className="record-panel__heading"><div><h3>Engineering changes</h3><p>Recorded BOM revisions and approval outcomes.</p></div></div>{sectionState.changes.length === 0 ? <EmptySection title="No engineering changes" detail="No BOM change history was returned." /> : <div className="item-detail__rows">{sectionState.changes.map(change => <div key={change.bom_id} className="item-detail__row"><strong>Revision {change.version}</strong><span>{change.reason}</span><span>{change.status} · Effective {change.effective_from}</span><span>Requested by {change.requested_by ?? "Unknown user"}</span><span>Approval: {change.approval_status ?? "Not linked"}</span></div>)}</div>}</section>}
    {section === "where-used" && sectionState.status === "where-used" && <section className="record-panel"><div className="record-panel__heading"><div><h3>Where used</h3><p>Live BOM versions containing this item.</p></div></div>{sectionState.boms.length === 0 ? <EmptySection title="Not used by a live BOM" detail="No live BOM currently lists this item as a component." /> : <div className="item-detail__rows">{sectionState.boms.map(bom => <div key={bom.bom_id} className="item-detail__row"><strong>{bom.item_code}</strong><span>{bom.item_name}</span><span>Revision {bom.version}</span><span>{bom.status}</span></div>)}</div>}</section>}
    {section === "customer-parts" && sectionState.status === "customer-parts" && <section className="record-panel"><div className="record-panel__heading"><div><h3>Customer parts</h3><p>Customer-specific part numbers for this item.</p></div>{canAddPart && makeable && <button className="button button--secondary" type="button" onClick={() => { setEditingPart(true); setFormError(null); }}><Plus size={16} />Add customer part</button>}</div>{sectionState.parts.length === 0 ? <EmptySection title="No customer parts" detail="No customer part numbers were returned." /> : <div className="item-detail__rows">{sectionState.parts.map(part => <div key={part.xref_id} className="item-detail__row"><strong>{part.customer_part_no}</strong><span>{part.customer_code}</span></div>)}</div>}{editingPart && <form className="record-form item-detail__form" onSubmit={savePart}><div className="record-form__grid"><div className="field"><label htmlFor="part-search">Search customers</label><input id="part-search" value={customerSearch} onChange={event => setCustomerSearch(event.target.value)} placeholder="Code or name" /></div><div className="field"><label htmlFor="part-customer">Customer</label><select id="part-customer" name="customer_id" required defaultValue=""><option value="" disabled>Select customer</option>{customerResults.map(customer => <option key={customer.customer_id} value={customer.customer_id}>{customer.code} — {customer.name}</option>)}</select></div><div className="field"><label htmlFor="part-number">Customer part number</label><input id="part-number" name="customer_part_no" required maxLength={60} /></div></div>{customerError && <RequestError error={customerError} />}{formError && <RequestError error={formError} />}<div className="record-form__actions"><button className="button button--secondary" type="button" onClick={() => setEditingPart(false)}>Cancel</button><button className="button button--primary" type="submit" disabled={saving}>{saving ? "Saving…" : "Save customer part"}</button></div></form>}</section>}
  </section>;
}
