import { FormEvent, KeyboardEvent, ReactNode, useEffect, useRef, useState } from "react";
import { X } from "lucide-react";
import {
  BusinessRule, Item, ItemType, PaymentTerm, Rfq, RfqAward, RfqQuote, TaxCode, Vendor,
  awardRfq, cancelRfq, fetchAllActiveItemsOfTypes, fetchAllApprovedVendors, fetchAllPaymentTerms,
  fetchAllTaxCodes, inviteRfqVendors, recordRfqQuote, sendRfq, updateRfq,
} from "../api-client";
import { RequestError } from "./RequestError";

type Action = "edit" | "vendors" | "send" | "cancel" | "quote" | "award";
type Load<T> = { status: "loading" } | { status: "ready"; data: T } | { status: "error"; error: Error };
type DraftLine = { key: number; itemId: string; quantity: string; requiredBy: string };
type QuoteLine = { lineId: string; include: boolean; unitPrice: string; quotedQty: string; leadDays: string };
const ITEM_TYPES: ItemType[] = ["RM", "WIP", "FG", "SPARE", "CONSUMABLE", "CAPITAL", "OTHER"];
const failure = (value: unknown, fallback: string) => value instanceof Error ? value : new Error(fallback);
const today = () => { const date = new Date(); return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}-${String(date.getDate()).padStart(2, "0")}`; };
const validPositive = (value: string, places: number) => new RegExp(`^\\d{1,15}(?:\\.\\d{1,${places}})?$`).test(value) && !/^0+(?:\.0+)?$/.test(value);
const validPrice = (value: string) => /^\d{1,16}(?:\.\d{1,2})?$/.test(value);

function Dialog({ title, id, onClose, closeRef, children }: { title: string; id: string; onClose: () => void; closeRef: React.RefObject<HTMLButtonElement | null>; children: ReactNode }) {
  function keys(event: KeyboardEvent<HTMLElement>) {
    if (event.key === "Escape") { event.preventDefault(); onClose(); return; }
    if (event.key !== "Tab") return;
    const focusable = Array.from(event.currentTarget.querySelectorAll<HTMLElement>('button:not(:disabled), input:not(:disabled), select:not(:disabled), textarea:not(:disabled)'));
    if (!focusable.length) return;
    if (event.shiftKey && document.activeElement === focusable[0]) { event.preventDefault(); focusable[focusable.length - 1].focus(); }
    if (!event.shiftKey && document.activeElement === focusable[focusable.length - 1]) { event.preventDefault(); focusable[0].focus(); }
  }
  return <div className="bom-editor-backdrop"><section className="bom-editor rfq-action-dialog" role="dialog" aria-modal="true" aria-labelledby={id} onKeyDown={keys}>
    <div className="bom-editor__header"><h3 id={id}>{title}</h3><button className="button button--secondary" type="button" ref={closeRef} onClick={onClose} aria-label="Close RFQ dialog"><X size={18} /></button></div>
    {children}
  </section></div>;
}

export function RfqActions({ rfq, settings, onUpdated, onQuote, onAward }: {
  rfq: Rfq; settings: BusinessRule[]; onUpdated: (data: Rfq) => void; onQuote: (data: Rfq) => void; onAward: (poNo: string) => void;
}) {
  const [action, setAction] = useState<Action | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<Error | null>(null);
  const [notice, setNotice] = useState("");
  const [draftLines, setDraftLines] = useState<DraftLine[]>([]);
  const [responseDue, setResponseDue] = useState("");
  const [originalResponseDue, setOriginalResponseDue] = useState("");
  const [items, setItems] = useState<Load<Item[]>>({ status: "loading" });
  const [vendors, setVendors] = useState<Load<Vendor[]>>({ status: "loading" });
  const [vendorId, setVendorId] = useState("");
  const [quoteReference, setQuoteReference] = useState("");
  const [validUntil, setValidUntil] = useState("");
  const [quoteLines, setQuoteLines] = useState<QuoteLine[]>([]);
  const [dueDate, setDueDate] = useState("");
  const [taxCodeId, setTaxCodeId] = useState("");
  const [paymentTermId, setPaymentTermId] = useState("");
  const [taxCodes, setTaxCodes] = useState<Load<TaxCode[]>>({ status: "loading" });
  const [paymentTerms, setPaymentTerms] = useState<Load<PaymentTerm[]>>({ status: "loading" });
  const closeRef = useRef<HTMLButtonElement>(null);
  const triggerRef = useRef<HTMLButtonElement | null>(null);

  useEffect(() => {
    if (!action) return;
    const previous = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    closeRef.current?.focus();
    return () => { document.body.style.overflow = previous; };
  }, [action]);

  useEffect(() => {
    if (action !== "edit" && action !== "vendors" && action !== "award") return;
    const controller = new AbortController();
    if (action === "edit") {
      setItems({ status: "loading" });
      void fetchAllActiveItemsOfTypes(ITEM_TYPES, controller.signal)
        .then(data => { if (!controller.signal.aborted) setItems({ status: "ready", data: [...new Map(data.map(item => [item.item_id, item])).values()] }); })
        .catch(value => { if (!controller.signal.aborted) setItems({ status: "error", error: failure(value, "Items could not be loaded.") }); });
    }
    if (action === "vendors") {
      setVendors({ status: "loading" });
      void fetchAllApprovedVendors(controller.signal)
        .then(data => { if (!controller.signal.aborted) setVendors({ status: "ready", data }); })
        .catch(value => { if (!controller.signal.aborted) setVendors({ status: "error", error: failure(value, "Approved vendors could not be loaded.") }); });
    }
    if (action === "award") {
      setTaxCodes({ status: "loading" }); setPaymentTerms({ status: "loading" });
      void fetchAllTaxCodes(true, controller.signal)
        .then(data => { if (!controller.signal.aborted) setTaxCodes({ status: "ready", data }); })
        .catch(value => { if (!controller.signal.aborted) setTaxCodes({ status: "error", error: failure(value, "Tax codes could not be loaded.") }); });
      void fetchAllPaymentTerms(controller.signal)
        .then(data => { if (!controller.signal.aborted) setPaymentTerms({ status: "ready", data }); })
        .catch(value => { if (!controller.signal.aborted) setPaymentTerms({ status: "error", error: failure(value, "Payment terms could not be loaded.") }); });
    }
    return () => controller.abort();
  }, [action]);

  function open(next: Action, trigger: HTMLButtonElement) {
    triggerRef.current = trigger;
    setError(null); setNotice(""); setVendorId("");
    if (next === "edit") {
      setDraftLines(rfq.lines.map((line, index) => ({ key: index + 1, itemId: line.item_id, quantity: line.quantity, requiredBy: line.required_by ?? "" })));
      setResponseDue(rfq.response_due_at?.slice(0, 16) ?? "");
      setOriginalResponseDue(rfq.response_due_at?.slice(0, 16) ?? "");
    }
    if (next === "quote") {
      setQuoteReference(""); setValidUntil("");
      setQuoteLines(rfq.lines.map(line => ({ lineId: line.rfq_line_id, include: true, unitPrice: "", quotedQty: line.quantity, leadDays: "" })));
    }
    if (next === "award") { setDueDate(""); setTaxCodeId(""); setPaymentTermId(""); }
    setAction(next);
  }
  function dismiss() {
    setAction(null); setError(null);
    requestAnimationFrame(() => { const trigger = triggerRef.current; if (trigger?.isConnected) trigger.focus(); else document.getElementById("rfq-detail-title")?.focus(); });
  }
  function close() { if (!busy) dismiss(); }
  function completed(data: Rfq, message: string) { onUpdated(data); setNotice(message); dismiss(); }
  async function run(fn: () => Promise<void>, fallback: string) {
    if (busy) return;
    setBusy(true); setError(null);
    try { await fn(); }
    catch (value) { setError(failure(value, fallback)); }
    finally { setBusy(false); }
  }
  function patchDraft(key: number, changes: Partial<DraftLine>) { setDraftLines(current => current.map(line => line.key === key ? { ...line, ...changes } : line)); }
  function patchQuote(lineId: string, changes: Partial<QuoteLine>) { setQuoteLines(current => current.map(line => line.lineId === lineId ? { ...line, ...changes } : line)); }

  async function saveDraft(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (draftLines.length < 1 || draftLines.length > 100 || draftLines.some(line => !line.itemId || !validPositive(line.quantity, 3) || (line.requiredBy && line.requiredBy < today())) || new Set(draftLines.map(line => line.itemId)).size !== draftLines.length) {
      setError(new Error("Choose distinct active items, positive quantities, and valid required-by dates.")); return;
    }
    await run(async () => {
      const responseDueAt = responseDue === originalResponseDue ? rfq.response_due_at : responseDue ? new Date(responseDue).toISOString() : null;
      const data = await updateRfq(rfq.rfq_id, { response_due_at: responseDueAt, lines: draftLines.map(line => ({ item_id: line.itemId, quantity: line.quantity, required_by: line.requiredBy || null })) });
      completed(data, "Draft RFQ saved.");
    }, "Draft RFQ could not be saved.");
  }
  async function invite(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!vendorId) { setError(new Error("Choose an approved vendor.")); return; }
    await run(async () => completed(await inviteRfqVendors(rfq.rfq_id, [vendorId]), "Vendor invited."), "Vendor could not be invited.");
  }
  async function send() { await run(async () => completed(await sendRfq(rfq.rfq_id), "RFQ sent."), "RFQ could not be sent."); }
  async function cancel() { await run(async () => completed(await cancelRfq(rfq.rfq_id), "RFQ cancelled."), "RFQ could not be cancelled."); }
  async function saveQuote(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const included = quoteLines.filter(line => line.include);
    if (!vendorId || !included.length || included.some(line => !validPrice(line.unitPrice) || !validPositive(line.quotedQty, 3) || (line.leadDays && (!/^\d{1,3}$/.test(line.leadDays) || Number(line.leadDays) > 730))) || (validUntil && validUntil < today())) {
      setError(new Error("Select an invited vendor and enter valid prices, quantities, lead days, and quote validity.")); return;
    }
    const body: RfqQuote = { vendor_id: vendorId, quote_reference: quoteReference.trim() || null, valid_until: validUntil || null,
      lines: included.map(line => ({ rfq_line_id: line.lineId, unit_price: line.unitPrice, quoted_qty: line.quotedQty, lead_days: line.leadDays ? Number(line.leadDays) : null })) };
    await run(async () => { const data = await recordRfqQuote(rfq.rfq_id, body); onQuote(data); setNotice("Quote recorded."); dismiss(); }, "Vendor quote could not be recorded.");
  }
  async function award(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!vendorId || !dueDate || dueDate < today()) { setError(new Error("Choose a quoted vendor and a future delivery date.")); return; }
    const body: RfqAward = { vendor_id: vendorId, due_date: dueDate, tax_code_id: taxCodeId || null, payment_term_id: paymentTermId || null };
    await run(async () => { const po = await awardRfq(rfq.rfq_id, body); onAward(po.po_no); dismiss(); }, "RFQ could not be awarded.");
  }

  const mayEdit = rfq.status === "DRAFT";
  const mayInvite = rfq.status === "DRAFT" || rfq.status === "SENT";
  const mayQuote = rfq.status === "SENT";
  const quotedVendors = rfq.invitations.filter(vendor => vendor.response_status === "QUOTED");
  const availableVendors = vendors.status === "ready" ? vendors.data.filter(vendor => !rfq.invitations.some(invitation => invitation.vendor_id === vendor.vendor_id)) : [];
  const minQuotesValue = settings.find(rule => rule.key === "minQuotes")?.value;
  const minInvited = typeof minQuotesValue === "number" && Number.isInteger(minQuotesValue) ? Math.max(1, minQuotesValue) : 1;
  const title = { edit: "Edit draft RFQ", vendors: "Add approved vendor", send: "Send this RFQ?", cancel: "Cancel this RFQ?", quote: "Record vendor quote", award: "Award RFQ to vendor" } as const;

  return <>
    <div className="indent-detail__actions rfq-detail__actions">
      {mayEdit && <button className="button button--secondary" type="button" disabled={busy} onClick={event => open("edit", event.currentTarget)}>Edit draft</button>}
      {mayInvite && <button className="button button--secondary" type="button" disabled={busy} onClick={event => open("vendors", event.currentTarget)}>Add vendors</button>}
      {mayEdit && <button className="button button--primary" type="button" disabled={busy} onClick={event => open("send", event.currentTarget)}>Send RFQ</button>}
      {mayQuote && <button className="button button--secondary" type="button" disabled={busy} onClick={event => open("quote", event.currentTarget)}>Record quote</button>}
      {mayQuote && <button className="button button--primary" type="button" disabled={busy} onClick={event => open("award", event.currentTarget)}>Award RFQ</button>}
      {mayInvite && <button className="button button--secondary" type="button" disabled={busy} onClick={event => open("cancel", event.currentTarget)}>Cancel RFQ</button>}
    </div>
    {notice && <p className="success-banner rfq-detail__notice" role="status">{notice}</p>}
    {action && <Dialog title={title[action]} id="rfq-action-title" onClose={close} closeRef={closeRef}>
      {action === "edit" && <form onSubmit={saveDraft}><div className="bom-editor__body"><p>Saving replaces the complete draft line list in this order.</p>
        <div className="field"><label htmlFor="rfq-edit-response-due">Response due</label><input id="rfq-edit-response-due" type="datetime-local" value={responseDue} onChange={event => setResponseDue(event.target.value)} /></div>
        {draftLines.map((line, index) => <fieldset className="bom-editor__line" key={line.key}><legend>Line {index + 1}</legend><div className="record-form__grid">
          <div className="field"><label htmlFor={`rfq-edit-item-${line.key}`}>Item</label><select id={`rfq-edit-item-${line.key}`} required value={line.itemId} onChange={event => patchDraft(line.key, { itemId: event.target.value })}><option value="" disabled>Select item</option>
            {items.status === "ready" && items.data.map(item => <option key={item.item_id} value={item.item_id}>{item.code} — {item.name}</option>)}
            {!((items.status === "ready" && items.data.some(item => item.item_id === line.itemId))) && line.itemId && <option value={line.itemId}>{rfq.lines.find(item => item.item_id === line.itemId)?.item_code ?? "Current item"}</option>}</select></div>
          <div className="field"><label htmlFor={`rfq-edit-qty-${line.key}`}>Quantity</label><input id={`rfq-edit-qty-${line.key}`} inputMode="decimal" required value={line.quantity} onChange={event => patchDraft(line.key, { quantity: event.target.value })} /></div>
          <div className="field"><label htmlFor={`rfq-edit-required-${line.key}`}>Required by</label><input id={`rfq-edit-required-${line.key}`} type="date" value={line.requiredBy} onChange={event => patchDraft(line.key, { requiredBy: event.target.value })} /></div></div>
          {draftLines.length > 1 && <button className="button button--secondary" type="button" onClick={() => setDraftLines(current => current.filter(value => value.key !== line.key))}>Remove line</button>}
        </fieldset>)}
        <button className="button button--secondary" type="button" disabled={items.status !== "ready" || draftLines.length >= 100} onClick={() => setDraftLines(current => [...current, { key: Math.max(0, ...current.map(line => line.key)) + 1, itemId: "", quantity: "", requiredBy: "" }])}>Add line</button>
        {items.status === "loading" && <p role="status">Loading active items…</p>}{items.status === "error" && <RequestError error={items.error} />}{error && <RequestError error={error} />}</div>
        <div className="bom-editor__actions"><button className="button button--secondary" type="button" onClick={close}>Cancel</button><button className="button button--primary" type="submit" disabled={busy}>{busy ? "Saving…" : "Save draft"}</button></div></form>}
      {action === "vendors" && <form onSubmit={invite}><div className="bom-editor__body"><p>Only approved vendors can be invited.</p><div className="field"><label htmlFor="rfq-approved-vendor">Approved vendor</label><select id="rfq-approved-vendor" required value={vendorId} onChange={event => setVendorId(event.target.value)}><option value="">Select vendor</option>{availableVendors.map(vendor => <option key={vendor.vendor_id} value={vendor.vendor_id}>{vendor.code} — {vendor.name}</option>)}</select></div>{vendors.status === "loading" && <p role="status">Loading approved vendors…</p>}{vendors.status === "ready" && !availableVendors.length && <p>No additional approved vendors available.</p>}{vendors.status === "error" && <RequestError error={vendors.error} />}{error && <RequestError error={error} />}</div><div className="bom-editor__actions"><button className="button button--secondary" type="button" onClick={close}>Cancel</button><button className="button button--primary" type="submit" disabled={busy || vendors.status !== "ready" || !availableVendors.length}>{busy ? "Inviting…" : "Invite vendor"}</button></div></form>}
      {action === "send" && <><div className="bom-editor__body"><p>Invite at least {minInvited} approved vendor{minInvited === 1 ? "" : "s"} before sending. The server checks the current rule.</p><p>{rfq.invitations.length} vendor{rfq.invitations.length === 1 ? "" : "s"} currently invited.</p>{error && <RequestError error={error} />}</div><div className="bom-editor__actions"><button className="button button--secondary" type="button" onClick={close}>Keep draft</button><button className="button button--primary" type="button" disabled={busy} onClick={() => void send()}>{busy ? "Sending…" : "Confirm send"}</button></div></>}
      {action === "cancel" && <><div className="bom-editor__body"><p>Cancel this RFQ? The server will reject a status that changed while you were working.</p>{error && <RequestError error={error} />}</div><div className="bom-editor__actions"><button className="button button--secondary" type="button" onClick={close}>Keep RFQ</button><button className="button button--primary" type="button" disabled={busy} onClick={() => void cancel()}>{busy ? "Cancelling…" : "Confirm cancellation"}</button></div></>}
      {action === "quote" && <form onSubmit={saveQuote}><div className="bom-editor__body"><p>A new quote from this vendor replaces their earlier quote. Compare the server response after saving.</p><div className="record-form__grid"><div className="field"><label htmlFor="rfq-quote-vendor">Quoted vendor</label><select id="rfq-quote-vendor" required value={vendorId} onChange={event => setVendorId(event.target.value)}><option value="">Select invited vendor</option>{rfq.invitations.map(vendor => <option key={vendor.vendor_id} value={vendor.vendor_id}>{vendor.vendor_code} — {vendor.vendor_name}</option>)}</select></div><div className="field"><label htmlFor="rfq-quote-reference">Quote reference</label><input id="rfq-quote-reference" maxLength={60} value={quoteReference} onChange={event => setQuoteReference(event.target.value)} /></div><div className="field"><label htmlFor="rfq-quote-valid">Valid until</label><input id="rfq-quote-valid" type="date" min={today()} value={validUntil} onChange={event => setValidUntil(event.target.value)} /></div></div>
        {quoteLines.map((line, index) => { const item = rfq.lines.find(value => value.rfq_line_id === line.lineId); return <fieldset className="bom-editor__line" key={line.lineId}><legend>Line {index + 1}: {item?.item_code}</legend><label className="rfq-action-dialog__include"><input type="checkbox" checked={line.include} onChange={event => patchQuote(line.lineId, { include: event.target.checked })} />Include in quote</label>{line.include && <div className="record-form__grid"><div className="field"><label htmlFor={`rfq-price-${line.lineId}`}>Unit price</label><input id={`rfq-price-${line.lineId}`} inputMode="decimal" required value={line.unitPrice} onChange={event => patchQuote(line.lineId, { unitPrice: event.target.value })} /></div><div className="field"><label htmlFor={`rfq-quoted-${line.lineId}`}>Quoted quantity</label><input id={`rfq-quoted-${line.lineId}`} inputMode="decimal" required value={line.quotedQty} onChange={event => patchQuote(line.lineId, { quotedQty: event.target.value })} /></div><div className="field"><label htmlFor={`rfq-lead-${line.lineId}`}>Lead days</label><input id={`rfq-lead-${line.lineId}`} inputMode="numeric" value={line.leadDays} onChange={event => patchQuote(line.lineId, { leadDays: event.target.value })} /></div></div>}</fieldset>; })}
        {error && <RequestError error={error} />}</div><div className="bom-editor__actions"><button className="button button--secondary" type="button" onClick={close}>Cancel</button><button className="button button--primary" type="submit" disabled={busy || rfq.invitations.length === 0}>{busy ? "Saving…" : "Save quote"}</button></div></form>}
      {action === "award" && <form onSubmit={award}><div className="bom-editor__body"><p>The server verifies full quantity, unexpired quotes, and vendor eligibility before creating a purchase order.</p><div className="record-form__grid"><div className="field"><label htmlFor="rfq-award-vendor">Quoted vendor</label><select id="rfq-award-vendor" required value={vendorId} onChange={event => setVendorId(event.target.value)}><option value="">Select quoted vendor</option>{quotedVendors.map(vendor => <option key={vendor.vendor_id} value={vendor.vendor_id}>{vendor.vendor_code} — {vendor.vendor_name}</option>)}</select></div><div className="field"><label htmlFor="rfq-award-due">Delivery due</label><input id="rfq-award-due" type="date" min={today()} required value={dueDate} onChange={event => setDueDate(event.target.value)} /></div><div className="field"><label htmlFor="rfq-award-tax">Tax code</label><select id="rfq-award-tax" value={taxCodeId} onChange={event => setTaxCodeId(event.target.value)}><option value="">No tax code</option>{taxCodes.status === "ready" && taxCodes.data.map(code => <option key={code.tax_code_id} value={code.tax_code_id}>{code.code} — {code.description}</option>)}</select></div><div className="field"><label htmlFor="rfq-award-term">Payment term</label><select id="rfq-award-term" value={paymentTermId} onChange={event => setPaymentTermId(event.target.value)}><option value="">No payment term</option>{paymentTerms.status === "ready" && paymentTerms.data.map(term => <option key={term.payment_term_id} value={term.payment_term_id}>{term.code} — {term.name}</option>)}</select></div></div>
        {taxCodes.status === "error" && <RequestError error={taxCodes.error} />}{paymentTerms.status === "error" && <RequestError error={paymentTerms.error} />}{!quotedVendors.length && <p>No invited vendor has a recorded quote yet.</p>}{error && <RequestError error={error} />}</div><div className="bom-editor__actions"><button className="button button--secondary" type="button" onClick={close}>Cancel</button><button className="button button--primary" type="submit" disabled={busy || !quotedVendors.length}>{busy ? "Creating…" : "Create purchase order"}</button></div></form>}
    </Dialog>}
  </>;
}
