import { FormEvent, KeyboardEvent, useEffect, useMemo, useRef, useState } from "react";
import { ArrowLeft, ClipboardList, X } from "lucide-react";
import { Link, useLocation } from "react-router-dom";
import { Grn, GrnLine, Me, fetchGrn, inspectGrnLine } from "../api-client";
import { InventoryTabs } from "./InventoryTabs";
import { DetailPageTemplate } from "./PageTemplates";
import { RequestError } from "./RequestError";

type Load<T> = { status: "loading" } | { status: "ready"; data: T } | { status: "error"; error: Error };
type Disposition = "accept" | "partial" | "reject";
const failure = (value: unknown, fallback: string) => value instanceof Error ? value : new Error(fallback);
const label = (value: string) => value.toLowerCase().replaceAll("_", " ").replace(/^./, match => match.toUpperCase());
const validDecimal = (value: string) => /^\d{1,10}(?:\.\d{1,3})?$/.test(value);
const scaled = (value: string) => { const [whole, fraction = ""] = value.split("."); return BigInt(whole) * 1000n + BigInt(fraction.padEnd(3, "0")); };
const formatted = (value: bigint) => `${value / 1000n}.${String(value % 1000n).padStart(3, "0")}`;
const sum = (lines: GrnLine[], field: "received_qty" | "accepted_qty" | "rejected_qty") => formatted(lines.reduce((total, line) => total + scaled(line[field]), 0n));

function InspectionDialog({ line, number, onUpdated }: { line: GrnLine; number: number; onUpdated: (updated: Grn) => void }) {
  const [open, setOpen] = useState(false);
  const [disposition, setDisposition] = useState<Disposition>("accept");
  const [accepted, setAccepted] = useState("");
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<Error | null>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const closeRef = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    if (!open) return;
    const previous = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    closeRef.current?.focus();
    return () => { document.body.style.overflow = previous; };
  }, [open]);
  function close() {
    setOpen(false); setError(null);
    requestAnimationFrame(() => {
      if (triggerRef.current?.isConnected) triggerRef.current.focus();
      else document.getElementById("grn-detail-title")?.focus();
    });
  }
  function keys(event: KeyboardEvent<HTMLElement>) {
    if (event.key === "Escape" && !busy) { event.preventDefault(); close(); return; }
    if (event.key !== "Tab") return;
    const focusable = Array.from(event.currentTarget.querySelectorAll<HTMLElement>('button:not(:disabled), input:not(:disabled), select:not(:disabled), textarea:not(:disabled)'));
    if (!focusable.length) return;
    if (event.shiftKey && document.activeElement === focusable[0]) { event.preventDefault(); focusable[focusable.length - 1].focus(); }
    if (!event.shiftKey && document.activeElement === focusable[focusable.length - 1]) { event.preventDefault(); focusable[0].focus(); }
  }
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); setError(null);
    const quantity = disposition === "accept" ? line.received_qty : disposition === "reject" ? "0" : accepted.trim();
    if (!validDecimal(quantity) || scaled(quantity) > 1_000_000_000_000n || scaled(quantity) > scaled(line.received_qty) || (disposition === "partial" && (scaled(quantity) <= 0n || scaled(quantity) >= scaled(line.received_qty)))) {
      setError(new Error("For partial acceptance, enter a quantity greater than zero and less than the received quantity."));
      return;
    }
    setBusy(true);
    try {
      const updated = await inspectGrnLine(line.grn_line_id, { accepted_qty: quantity, note: note.trim() || null });
      onUpdated(updated);
      close();
    } catch (value) { setError(failure(value, "Inspection could not be recorded.")); }
    finally { setBusy(false); }
  }
  return <>
    <button ref={triggerRef} className="button button--primary" type="button" onClick={() => { setDisposition("accept"); setAccepted(""); setNote(""); setError(null); setOpen(true); }}>Inspect line {number}</button>
    {open && <div className="bom-editor-backdrop"><section className="bom-editor grn-inspection" role="dialog" aria-modal="true" aria-labelledby="grn-inspection-title" onKeyDown={keys}>
      <div className="bom-editor__header"><div><p className="eyebrow">{line.item_code} · {line.lot_no}</p><h3 id="grn-inspection-title">Inspect received line</h3><p>Received {line.received_qty}. Rejected quantity is the remainder after acceptance.</p></div><button ref={closeRef} className="button button--secondary" type="button" disabled={busy} onClick={close} aria-label="Close inspection dialog"><X size={18} /></button></div>
      <form onSubmit={submit}><div className="bom-editor__body"><div className="field"><label htmlFor="grn-disposition">Disposition</label><select id="grn-disposition" value={disposition} onChange={event => { setDisposition(event.target.value as Disposition); setError(null); }}><option value="accept">Accept all</option><option value="partial">Partial acceptance</option><option value="reject">Reject all</option></select></div>
        {disposition === "partial" && <div className="field"><label htmlFor="grn-accepted">Accepted quantity</label><input id="grn-accepted" inputMode="decimal" required value={accepted} onChange={event => { setAccepted(event.target.value); setError(null); }} /><small>Enter more than 0 and less than {line.received_qty}.</small></div>}
        <div className="field"><label htmlFor="grn-inspection-note">Inspection note</label><textarea id="grn-inspection-note" maxLength={500} value={note} onChange={event => { setNote(event.target.value); setError(null); }} /></div>
        {error && <RequestError error={error} />}</div><div className="bom-editor__actions"><button className="button button--secondary" type="button" disabled={busy} onClick={close}>Cancel</button><button className="button button--primary" type="submit" disabled={busy}>{busy ? "Recording…" : "Record inspection"}</button></div></form>
    </section></div>}
  </>;
}

