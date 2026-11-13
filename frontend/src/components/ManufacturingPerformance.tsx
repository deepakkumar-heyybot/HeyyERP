import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { ArrowUpRight, RefreshCw } from "lucide-react";
import { Link, useNavigationType, useSearchParams } from "react-router-dom";
import {
  fetchAllOrganizationUnits, fetchManufacturingOee, fetchManufacturingWip, fetchMissingShiftEntries,
  type ManufacturingOee, type ManufacturingWip, type Me, type MissingShiftEntry, type Unit,
} from "../api-client";
import { ManufacturingTabs } from "./ManufacturingTabs";
import { RegisterPageTemplate } from "./PageTemplates";
import { RequestError } from "./RequestError";

type Load<T> = { status: "loading" } | { status: "ready"; data: T } | { status: "error"; error: Error };
const failure = (value: unknown, message: string) => value instanceof Error ? value : new Error(message);
const isoDate = (date: Date) => `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}-${String(date.getDate()).padStart(2, "0")}`;
const today = () => isoDate(new Date());
const weekAgo = () => { const date = new Date(); date.setDate(date.getDate() - 7); return isoDate(date); };

function ReportState<T>({ state, name, retry, empty, children }: { state: Load<T[]>; name: string; retry: () => void; empty: string; children: (rows: T[]) => ReactNode }) {
  if (state.status === "loading") return <div className="master-state" role="status"><span className="spinner" /><p>Loading {name.toLowerCase()}…</p></div>;
  if (state.status === "error") return <><RequestError error={state.error} /><button className="button button--secondary" type="button" onClick={retry}>Retry {name.toLowerCase()}</button></>;
  if (!state.data.length) return <div className="master-state"><h3>{empty}</h3><p>No records match the supported filters.</p></div>;
  return <>{children(state.data)}</>;
}

