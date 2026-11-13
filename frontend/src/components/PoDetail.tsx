import { useEffect, useRef, useState } from "react";
import { ArrowLeft, RefreshCw } from "lucide-react";
import { Link, useLocation } from "react-router-dom";
import { BusinessRule, Me, Po, PoHistory, PoReceivingLine, fetchPo, fetchPoHistory, fetchPoReceiving } from "../api-client";
import { ProcurementTabs } from "./ProcurementTabs";
import { RequestError } from "./RequestError";
import { PoActions } from "./PoActions";

type Load<T> = { status: "loading" } | { status: "ready"; data: T } | { status: "error"; error: Error };
const failure = (value: unknown, fallback: string) => value instanceof Error ? value : new Error(fallback);
const label = (value: string) => value.toLowerCase().replaceAll("_", " ").replace(/^./, letter => letter.toUpperCase());
const dateTime = (value: string) => value.replace("T", " ").slice(0, 16);

export function PoDetail({ id, me, settings }: { id: string; me: Me; settings: BusinessRule[] }) {
  const location = useLocation();
  const headingRef = useRef<HTMLHeadingElement>(null);
  const [po, setPo] = useState<Load<Po>>({ status: "loading" });
  const [history, setHistory] = useState<Load<PoHistory>>({ status: "loading" });
  const [receiving, setReceiving] = useState<Load<PoReceivingLine[]>>({ status: "loading" });
  const [attempt, setAttempt] = useState(0);
  const [auxRevision, setAuxRevision] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    setPo({ status: "loading" });
    void fetchPo(id, controller.signal).then(data => { if (!controller.signal.aborted) setPo({ status: "ready", data }); })
      .catch(value => { if (!controller.signal.aborted) setPo({ status: "error", error: failure(value, "Purchase order could not be loaded.") }); });
    return () => controller.abort();
  }, [id, attempt]);
  useEffect(() => {
    const controller = new AbortController();
    setHistory({ status: "loading" });
    setReceiving({ status: "loading" });
    void fetchPoHistory(id, controller.signal).then(data => { if (!controller.signal.aborted) setHistory({ status: "ready", data }); })
      .catch(value => { if (!controller.signal.aborted) setHistory({ status: "error", error: failure(value, "Order history could not be loaded.") }); });
    void fetchPoReceiving(id, controller.signal).then(data => { if (!controller.signal.aborted) setReceiving({ status: "ready", data }); })
      .catch(value => { if (!controller.signal.aborted) setReceiving({ status: "error", error: failure(value, "Receiving status could not be loaded.") }); });
    return () => controller.abort();
  }, [id, attempt, auxRevision]);
  const order = po.status === "ready" ? po.data : null;
  const retry = () => setAttempt(value => value + 1);
  return <section className="master-detail po-detail" aria-labelledby="po-detail-title"><ProcurementTabs active="orders" />
    <div className="master-detail__heading"><div><Link to={`/app/procurement/purchase-orders${location.search}`}><ArrowLeft size={16} />Back to purchase orders</Link><h2 id="po-detail-title" ref={headingRef} tabIndex={-1}>{order?.po_no ?? "Purchase order detail"}</h2></div>{order && <span className="status-badge">{label(order.status)}</span>}</div>
    {po.status === "loading" && <div className="master-state" role="status"><span className="spinner" /><p>Loading purchase order…</p></div>}
    {po.status === "error" && <><RequestError error={po.error} /><button className="button button--secondary" type="button" onClick={retry}>Try again</button></>}
    {order && <>
      <PoActions po={order} me={me} settings={settings} onUpdated={data => { setPo({ status: "ready", data }); setAuxRevision(value => value + 1); }} onStateChanged={() => headingRef.current?.focus()} />
      <section className="record-panel"><div className="record-panel__heading"><h3>Order summary</h3></div><dl className="record-facts"><div><dt>Vendor</dt><dd>{order.vendor_code} — {order.vendor_name}</dd></div><div><dt>Plant</dt><dd>{order.unit_code}</dd></div><div><dt>Created</dt><dd>{dateTime(order.created_at)}</dd></div><div><dt>Created by</dt><dd>{order.created_by_name}</dd></div><div><dt>Status</dt><dd>{label(order.status)}</dd></div><div><dt>Payment term</dt><dd>{order.payment_term_id ? "Payment term linked" : "Vendor default"}</dd></div></dl></section>
      <section className="record-panel"><div className="record-panel__heading"><h3>Source documents</h3></div><div className="po-detail__sources"><p>RFQ: {order.rfq_id && order.rfq_no ? <Link to={`/app/procurement/rfqs/${order.rfq_id}`}>{order.rfq_no}</Link> : "Direct or indent purchase order"}</p><p>Indents: {order.indents.length ? order.indents.map(indent => <Link key={indent.indent_id} to={`/app/procurement/indents/${indent.indent_id}`}>{indent.indent_no}</Link>) : "None linked"}</p></div></section>
      <section className="record-panel"><div className="record-panel__heading"><h3>Order lines</h3></div><ul className="po-detail__lines">{order.lines.map(line => <li key={line.po_line_id}><div className="po-detail__line-header"><strong>{line.line_no}. {line.item_code} — {line.item_name}</strong><span>{line.tax_code ?? "No tax code"}</span></div><div className="po-detail__line-facts"><span>Ordered {line.ordered_qty} {line.uom_code}</span><span>Received {line.received_qty} {line.uom_code}</span><span>Unit price {order.currency_code} {line.unit_price}</span><span>Net {order.currency_code} {line.net}</span><span>GST {order.currency_code} {line.gst}</span><span>Gross {order.currency_code} {line.gross}</span></div><div className="po-detail__schedules"><h4>Delivery schedules</h4>{line.schedule.length ? line.schedule.map((delivery, index) => <p key={`${line.po_line_id}-${index}`}><time dateTime={delivery.due_date}>{delivery.due_date}</time><span>Scheduled {delivery.scheduled_qty} {line.uom_code}</span><span>Received {delivery.received_qty} {line.uom_code}</span></p>) : <p>No delivery schedule returned.</p>}</div></li>)}</ul></section>
      <section className="record-panel"><div className="record-panel__heading"><h3>Order totals</h3></div><dl className="po-detail__totals"><div><dt>Net</dt><dd>{order.currency_code} {order.net_total}</dd></div><div><dt>GST</dt><dd>{order.currency_code} {order.gst_total}</dd></div><div><dt>Gross total</dt><dd>{order.currency_code} {order.gross_total}</dd></div></dl></section>
      <section className="record-panel"><div className="record-panel__heading"><h3>Receiving</h3><button className="button button--secondary" type="button" onClick={() => setAuxRevision(value => value + 1)}><RefreshCw size={15} />Refresh receiving and history</button></div>{receiving.status === "loading" && <p role="status">Loading receiving status…</p>}{receiving.status === "error" && <><RequestError error={receiving.error} /><button className="button button--secondary" type="button" onClick={() => setAuxRevision(value => value + 1)}>Try again</button></>}{receiving.status === "ready" && (receiving.data.length ? <ul className="po-detail__receiving">{receiving.data.map(line => <li key={line.po_line_id}><strong>{line.item_code}</strong><span>Pending {line.pending_qty} {order.lines.find(item => item.po_line_id === line.po_line_id)?.uom_code ?? ""}</span><span>Maximum receivable {line.max_receivable_qty} {order.lines.find(item => item.po_line_id === line.po_line_id)?.uom_code ?? ""}</span></li>)}</ul> : <p>No receiving status was returned.</p>)}</section>
      <section className="record-panel"><div className="record-panel__heading"><h3>History and approval</h3></div>{history.status === "loading" && <p role="status">Loading order history…</p>}{history.status === "error" && <><RequestError error={history.error} /><button className="button button--secondary" type="button" onClick={() => setAuxRevision(value => value + 1)}>Try again</button></>}{history.status === "ready" && <><p>Approval: {history.data.approval?.status ? <>{label(history.data.approval.status)} {order.approval_id && me.modules.includes("approvals") && <Link to={`/app/approvals/${order.approval_id}`}>Open approval</Link>}</> : "No approval linked"}</p>{history.data.events.length ? <ol className="po-detail__history">{history.data.events.map((event, index) => <li key={`${event.occurred_at}-${index}`}><strong>{event.action}</strong><span>{event.actor ?? "System"} · {dateTime(event.occurred_at)}</span></li>)}</ol> : <p>No history events returned.</p>}</>}</section>
    </>}
  </section>;
}