export function GrnDetail({ id, me }: { id: string; me: Me }) {
  const location = useLocation();
  const headingRef = useRef<HTMLHeadingElement>(null);
  const [state, setState] = useState<Load<Grn>>({ status: "loading" });
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    setState({ status: "loading" });
    void fetchGrn(id, controller.signal).then(data => { if (!controller.signal.aborted) setState({ status: "ready", data }); }).catch(value => { if (!controller.signal.aborted) setState({ status: "error", error: failure(value, "Goods receipt could not be loaded.") }); });
    return () => controller.abort();
  }, [attempt, id]);
  const grn = state.status === "ready" ? state.data : null;
  const totals = useMemo(() => grn ? { received: sum(grn.lines, "received_qty"), accepted: sum(grn.lines, "accepted_qty"), rejected: sum(grn.lines, "rejected_qty") } : null, [grn]);
  return <DetailPageTemplate className="grn-detail" headingId="grn-detail-title" headingRef={headingRef} title={grn?.grn_no ?? "Goods receipt detail"} navigation={<InventoryTabs canIssue={me.permissions.includes("inv.issue")} />} back={<Link to={`/app/inventory/grns${location.search}`}><ArrowLeft size={16} />Back to goods receipts</Link>}>
    {state.status === "loading" && <div className="master-state" role="status"><span className="spinner" /><p>Loading goods receipt…</p></div>}
    {state.status === "error" && <><RequestError error={state.error} /><button className="button button--secondary" type="button" onClick={() => setAttempt(value => value + 1)}>Try again</button></>}
    {grn && <>
      <section className="record-panel grn-detail__summary"><div className="record-panel__heading"><h3>Receipt summary</h3><span className="status-badge">{label(grn.status)}</span></div><dl className="record-facts"><div><dt>Purchase order</dt><dd><Link to={`/app/procurement/purchase-orders/${grn.po_id}`}>{grn.po_no}</Link></dd></div><div><dt>Vendor</dt><dd>{grn.vendor_code}</dd></div><div><dt>Plant</dt><dd>{grn.unit_code}</dd></div><div><dt>Received</dt><dd>{grn.received_at.slice(0, 16).replace("T", " ")}</dd></div><div><dt>Delivery challan</dt><dd>{grn.supplier_dc_no ?? "Not provided"}</dd></div></dl></section>
      <section className="record-panel grn-detail__totals" aria-label="Quantity totals"><div className="record-panel__heading"><h3>Quantity totals</h3></div><dl><div><dt>Received</dt><dd>{totals?.received}</dd></div><div><dt>Accepted</dt><dd>{totals?.accepted}</dd></div><div><dt>Rejected</dt><dd>{totals?.rejected}</dd></div></dl></section>
      <section className="record-panel grn-detail__receipts"><div className="record-panel__heading"><h3>Received lines</h3></div>{grn.lines.length ? <ul className="grn-detail__lines" aria-label="Received lines">{grn.lines.map((line, index) => <li key={line.grn_line_id}><div className="grn-detail__line-head"><strong>{line.item_code}</strong><span className="status-badge">{line.inspected ? "Inspected" : "Awaiting inspection"}</span></div><dl><div><dt>Received</dt><dd>{line.received_qty}</dd></div><div><dt>Accepted</dt><dd>{line.accepted_qty}</dd></div><div><dt>Rejected</dt><dd>{line.rejected_qty}</dd></div><div><dt>Store</dt><dd>{line.store_code}</dd></div><div><dt>Lot</dt><dd>{line.lot_no}</dd></div><div><dt>Lot status</dt><dd>{label(line.lot_status)}</dd></div><div><dt>Expiry</dt><dd>{line.expiry_at?.slice(0, 10) ?? "Not set"}</dd></div></dl>{me.permissions.includes("qa.approve") && !line.inspected && line.lot_status === "QA_HOLD" && <InspectionDialog line={line} number={index + 1} onUpdated={updated => setState({ status: "ready", data: updated })} />}</li>)}</ul> : <div className="master-state"><ClipboardList size={25} /><h4>No received lines</h4><p>This goods receipt has no lines in the current response.</p></div>}</section>
    </>}
  </DetailPageTemplate>;
}
