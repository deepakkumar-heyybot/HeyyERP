import { useEffect, useRef, useState } from "react";
import { ArrowLeft, RefreshCw } from "lucide-react";
import { Link } from "react-router-dom";
import {
  ApiError, fetchAllStores, fetchProductionLot, fetchProductionLotTrace, moveProductionLot,
  packProductionLot, reworkProductionLot, returnProductionLotMaterial, type Me,
  type ProductionLot, type ProductionLotTrace, type Store,
} from "../api-client";
import { ManufacturingTabs } from "./ManufacturingTabs";
import { DetailPageTemplate } from "./PageTemplates";
import { RequestError } from "./RequestError";

type Load<T> = { status: "loading" } | { status: "ready"; data: T } | { status: "error"; error: Error };
type Pending =
  | { kind: "move"; body: { rejects: string; notes: string | null }; title: string }
  | { kind: "pack"; body: { fg_store_id: string; notes: string | null }; title: string }
  | { kind: "rework"; body: { quantity: string; reason: string }; title: string }
  | { kind: "return"; body: { source_lot_no: string; quantity: string }; title: string };

const label = (value: string) => value.toLowerCase().replaceAll("_", " ").replace(/^./, letter => letter.toUpperCase());
function validDecimal(value: string, allowZero = false) {
  if (!/^\d{1,10}(?:\.\d{1,3})?$/.test(value)) return false;
  const [whole, fraction = ""] = value.split(".");
  const amount = BigInt(whole) * 1000n + BigInt(fraction.padEnd(3, "0"));
  return allowZero ? amount >= 0n : amount > 0n;
}
function ErrorNotice({ error }: { error: Error }) {
  return <><RequestError error={error} />{error instanceof ApiError && error.status < 500 && error.requestId && <p>Request ID: {error.requestId}</p>}</>;
}

function TraceNode({ node, nested = false }: { node: ProductionLotTrace; nested?: boolean }) {
  const quantityUsed = (node as ProductionLotTrace & { quantity_used?: string }).quantity_used;
  return <div className={nested ? "record-panel" : undefined}>
    {nested && <p><strong>{node.lot_no}</strong> · {node.item_code} · used {quantityUsed ?? node.quantity}</p>}
    {node.raw_material.length > 0 && <><h4>Raw material</h4><ul>{node.raw_material.map(row => <li key={`${row.lot_no}-${row.item_code}`}>{row.lot_no} · {row.item_code} · {row.quantity}</li>)}</ul></>}
    {node.compound_batches.length > 0 && <><h4>Compound batches</h4>{node.compound_batches.map(batch => <div key={batch.batch_no}><p><strong>{batch.batch_no}</strong> · {batch.compound_code} · {batch.quantity}</p><ul>{batch.raw_material.map(row => <li key={`${batch.batch_no}-${row.lot_no}`}>{row.lot_no} · {row.item_code} · {row.quantity}</li>)}</ul></div>)}</>}
    {node.metal_lots.length > 0 && <><h4>Metal production lots</h4>{node.metal_lots.map(child => <TraceNode key={child.lot_no} node={child} nested />)}</>}
  </div>;
}

