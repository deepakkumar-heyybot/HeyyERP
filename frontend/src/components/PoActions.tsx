import { FormEvent, KeyboardEvent, useEffect, useRef, useState } from "react";
import { Plus, X } from "lucide-react";
import { BusinessRule, Item, ItemType, Me, PaymentTerm, Po, PoUpdate, TaxCode, cancelPo, closePo, fetchAllActiveItemsOfTypes, fetchAllPaymentTerms, fetchAllTaxCodes, submitPo, updatePo } from "../api-client";
import { RequestError } from "./RequestError";

type Action = "edit" | "submit" | "cancel" | "close";
type Load<T> = { status: "loading" } | { status: "ready"; data: T } | { status: "error"; error: Error };
type Delivery = { key: number; date: string; quantity: string };
type DraftLine = { key: number; itemId: string; quantity: string; price: string; taxCodeId: string; dueDate: string; deliveries: Delivery[] };
const ITEM_TYPES: ItemType[] = ["RM", "WIP", "FG", "SPARE", "CONSUMABLE", "CAPITAL", "OTHER"];
const failure = (value: unknown, fallback: string) => value instanceof Error ? value : new Error(fallback);
const today = () => { const date = new Date(); return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}-${String(date.getDate()).padStart(2, "0")}`; };
const validQty = (value: string) => /^\d{1,10}(?:\.\d{1,3})?$/.test(value) && !/^0+(?:\.0+)?$/.test(value) && scaledQty(value) <= 1_000_000_000_000n;
const validPrice = (value: string) => /^\d{1,16}(?:\.\d{1,2})?$/.test(value);
const scaledQty = (value: string) => { const [whole, fraction = ""] = value.split("."); return BigInt(whole) * 1000n + BigInt(fraction.padEnd(3, "0")); };
const newLine = (key: number): DraftLine => ({ key, itemId: "", quantity: "", price: "", taxCodeId: "", dueDate: "", deliveries: [] });
const fromPo = (po: Po): DraftLine[] => po.lines.map((line, index) => ({ key: index + 1, itemId: line.item_id, quantity: line.ordered_qty, price: line.unit_price, taxCodeId: line.tax_code_id ?? "", dueDate: line.schedule.length === 1 ? line.schedule[0].due_date : "", deliveries: line.schedule.length > 1 ? line.schedule.map((delivery, row) => ({ key: row + 1, date: delivery.due_date, quantity: delivery.scheduled_qty })) : [] }));

export function PoActions({ po, me, settings, onUpdated, onStateChanged }: { po: Po; me: Me; settings: BusinessRule[]; onUpdated: (po: Po) => void; onStateChanged: () => void }) {
  const [action, setAction] = useState<Action | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<Error | null>(null);
  const [lines, setLines] = useState<DraftLine[]>([]);
  const [paymentTermId, setPaymentTermId] = useState("");
  const [items, setItems] = useState<Load<Item[]>>({ status: "loading" });
  const [taxCodes, setTaxCodes] = useState<Load<TaxCode[]>>({ status: "loading" });
  const [terms, setTerms] = useState<Load<PaymentTerm[]>>({ status: "loading" });
  const closeRef = useRef<HTMLButtonElement>(null);
  const triggerRef = useRef<HTMLButtonElement | null>(null);
  const canWrite = me.permissions.includes("po.create");
  const canEdit = canWrite && po.status === "DRAFT";
  const canCancel = canWrite && (po.status === "DRAFT" || po.status === "OPEN");
  const canClose = canWrite && (po.status === "PARTIALLY_RECEIVED" || po.status === "RECEIVED");
  const requireIndent = settings.find(rule => rule.key === "requireIndent")?.value === true;
  const minQuotes = settings.find(rule => rule.key === "minQuotes")?.value;
  function open(next: Action, trigger: HTMLButtonElement) {
    triggerRef.current = trigger; setError(null); setAction(next);
    if (next === "edit") { setLines(fromPo(po)); setPaymentTermId(po.payment_term_id ?? ""); }
  }
  function close(restore = true) {
    setAction(null); setError(null); setBusy(false);
    if (restore) requestAnimationFrame(() => { if (triggerRef.current?.isConnected) triggerRef.current.focus(); else onStateChanged(); });
  }
  useEffect(() => {
    if (!action) return;
    const previous = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    closeRef.current?.focus();
    return () => { document.body.style.overflow = previous; };
  }, [action]);
  useEffect(() => {
    if (action !== "edit") return;
    const controller = new AbortController();
    void fetchAllActiveItemsOfTypes(ITEM_TYPES, controller.signal)
      .then(data => { if (!controller.signal.aborted) setItems({ status: "ready", data: [...new Map(data.map(item => [item.item_id, item])).values()] }); })
      .catch(value => { if (!controller.signal.aborted) setItems({ status: "error", error: failure(value, "Active items could not be loaded.") }); });
    void fetchAllTaxCodes(true, controller.signal)
      .then(data => { if (!controller.signal.aborted) setTaxCodes({ status: "ready", data }); })
      .catch(value => { if (!controller.signal.aborted) setTaxCodes({ status: "error", error: failure(value, "Tax codes could not be loaded.") }); });
    void fetchAllPaymentTerms(controller.signal)
      .then(data => { if (!controller.signal.aborted) setTerms({ status: "ready", data }); })
      .catch(value => { if (!controller.signal.aborted) setTerms({ status: "error", error: failure(value, "Payment terms could not be loaded.") }); });
    return () => controller.abort();
  }, [action]);
  function keys(event: KeyboardEvent<HTMLElement>) {
    if (event.key === "Escape" && !busy) { event.preventDefault(); close(); return; }
    if (event.key !== "Tab") return;
    const focusable = Array.from(event.currentTarget.querySelectorAll<HTMLElement>('button:not(:disabled), input:not(:disabled), select:not(:disabled), textarea:not(:disabled)'));
    if (!focusable.length) return;
    if (event.shiftKey && document.activeElement === focusable[0]) { event.preventDefault(); focusable[focusable.length - 1].focus(); }
    if (!event.shiftKey && document.activeElement === focusable[focusable.length - 1]) { event.preventDefault(); focusable[0].focus(); }
  }
  function patchLine(key: number, patch: Partial<DraftLine>) { setLines(current => current.map(line => line.key === key ? { ...line, ...patch } : line)); }
  function addDelivery(key: number) {
    setLines(current => current.map(line => line.key !== key ? line : { ...line, dueDate: "", deliveries: line.deliveries.length ? [...line.deliveries, { key: Math.max(...line.deliveries.map(item => item.key)) + 1, date: "", quantity: "" }] : [{ key: 1, date: line.dueDate, quantity: line.quantity }, { key: 2, date: "", quantity: "" }] }));
  }
  function patchDelivery(lineKey: number, deliveryKey: number, patch: Partial<Delivery>) {
    setLines(current => current.map(line => line.key === lineKey ? { ...line, deliveries: line.deliveries.map(delivery => delivery.key === deliveryKey ? { ...delivery, ...patch } : delivery) } : line));
  }
  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); setError(null);
    if (lines.length < 1 || lines.length > 200 || new Set(lines.map(line => line.itemId)).size !== lines.length || lines.some(line => !line.itemId || !validQty(line.quantity) || !validPrice(line.price) || (line.deliveries.length ? line.deliveries.length > 24 || line.deliveries.some(delivery => !delivery.date || !validQty(delivery.quantity)) || line.deliveries.reduce((sum, delivery) => sum + (validQty(delivery.quantity) ? scaledQty(delivery.quantity) : 0n), 0n) !== scaledQty(line.quantity) : !line.dueDate))) {
      setError(new Error("Use distinct active items, valid decimal quantities and prices, and delivery quantities that exactly match each ordered quantity.")); return;
    }
    const body: PoUpdate = { payment_term_id: paymentTermId || null, lines: lines.map(line => ({ item_id: line.itemId, ordered_qty: line.quantity, unit_price: line.price, tax_code_id: line.taxCodeId || null, due_date: line.deliveries.length ? null : line.dueDate, schedule: line.deliveries.map(delivery => ({ due_date: delivery.date, quantity: delivery.quantity })) })) };
    setBusy(true);
    try { const updated = await updatePo(po.po_id, body); onUpdated(updated); close(); }
    catch (value) { setError(failure(value, "Purchase order could not be updated.")); }
    finally { setBusy(false); }
  }
  async function confirm() {
    if (!action || action === "edit" || busy) return;
    setBusy(true); setError(null);
    try {
      const updated = action === "submit" ? await submitPo(po.po_id) : action === "cancel" ? await cancelPo(po.po_id) : await closePo(po.po_id);
      onUpdated(updated); close();
    } catch (value) { setError(failure(value, `Purchase order could not be ${action === "submit" ? "submitted" : action === "cancel" ? "cancelled" : "closed"}.`)); }
    finally { setBusy(false); }
  }
  const title = action === "edit" ? "Edit purchase order" : action === "submit" ? "Submit purchase order" : action === "cancel" ? "Cancel purchase order" : "Close purchase order";
  return <><div className="po-detail__actions" aria-label="Purchase order actions">
    {canEdit && <><button className="button button--secondary" type="button" onClick={event => open("edit", event.currentTarget)}>Edit draft</button><button className="button button--primary" type="button" onClick={event => open("submit", event.currentTarget)}>Submit for approval</button></>}
    {canCancel && <button className="button button--secondary" type="button" onClick={event => open("cancel", event.currentTarget)}>Cancel order</button>}
    {canClose && <button className="button button--primary" type="button" onClick={event => open("close", event.currentTarget)}>Close order</button>}
  </div>
  {action && <div className="bom-editor-backdrop"><section className="bom-editor po-editor" role="dialog" aria-modal="true" aria-labelledby="po-action-title" onKeyDown={keys}><div className="bom-editor__header"><div><p className="eyebrow">{po.po_no}</p><h3 id="po-action-title">{title}</h3></div><button className="button button--secondary" type="button" ref={closeRef} onClick={() => close()} aria-label="Close purchase order dialog"><X size={18} /></button></div>
    {action === "edit" ? <form onSubmit={save}><div className="bom-editor__body"><p>Replace draft lines and schedules. Prices and quantities remain exact decimal strings; the backend calculates totals.</p><div className="field"><label htmlFor="po-edit-term">Payment term</label><select id="po-edit-term" value={paymentTermId} onChange={event => setPaymentTermId(event.target.value)}><option value="">Vendor default</option>{terms.status === "ready" && terms.data.map(term => <option key={term.payment_term_id} value={term.payment_term_id}>{term.code} — {term.name}</option>)}{paymentTermId && terms.status !== "ready" && <option value={paymentTermId}>Current payment term</option>}</select></div>
      {lines.map((line, index) => <fieldset className="bom-editor__line" key={line.key}><legend>Line {index + 1}</legend><div className="record-form__grid"><div className="field"><label htmlFor={`po-edit-item-${line.key}`}>Item for line {index + 1}</label><select id={`po-edit-item-${line.key}`} value={line.itemId} required onChange={event => patchLine(line.key, { itemId: event.target.value })}><option value="" disabled>Select active item</option>{items.status === "ready" && items.data.map(item => <option key={item.item_id} value={item.item_id}>{item.code} — {item.name}</option>)}{line.itemId && (items.status !== "ready" || !items.data.some(item => item.item_id === line.itemId)) && <option value={line.itemId}>{po.lines.find(item => item.item_id === line.itemId)?.item_code ?? "Current item"}</option>}</select></div><div className="field"><label htmlFor={`po-edit-qty-${line.key}`}>Ordered quantity for line {index + 1}</label><input id={`po-edit-qty-${line.key}`} inputMode="decimal" required value={line.quantity} onChange={event => patchLine(line.key, { quantity: event.target.value })} /></div><div className="field"><label htmlFor={`po-edit-price-${line.key}`}>Unit price for line {index + 1}</label><input id={`po-edit-price-${line.key}`} inputMode="decimal" required value={line.price} onChange={event => patchLine(line.key, { price: event.target.value })} /></div><div className="field"><label htmlFor={`po-edit-tax-${line.key}`}>Tax code for line {index + 1}</label><select id={`po-edit-tax-${line.key}`} value={line.taxCodeId} onChange={event => patchLine(line.key, { taxCodeId: event.target.value })}><option value="">No tax code yet</option>{taxCodes.status === "ready" && taxCodes.data.map(code => <option key={code.tax_code_id} value={code.tax_code_id}>{code.code} — {code.description}</option>)}{line.taxCodeId && (taxCodes.status !== "ready" || !taxCodes.data.some(code => code.tax_code_id === line.taxCodeId)) && <option value={line.taxCodeId}>{po.lines.find(item => item.item_id === line.itemId)?.tax_code ?? "Current tax code"}</option>}</select></div></div>
        {line.deliveries.length ? <div className="po-editor__deliveries">{line.deliveries.map((delivery, row) => <div className="record-form__grid" key={delivery.key}><div className="field"><label htmlFor={`po-delivery-date-${line.key}-${delivery.key}`}>Delivery {row + 1} date for line {index + 1}</label><input id={`po-delivery-date-${line.key}-${delivery.key}`} type="date" required value={delivery.date} onChange={event => patchDelivery(line.key, delivery.key, { date: event.target.value })} /></div><div className="field"><label htmlFor={`po-delivery-qty-${line.key}-${delivery.key}`}>Delivery {row + 1} quantity for line {index + 1}</label><input id={`po-delivery-qty-${line.key}-${delivery.key}`} inputMode="decimal" required value={delivery.quantity} onChange={event => patchDelivery(line.key, delivery.key, { quantity: event.target.value })} /></div>{line.deliveries.length > 2 && <button className="button button--secondary" type="button" onClick={() => patchLine(line.key, { deliveries: line.deliveries.filter(item => item.key !== delivery.key) })}>Remove delivery {row + 1}</button>}</div>)}<button className="button button--secondary" type="button" disabled={line.deliveries.length >= 24} onClick={() => addDelivery(line.key)}><Plus size={15} />Add delivery</button><button className="button button--secondary" type="button" onClick={() => patchLine(line.key, { dueDate: line.deliveries[0]?.date ?? "", deliveries: [] })}>Use one delivery</button></div> : <div className="po-editor__deliveries"><div className="field"><label htmlFor={`po-edit-due-${line.key}`}>Delivery due for line {index + 1}</label><input id={`po-edit-due-${line.key}`} type="date" required value={line.dueDate} onChange={event => patchLine(line.key, { dueDate: event.target.value })} /></div><button className="button button--secondary" type="button" onClick={() => addDelivery(line.key)}><Plus size={15} />Add delivery</button></div>}
        {lines.length > 1 && <button className="button button--secondary" type="button" onClick={() => setLines(current => current.filter(item => item.key !== line.key))}>Remove line {index + 1}</button>}
      </fieldset>)}<button className="button button--secondary" type="button" disabled={lines.length >= 200 || items.status !== "ready"} onClick={() => setLines(current => [...current, newLine(Math.max(...current.map(line => line.key)) + 1)])}><Plus size={16} />Add line</button>{items.status === "error" && <RequestError error={items.error} />}{taxCodes.status === "error" && <RequestError error={taxCodes.error} />}{terms.status === "error" && <RequestError error={terms.error} />}{error && <RequestError error={error} />}</div><div className="bom-editor__actions"><button className="button button--secondary" type="button" disabled={busy} onClick={() => close()}>Cancel</button><button className="button button--primary" type="submit" disabled={busy}>{busy ? "Saving…" : "Save draft"}</button></div></form>
    : <><div className="bom-editor__body"><p>{action === "submit" ? "Submit this draft for approval? The backend checks tax codes and current requireIndent/minQuotes settings." : action === "cancel" ? "Cancel this order? The backend will refuse cancellation if goods have already been received." : "Close this order? Remaining quantities will not be delivered."}</p>{action === "submit" && requireIndent && !po.indents.length && <p className="indent-list__filter-note">Current requireIndent setting requires an approved indent source.</p>}{action === "submit" && Boolean(minQuotes) && !po.rfq_id && <p className="indent-list__filter-note">Current minQuotes setting requires an RFQ with enough quoted vendors.</p>}{error && <RequestError error={error} />}</div><div className="bom-editor__actions"><button className="button button--secondary" type="button" disabled={busy} onClick={() => close()}>Keep order</button><button className="button button--primary" type="button" disabled={busy} onClick={() => void confirm()}>{busy ? "Working…" : action === "submit" ? "Confirm submission" : action === "cancel" ? "Confirm cancellation" : "Confirm closure"}</button></div></>}
  </section></div>}</>;
}
