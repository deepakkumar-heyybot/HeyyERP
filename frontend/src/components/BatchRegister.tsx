import { useEffect, useRef, useState } from "react";
import { Boxes, ChevronLeft, ChevronRight, Plus, RefreshCw } from "lucide-react";
import { Link, useNavigate, useNavigationType, useSearchParams } from "react-router-dom";
import {
  fetchAllCompoundBatches, fetchAllCompounds, listCompoundBatchesPage,
  type Compound, type CompoundBatchSummary, type Me,
} from "../api-client";
import { useActivePlant } from "../plant-context";
import { ManufacturingTabs } from "./ManufacturingTabs";
import { RegisterPageTemplate } from "./PageTemplates";
import { RequestError } from "./RequestError";

const PAGE_SIZE = 20;
const STATUSES = ["WEIGHED", "MIXING", "MATURING", "AWAITING_QA", "READY", "CONSUMED", "EXPIRED", "REJECTED", "ON_HOLD"] as const;
type ListState = { status: "loading"; key: string } | { status: "ready"; key: string; items: CompoundBatchSummary[]; total: number } | { status: "error"; key: string; error: Error };
type CompoundsState = { status: "loading" } | { status: "ready"; items: Compound[] } | { status: "error"; error: Error };

function statusLabel(value: string) {
  return value.toLowerCase().replaceAll("_", " ").replace(/^./, letter => letter.toUpperCase());
}
function StatusBadge({ status }: { status: string }) {
  const tone = status === "READY" ? "approved" : status === "REJECTED" || status === "EXPIRED" ? "cancelled" : "pending";
  return <span className={`status-badge status-badge--${tone}`}>{statusLabel(status)}</span>;
}
function Timestamp({ value }: { value: string | null }) {
  return value ? <time dateTime={value} style={{ overflowWrap: "anywhere" }}>{value}</time> : <>Not recorded</>;
}

