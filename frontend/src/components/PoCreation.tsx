import { FormEvent, useEffect, useMemo, useState } from "react";
import { ArrowLeft, ClipboardList, Plus } from "lucide-react";
import { Link, useNavigate } from "react-router-dom";
import {
  BusinessRule, Indent, IndentSummary, Item, ItemType, Me, PaymentTerm, PoCreate, PoFromIndents, TaxCode, Unit, Vendor,
  createPo, createPoFromIndents, fetchAllActiveItemsOfTypes, fetchAllApprovedVendors, fetchAllIndents,
  fetchAllOrganizationUnits, fetchAllPaymentTerms, fetchAllTaxCodes, fetchIndent,
} from "../api-client";
import { useActivePlant } from "../plant-context";
import { ProcurementTabs } from "./ProcurementTabs";
import { RequestError } from "./RequestError";

const PATH = "/app/procurement/purchase-orders";
const ITEM_TYPES: ItemType[] = ["RM", "WIP", "FG", "SPARE", "CONSUMABLE", "CAPITAL", "OTHER"];
type Load<T> = { status: "loading" } | { status: "ready"; data: T } | { status: "error"; error: Error };
type Line = { key: number; itemId: string; quantity: string; price: string; taxCodeId: string; dueDate: string };
const blankLine = (key: number): Line => ({ key, itemId: "", quantity: "", price: "", taxCodeId: "", dueDate: "" });
const failure = (value: unknown, fallback: string) => value instanceof Error ? value : new Error(fallback);
const today = () => { const date = new Date(); return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}-${String(date.getDate()).padStart(2, "0")}`; };
const validQuantity = (value: string) => /^\d{1,10}(?:\.\d{1,3})?$/.test(value) && !/^0+(?:\.0+)?$/.test(value);
const validPrice = (value: string) => /^\d{1,16}(?:\.\d{1,2})?$/.test(value);

export function PoCreation({ mode, me, settings }: { mode: "direct" | "indents"; me: Me; settings: BusinessRule[] }) {
  const navigate = useNavigate();
  const { activeUnit } = useActivePlant();
  const [units, setUnits] = useState<Load<Unit[]>>({ status: "loading" });
  const [vendors, setVendors] = useState<Load<Vendor[]>>({ status: "loading" });
  const [paymentTerms, setPaymentTerms] = useState<Load<PaymentTerm[]>>({ status: "loading" });
  const [taxCodes, setTaxCodes] = useState<Load<TaxCode[]>>({ status: "loading" });
  const [items, setItems] = useState<Load<Item[]>>({ status: "loading" });
  const [indents, setIndents] = useState<Load<IndentSummary[]>>({ status: "loading" });
  const [selectedDetails, setSelectedDetails] = useState<Load<Indent[]>>({ status: "ready", data: [] });
  const [unitId, setUnitId] = useState(activeUnit?.unit_id ?? "");
  const [vendorId, setVendorId] = useState("");
  const [paymentTermId, setPaymentTermId] = useState("");
  const [lines, setLines] = useState<Line[]>([blankLine(1)]);
  const [selectedIds, setSelectedIds] = useState<string[]>([]);
  const [dueDate, setDueDate] = useState("");
  const [prices, setPrices] = useState<Record<string, { price: string; taxCodeId: string }>>({});
  const [error, setError] = useState<Error | null>(null);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    const controller = new AbortController();
    void fetchAllOrganizationUnits(controller.signal)
      .then(data => { if (!controller.signal.aborted) setUnits({ status: "ready", data: data.filter(unit => unit.active && (me.all_plants || me.unit_ids.includes(unit.unit_id))) }); })
      .catch(value => { if (!controller.signal.aborted) setUnits({ status: "error", error: failure(value, "Plants could not be loaded.") }); });
    void fetchAllApprovedVendors(controller.signal)
      .then(data => { if (!controller.signal.aborted) setVendors({ status: "ready", data }); })
      .catch(value => { if (!controller.signal.aborted) setVendors({ status: "error", error: failure(value, "Approved vendors could not be loaded.") }); });
    void fetchAllPaymentTerms(controller.signal)
      .then(data => { if (!controller.signal.aborted) setPaymentTerms({ status: "ready", data }); })
      .catch(value => { if (!controller.signal.aborted) setPaymentTerms({ status: "error", error: failure(value, "Payment terms could not be loaded.") }); });
    void fetchAllTaxCodes(true, controller.signal)
      .then(data => { if (!controller.signal.aborted) setTaxCodes({ status: "ready", data }); })
      .catch(value => { if (!controller.signal.aborted) setTaxCodes({ status: "error", error: failure(value, "Tax codes could not be loaded.") }); });
    if (mode === "direct") {
      void fetchAllActiveItemsOfTypes(ITEM_TYPES, controller.signal)
        .then(data => { if (!controller.signal.aborted) setItems({ status: "ready", data: [...new Map(data.map(item => [item.item_id, item])).values()] }); })
        .catch(value => { if (!controller.signal.aborted) setItems({ status: "error", error: failure(value, "Active items could not be loaded.") }); });
    }
    return () => controller.abort();
  }, [me.all_plants, me.unit_ids, mode]);

  useEffect(() => {
    if (mode !== "indents" || !unitId) return;
    const controller = new AbortController();
    setIndents({ status: "loading" });
    void fetchAllIndents({ status: "APPROVED", indentType: null, unitId, mine: false }, controller.signal)
      .then(data => { if (!controller.signal.aborted) setIndents({ status: "ready", data }); })
      .catch(value => { if (!controller.signal.aborted) setIndents({ status: "error", error: failure(value, "Approved indents could not be loaded.") }); });
    return () => controller.abort();
  }, [mode, unitId]);
  const selectedKey = selectedIds.join("|");
  useEffect(() => {
    if (mode !== "indents" || !selectedKey) { setSelectedDetails({ status: "ready", data: [] }); return; }
    const controller = new AbortController();
    setSelectedDetails({ status: "loading" });
    void Promise.all(selectedKey.split("|").map(id => fetchIndent(id, controller.signal)))
      .then(data => { if (!controller.signal.aborted) setSelectedDetails({ status: "ready", data }); })
      .catch(value => { if (!controller.signal.aborted) setSelectedDetails({ status: "error", error: failure(value, "Indent lines could not be loaded.") }); });
    return () => controller.abort();
  }, [mode, selectedKey]);

  const pricedItems = useMemo(() => selectedDetails.status === "ready"
    ? [...new Map(selectedDetails.data.flatMap(indent => indent.lines.map(line => [line.item_id, { itemId: line.item_id, code: line.item_code, name: line.item_name }] as const))).values()]
    : [], [selectedDetails]);
  function patchLine(key: number, patch: Partial<Line>) { setLines(current => current.map(line => line.key === key ? { ...line, ...patch } : line)); }
  function changePlant(value: string) { setUnitId(value); setSelectedIds([]); setPrices({}); setError(null); }
  function toggleIndent(id: string, checked: boolean) { setSelectedIds(current => checked ? [...current, id] : current.filter(value => value !== id)); setError(null); }

  async function submitDirect(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); setError(null);
    if (!unitId || !vendorId || lines.length < 1 || lines.length > 200 || lines.some(line => !line.itemId || !validQuantity(line.quantity) || !validPrice(line.price) || !line.dueDate || line.dueDate < today()) || new Set(lines.map(line => line.itemId)).size !== lines.length) {
      setError(new Error("Choose an active plant, approved vendor, distinct active items, valid decimal quantities/prices, and future delivery dates.")); return;
    }
    const body: PoCreate = { unit_id: unitId, vendor_id: vendorId, payment_term_id: paymentTermId || null, rfq_id: null, indent_ids: [],
      lines: lines.map(line => ({ item_id: line.itemId, ordered_qty: line.quantity, unit_price: line.price, tax_code_id: line.taxCodeId || null, due_date: line.dueDate, schedule: [] })) };
    setSaving(true);
    try { const po = await createPo(body); navigate(`${PATH}/${po.po_id}`); }
    catch (value) { setError(failure(value, "Purchase order could not be created.")); }
    finally { setSaving(false); }
  }
  async function submitIndents(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); setError(null);
    if (!vendorId || !unitId || selectedDetails.status !== "ready" || selectedIds.length < 1 || selectedIds.length > 20 || pricedItems.length < 1 || !dueDate || dueDate < today() || pricedItems.some(item => !validPrice(prices[item.itemId]?.price ?? ""))) {
      setError(new Error("Select approved indents from one plant, an approved vendor, a future delivery date, and a price for every item.")); return;
    }
    const body: PoFromIndents = { indent_ids: selectedIds, vendor_id: vendorId, payment_term_id: paymentTermId || null, due_date: dueDate,
      prices: pricedItems.map(item => ({ item_id: item.itemId, unit_price: prices[item.itemId].price, tax_code_id: prices[item.itemId].taxCodeId || null })) };
    setSaving(true);
    try { const po = await createPoFromIndents(body); navigate(`${PATH}/${po.po_id}`); }
    catch (value) { setError(failure(value, "Purchase order could not be created from indents.")); }
    finally { setSaving(false); }
  }

  const fromIndents = mode === "indents";
  const requireIndent = settings.find(rule => rule.key === "requireIndent")?.value === true;
  return <section className="master-detail po-create" aria-labelledby="po-create-title"><ProcurementTabs active="orders" />
    <div className="master-detail__heading"><div><Link to={PATH}><ArrowLeft size={16} />Back to purchase orders</Link><h2 id="po-create-title">{fromIndents ? "Purchase order from indents" : "New purchase order"}</h2><p>{fromIndents ? "Price each distinct item from approved indents; the backend combines quantities." : "Create a draft directly with one or more active items. No total is calculated in the browser."}</p></div></div>
    <div className="form-panel"><form className="record-form" onSubmit={fromIndents ? submitIndents : submitDirect}><div className="record-form__heading"><h3>Order source</h3></div>
      {requireIndent && !fromIndents && <p className="indent-list__filter-note">The current requireIndent setting may prevent submitting a directly created order. Choose approved indents if this order must proceed to approval.</p>}
      <div className="record-form__grid"><div className="field"><label htmlFor="po-new-plant">Plant</label><select id="po-new-plant" value={unitId} required onChange={event => changePlant(event.target.value)}><option value="" disabled>Select plant</option>{units.status === "ready" && units.data.map(unit => <option key={unit.unit_id} value={unit.unit_id}>{unit.code} — {unit.name}</option>)}</select></div>
        <div className="field"><label htmlFor="po-new-vendor">Approved vendor</label><select id="po-new-vendor" value={vendorId} required onChange={event => setVendorId(event.target.value)}><option value="" disabled>Select approved vendor</option>{vendors.status === "ready" && vendors.data.map(vendor => <option key={vendor.vendor_id} value={vendor.vendor_id}>{vendor.code} — {vendor.name}</option>)}</select></div>
        <div className="field"><label htmlFor="po-new-term">Payment term</label><select id="po-new-term" value={paymentTermId} onChange={event => setPaymentTermId(event.target.value)}><option value="">Use vendor default</option>{paymentTerms.status === "ready" && paymentTerms.data.map(term => <option key={term.payment_term_id} value={term.payment_term_id}>{term.code} — {term.name}</option>)}</select></div>
        {fromIndents && <div className="field"><label htmlFor="po-new-due">Delivery due</label><input id="po-new-due" type="date" min={today()} required value={dueDate} onChange={event => setDueDate(event.target.value)} /></div>}
      </div>
      {fromIndents ? <><div className="indent-create__lines"><div className="record-form__heading"><h3>Approved indents</h3><p>Select 1–20 from the same plant. The backend validates their current status.</p></div>
        {indents.status === "loading" && <p role="status">Loading approved indents…</p>}
        {indents.status === "ready" && indents.data.length === 0 && <div className="master-state"><ClipboardList size={24} /><h3>No approved indents</h3><p>Choose another plant or wait until an indent is approved.</p></div>}
        {indents.status === "ready" && indents.data.map(indent => <label className="po-create__indent" key={indent.indent_id}><input type="checkbox" aria-label={`Select ${indent.indent_no}`} checked={selectedIds.includes(indent.indent_id)} disabled={selectedIds.length >= 20 && !selectedIds.includes(indent.indent_id)} onChange={event => toggleIndent(indent.indent_id, event.target.checked)} /><strong>{indent.indent_no}</strong><span>{indent.indent_type} · {indent.requester_name} · Needed {indent.needed_by ?? "not set"}</span></label>)}
      </div><div className="indent-create__lines"><div className="record-form__heading"><h3>Item prices</h3><p>Enter prices as decimal strings. The backend totals quantities from the selected indent lines.</p></div>
        {selectedDetails.status === "loading" && <p role="status">Loading selected indent lines…</p>}
        {pricedItems.map(item => <div className="record-panel po-create__price" key={item.itemId}><h4>{item.code} — {item.name}</h4><div className="record-form__grid"><div className="field"><label htmlFor={`po-price-${item.itemId}`}>Unit price for {item.code}</label><input id={`po-price-${item.itemId}`} inputMode="decimal" required value={prices[item.itemId]?.price ?? ""} onChange={event => setPrices(current => ({ ...current, [item.itemId]: { ...current[item.itemId], price: event.target.value, taxCodeId: current[item.itemId]?.taxCodeId ?? "" } }))} /></div><div className="field"><label htmlFor={`po-tax-${item.itemId}`}>Tax code for {item.code}</label><select id={`po-tax-${item.itemId}`} value={prices[item.itemId]?.taxCodeId ?? ""} onChange={event => setPrices(current => ({ ...current, [item.itemId]: { price: current[item.itemId]?.price ?? "", taxCodeId: event.target.value } }))}><option value="">No tax code yet</option>{taxCodes.status === "ready" && taxCodes.data.map(code => <option key={code.tax_code_id} value={code.tax_code_id}>{code.code} — {code.description}</option>)}</select></div></div></div>)}
      </div></> : <div className="indent-create__lines"><div className="record-form__heading"><h3>Order lines</h3><p>One to 200 distinct items. Choose a delivery date for each line; schedules can be edited on the detail page.</p></div>
        {lines.map((line, index) => <div className="record-panel indent-create__line" key={line.key}><div className="record-panel__heading"><h4>Line {index + 1}</h4>{lines.length > 1 && <button className="button button--secondary" type="button" onClick={() => setLines(current => current.filter(value => value.key !== line.key))}>Remove line</button>}</div><div className="record-form__grid"><div className="field"><label htmlFor={`po-item-${line.key}`}>Item</label><select id={`po-item-${line.key}`} required value={line.itemId} onChange={event => patchLine(line.key, { itemId: event.target.value })}><option value="" disabled>Select active item</option>{items.status === "ready" && items.data.map(item => <option key={item.item_id} value={item.item_id}>{item.code} — {item.name} ({item.uom_code})</option>)}</select></div><div className="field"><label htmlFor={`po-qty-${line.key}`}>Ordered quantity</label><input id={`po-qty-${line.key}`} inputMode="decimal" required value={line.quantity} onChange={event => patchLine(line.key, { quantity: event.target.value })} /></div><div className="field"><label htmlFor={`po-unit-price-${line.key}`}>Unit price</label><input id={`po-unit-price-${line.key}`} inputMode="decimal" required value={line.price} onChange={event => patchLine(line.key, { price: event.target.value })} /></div><div className="field"><label htmlFor={`po-due-${line.key}`}>Delivery due</label><input id={`po-due-${line.key}`} type="date" min={today()} required value={line.dueDate} onChange={event => patchLine(line.key, { dueDate: event.target.value })} /></div><div className="field"><label htmlFor={`po-tax-${line.key}`}>Tax code</label><select id={`po-tax-${line.key}`} value={line.taxCodeId} onChange={event => patchLine(line.key, { taxCodeId: event.target.value })}><option value="">No tax code yet</option>{taxCodes.status === "ready" && taxCodes.data.map(code => <option key={code.tax_code_id} value={code.tax_code_id}>{code.code} — {code.description}</option>)}</select></div></div></div>)}
        <button className="button button--secondary" type="button" disabled={lines.length >= 200 || items.status !== "ready"} onClick={() => setLines(current => [...current, blankLine(Math.max(...current.map(line => line.key)) + 1)])}><Plus size={16} />Add line</button>
      </div>}
      {units.status === "error" && <RequestError error={units.error} />}{vendors.status === "error" && <RequestError error={vendors.error} />}{paymentTerms.status === "error" && <RequestError error={paymentTerms.error} />}{taxCodes.status === "error" && <RequestError error={taxCodes.error} />}{items.status === "error" && !fromIndents && <RequestError error={items.error} />}{indents.status === "error" && fromIndents && <RequestError error={indents.error} />}{selectedDetails.status === "error" && fromIndents && <RequestError error={selectedDetails.error} />}{error && <RequestError error={error} />}
      {items.status === "ready" && !fromIndents && items.data.length === 0 && <div className="master-state"><ClipboardList size={24} /><h3>No active items</h3><p>Create an active item before starting a purchase order.</p></div>}
      {vendors.status === "ready" && vendors.data.length === 0 && <div className="master-state"><ClipboardList size={24} /><h3>No approved vendors</h3><p>An approved vendor is required to create a purchase order.</p></div>}
      <div className="record-form__actions"><Link className="button button--secondary" to={PATH}>Cancel</Link><button className="button button--primary" type="submit" disabled={saving || units.status !== "ready" || vendors.status !== "ready" || !vendors.data.length || (fromIndents ? indents.status !== "ready" || selectedDetails.status !== "ready" || !selectedIds.length : items.status !== "ready" || !items.data.length)}>{saving ? "Creating…" : fromIndents ? "Create from indents" : "Create purchase order"}</button></div>
    </form></div>
  </section>;
}
