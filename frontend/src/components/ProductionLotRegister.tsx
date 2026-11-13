import { useEffect, useRef, useState } from "react";
import { Boxes, ChevronLeft, ChevronRight, Plus, RefreshCw } from "lucide-react";
import { Link, useNavigationType, useSearchParams } from "react-router-dom";
import {
  fetchAllItems, fetchAllOrganizationUnits, fetchAllWorkOrders, listProductionLotsPage,
  listProductionRoutes, type Item, type Me, type ProductionLotSummary, type Unit,
  type WorkOrderSummary,
} from "../api-client";
import { useActivePlant } from "../plant-context";
import { ManufacturingTabs } from "./ManufacturingTabs";
import { RegisterPageTemplate } from "./PageTemplates";
import { RequestError } from "./RequestError";

const PAGE_SIZE = 20;
const STATUSES = ["IN_PROCESS", "READY", "HOLD", "COMPLETED", "CONSUMED", "SCRAPPED"] as const;
type OptionsState<T> = { status: "loading" } | { status: "ready"; items: T[] } | { status: "error"; error: Error };
type StageOption = { code: string; name: string };
type ListState =
  | { status: "loading"; key: string }
  | { status: "ready"; key: string; items: ProductionLotSummary[]; total: number }
  | { status: "error"; key: string; error: Error };

const asError = (value: unknown, fallback: string) => value instanceof Error ? value : new Error(fallback);
const statusLabel = (value: string) => value.toLowerCase().replaceAll("_", " ").replace(/^./, letter => letter.toUpperCase());

function StatusBadge({ status }: { status: string }) {
  const tone = status === "READY" || status === "COMPLETED" ? "approved" : status === "SCRAPPED" ? "cancelled" : "pending";
  return <span className={`status-badge status-badge--${tone}`}>{statusLabel(status)}</span>;
}

// Plant and item are not lot-list query parameters. Load every server-filtered page
// before applying either client filter, and fail closed if a page is incomplete.
async function loadAllMatchingLots(workOrderId: string | null, status: string | null, stage: string | null, signal: AbortSignal) {
  const items: ProductionLotSummary[] = [];
  const seen = new Set<string>();
  let offset = 0;
  let total: number | null = null;
  while (total === null || offset < total) {
    const page = await listProductionLotsPage({ workOrderId, status, stage, limit: 200, offset }, signal);
    if (page.offset !== offset || !Number.isSafeInteger(page.total) || page.total < 0 ||
        (total !== null && page.total !== total) ||
        (!page.items.length && offset < page.total) || page.items.length > 200 ||
        offset + page.items.length > page.total) {
      throw new Error("Production lot results changed or were incomplete. Refresh before filtering.");
    }
    for (const item of page.items) {
      if (seen.has(item.prod_lot_id)) throw new Error("Production lot results changed while loading. Refresh before filtering.");
      seen.add(item.prod_lot_id);
      items.push(item);
    }
    total = page.total;
    offset += page.items.length;
  }
  return items;
}

