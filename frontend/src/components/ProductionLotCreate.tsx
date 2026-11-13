import { useEffect, useRef, useState, type FormEvent } from "react";
import { ArrowLeft } from "lucide-react";
import { Link } from "react-router-dom";
import {
  ApiError, createProductionLot, fetchAllStores, fetchAllWorkOrders, fetchWorkOrder,
  type Me, type ProductionLotCreate as CreateBody, type ProductionLotSummary,
  type Store, type WorkOrder, type WorkOrderSummary,
} from "../api-client";
import { ManufacturingTabs } from "./ManufacturingTabs";
import { DetailPageTemplate, FormPageTemplate } from "./PageTemplates";
import { RequestError } from "./RequestError";

type Catalog = { status: "loading" } | { status: "ready"; orders: WorkOrderSummary[]; stores: Store[] } | { status: "error"; error: Error };
type Detail = { status: "idle" } | { status: "loading"; id: string } | { status: "ready"; id: string; order: WorkOrder } | { status: "error"; id: string; error: Error };
type Draft = { workOrderId: string; quantity: string; storeId: string };
type Safety = { kind: "clear" } | { kind: "unknown"; draft: Draft | null } | { kind: "unavailable" };

function parseDraft(raw: string | null): Draft | null {
  if (raw === null) return null;
  try {
    const value: unknown = JSON.parse(raw);
    if (value && typeof value === "object") {
      const data = value as Record<string, unknown>;
      if (typeof data.workOrderId === "string" && typeof data.quantity === "string" && typeof data.storeId === "string")
        return { workOrderId: data.workOrderId, quantity: data.quantity, storeId: data.storeId };
    }
  } catch { /* A damaged draft must not remove the duplicate-write guard. */ }
  return null;
}

function readSafety(markerKey: string, draftKey: string): Safety {
  try {
    const marker = window.localStorage.getItem(markerKey);
    const rawDraft = window.sessionStorage.getItem(draftKey);
    if (marker === null && rawDraft === null) return { kind: "clear" };
    if (marker !== "1") window.localStorage.setItem(markerKey, "1");
    return { kind: "unknown", draft: parseDraft(rawDraft) };
  } catch { return { kind: "unavailable" }; }
}

function clearSafety(markerKey: string, draftKey: string) {
  try {
    window.sessionStorage.removeItem(draftKey);
    window.localStorage.removeItem(markerKey);
    return true;
  } catch { return false; }
}

function validQuantity(value: string) {
  if (!/^\d{1,10}(?:\.\d{1,3})?$/.test(value)) return false;
  const [whole, fraction = ""] = value.split(".");
  const milli = BigInt(whole) * 1000n + BigInt(fraction.padEnd(3, "0"));
  return milli > 0n && milli <= 1_000_000_000_000n;
}

function ErrorNotice({ error }: { error: Error }) {
  return <><RequestError error={error} />
    {error instanceof ApiError && error.status < 500 && error.requestId && <p>Request ID: {error.requestId}</p>}
  </>;
}