function BatchList({ me }: { me: Me }) {
  const navigate = useNavigate();
  const { activePlantId, activeUnit } = useActivePlant();
  const [params, setParams] = useSearchParams();
  const navigationType = useNavigationType();
  const pendingParams = useRef(new URLSearchParams(params));
  const previousPlant = useRef(activePlantId);
  useEffect(() => { if (navigationType !== "REPLACE") pendingParams.current = new URLSearchParams(params); }, [navigationType, params]);
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
  const statusParam = params.get("status") ?? "";
  const status = STATUSES.some(value => value === statusParam) ? statusParam : null;
  const compoundId = params.get("compound_id") || null;
  const pageValue = Number(params.get("page") ?? "1");
  const currentPage = Number.isSafeInteger(pageValue) && pageValue > 0 ? pageValue : 1;
  const singlePlantScope = !me.all_plants && me.unit_ids.length === 1 && me.unit_ids[0] === activePlantId;
  const filterPlant = Boolean(activePlantId && activePlantId !== "all" && !singlePlantScope);
  const requestKey = JSON.stringify([activePlantId, status, compoundId, currentPage]);
  const [state, setState] = useState<ListState>({ status: "loading", key: "" });
  const [compounds, setCompounds] = useState<CompoundsState>({ status: "loading" });
  const [attempt, setAttempt] = useState(0);
  const [compoundAttempt, setCompoundAttempt] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    setCompounds({ status: "loading" });
    void fetchAllCompounds(null, null, controller.signal)
      .then(items => { if (!controller.signal.aborted) setCompounds({ status: "ready", items }); })
      .catch(problem => { if (!controller.signal.aborted) setCompounds({ status: "error", error: problem instanceof Error ? problem : new Error("Compounds could not be loaded.") }); });
    return () => controller.abort();
  }, [compoundAttempt]);

  useEffect(() => {
    const controller = new AbortController();
    if (!activePlantId) return () => controller.abort();
    setState({ status: "loading", key: requestKey });
    void (async () => {
      try {
        const offset = (currentPage - 1) * PAGE_SIZE;
        if (filterPlant) {
          const all = await fetchAllCompoundBatches(status, compoundId, controller.signal);
          const matching = all.filter(batch => batch.unit_id === activePlantId);
          if (!controller.signal.aborted) setState({ status: "ready", key: requestKey, items: matching.slice(offset, offset + PAGE_SIZE), total: matching.length });
        } else {
          const result = await listCompoundBatchesPage({ status, compoundId, limit: PAGE_SIZE, offset }, controller.signal);
          if (!controller.signal.aborted) setState({ status: "ready", key: requestKey, items: result.items, total: result.total });
        }
      } catch (problem) {
        if (!controller.signal.aborted) setState({ status: "error", key: requestKey, error: problem instanceof Error ? problem : new Error("Batches could not be loaded.") });
      }
    })();
    return () => controller.abort();
  }, [activePlantId, attempt, compoundId, currentPage, filterPlant, requestKey, status]);

  function setFilter(name: string, value: string, resetPage = true) {
    const next = new URLSearchParams(pendingParams.current);
    if (value) next.set(name, value); else next.delete(name);
    if (resetPage) next.delete("page");
    pendingParams.current = next;
    setParams(next, { replace: true });
  }
  const visibleState = state.key === requestKey ? state : { status: "loading" as const, key: requestKey };
  const items = visibleState.status === "ready" ? visibleState.items : [];
  const total = visibleState.status === "ready" ? visibleState.total : 0;
  const first = total ? (currentPage - 1) * PAGE_SIZE + 1 : 0;
  const last = Math.min(currentPage * PAGE_SIZE, total);
  const maxPage = Math.max(1, Math.ceil(total / PAGE_SIZE));

  return <RegisterPageTemplate
    title="Batches"
    navigation={<ManufacturingTabs active="batches" />}
    description="Compound batches, quantities and current backend status."

    filters={<>
      <div className="field"><label htmlFor="batch-status">Status filter</label><select id="batch-status" value={status ?? ""} onChange={event => setFilter("status", event.target.value)}><option value="">All statuses</option>{STATUSES.map(value => <option key={value} value={value}>{statusLabel(value)}</option>)}</select></div>
      <div className="field"><label htmlFor="batch-compound">Compound filter</label><select id="batch-compound" value={compoundId ?? ""} disabled={compounds.status !== "ready"} onChange={event => setFilter("compound_id", event.target.value)}><option value="">All compounds</option>{compoundId && compounds.status === "ready" && !compounds.items.some(item => item.compound_id === compoundId) && <option value={compoundId}>Unknown compound</option>}{compounds.status === "ready" && compounds.items.map(item => <option key={item.compound_id} value={item.compound_id}>{item.code} — {item.name}</option>)}</select></div>
      <button className="button button--secondary" type="button" disabled={visibleState.status === "loading"} onClick={() => setAttempt(value => value + 1)}><RefreshCw size={16} />Refresh</button>
    </>}
  >
    {compounds.status === "error" && <div><RequestError error={compounds.error} /><button className="button button--secondary" type="button" onClick={() => setCompoundAttempt(value => value + 1)}>Retry compounds</button></div>}
    {!activePlantId ? <div className="master-state"><p>Select a plant to view batches.</p></div> : visibleState.status === "loading" ? <div className="master-state" role="status"><span className="spinner" /><p>Loading batches…</p></div>
      : visibleState.status === "error" ? <div><RequestError error={visibleState.error} /><button className="button button--secondary" type="button" onClick={() => setAttempt(value => value + 1)}>Try again</button></div>
      : items.length === 0 ? <div className="master-state"><Boxes size={28} /><h3>No batches found</h3><p>Change the filters or select another plant.</p>{currentPage > 1 && <button className="button button--secondary" type="button" onClick={() => setFilter("page", "", false)}>Return to first page</button>}</div>
      : <>
        <p>Showing batches for {activePlantId === "all" ? "all authorized plants" : activeUnit?.name ?? "the selected plant"}.</p>
        <p className="compound-scroll-hint">Scroll the table horizontally to view all batch fields.</p>
        <div className="master-table-wrap" role="region" aria-label="Scrollable batch results" tabIndex={0}><table className="master-table" aria-label="Batch results" style={{ minWidth: 1050, tableLayout: "auto" }}>
          <thead><tr>{["Batch", "Compound", "Weighed at", "Mixed at", "Total (kg)", "Remaining (kg)", "Status"].map(label => <th key={label} style={{ width: "auto", whiteSpace: "nowrap" }}>{label}</th>)}</tr></thead>
          <tbody>{items.map(item => <tr key={item.batch_id}><td><Link to={`/app/manufacturing/batches/${encodeURIComponent(item.batch_id)}`} aria-label={`Open batch ${item.batch_no}`}><strong>{item.batch_no}</strong></Link></td><td>{item.compound_code} — {item.compound_name}</td><td><Timestamp value={item.weighed_at} /></td><td><Timestamp value={item.mixed_at} /></td><td>{item.total_kg}</td><td>{item.remaining_kg}</td><td><StatusBadge status={item.status} /></td></tr>)}</tbody>
        </table></div>
        <ul className="master-cards" aria-label="Batch results">{items.map(item => <li key={item.batch_id} style={{ overflowWrap: "anywhere" }}><div><span>{item.batch_no}</span><StatusBadge status={item.status} /></div><h3>{item.compound_code} — {item.compound_name}</h3><dl><div><dt>Weighed at</dt><dd><Timestamp value={item.weighed_at} /></dd></div><div><dt>Mixed at</dt><dd><Timestamp value={item.mixed_at} /></dd></div><div><dt>Total (kg)</dt><dd>{item.total_kg}</dd></div><div><dt>Remaining (kg)</dt><dd>{item.remaining_kg}</dd></div></dl><Link className="button button--secondary" to={`/app/manufacturing/batches/${encodeURIComponent(item.batch_id)}`}>Open batch {item.batch_no}</Link></li>)}</ul>
        <div className="reference-pagination"><p className="result-summary" aria-live="polite">Showing {first}–{last} of {total} records</p>{total > PAGE_SIZE && <div><button className="button button--secondary" type="button" aria-label="Previous page" disabled={currentPage <= 1} onClick={() => setFilter("page", String(currentPage - 1), false)}><ChevronLeft size={16} /></button><span>Page {currentPage} of {maxPage}</span><button className="button button--secondary" type="button" aria-label="Next page" disabled={currentPage >= maxPage} onClick={() => setFilter("page", String(currentPage + 1), false)}><ChevronRight size={16} /></button></div>}</div>
      </>}
  </RegisterPageTemplate>;
}

export function BatchRegister({ me }: { me: Me }) {
  if (!me.modules.includes("manufacturing")) return <section className="master-state"><h2>Manufacturing unavailable</h2><p>Access to the manufacturing module is required.</p></section>;
  return <BatchList me={me} />;
}
