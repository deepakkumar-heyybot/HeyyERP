import { useEffect, useRef, useState, type FormEvent, type KeyboardEvent } from "react";
import { ArrowLeft, X } from "lucide-react";
import { Link, useLocation } from "react-router-dom";
import { LotTrace, Me, StockRow, fetchLotTrace, fetchStockLot, quarantineLot, unquarantineLot } from "../api-client";
import { DetailPageTemplate } from "./PageTemplates";
import { InventoryTabs } from "./InventoryTabs";
import { RequestError } from "./RequestError";

type Load<T> = { status: "loading" } | { status: "ready"; data: T } | { status: "error"; error: Error };
type Action = "quarantine" | "release";
const failure = (value: unknown, fallback: string) => value instanceof Error ? value : new Error(fallback);
const label = (value: string) => value.toLowerCase().replaceAll("_", " ").replace(/^./, match => match.toUpperCase());
const date = (value: string) => value.slice(0, 16).replace("T", " ");

export function StockLotDetail({ id, me }: { id: string; me: Me }) {
  const location = useLocation();
  const headingRef = useRef<HTMLHeadingElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const closeRef = useRef<HTMLButtonElement>(null);
  const [state, setState] = useState<Load<StockRow>>({ status: "loading" });
  const [trace, setTrace] = useState<Load<LotTrace>>({ status: "loading" });
  const [attempt, setAttempt] = useState(0);
  const [traceAttempt, setTraceAttempt] = useState(0);
  const [action, setAction] = useState<Action | null>(null);
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [actionError, setActionError] = useState<Error | null>(null);
  useEffect(() => {
    const controller = new AbortController();
    setState({ status: "loading" });
    void fetchStockLot(id, controller.signal).then(data => { if (!controller.signal.aborted) setState({ status: "ready", data }); }).catch(value => { if (!controller.signal.aborted) setState({ status: "error", error: failure(value, "Lot could not be loaded.") }); });
    return () => controller.abort();
  }, [attempt, id]);
  useEffect(() => {
    const controller = new AbortController();
    setTrace({ status: "loading" });
    void fetchLotTrace(id, controller.signal).then(data => { if (!controller.signal.aborted) setTrace({ status: "ready", data }); }).catch(value => { if (!controller.signal.aborted) setTrace({ status: "error", error: failure(value, "Lot trace could not be loaded.") }); });
    return () => controller.abort();
  }, [id, traceAttempt]);
  useEffect(() => {
    if (!action) return;
    const previous = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    closeRef.current?.focus();
    return () => { document.body.style.overflow = previous; };
  }, [action]);
  function close() {
    setAction(null); setActionError(null);
    requestAnimationFrame(() => {
      if (triggerRef.current?.isConnected) triggerRef.current.focus();
      else document.getElementById("stock-lot-title")?.focus();
    });
  }
  function keys(event: KeyboardEvent<HTMLElement>) {
    if (event.key === "Escape" && !busy) { event.preventDefault(); close(); return; }
    if (event.key !== "Tab") return;
    const focusable = Array.from(event.currentTarget.querySelectorAll<HTMLElement>('button:not(:disabled), input:not(:disabled), textarea:not(:disabled)'));
    if (!focusable.length) return;
    if (event.shiftKey && document.activeElement === focusable[0]) { event.preventDefault(); focusable[focusable.length - 1].focus(); }
    if (!event.shiftKey && document.activeElement === focusable[focusable.length - 1]) { event.preventDefault(); focusable[0].focus(); }
  }
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); setActionError(null);
    if (!action) return;
    const clean = reason.trim();
    if (clean.length < 3 || clean.length > 300) { setActionError(new Error("Reason must contain at least 3 and at most 300 characters.")); return; }
    setBusy(true);
    try {
      const updated = action === "quarantine" ? await quarantineLot(id, clean) : await unquarantineLot(id, clean);
      setState({ status: "ready", data: updated });
      setTrace(current => current.status === "ready" ? { status: "ready", data: { ...current.data, status: updated.lot_status } } : current);
      close();
    } catch (value) { setActionError(failure(value, "Lot status could not be changed.")); }
    finally { setBusy(false); }
  }
  const lot = state.status === "ready" ? state.data : null;
  const canQuarantine = !!lot && me.permissions.includes("qa.approve") && lot.lot_status === "AVAILABLE";
  const canRelease = !!lot && me.permissions.includes("qa.approve") && lot.lot_status === "QUARANTINE";
  return <DetailPageTemplate className="stock-lot-detail" title={lot?.lot_no ?? "Lot detail"} headingId="stock-lot-title" headingRef={headingRef} navigation={<InventoryTabs active="stock" canIssue={me.permissions.includes("inv.issue")} />} back={<Link to={`/app/inventory/stock${location.search}`}><ArrowLeft size={16} />Back to stock</Link>} actions={(canQuarantine || canRelease) && <button ref={triggerRef} className="button button--primary" type="button" onClick={() => { setReason(""); setActionError(null); setAction(canQuarantine ? "quarantine" : "release"); }}>{canQuarantine ? "Quarantine lot" : "Release quarantine"}</button>}>
    {state.status === "loading" && <div className="master-state" role="status"><span className="spinner" /><p>Loading lot balance…</p></div>}
    {state.status === "error" && <><RequestError error={state.error} /><button className="button button--secondary" type="button" onClick={() => setAttempt(value => value + 1)}>Try again</button></>}
    {lot && <><section className="record-panel"><div className="record-panel__heading"><h3>Lot balance</h3><span className="status-badge">{label(lot.lot_status)}</span></div><dl className="record-facts"><div><dt>Item</dt><dd>{lot.item_code} — {lot.item_name}</dd></div><div><dt>Plant</dt><dd>{lot.unit_code}</dd></div><div><dt>Store</dt><dd>{lot.store_code}</dd></div><div><dt>Received</dt><dd>{date(lot.received_at)}</dd></div><div><dt>Expiry</dt><dd>{lot.expiry_at?.slice(0, 10) ?? "Not set"}{lot.expired && " · Expired"}</dd></div></dl></section><section className="record-panel stock-lot-detail__balances"><div className="record-panel__heading"><h3>Quantity balance</h3></div><dl><div><dt>On hand</dt><dd>{lot.on_hand} {lot.uom_code}</dd></div><div><dt>Reserved</dt><dd>{lot.reserved} {lot.uom_code}</dd></div><div><dt>Available</dt><dd>{lot.available} {lot.uom_code}</dd></div></dl></section></>}
    {trace.status === "loading" && <div className="master-state" role="status"><span className="spinner" /><p>Loading lot trace…</p></div>}
    {trace.status === "error" && <><RequestError error={trace.error} /><button className="button button--secondary" type="button" onClick={() => setTraceAttempt(value => value + 1)}>Retry trace</button></>}
    {trace.status === "ready" && <>
      <section className="record-panel stock-lot-trace"><div className="record-panel__heading"><h3>Origin</h3></div>{trace.data.origin ? <dl className="record-facts"><div><dt>Goods receipt</dt><dd>{trace.data.origin.grn_no}</dd></div><div><dt>Purchase order</dt><dd>{trace.data.origin.po_no}</dd></div><div><dt>Vendor</dt><dd>{trace.data.origin.vendor}</dd></div><div><dt>Supplier challan</dt><dd>{trace.data.origin.supplier_dc_no ?? "Not provided"}</dd></div><div><dt>Received</dt><dd>{date(trace.data.origin.received_at)}</dd></div><div><dt>Received quantity</dt><dd>{trace.data.origin.received_qty}</dd></div><div><dt>Accepted quantity</dt><dd>{trace.data.origin.accepted_qty}</dd></div><div><dt>Rejected quantity</dt><dd>{trace.data.origin.rejected_qty}</dd></div></dl> : <p className="stock-lot-trace__empty">No supplier receipt origin is recorded for this lot.</p>}</section>
      <section className="record-panel stock-lot-trace"><div className="record-panel__heading"><h3>Movement history</h3></div>{trace.data.moves.length ? <ol className="stock-lot-trace__entries" aria-label="Stock movements">{trace.data.moves.map((move, index) => <li key={`${move.reference_id}:${move.occurred_at}:${index}`}><div><strong>{label(move.movement_type)}</strong><time dateTime={move.occurred_at}>{date(move.occurred_at)}</time></div><dl><div><dt>Quantity change</dt><dd>{move.quantity} {lot?.uom_code ?? ""}</dd></div><div><dt>Document</dt><dd>{move.document_no ?? "No document number"}</dd></div><div><dt>Reference type</dt><dd>{label(move.reference_type)}</dd></div></dl></li>)}</ol> : <p className="stock-lot-trace__empty">No stock movements are recorded for this lot.</p>}</section>
      <section className="record-panel stock-lot-trace"><div className="record-panel__heading"><h3>Transfer trace</h3></div>{trace.data.links.length ? <ul className="stock-lot-trace__entries" aria-label="Received transfer links">{trace.data.links.map((link, index) => <li key={`${link.transfer_no}:${link.direction}:${index}`}><div><strong>{link.direction === "to" ? "To" : "From"} {link.store_code}</strong><span>{link.transfer_no}</span></div><p>{link.quantity} {lot?.uom_code ?? ""} · {link.lot_id ? <Link to={`/app/inventory/lots/${link.lot_id}${location.search}`}>{link.lot_no} in {link.store_code}</Link> : `${link.lot_no} in ${link.store_code}`}</p></li>)}</ul> : <p className="stock-lot-trace__empty">No received transfer links are recorded for this lot.</p>}</section>
    </>}
    {action && <div className="bom-editor-backdrop"><section className="bom-editor stock-lot-action" role="dialog" aria-modal="true" aria-labelledby="stock-lot-action-title" onKeyDown={keys}><div className="bom-editor__header"><div><p className="eyebrow">{lot?.lot_no}</p><h3 id="stock-lot-action-title">{action === "quarantine" ? "Quarantine lot" : "Release quarantine"}</h3><p>{action === "quarantine" ? "Quarantined stock cannot be issued until released." : "Releasing quarantine makes this lot available for issue if other stock rules allow it."}</p></div><button ref={closeRef} className="button button--secondary" type="button" disabled={busy} onClick={close} aria-label="Close lot action"><X size={18} /></button></div><form onSubmit={submit}><div className="bom-editor__body"><div className="field"><label htmlFor="stock-lot-reason">Reason</label><textarea id="stock-lot-reason" value={reason} onChange={event => { setReason(event.target.value); setActionError(null); }} maxLength={300} /></div>{actionError && <RequestError error={actionError} />}</div><div className="bom-editor__actions"><button className="button button--secondary" type="button" disabled={busy} onClick={close}>Cancel</button><button className="button button--primary" type="submit" disabled={busy}>{busy ? "Recording…" : action === "quarantine" ? "Confirm quarantine" : "Confirm release"}</button></div></form></section></div>}
  </DetailPageTemplate>;
}
