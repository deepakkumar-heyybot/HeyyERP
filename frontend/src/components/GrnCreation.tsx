import { FormEvent, useEffect, useMemo, useState } from "react";
import { ArrowLeft, ClipboardList } from "lucide-react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { GrnCreate, Po, PoReceivingLine, PoSummary, Store, createGrn, fetchAllPos, fetchAllStores, fetchPo, fetchPoReceiving } from "../api-client";
import { RequestError } from "./RequestError";
import { InventoryTabs } from "./InventoryTabs";

const PATH = "/app/inventory/grns";
type Load<T> = { status: "loading" } | { status: "ready"; data: T } | { status: "error"; error: Error };
type Entry = { selected: boolean; storeId: string; quantity: string; supplierLot: string };
const failure = (value: unknown, fallback: string) => value instanceof Error ? value : new Error(fallback);
const decimal = (value: string) => { const [whole, fraction = ""] = value.split("."); return BigInt(whole) * 1000n + BigInt(fraction.padEnd(3, "0")); };
const validQty = (value: string) => /^\d{1,10}(?:\.\d{1,3})?$/.test(value) && decimal(value) > 0n && decimal(value) <= 1_000_000_000_000n;

export function GrnCreation({ canIssue }: { canIssue: boolean }) {
  const navigate = useNavigate();
  const [params, setParams] = useSearchParams();
  const poId = params.get("po_id") ?? "";
  const [orders, setOrders] = useState<Load<PoSummary[]>>({ status: "loading" });
  const [po, setPo] = useState<Load<Po> | null>(null);
  const [receiving, setReceiving] = useState<Load<PoReceivingLine[]> | null>(null);
  const [stores, setStores] = useState<Load<Store[]> | null>(null);
  const [entries, setEntries] = useState<Record<string, Entry>>({});
  const [challan, setChallan] = useState("");
  const [receivedAt, setReceivedAt] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<Error | null>(null);
  useEffect(() => {
    const controller = new AbortController();
    void Promise.all([fetchAllPos("OPEN", null, null, controller.signal), fetchAllPos("PARTIALLY_RECEIVED", null, null, controller.signal)])
      .then(([open, partial]) => { if (!controller.signal.aborted) setOrders({ status: "ready", data: [...open, ...partial] }); })
      .catch(value => { if (!controller.signal.aborted) setOrders({ status: "error", error: failure(value, "Receivable purchase orders could not be loaded.") }); });
    return () => controller.abort();
  }, []);
  useEffect(() => {
    if (!poId) { setPo(null); setReceiving(null); setStores(null); setEntries({}); return; }
    const controller = new AbortController();
    setPo({ status: "loading" }); setReceiving({ status: "loading" }); setStores({ status: "loading" }); setEntries({});
    void fetchPo(poId, controller.signal).then(data => {
      if (controller.signal.aborted) return;
      setPo({ status: "ready", data });
      setEntries(Object.fromEntries(data.lines.map(line => [line.po_line_id, { selected: false, storeId: "", quantity: "", supplierLot: "" }])));
      void fetchAllStores(true, data.unit_id, null, controller.signal).then(found => { if (!controller.signal.aborted) setStores({ status: "ready", data: found.filter(store => store.active && store.unit_id === data.unit_id) }); }).catch(value => { if (!controller.signal.aborted) setStores({ status: "error", error: failure(value, "Active stores could not be loaded.") }); });
    }).catch(value => { if (!controller.signal.aborted) setPo({ status: "error", error: failure(value, "Purchase order could not be loaded.") }); });
    void fetchPoReceiving(poId, controller.signal).then(data => { if (!controller.signal.aborted) setReceiving({ status: "ready", data }); }).catch(value => { if (!controller.signal.aborted) setReceiving({ status: "error", error: failure(value, "Receivable quantities could not be loaded.") }); });
    return () => controller.abort();
  }, [poId]);
  const order = po?.status === "ready" ? po.data : null;
  const available = useMemo(() => receiving?.status === "ready" ? new Map(receiving.data.map(line => [line.po_line_id, line])) : new Map<string, PoReceivingLine>(), [receiving]);
  function patch(id: string, change: Partial<Entry>) { setEntries(current => ({ ...current, [id]: { ...current[id], ...change } })); setError(null); }
  function selectPo(id: string) { const next = new URLSearchParams(params); if (id) next.set("po_id", id); else next.delete("po_id"); setParams(next, { replace: true }); setError(null); }
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); setError(null);
    const selected = order?.lines.filter(line => entries[line.po_line_id]?.selected) ?? [];
    if (!order || !["OPEN", "PARTIALLY_RECEIVED"].includes(order.status) || receiving?.status !== "ready" || stores?.status !== "ready" || !challan.trim() || !selected.length || selected.length > 100 || selected.some(line => {
      const entry = entries[line.po_line_id], limit = available.get(line.po_line_id);
      return !entry.storeId || !stores.data.some(store => store.store_id === entry.storeId) || !limit || !validQty(entry.quantity) || decimal(entry.quantity) > decimal(limit.max_receivable_qty);
    })) { setError(new Error("Choose an open purchase order, delivery challan, and valid store and receipt quantity for each selected line.")); return; }
    const body: GrnCreate = { po_id: order.po_id, supplier_dc_no: challan.trim(), received_at: receivedAt ? new Date(receivedAt).toISOString() : null,
      lines: selected.map(line => ({ po_line_id: line.po_line_id, store_id: entries[line.po_line_id].storeId, received_qty: entries[line.po_line_id].quantity, supplier_lot_no: entries[line.po_line_id].supplierLot.trim() || null })) };
    setSaving(true);
    try { const grn = await createGrn(body); navigate(`${PATH}/${grn.grn_id}`); }
    catch (value) { setError(failure(value, "Goods receipt could not be posted.")); }
    finally { setSaving(false); }
  }
  return <section className="master-detail grn-create" aria-labelledby="grn-create-title"><InventoryTabs canIssue={canIssue} /><div className="master-detail__heading"><div><Link to={PATH}><ArrowLeft size={16} />Back to goods receipts</Link><h2 id="grn-create-title">New goods receipt</h2><p>Receive eligible purchase-order lines into an active store. New lots begin in QA hold.</p></div></div><div className="form-panel"><form className="record-form" onSubmit={submit}><div className="record-form__heading"><h3>Delivery source</h3></div><div className="record-form__grid"><div className="field"><label htmlFor="grn-po">Purchase order</label><select id="grn-po" required value={poId} onChange={event => selectPo(event.target.value)}><option value="">Select open purchase order</option>{orders.status === "ready" && orders.data.map(item => <option key={item.po_id} value={item.po_id}>{item.po_no} — {item.vendor_code} — {item.unit_code}</option>)}{poId && orders.status === "ready" && !orders.data.some(item => item.po_id === poId) && <option value={poId}>Selected purchase order</option>}</select></div><div className="field"><label htmlFor="grn-challan">Supplier delivery challan</label><input id="grn-challan" required maxLength={60} value={challan} onChange={event => setChallan(event.target.value)} /></div><div className="field"><label htmlFor="grn-received-at">Received at (optional)</label><input id="grn-received-at" type="datetime-local" value={receivedAt} onChange={event => setReceivedAt(event.target.value)} /></div></div>
      {orders.status === "loading" && <p role="status">Loading receivable purchase orders…</p>}{orders.status === "error" && <RequestError error={orders.error} />}{orders.status === "ready" && orders.data.length === 0 && <div className="master-state"><ClipboardList size={24} /><h3>No open purchase orders</h3><p>An open or partly received order is required before posting a goods receipt.</p></div>}
      {po?.status === "loading" && <p role="status">Loading purchase order…</p>}{po?.status === "error" && <RequestError error={po.error} />}
      {order && <><div className="grn-create__context"><strong>{order.po_no}</strong><span>{order.vendor_code} — {order.vendor_name}</span><span>{order.unit_code} · {order.status.toLowerCase().replaceAll("_", " ")}</span></div>{!["OPEN", "PARTIALLY_RECEIVED"].includes(order.status) && <RequestError error={new Error("This purchase order is not open for receiving.")} />}</>}
      {receiving?.status === "loading" && <p role="status">Loading receivable quantities…</p>}{receiving?.status === "error" && <RequestError error={receiving.error} />}{stores?.status === "loading" && <p role="status">Loading active stores…</p>}{stores?.status === "error" && <RequestError error={stores.error} />}{stores?.status === "ready" && stores.data.length === 0 && <div className="master-state"><ClipboardList size={24} /><h3>No active stores in this plant</h3><p>An active store of the purchase-order plant is required.</p></div>}
      {order && receiving?.status === "ready" && <div className="indent-create__lines"><div className="record-form__heading"><h3>Received lines</h3><p>Select up to 100 distinct PO lines. Each quantity must fit the permitted receiving balance.</p></div>{order.lines.map((line, index) => { const limit = available.get(line.po_line_id), entry = entries[line.po_line_id], canReceive = Boolean(limit && /[1-9]/.test(limit.max_receivable_qty)); return <div className="record-panel grn-create__line" key={line.po_line_id}><label className="grn-create__select"><input type="checkbox" aria-label={`Receive line ${index + 1}`} checked={entry?.selected ?? false} disabled={!canReceive} onChange={event => patch(line.po_line_id, { selected: event.target.checked })} /><strong>{line.item_code} — {line.item_name}</strong></label><p>Ordered {line.ordered_qty} {line.uom_code} · Pending {limit?.pending_qty ?? "Unavailable"} {line.uom_code} · Maximum receivable {limit?.max_receivable_qty ?? "Unavailable"} {line.uom_code}</p>{entry?.selected && <div className="record-form__grid"><div className="field"><label htmlFor={`grn-store-${index}`}>Store for line {index + 1}</label><select id={`grn-store-${index}`} required value={entry.storeId} onChange={event => patch(line.po_line_id, { storeId: event.target.value })}><option value="">Select active store</option>{stores?.status === "ready" && stores.data.map(store => <option key={store.store_id} value={store.store_id}>{store.code} — {store.name}</option>)}</select></div><div className="field"><label htmlFor={`grn-qty-${index}`}>Received quantity for line {index + 1}</label><input id={`grn-qty-${index}`} inputMode="decimal" required value={entry.quantity} onChange={event => patch(line.po_line_id, { quantity: event.target.value })} /></div><div className="field"><label htmlFor={`grn-lot-${index}`}>Supplier lot for line {index + 1}</label><input id={`grn-lot-${index}`} maxLength={60} value={entry.supplierLot} onChange={event => patch(line.po_line_id, { supplierLot: event.target.value })} /></div></div>}</div>; })}</div>}
      {error && <RequestError error={error} />}<div className="record-form__actions"><Link className="button button--secondary" to={PATH}>Cancel</Link><button className="button button--primary" type="submit" disabled={saving || orders.status !== "ready" || !order || !["OPEN", "PARTIALLY_RECEIVED"].includes(order.status) || receiving?.status !== "ready" || stores?.status !== "ready" || !stores.data.length}>{saving ? "Posting…" : "Post goods receipt"}</button></div></form></div></section>;
}