function ProductionLotList({ me }: { me: Me }) {
  const { activePlantId, activeUnit } = useActivePlant();
  const [params, setParams] = useSearchParams();
  const navigationType = useNavigationType();
  const pendingParams = useRef(new URLSearchParams(params));
  const previousPlant = useRef(activePlantId);
  useEffect(() => {
    if (navigationType !== "REPLACE") pendingParams.current = new URLSearchParams(params);
  }, [navigationType, params]);
  useEffect(() => {
    if (previousPlant.current !== activePlantId) {
      previousPlant.current = activePlantId;
      if (pendingParams.current.has("page")) {
        const next = new URLSearchParams(pendingParams.current);
        next.delete("page");
        pendingParams.current = next;
        setParams(next, { replace: true });
      }
    }
  }, [activePlantId, setParams]);

  const unitParam = params.get("unit_id") ?? "";
  const itemId = params.get("item_id") || null;
  const workOrderId = params.get("work_order_id") || null;
  const statusParam = params.get("status") ?? "";
  // The API accepts a string status and matches it exactly after uppercasing.
  // Preserve an unknown URL value as a narrowing filter instead of showing all lots.
  const status = statusParam || null;
  const stage = params.get("stage") || null;
  const pageValue = Number(params.get("page") ?? "1");
  const currentPage = Number.isSafeInteger(pageValue) && pageValue > 0 &&
    pageValue <= Math.floor(Number.MAX_SAFE_INTEGER / PAGE_SIZE) ? pageValue : 1;
  const scopeKey = me.unit_ids.join(",");

  const [units, setUnits] = useState<OptionsState<Unit>>({ status: "loading" });
  const [items, setItems] = useState<OptionsState<Item>>({ status: "loading" });
  const [orders, setOrders] = useState<OptionsState<WorkOrderSummary>>({ status: "loading" });
  const [stages, setStages] = useState<OptionsState<StageOption>>({ status: "loading" });
  const [unitAttempt, setUnitAttempt] = useState(0);
  const [itemAttempt, setItemAttempt] = useState(0);
  const [orderAttempt, setOrderAttempt] = useState(0);
  const [stageAttempt, setStageAttempt] = useState(0);
  const [attempt, setAttempt] = useState(0);
  const [state, setState] = useState<ListState>({ status: "loading", key: "" });

  useEffect(() => {
    const controller = new AbortController();
    setUnits({ status: "loading" });
    void fetchAllOrganizationUnits(controller.signal)
      .then(found => { if (!controller.signal.aborted) setUnits({ status: "ready", items: found.filter(unit => me.all_plants || me.unit_ids.includes(unit.unit_id)) }); })
      .catch(value => { if (!controller.signal.aborted) setUnits({ status: "error", error: asError(value, "Plants could not be loaded.") }); });
    return () => controller.abort();
  }, [me.all_plants, scopeKey, unitAttempt]);

  useEffect(() => {
    const controller = new AbortController();
    setItems({ status: "loading" });
    void fetchAllItems(controller.signal)
      .then(found => { if (!controller.signal.aborted) setItems({ status: "ready", items: found.filter(item => item.item_type === "FG" || item.item_type === "WIP") }); })
      .catch(value => { if (!controller.signal.aborted) setItems({ status: "error", error: asError(value, "Items could not be loaded.") }); });
    return () => controller.abort();
  }, [itemAttempt]);

  useEffect(() => {
    const controller = new AbortController();
    setOrders({ status: "loading" });
    void fetchAllWorkOrders(controller.signal)
      .then(found => { if (!controller.signal.aborted) setOrders({ status: "ready", items: found.filter(order => me.all_plants || me.unit_ids.includes(order.unit_id)) }); })
      .catch(value => { if (!controller.signal.aborted) setOrders({ status: "error", error: asError(value, "Work orders could not be loaded.") }); });
    return () => controller.abort();
  }, [me.all_plants, scopeKey, orderAttempt]);

  useEffect(() => {
    const controller = new AbortController();
    setStages({ status: "loading" });
    void Promise.all([listProductionRoutes(true, controller.signal), listProductionRoutes(false, controller.signal)])
      .then(([active, inactive]) => {
        if (controller.signal.aborted) return;
        const discovered = new Map<string, string>();
        for (const route of [...active, ...inactive]) for (const routeStage of route.stages) {
          if (!discovered.has(routeStage.stage_code)) discovered.set(routeStage.stage_code, routeStage.stage_name);
        }
        setStages({ status: "ready", items: [...discovered].map(([code, name]) => ({ code, name })).sort((a, b) => a.code.localeCompare(b.code)) });
      })
      .catch(value => { if (!controller.signal.aborted) setStages({ status: "error", error: asError(value, "Route stages could not be loaded.") }); });
    return () => controller.abort();
  }, [stageAttempt]);

  const unitPending = Boolean(unitParam && units.status === "loading");
  const unitUnavailable = Boolean(unitParam && units.status === "error");
  const invalidUnit = Boolean(unitParam && units.status === "ready" && !units.items.some(unit => unit.unit_id === unitParam));
  const conflictingUnit = Boolean(unitParam && activePlantId && activePlantId !== "all" && unitParam !== activePlantId);
  const effectivePlant = unitParam || (activePlantId && activePlantId !== "all" ? activePlantId : null);
  const singlePlantScope = !me.all_plants && me.unit_ids.length === 1 && me.unit_ids[0] === effectivePlant;
  const filterPlant = Boolean(effectivePlant && !singlePlantScope);
  const clientFilter = filterPlant || Boolean(itemId);
  const requestKey = JSON.stringify([workOrderId, status, stage, effectivePlant, itemId, currentPage, scopeKey, me.all_plants]);

  useEffect(() => {
    const controller = new AbortController();
    if (unitPending || unitUnavailable || invalidUnit || conflictingUnit || !activePlantId) return () => controller.abort();
    setState({ status: "loading", key: requestKey });
    void (async () => {
      try {
        const offset = (currentPage - 1) * PAGE_SIZE;
        if (clientFilter) {
          const all = await loadAllMatchingLots(workOrderId, status, stage, controller.signal);
          const matching = all.filter(lot => (me.all_plants || me.unit_ids.includes(lot.unit_id))
            && (!effectivePlant || lot.unit_id === effectivePlant)
            && (!itemId || lot.item_id === itemId));
          if (!controller.signal.aborted) setState({ status: "ready", key: requestKey, items: matching.slice(offset, offset + PAGE_SIZE), total: matching.length });
        } else {
          const result = await listProductionLotsPage({ workOrderId, status, stage, limit: PAGE_SIZE, offset }, controller.signal);
          if (!controller.signal.aborted) setState({ status: "ready", key: requestKey, items: result.items, total: result.total });
        }
      } catch (value) {
        if (!controller.signal.aborted) setState({ status: "error", key: requestKey, error: asError(value, "Production lots could not be loaded.") });
      }
    })();
    return () => controller.abort();
  }, [activePlantId, attempt, clientFilter, conflictingUnit, currentPage, effectivePlant, invalidUnit,
    itemId, me.all_plants, requestKey, scopeKey, stage, status, unitPending, unitUnavailable, workOrderId]);

  function setFilter(name: string, value: string, resetPage = true) {
    const next = new URLSearchParams(pendingParams.current);
    if (value) next.set(name, value); else next.delete(name);
    if (resetPage) next.delete("page");
    pendingParams.current = next;
    setParams(next, { replace: true });
  }

  const visibleState = state.key === requestKey && !unitPending && !unitUnavailable && !invalidUnit && !conflictingUnit
    ? state : { status: "loading" as const, key: requestKey };
  const lots = visibleState.status === "ready" ? visibleState.items : [];
  const total = visibleState.status === "ready" ? visibleState.total : 0;
  const maxPage = Math.max(1, Math.ceil(total / PAGE_SIZE));
  const first = total ? (currentPage - 1) * PAGE_SIZE + 1 : 0;
  const last = Math.min(currentPage * PAGE_SIZE, total);
  const unitName = (id: string) => units.status === "ready" ? units.items.find(unit => unit.unit_id === id)?.name ?? id : id;
  const plantOptions = units.status === "ready" ? units.items.filter(unit => activePlantId === "all" || unit.unit_id === activePlantId) : [];
  const workOrderOptions = orders.status === "ready" ? orders.items.filter(order => !effectivePlant || order.unit_id === effectivePlant) : [];

  return <RegisterPageTemplate
    title="Production lots"
    description="Current production lots, work orders, route stages and quantities."
    navigation={<ManufacturingTabs active="lots" />}
    actions={me.permissions.includes("route.move") && <Link className="button button--primary" to="/app/manufacturing/lots/new"><Plus size={17} />New production lot</Link>}
    filters={<>
      <div className="field"><label htmlFor="production-lot-plant">Plant filter</label><select id="production-lot-plant" value={invalidUnit || conflictingUnit ? "" : unitParam} disabled={units.status !== "ready"} onChange={event => setFilter("unit_id", event.target.value)}><option value="">{activePlantId === "all" ? "All authorized plants" : "Current plant"}</option>{plantOptions.map(unit => <option key={unit.unit_id} value={unit.unit_id}>{unit.code} — {unit.name}{unit.active ? "" : " (inactive)"}</option>)}</select></div>
      <div className="field"><label htmlFor="production-lot-item">Item filter</label><select id="production-lot-item" value={itemId ?? ""} disabled={items.status !== "ready"} onChange={event => setFilter("item_id", event.target.value)}><option value="">All items</option>{itemId && items.status === "ready" && !items.items.some(item => item.item_id === itemId) && <option value={itemId}>{itemId}</option>}{items.status === "ready" && items.items.map(item => <option key={item.item_id} value={item.item_id}>{item.code} — {item.name}</option>)}</select></div>
      <div className="field"><label htmlFor="production-lot-order">Work order filter</label><select id="production-lot-order" value={workOrderId ?? ""} disabled={orders.status !== "ready"} onChange={event => setFilter("work_order_id", event.target.value)}><option value="">All work orders</option>{workOrderId && orders.status === "ready" && !workOrderOptions.some(order => order.work_order_id === workOrderId) && <option value={workOrderId}>{workOrderId}</option>}{workOrderOptions.map(order => <option key={order.work_order_id} value={order.work_order_id}>{order.work_order_no} — {order.item_code}</option>)}</select></div>
      <div className="field"><label htmlFor="production-lot-status">Status filter</label><select id="production-lot-status" value={statusParam} onChange={event => setFilter("status", event.target.value)}><option value="">All statuses</option>{statusParam && !STATUSES.some(value => value === statusParam) && <option value={statusParam}>{statusParam} (unknown)</option>}{STATUSES.map(value => <option key={value} value={value}>{statusLabel(value)}</option>)}</select></div>
      <div className="field"><label htmlFor="production-lot-stage">Stage filter</label><select id="production-lot-stage" value={stage ?? ""} disabled={stages.status !== "ready"} onChange={event => setFilter("stage", event.target.value)}><option value="">All route stages</option>{stage && stages.status === "ready" && !stages.items.some(option => option.code === stage) && <option value={stage}>{stage} (not in loaded routes)</option>}{stages.status === "ready" && stages.items.map(option => <option key={option.code} value={option.code}>{option.code} — {option.name}</option>)}</select></div>
      <button className="button button--secondary" type="button" disabled={visibleState.status === "loading" && !invalidUnit && !conflictingUnit} onClick={() => setAttempt(value => value + 1)}><RefreshCw size={16} />Refresh</button>
    </>}
  >
    {units.status === "error" && <div><RequestError error={units.error} /><button className="button button--secondary" type="button" onClick={() => setUnitAttempt(value => value + 1)}>Retry plants</button></div>}
    {items.status === "error" && <div><RequestError error={items.error} /><button className="button button--secondary" type="button" onClick={() => setItemAttempt(value => value + 1)}>Retry items</button></div>}
    {orders.status === "error" && <div><RequestError error={orders.error} /><button className="button button--secondary" type="button" onClick={() => setOrderAttempt(value => value + 1)}>Retry work orders</button></div>}
    {stages.status === "error" && <div><RequestError error={stages.error} /><button className="button button--secondary" type="button" onClick={() => setStageAttempt(value => value + 1)}>Retry route stages</button></div>}
    {(invalidUnit || conflictingUnit) && <div className="master-state" role="alert"><p>The selected plant is not available in your current plant scope.</p><button className="button button--secondary" type="button" onClick={() => setFilter("unit_id", "")}>Show current plant</button></div>}
    {!(invalidUnit || conflictingUnit || unitUnavailable) && (unitPending || visibleState.status === "loading") && <div className="master-state" role="status"><span className="spinner" /><p>Loading production lots…</p></div>}
    {!(invalidUnit || conflictingUnit || unitUnavailable) && !unitPending && visibleState.status === "error" && <div><RequestError error={visibleState.error} /><button className="button button--secondary" type="button" onClick={() => setAttempt(value => value + 1)}>Try again</button></div>}
    {!(invalidUnit || conflictingUnit || unitUnavailable) && !unitPending && visibleState.status === "ready" && lots.length === 0 && <div className="master-state"><Boxes size={28} /><h3>No production lots found</h3><p>Change the filters or select another plant.</p>{currentPage > 1 && <button className="button button--secondary" type="button" onClick={() => setFilter("page", "", false)}>Return to first page</button>}</div>}
    {!(invalidUnit || conflictingUnit || unitUnavailable) && !unitPending && visibleState.status === "ready" && lots.length > 0 && <>
      <p>Showing lots for {effectivePlant ? activeUnit?.unit_id === effectivePlant ? activeUnit.name : unitName(effectivePlant) : "all authorized plants"}.</p>
      <p className="compound-scroll-hint">Scroll the table horizontally to view all production lot fields.</p>
      <div className="master-table-wrap" role="region" aria-label="Scrollable production lot results" tabIndex={0}>
        <table className="master-table" aria-label="Production lot results" style={{ minWidth: 1080, tableLayout: "auto" }}>
          <thead><tr>{["Production lot", "Work order", "Plant", "Item", "Route", "Stage", "Quantity", "Status", "Created at"].map(label => <th key={label} style={{ width: "auto", whiteSpace: "nowrap" }}>{label}</th>)}</tr></thead>
          <tbody>{lots.map(lot => <tr key={lot.prod_lot_id}>
            <td><Link to={`/app/manufacturing/lots/${encodeURIComponent(lot.prod_lot_id)}`}><strong>{lot.lot_no}</strong></Link></td>
            <td><Link to={`/app/manufacturing/work-orders/${encodeURIComponent(lot.work_order_id)}`} aria-label={`Open work order ${lot.work_order_no}`}>{lot.work_order_no}</Link></td>
            <td>{unitName(lot.unit_id)}</td><td>{lot.item_code}</td><td>{lot.route_code}</td>
            <td>{lot.stage_code} — {lot.stage_name}</td><td>{lot.quantity}</td><td><StatusBadge status={lot.status} /></td>
            <td><time dateTime={lot.created_at}>{lot.created_at}</time></td>
          </tr>)}</tbody>
        </table>
      </div>
      <ul className="master-cards" aria-label="Production lot results">{lots.map(lot => <li key={lot.prod_lot_id} style={{ overflowWrap: "anywhere" }}>
        <div><span>{lot.lot_no}</span><StatusBadge status={lot.status} /></div><h3>{lot.item_code}</h3>
        <dl><div><dt>Work order</dt><dd>{lot.work_order_no}</dd></div><div><dt>Plant</dt><dd>{unitName(lot.unit_id)}</dd></div><div><dt>Route</dt><dd>{lot.route_code}</dd></div><div><dt>Stage</dt><dd>{lot.stage_code} — {lot.stage_name}</dd></div><div><dt>Quantity</dt><dd>{lot.quantity}</dd></div><div><dt>Created at</dt><dd><time dateTime={lot.created_at}>{lot.created_at}</time></dd></div></dl>
        <Link className="button button--secondary" to={`/app/manufacturing/lots/${encodeURIComponent(lot.prod_lot_id)}`}>Open production lot {lot.lot_no}</Link>
      </li>)}</ul>
      <div className="reference-pagination"><p className="result-summary" aria-live="polite">Showing {first}–{last} of {total} records</p>{total > PAGE_SIZE && <div><button className="button button--secondary" type="button" aria-label="Previous page" disabled={currentPage <= 1} onClick={() => setFilter("page", String(currentPage - 1), false)}><ChevronLeft size={16} /></button><span>Page {currentPage} of {maxPage}</span><button className="button button--secondary" type="button" aria-label="Next page" disabled={currentPage >= maxPage} onClick={() => setFilter("page", String(currentPage + 1), false)}><ChevronRight size={16} /></button></div>}</div>
    </>}
  </RegisterPageTemplate>;
}

export function ProductionLotRegister({ me }: { me: Me }) {
  if (!me.modules.includes("manufacturing")) return <section className="master-state"><h2>Manufacturing unavailable</h2><p>Access to the manufacturing module is required.</p></section>;
  return <ProductionLotList me={me} />;
}