export function ManufacturingPerformance({ me }: { me: Me }) {
  const [params, setParams] = useSearchParams();
  const navigationType = useNavigationType();
  const pending = useRef(new URLSearchParams(params));
  useEffect(() => { if (navigationType !== "REPLACE") pending.current = new URLSearchParams(params); }, [navigationType, params]);
  const unitId = params.get("unit_id") ?? "";
  const dateFrom = params.get("date_from") ?? weekAgo();
  const dateTo = params.get("date_to") ?? today();
  const missingDate = params.get("missing_date") ?? today();
  const shift = params.get("shift_code") ?? "";
  const machineId = params.get("machine_id") ?? "";
  const stage = params.get("stage") ?? "";
  const [units, setUnits] = useState<Load<Unit[]>>({ status: "loading" });
  const [wip, setWip] = useState<Load<ManufacturingWip[]>>({ status: "loading" });
  const [oee, setOee] = useState<Load<ManufacturingOee[]>>({ status: "loading" });
  const [missing, setMissing] = useState<Load<MissingShiftEntry[]>>({ status: "loading" });
  const [wipAttempt, setWipAttempt] = useState(0), [oeeAttempt, setOeeAttempt] = useState(0), [missingAttempt, setMissingAttempt] = useState(0);

  useEffect(() => {
    const controller = new AbortController(); setUnits({ status: "loading" });
    void fetchAllOrganizationUnits(controller.signal).then(rows => { if (!controller.signal.aborted) setUnits({ status: "ready", data: rows.filter(row => me.all_plants || me.unit_ids.includes(row.unit_id)) }); })
      .catch(value => { if (!controller.signal.aborted) setUnits({ status: "error", error: failure(value, "Plant options could not be loaded.") }); });
    return () => controller.abort();
  }, [me.all_plants, me.unit_ids]);
  const validPlant = units.status === "ready" && (!unitId || units.data.some(unit => unit.unit_id === unitId));
  useEffect(() => {
    if (!validPlant) return;
    const controller = new AbortController(); setWip({ status: "loading" });
    void fetchManufacturingWip(unitId || null, controller.signal).then(data => { if (!controller.signal.aborted) setWip({ status: "ready", data }); }).catch(value => { if (!controller.signal.aborted) setWip({ status: "error", error: failure(value, "WIP unavailable") }); });
    return () => controller.abort();
  }, [unitId, validPlant, wipAttempt]);
  useEffect(() => {
    if (!validPlant) return;
    const controller = new AbortController(); setOee({ status: "loading" });
    void fetchManufacturingOee(dateFrom, dateTo, unitId || null, controller.signal).then(data => { if (!controller.signal.aborted) setOee({ status: "ready", data }); }).catch(value => { if (!controller.signal.aborted) setOee({ status: "error", error: failure(value, "OEE unavailable") }); });
    return () => controller.abort();
  }, [dateFrom, dateTo, oeeAttempt, unitId, validPlant]);
  useEffect(() => {
    if (!validPlant) return;
    const controller = new AbortController(); setMissing({ status: "loading" });
    void fetchMissingShiftEntries(missingDate, shift === "A" || shift === "B" || shift === "C" ? shift : null, controller.signal).then(data => { if (!controller.signal.aborted) setMissing({ status: "ready", data }); }).catch(value => { if (!controller.signal.aborted) setMissing({ status: "error", error: failure(value, "Missing shift entries unavailable") }); });
    return () => controller.abort();
  }, [missingAttempt, missingDate, shift, validPlant]);

  function setFilter(key: string, value: string) {
    const next = new URLSearchParams(pending.current);
    value ? next.set(key, value) : next.delete(key);
    pending.current = next;
    setParams(next);
  }
  const filteredWip = useMemo(() => wip.status === "ready" ? wip.data.filter(row => !stage || row.stage_code === stage) : [], [stage, wip]);
  const filteredOee = useMemo(() => oee.status === "ready" ? oee.data.filter(row => !machineId || row.machine_id === machineId) : [], [machineId, oee]);
  const filteredMissing = useMemo(() => missing.status === "ready" ? missing.data.filter(row => (!unitId || row.unit_id === unitId) && (!machineId || row.machine_id === machineId)) : [], [machineId, missing, unitId]);
  const stages = wip.status === "ready" ? [...new Map(wip.data.map(row => [row.stage_code, row.stage_name])).entries()] : [];
  const machines = oee.status === "ready" ? oee.data : [];
  const lots = filteredWip.reduce((sum, row) => sum + row.lots, 0);
  const invalidPlant = units.status === "ready" && Boolean(unitId) && !validPlant;
  if (!me.modules.includes("manufacturing")) return <section className="master-state"><h2>Access denied</h2><p>The manufacturing module is required.</p></section>;

  return <RegisterPageTemplate className="manufacturing-performance" title="WIP & OEE" navigation={<ManufacturingTabs active="performance" />} description="Current work in progress, machine availability and quality, and missing shift-entry alerts."
    filters={<>
      <div className="field"><label htmlFor="performance-plant">Plant filter</label><select id="performance-plant" value={unitId} onChange={event => setFilter("unit_id", event.target.value)}><option value="">All authorized plants</option>{units.status === "ready" && units.data.map(unit => <option key={unit.unit_id} value={unit.unit_id}>{unit.code} — {unit.name}</option>)}</select></div>
      <div className="field"><label htmlFor="performance-from">From date</label><input id="performance-from" type="date" value={dateFrom} onChange={event => setFilter("date_from", event.target.value)} /></div>
      <div className="field"><label htmlFor="performance-to">To date</label><input id="performance-to" type="date" value={dateTo} onChange={event => setFilter("date_to", event.target.value)} /></div>
      <div className="field"><label htmlFor="performance-stage">Stage filter</label><select id="performance-stage" value={stage} onChange={event => setFilter("stage", event.target.value)}><option value="">All stages</option>{stages.map(([code, name]) => <option key={code} value={code}>{code} — {name}</option>)}</select></div>
      <div className="field"><label htmlFor="performance-machine">Machine filter</label><select id="performance-machine" value={machineId} onChange={event => setFilter("machine_id", event.target.value)}><option value="">All machines</option>{machines.map(machine => <option key={machine.machine_id} value={machine.machine_id}>{machine.machine_code}</option>)}</select></div>
      <div className="field"><label htmlFor="performance-missing-date">Missing-entry date</label><input id="performance-missing-date" type="date" value={missingDate} onChange={event => setFilter("missing_date", event.target.value)} /></div>
      <div className="field"><label htmlFor="performance-shift">Missing shift</label><select id="performance-shift" value={shift} onChange={event => setFilter("shift_code", event.target.value)}><option value="">All shifts</option><option>A</option><option>B</option><option>C</option></select></div>
    </>}
  >
    {units.status === "loading" && <p role="status">Loading plant scope…</p>}
    {units.status === "error" && <RequestError error={units.error} />}
    {invalidPlant && <div className="decision-error" role="alert">Invalid plant: this plant is not available in your authorized scope.</div>}
    {validPlant && <>
      <div className="performance-kpis" aria-label="Manufacturing performance summary"><article><span>Total WIP lots</span><strong>{lots}</strong></article><article><span>WIP groups</span><strong>{filteredWip.length}</strong></article><article><span>Machines reported</span><strong>{filteredOee.length}</strong></article><article><span>Missing entries</span><strong>{filteredMissing.length}</strong></article></div>
      <section className="record-panel"><div className="record-panel__heading"><div><h3>Work in progress</h3><p>Current positive-quantity production groups.</p></div><button className="icon-button" type="button" aria-label="Retry WIP" onClick={() => setWipAttempt(value => value + 1)}><RefreshCw size={16} /></button></div>
        <ReportState state={wip.status === "ready" ? { status: "ready", data: filteredWip } : wip} name="WIP" retry={() => setWipAttempt(value => value + 1)} empty="No WIP found">{rows => <div className="master-table-wrap" role="region" aria-label="Scrollable WIP results" tabIndex={0} style={{ display: "block" }}><table className="master-table" aria-label="WIP results"><thead><tr><th>Route</th><th>Stage</th><th>Item</th><th>Lots</th><th>Quantity</th><th>Context</th></tr></thead><tbody>{rows.map(row => <tr key={`${row.route_code}:${row.stage_code}:${row.item_code}`}><td>{row.route_code}</td><td>{row.stage_name}<br /><small>{row.stage_code}</small></td><td>{row.item_code}</td><td>{row.lots}</td><td>{row.quantity}</td><td><Link className="icon-link" aria-label={`View lots at ${row.stage_name}`} to={`/app/manufacturing/lots?${new URLSearchParams({ stage: row.stage_code, ...(unitId ? { unit_id: unitId } : {}) })}`}><ArrowUpRight size={16} /></Link></td></tr>)}</tbody></table></div>}</ReportState>
      </section>
      <section className="record-panel"><div className="record-panel__heading"><div><h3>Machine performance</h3><p>Backend aggregates for the selected inclusive date range.</p></div></div>
        <ReportState state={oee.status === "ready" ? { status: "ready", data: filteredOee } : oee} name="Machine performance" retry={() => setOeeAttempt(value => value + 1)} empty="No machine performance found">{rows => <div className="master-table-wrap" role="region" aria-label="Scrollable machine performance" tabIndex={0} style={{ display: "block" }}><table className="master-table" aria-label="Machine performance"><thead><tr><th>Machine</th><th>Shifts</th><th>Planned</th><th>Downtime</th><th>Availability</th><th>Good</th><th>Reject</th><th>Quality</th><th>Performance</th><th>Context</th></tr></thead><tbody>{rows.map(row => <tr key={row.machine_id}><td>{row.machine_code}</td><td>{row.shifts}</td><td>{row.planned_minutes} min</td><td>{row.downtime_minutes} min</td><td><span className="ratio-bar" style={{ "--ratio": `${Math.min(100, Number(row.availability) * 100)}%` } as React.CSSProperties}>{row.availability}</span></td><td>{row.good_qty}</td><td>{row.reject_qty}</td><td>{row.quality ?? "Not available"}</td><td>{row.performance ?? "Not available"}</td><td><Link className="icon-link" aria-label={`View equipment ${row.machine_code}`} to={`/app/manufacturing/equipment?${new URLSearchParams({ q: row.machine_code, ...(unitId ? { unit_id: unitId } : {}) })}`}><ArrowUpRight size={16} /></Link></td></tr>)}</tbody></table></div>}</ReportState>
      </section>
      <section className="record-panel"><div className="record-panel__heading"><div><h3>Missing shift entries</h3><p>Active machines with recent operation but no entry for the selected date and shift.</p></div></div>
        <ReportState state={missing.status === "ready" ? { status: "ready", data: filteredMissing } : missing} name="Missing shift entries" retry={() => setMissingAttempt(value => value + 1)} empty="No missing shift entries">{rows => <div className="master-table-wrap" role="region" aria-label="Scrollable missing shift entries" tabIndex={0} style={{ display: "block" }}><table className="master-table" aria-label="Missing shift entries"><thead><tr><th>Machine</th><th>Date</th><th>Shift</th><th>Action</th></tr></thead><tbody>{rows.map(row => <tr key={`${row.unit_id}:${row.machine_id}:${row.shift_code}`}><td>{row.machine_code}</td><td>{row.work_date}</td><td>{row.shift_code}</td><td><Link className="button button--secondary" aria-label={`Enter shift for ${row.machine_code}`} to={`/app/manufacturing/shift-entries/new?${new URLSearchParams({ machine_id: row.machine_id, work_date: row.work_date, shift_code: row.shift_code })}`}>Enter shift</Link></td></tr>)}</tbody></table></div>}</ReportState>
      </section>
    </>}
  </RegisterPageTemplate>;
}