function CreateForm({ me }: { me: Me }) {
  const markerKey = `production-lot-create-unknown:${me.user_id}`;
  const draftKey = `production-lot-create-draft:${me.user_id}`;
  const [initialSafety] = useState(() => readSafety(markerKey, draftKey));
  const restored = initialSafety.kind === "unknown" ? initialSafety.draft : null;
  const [safety, setSafety] = useState<Safety>(initialSafety);
  const [unknownRequestId, setUnknownRequestId] = useState<string>();
  const [workOrderId, setWorkOrderId] = useState(restored?.workOrderId ?? "");
  const [quantity, setQuantity] = useState(restored?.quantity ?? "");
  const [storeId, setStoreId] = useState(restored?.storeId ?? "");
  const [confirmed, setConfirmed] = useState(false);
  const [catalog, setCatalog] = useState<Catalog>({ status: "loading" });
  const [detail, setDetail] = useState<Detail>({ status: "idle" });
  const [attempt, setAttempt] = useState(0);
  const [validation, setValidation] = useState("");
  const [error, setError] = useState<Error | null>(null);
  const [busy, setBusy] = useState(false);
  const [created, setCreated] = useState<ProductionLotSummary | null>(null);
  const inFlight = useRef(false);
  const mounted = useRef(true);
  const titleRef = useRef<HTMLHeadingElement>(null);

  useEffect(() => { mounted.current = true; titleRef.current?.focus(); return () => { mounted.current = false; }; }, []);
  useEffect(() => {
    const controller = new AbortController();
    setCatalog({ status: "loading" });
    void Promise.all([fetchAllWorkOrders(controller.signal), fetchAllStores(true, null, null, controller.signal)])
      .then(([orders, stores]) => {
        if (controller.signal.aborted) return;
        const authorized = (unitId: string) => me.all_plants || me.unit_ids.includes(unitId);
        setCatalog({ status: "ready",
          orders: orders.filter(order => authorized(order.unit_id) && (order.status === "RELEASED" || order.status === "IN_PROGRESS")),
          stores: stores.filter(store => store.active && authorized(store.unit_id)),
        });
      }).catch(value => { if (!controller.signal.aborted) setCatalog({ status: "error", error: value instanceof Error ? value : new Error("Production-lot options could not be loaded.") }); });
    return () => controller.abort();
  }, [attempt, me.all_plants, me.unit_ids]);

  useEffect(() => {
    if (!workOrderId) { setDetail({ status: "idle" }); return; }
    const controller = new AbortController();
    setDetail({ status: "loading", id: workOrderId });
    void fetchWorkOrder(workOrderId, controller.signal)
      .then(order => { if (!controller.signal.aborted) setDetail({ status: "ready", id: workOrderId, order }); })
      .catch(value => { if (!controller.signal.aborted) setDetail({ status: "error", id: workOrderId, error: value instanceof Error ? value : new Error("Work-order materials could not be loaded.") }); });
    return () => controller.abort();
  }, [workOrderId]);

  const ready = catalog.status === "ready" ? catalog : null;
  const selected = ready?.orders.find(order => order.work_order_id === workOrderId);
  const selectedDetail = detail.status === "ready" && detail.id === workOrderId ? detail.order : null;
  const needsStore = Boolean(selectedDetail?.materials.some(line => line.kind !== "COMPOUND"));
  const stores = selected ? ready?.stores.filter(store => store.unit_id === selected.unit_id) ?? [] : [];

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (busy || inFlight.current || safety.kind !== "clear") return;
    setValidation(""); setError(null);
    const amount = quantity.trim();
    if (!selected || !selectedDetail) { setValidation("Select an eligible released or in-progress work order and wait for its materials to load."); return; }
    if (!validQuantity(amount)) { setValidation("Quantity must be greater than zero and at most 1,000,000,000, with up to three decimal places."); return; }
    if (needsStore && !stores.some(store => store.store_id === storeId)) { setValidation("Select an active issue store in the work order plant."); return; }
    if (!confirmed) { setValidation("Confirm that starting the lot will consume stock and post accounting entries."); return; }
    const draft: Draft = { workOrderId, quantity: amount, storeId: needsStore ? storeId : "" };
    const existing = readSafety(markerKey, draftKey);
    if (existing.kind !== "clear") { setSafety(existing); return; }
    try {
      window.sessionStorage.setItem(draftKey, JSON.stringify(draft));
      window.localStorage.setItem(markerKey, "1");
    } catch {
      try { window.sessionStorage.removeItem(draftKey); } catch { /* Keep submission blocked. */ }
      setSafety({ kind: "unavailable" }); return;
    }
    const body: CreateBody = { work_order_id: selected.work_order_id, quantity: amount, store_id: needsStore ? storeId : null };
    inFlight.current = true; setBusy(true);
    try {
      const result = await createProductionLot(body);
      const cleared = clearSafety(markerKey, draftKey);
      if (mounted.current) { setCreated(result); if (!cleared) setSafety({ kind: "unavailable" }); }
    } catch (value) {
      const rejected = value instanceof ApiError && value.status >= 400 && value.status < 500 && value.status !== 408;
      if (rejected) {
        const cleared = clearSafety(markerKey, draftKey);
        if (mounted.current) { setError(value); if (!cleared) setSafety({ kind: "unavailable" }); }
      } else if (mounted.current) {
        setUnknownRequestId(value instanceof ApiError ? value.requestId : undefined);
        setSafety({ kind: "unknown", draft });
      }
    } finally { inFlight.current = false; if (mounted.current) setBusy(false); }
  }

  function acknowledge() {
    if (clearSafety(markerKey, draftKey)) { setUnknownRequestId(undefined); setSafety({ kind: "clear" }); }
    else setSafety({ kind: "unavailable" });
  }

  if (created) return <DetailPageTemplate title="Production lot started" navigation={<ManufacturingTabs active="lots" />} back={<Link to="/app/manufacturing/lots"><ArrowLeft size={16} />Back to production lots</Link>}>
    <section className="record-panel"><p role="status">Production lot {created.lot_no} started.</p><p>Work order: {created.work_order_no}</p><p>Quantity: {created.quantity}</p><p>Stage: {created.stage_code} — {created.stage_name}</p><p>Status: {created.status.replaceAll("_", " ")}</p></section>
  </DetailPageTemplate>;

  return <FormPageTemplate className="issue-planner" title="New production lot" headingRef={titleRef} navigation={<ManufacturingTabs active="lots" />} back={<Link to="/app/manufacturing/lots"><ArrowLeft size={16} />Back to production lots</Link>}
    description="Start an eligible work order lot at its first route stage." formTitle="Production lot details" onSubmit={event => void submit(event)}
    formActions={<><Link className="button button--secondary" to="/app/manufacturing/lots" aria-disabled={busy} onClick={event => { if (busy) event.preventDefault(); }}>Cancel</Link><button className="button button--primary" type="submit" disabled={!ready || busy || safety.kind !== "clear"}>Start production lot</button></>}
    feedback={<>{validation && <div className="decision-error" role="alert">{validation}</div>}{error && <ErrorNotice error={error} />}</>}
  >
    <p>Starting a production lot immediately consumes or allocates required stock, creates inventory movements and posts the related accounting entries.</p>
    {safety.kind === "unknown" && <div className="decision-error" role="alert"><div><strong>Production lot outcome unknown.</strong><p>We could not confirm whether the lot was started. Check the production lot register for a matching lot before trying again.</p>{unknownRequestId && <p>Request ID: {unknownRequestId}</p>}<p><Link to="/app/manufacturing/lots" target="_blank" rel="noopener noreferrer">Open production lot register</Link></p><button className="button button--secondary" type="button" onClick={acknowledge}>I checked the production lot register</button></div></div>}
    {safety.kind === "unavailable" && <div className="decision-error" role="alert">Browser storage is unavailable. Production-lot creation is blocked until storage is available and this page is reloaded.</div>}
    {catalog.status === "loading" && <p role="status">Loading work orders and stores…</p>}
    {catalog.status === "error" && <><ErrorNotice error={catalog.error} /><button className="button button--secondary" type="button" onClick={() => setAttempt(value => value + 1)}>Try again</button></>}
    {ready && <fieldset className="issue-planner__fieldset" disabled={busy || safety.kind !== "clear"}><div className="record-form__grid">
      <div className="field"><label htmlFor="production-lot-order">Work order</label><select id="production-lot-order" required value={workOrderId} onChange={event => { setWorkOrderId(event.target.value); setStoreId(""); setConfirmed(false); }}><option value="">Select work order</option>{ready.orders.map(order => <option key={order.work_order_id} value={order.work_order_id}>{order.work_order_no} — {order.item_code}</option>)}</select></div>
      <div className="field"><label htmlFor="production-lot-quantity">Quantity</label><input id="production-lot-quantity" required inputMode="decimal" pattern="[0-9]{1,10}([.][0-9]{1,3})?" value={quantity} onChange={event => setQuantity(event.target.value)} /></div>
      {detail.status === "loading" && detail.id === workOrderId && <p role="status">Loading work-order materials…</p>}
      {detail.status === "error" && detail.id === workOrderId && <ErrorNotice error={detail.error} />}
      {selectedDetail && needsStore && <div className="field"><label htmlFor="production-lot-store">Issue store</label><select id="production-lot-store" required value={storeId} onChange={event => setStoreId(event.target.value)}><option value="">Select active store</option>{stores.map(store => <option key={store.store_id} value={store.store_id}>{store.code} — {store.name}</option>)}</select></div>}
    </div><label className="grn-create__select"><input type="checkbox" checked={confirmed} onChange={event => setConfirmed(event.target.checked)} />I understand that starting this lot will consume stock and post accounting entries.</label></fieldset>}
  </FormPageTemplate>;
}

export function ProductionLotCreate({ me }: { me: Me }) {
  if (!me.modules.includes("manufacturing")) return <section className="master-state"><h2>Manufacturing unavailable</h2><p>Access to the manufacturing module is required.</p></section>;
  if (!me.permissions.includes("route.move")) return <section className="master-state"><h2>Access denied</h2><p>The route.move permission is required to start production lots.</p></section>;
  return <CreateForm me={me} />;
}