function LotDetail({ id, me }: { id: string; me: Me }) {
  const [lot, setLot] = useState<Load<ProductionLot>>({ status: "loading" });
  const [trace, setTrace] = useState<Load<ProductionLotTrace>>({ status: "loading" });
  const [stores, setStores] = useState<Load<Store[]>>({ status: "loading" });
  const [attempt, setAttempt] = useState(0);
  const [rejects, setRejects] = useState("0.000");
  const [moveNotes, setMoveNotes] = useState("");
  const [fgStore, setFgStore] = useState("");
  const [packingNotes, setPackingNotes] = useState("");
  const [reworkQty, setReworkQty] = useState("");
  const [reworkReason, setReworkReason] = useState("");
  const [sourceLot, setSourceLot] = useState("");
  const [returnQty, setReturnQty] = useState("");
  const [validation, setValidation] = useState("");
  const [actionError, setActionError] = useState<Error | null>(null);
  const [unknownRequestId, setUnknownRequestId] = useState<string>();
  const [pending, setPending] = useState<Pending | null>(null);
  const [busy, setBusy] = useState(false);
  const inFlight = useRef(false);
  const headingRef = useRef<HTMLHeadingElement>(null);

  useEffect(() => {
    const controller = new AbortController();
    setLot({ status: "loading" }); setTrace({ status: "loading" });
    void fetchProductionLot(id, controller.signal).then(data => { if (!controller.signal.aborted) { setLot({ status: "ready", data }); setUnknownRequestId(undefined); } }).catch(value => { if (!controller.signal.aborted) setLot({ status: "error", error: value instanceof Error ? value : new Error("Production lot could not be loaded.") }); });
    void fetchProductionLotTrace(id, controller.signal).then(data => { if (!controller.signal.aborted) setTrace({ status: "ready", data }); }).catch(value => { if (!controller.signal.aborted) setTrace({ status: "error", error: value instanceof Error ? value : new Error("Production genealogy could not be loaded.") }); });
    return () => controller.abort();
  }, [attempt, id]);

  const current = lot.status === "ready" ? lot.data : null;
  const currentUnitId = current?.unit_id ?? null;
  const canOperate = current?.status === "IN_PROCESS" && me.permissions.includes("route.move");
  const canMove = current?.status === "IN_PROCESS" && (current.is_qa_gate ? me.permissions.includes("qa.approve") : me.permissions.includes("route.move"));
  const isPacking = current?.stage_code === "PACKING";

  useEffect(() => {
    if (!currentUnitId || !isPacking || !canOperate) return;
    const controller = new AbortController(); setStores({ status: "loading" });
    void fetchAllStores(true, currentUnitId, "FINISHED_GOODS", controller.signal)
      .then(data => { if (!controller.signal.aborted) setStores({ status: "ready", data: data.filter(store => store.active && store.unit_id === currentUnitId && store.store_type === "FINISHED_GOODS") }); })
      .catch(value => { if (!controller.signal.aborted) setStores({ status: "error", error: value instanceof Error ? value : new Error("Finished-goods stores could not be loaded.") }); });
    return () => controller.abort();
  }, [canOperate, current?.prod_lot_id, currentUnitId, isPacking]);

  function prepare(kind: Pending["kind"]) {
    setValidation(""); setActionError(null);
    if (kind === "move") {
      const value = rejects.trim(); if (!validDecimal(value, true)) return setValidation("Reject quantity must be zero or greater, with up to three decimal places.");
      setPending({ kind, body: { rejects: value, notes: moveNotes.trim() || null }, title: "Confirm stage movement" });
    } else if (kind === "pack") {
      if (stores.status !== "ready" || !stores.data.some(store => store.store_id === fgStore)) return setValidation("Select an active finished-goods store in this plant.");
      setPending({ kind, body: { fg_store_id: fgStore, notes: packingNotes.trim() || null }, title: "Confirm finished-goods packing" });
    } else if (kind === "rework") {
      const value = reworkQty.trim(); if (!validDecimal(value) || reworkReason.trim().length < 3) return setValidation("Enter a positive rework quantity and a reason of at least three characters.");
      setPending({ kind, body: { quantity: value, reason: reworkReason.trim() }, title: "Confirm rework" });
    } else {
      const value = returnQty.trim(); if (!sourceLot || !validDecimal(value)) return setValidation("Select a source stock lot and enter a positive return quantity.");
      setPending({ kind, body: { source_lot_no: sourceLot, quantity: value }, title: "Confirm material return" });
    }
  }

  async function execute() {
    if (!pending || inFlight.current || unknownRequestId) return;
    inFlight.current = true; setBusy(true); setActionError(null);
    try {
      const result = pending.kind === "move" ? await moveProductionLot(id, pending.body)
        : pending.kind === "pack" ? await packProductionLot(id, pending.body)
        : pending.kind === "rework" ? await reworkProductionLot(id, pending.body)
        : await returnProductionLotMaterial(id, pending.body);
      setLot({ status: "ready", data: result }); setPending(null);
    } catch (value) {
      setPending(null);
      const error = value instanceof Error ? value : new Error("The production-lot action failed.");
      const definite = value instanceof ApiError && value.status >= 400 && value.status < 500 && value.status !== 408;
      if (definite) setActionError(error);
      else setUnknownRequestId(value instanceof ApiError ? value.requestId : "unknown");
    } finally { inFlight.current = false; setBusy(false); }
  }

  if (lot.status === "loading") return <section className="master-state" role="status"><span className="spinner" /><p>Loading production lot…</p></section>;
  if (lot.status === "error") return <section><ErrorNotice error={lot.error} /><button className="button button--secondary" type="button" onClick={() => setAttempt(value => value + 1)}>Try again</button></section>;
  if (!current) return null;

  const rawSources = current.sources.filter(source => source.consumption_type === "RAW_MATERIAL");
  return <>
    <DetailPageTemplate title={current.lot_no} headingRef={headingRef} navigation={<ManufacturingTabs active="lots" />} back={<Link to="/app/manufacturing/lots"><ArrowLeft size={16} />Back to production lots</Link>}
      description={`${current.item_code} · ${current.work_order_no}`} status={<span className="status-badge status-badge--pending">{current.status}</span>}>
      <div className="detail-grid"><section className="record-panel"><h3>Lot summary</h3><dl className="detail-list"><div><dt>Work order</dt><dd>{current.work_order_no}</dd></div><div><dt>Item</dt><dd>{current.item_code}</dd></div><div><dt>Route</dt><dd>{current.route_code}</dd></div><div><dt>Current stage</dt><dd>{current.stage_code} — {current.stage_name}</dd></div><div><dt>Quantity</dt><dd>{current.quantity}</dd></div><div><dt>Created</dt><dd><time dateTime={current.created_at}>{current.created_at}</time></dd></div></dl></section>
      <section className="record-panel"><h3>Consumed sources</h3>{current.sources.length ? <ul>{current.sources.map(source => <li key={`${source.consumption_type}-${source.source}`}>{source.source}{source.item_code ? ` · ${source.item_code}` : ""} · {source.quantity} · {label(source.consumption_type)}</li>)}</ul> : <p>No consumed sources were returned.</p>}</section></div>
      <section className="record-panel"><h3>Movement history</h3>{current.moves.length ? <><p className="compound-scroll-hint">Scroll the movement history horizontally to view timestamps and notes.</p><div className="master-table-wrap" role="region" aria-label="Scrollable movement history" tabIndex={0}><table className="master-table" aria-label="Movement history" style={{ minWidth: 1100, tableLayout: "auto" }}><thead><tr><th>From</th><th>To</th><th>Actor</th><th>Quantity in</th><th>Quantity out</th><th>Rejects</th><th style={{ minWidth: 190 }}>Moved</th><th style={{ minWidth: 180 }}>Notes</th></tr></thead><tbody>{current.moves.map((move, index) => <tr key={`${move.moved_at}-${index}`}><td>{move.from_stage ?? "Start"}</td><td>{move.to_stage}</td><td>{move.actor}</td><td>{move.quantity_in}</td><td>{move.quantity_out}</td><td>{move.rejects}</td><td style={{ whiteSpace: "nowrap" }}>{move.moved_at}</td><td>{move.notes ?? "—"}</td></tr>)}</tbody></table></div><ul className="master-cards" aria-label="Movement history">{current.moves.map((move, index) => <li key={`${move.moved_at}-card-${index}`}><div><span>{move.from_stage ?? "Start"} → {move.to_stage}</span></div><h3>{move.actor}</h3><dl><div><dt>Quantity in</dt><dd>{move.quantity_in}</dd></div><div><dt>Quantity out</dt><dd>{move.quantity_out}</dd></div><div><dt>Rejects</dt><dd>{move.rejects}</dd></div><div><dt>Moved</dt><dd>{move.moved_at}</dd></div><div><dt>Notes</dt><dd>{move.notes ?? "—"}</dd></div></dl></li>)}</ul></> : <p>No movement history was returned.</p>}</section>
      {current.packing && <section className="record-panel"><h3>Packing</h3><p>{current.packing.label_code} · {current.packing.packed_qty} · {current.packing.fg_lot_no ?? "No finished-goods lot number"}</p></section>}
      <section className="record-panel"><h3>Genealogy</h3>{trace.status === "loading" ? <p role="status">Loading production genealogy…</p> : trace.status === "error" ? <ErrorNotice error={trace.error} /> : <TraceNode node={trace.data} />}</section>
      {current.status === "IN_PROCESS" && <section className="record-panel"><h3>Production actions</h3>
        {current.is_qa_gate && !me.permissions.includes("qa.approve") && <p>The qa.approve permission is required to pass this QA gate.</p>}
        {!isPacking && canMove && <div className="record-form__grid"><div className="field"><label htmlFor="lot-rejects">Reject quantity</label><input id="lot-rejects" inputMode="decimal" value={rejects} onChange={event => setRejects(event.target.value)} /></div><div className="field"><label htmlFor="lot-move-notes">Move notes</label><input id="lot-move-notes" maxLength={500} value={moveNotes} onChange={event => setMoveNotes(event.target.value)} /></div><button className="button button--primary" type="button" disabled={Boolean(unknownRequestId) || busy} onClick={() => prepare("move")}>Move to next stage</button></div>}
        {isPacking && canOperate && <div className="record-form__grid">{stores.status === "loading" ? <p role="status">Loading finished-goods stores…</p> : stores.status === "error" ? <ErrorNotice error={stores.error} /> : <div className="field"><label htmlFor="lot-fg-store">Finished-goods store</label><select id="lot-fg-store" value={fgStore} onChange={event => setFgStore(event.target.value)}><option value="">Select store</option>{stores.data.map(store => <option key={store.store_id} value={store.store_id}>{store.code} — {store.name}</option>)}</select></div>}<div className="field"><label htmlFor="lot-pack-notes">Packing notes</label><input id="lot-pack-notes" maxLength={500} value={packingNotes} onChange={event => setPackingNotes(event.target.value)} /></div><button className="button button--primary" type="button" disabled={Boolean(unknownRequestId) || busy} onClick={() => prepare("pack")}>Complete packing into finished goods</button></div>}
        {canOperate && <div className="detail-grid"><div><h4>Rework</h4><div className="field"><label htmlFor="lot-rework-qty">Rework quantity</label><input id="lot-rework-qty" inputMode="decimal" value={reworkQty} onChange={event => setReworkQty(event.target.value)} /></div><div className="field"><label htmlFor="lot-rework-reason">Rework reason</label><input id="lot-rework-reason" maxLength={200} value={reworkReason} onChange={event => setReworkReason(event.target.value)} /></div><button className="button button--secondary" type="button" disabled={Boolean(unknownRequestId) || busy} onClick={() => prepare("rework")}>Record rework</button></div>
        <div><h4>Return raw material</h4><div className="field"><label htmlFor="lot-return-source">Source stock lot</label><select id="lot-return-source" value={sourceLot} onChange={event => setSourceLot(event.target.value)}><option value="">Select source lot</option>{rawSources.map(source => <option key={source.source} value={source.source}>{source.source}{source.item_code ? ` — ${source.item_code}` : ""}</option>)}</select></div><div className="field"><label htmlFor="lot-return-qty">Return quantity</label><input id="lot-return-qty" inputMode="decimal" value={returnQty} onChange={event => setReturnQty(event.target.value)} /></div><button className="button button--secondary" type="button" disabled={Boolean(unknownRequestId) || busy} onClick={() => prepare("return")}>Return material</button></div></div>}
      </section>}
      {validation && <div className="decision-error" role="alert">{validation}</div>}{actionError && <ErrorNotice error={actionError} />}
      {unknownRequestId && <div className="decision-error" role="alert"><div><strong>Production lot action outcome unknown.</strong><p>We could not confirm whether the action completed. Refresh the current lot before trying again.</p>{unknownRequestId !== "unknown" && <p>Request ID: {unknownRequestId}</p>}<button className="button button--secondary" type="button" onClick={() => setAttempt(value => value + 1)}><RefreshCw size={16} />Refresh production lot</button></div></div>}
    </DetailPageTemplate>
    {pending && <div className="bom-editor-backdrop"><section className="bom-editor" role="dialog" aria-modal="true" aria-labelledby="lot-action-title"><div className="bom-editor__header"><h3 id="lot-action-title">{pending.title}</h3></div><div className="bom-editor__body"><p>This operation changes production and inventory records. Continue only after checking the entered values.</p>{busy && <p role="status">Saving production action…</p>}</div><div className="bom-editor__actions"><button className="button button--secondary" type="button" disabled={busy} onClick={() => setPending(null)}>Cancel</button><button className="button button--primary" type="button" disabled={busy} onClick={() => void execute()}>Confirm</button></div></section></div>}
  </>;
}

export function ProductionLotDetail({ id, me }: { id: string; me: Me }) {
  if (!me.modules.includes("manufacturing")) return <section className="master-state"><h2>Access denied</h2><p>This page is outside your current module access.</p></section>;
  return <LotDetail id={id} me={me} />;
}
