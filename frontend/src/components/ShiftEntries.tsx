import { useEffect, useMemo, useRef, useState, type FormEvent } from "react";
import { ArrowLeft, Plus, Trash2 } from "lucide-react";
import { Link, useLocation, useNavigationType, useSearchParams } from "react-router-dom";
import {
  ApiError, createShiftEntry, fetchAllMachines, fetchAllMoulds, fetchAllOrganizationUnits,
  fetchAllShiftEntries, fetchAllWorkOrders, type Machine, type Me, type Mould,
  type ShiftEntry, type ShiftEntryCreate, type Unit, type WorkOrderSummary,
} from "../api-client";
import { ManufacturingTabs } from "./ManufacturingTabs";
import { DetailPageTemplate, FormPageTemplate, RegisterPageTemplate } from "./PageTemplates";
import { RequestError } from "./RequestError";

const PATH = "/app/manufacturing/shift-entries";
const PAGE_SIZE = 25;
type Catalog = { orders: WorkOrderSummary[]; machines: Machine[]; moulds: Mould[]; units: Unit[] };
type RejectRow = { reason: string; quantity: string };
type DowntimeRow = { reason: string; minutes: string };
type Draft = { workOrderId: string; machineId: string; workDate: string; shiftCode: string; goodQty: string; rejects: RejectRow[]; downtime: DowntimeRow[]; mouldId: string; shots: string };

function errorNotice(error: Error) {
  return <><RequestError error={error} />{error instanceof ApiError && error.status < 500 && error.requestId && <p>Request ID: {error.requestId}</p>}</>;
}

function validDecimal(value: string, allowZero = false) {
  if (!/^\d{1,10}(?:\.\d{1,3})?$/.test(value)) return false;
  const [whole, fraction = ""] = value.split(".");
  const milli = BigInt(whole) * 1000n + BigInt(fraction.padEnd(3, "0"));
  return allowZero ? milli <= 1_000_000_000_000n : milli > 0n && milli <= 1_000_000_000_000n;
}

function loadCatalog(me: Me, signal: AbortSignal) {
  return Promise.all([
    fetchAllWorkOrders(signal), fetchAllMachines(null, "true", signal), fetchAllMoulds(null, "true", signal), fetchAllOrganizationUnits(signal),
  ]).then(([orders, machines, moulds, units]) => {
    const authorized = (unitId: string) => me.all_plants || me.unit_ids.includes(unitId);
    return {
      orders: orders.filter(order => authorized(order.unit_id) && ["RELEASED", "IN_PROGRESS", "COMPLETED"].includes(order.status)),
      machines: machines.filter(machine => machine.active && authorized(machine.unit_id)),
      moulds: moulds.filter(mould => mould.active && authorized(mould.unit_id)),
      units: units.filter(unit => authorized(unit.unit_id)),
    } satisfies Catalog;
  });
}

function Register({ me }: { me: Me }) {
  const [params, setParams] = useSearchParams();
  const navigationType = useNavigationType();
  const paramsRef = useRef(new URLSearchParams(params));
  const [catalog, setCatalog] = useState<Catalog | null>(null);
  const [catalogError, setCatalogError] = useState<Error | null>(null);
  const [entries, setEntries] = useState<ShiftEntry[] | null>(null);
  const [error, setError] = useState<Error | null>(null);
  const [attempt, setAttempt] = useState(0);
  const [page, setPage] = useState(0);
  const workOrderId = params.get("work_order_id") ?? "";
  const machineId = params.get("machine_id") ?? "";
  const dateFrom = params.get("date_from") ?? "";
  const dateTo = params.get("date_to") ?? "";
  const shift = params.get("shift") ?? "";
  const plant = params.get("unit_id") ?? "";
  useEffect(() => { if (navigationType !== "REPLACE") paramsRef.current = new URLSearchParams(params); }, [navigationType, params]);

  useEffect(() => {
    const controller = new AbortController();
    setCatalogError(null);
    void loadCatalog(me, controller.signal).then(setCatalog).catch(value => { if (!controller.signal.aborted) setCatalogError(value instanceof Error ? value : new Error("Shift-entry filters could not be loaded.")); });
    return () => controller.abort();
  }, [me, attempt]);
  useEffect(() => {
    const controller = new AbortController();
    setEntries(null); setError(null); setPage(0);
    void fetchAllShiftEntries({ workOrderId: workOrderId || null, machineId: machineId || null, dateFrom: dateFrom || null, dateTo: dateTo || null }, controller.signal)
      .then(setEntries).catch(value => { if (!controller.signal.aborted) setError(value instanceof Error ? value : new Error("Shift entries could not be loaded.")); });
    return () => controller.abort();
  }, [workOrderId, machineId, dateFrom, dateTo, attempt]);

  const update = (key: string, value: string) => {
    const next = new URLSearchParams(paramsRef.current);
    value ? next.set(key, value) : next.delete(key);
    paramsRef.current = next;
    setParams(next, { replace: true });
    setPage(0);
  };
  const filtered = useMemo(() => (entries ?? []).filter(entry => (!plant || entry.unit_id === plant) && (!shift || entry.shift_code === shift)), [entries, plant, shift]);
  const visible = filtered.slice(page * PAGE_SIZE, (page + 1) * PAGE_SIZE);
  const maxPage = Math.max(0, Math.ceil(filtered.length / PAGE_SIZE) - 1);

  return <RegisterPageTemplate className="shift-entries" title="Shift entries" navigation={<ManufacturingTabs active="shifts" />} description="Review recorded production output, rejects and downtime by shift."
    actions={me.permissions.includes("mfg.entry") && <Link className="button button--primary" to={`${PATH}/new`}><Plus size={16} />New shift entry</Link>}
    filters={<>
      <div className="field"><label htmlFor="shift-order-filter">Work order filter</label><select id="shift-order-filter" value={workOrderId} onChange={event => update("work_order_id", event.target.value)}><option value="">All work orders</option>{catalog?.orders.map(order => <option key={order.work_order_id} value={order.work_order_id}>{order.work_order_no}</option>)}</select></div>
      <div className="field"><label htmlFor="shift-machine-filter">Machine filter</label><select id="shift-machine-filter" value={machineId} onChange={event => update("machine_id", event.target.value)}><option value="">All machines</option>{catalog?.machines.map(machine => <option key={machine.machine_id} value={machine.machine_id}>{machine.code}</option>)}</select></div>
      <div className="field"><label htmlFor="shift-plant-filter">Plant filter</label><select id="shift-plant-filter" value={plant} onChange={event => update("unit_id", event.target.value)}><option value="">All plants</option>{catalog?.units.map(unit => <option key={unit.unit_id} value={unit.unit_id}>{unit.code}</option>)}</select></div>
      <div className="field"><label htmlFor="shift-code-filter">Shift filter</label><select id="shift-code-filter" value={shift} onChange={event => update("shift", event.target.value)}><option value="">All shifts</option><option>A</option><option>B</option><option>C</option></select></div>
      <div className="field"><label htmlFor="shift-from">From date</label><input id="shift-from" type="date" value={dateFrom} onChange={event => update("date_from", event.target.value)} /></div>
      <div className="field"><label htmlFor="shift-to">To date</label><input id="shift-to" type="date" value={dateTo} onChange={event => update("date_to", event.target.value)} /></div>
    </>}
    footer={filtered.length > PAGE_SIZE && <div className="pagination"><button className="button button--secondary" type="button" disabled={page === 0} onClick={() => setPage(value => value - 1)}>Previous page</button><span>Page {page + 1} of {maxPage + 1}</span><button className="button button--secondary" type="button" disabled={page >= maxPage} onClick={() => setPage(value => value + 1)}>Next page</button></div>}
  >
    {catalogError && <>{errorNotice(catalogError)}</>}
    {entries === null && !error && <p role="status">Loading shift entries…</p>}
    {error && <>{errorNotice(error)}<button className="button button--secondary" type="button" onClick={() => setAttempt(value => value + 1)}>Try again</button></>}
    {entries && !filtered.length && <div className="empty-state"><h3>No shift entries found</h3><p>Change the filters or record the first shift entry.</p></div>}
    {entries && filtered.length > 0 && <>
      <div className="master-table-wrap shift-entries__desktop" role="region" aria-label="Scrollable shift entry results" tabIndex={0}><table className="master-table" aria-label="Shift entry results"><thead><tr><th>Date / shift</th><th>Work order</th><th>Machine / operator</th><th>Output</th><th>Losses</th><th>Mould / shots</th></tr></thead><tbody>{visible.map(entry => <tr key={entry.shift_entry_id}><td>{entry.work_date}<br /><span className="status-badge">Shift {entry.shift_code}</span></td><td>{entry.work_order_no}</td><td>{entry.machine_code}<br /><span>{entry.operator}</span></td><td>Good {entry.good_qty}<br />Reject {entry.reject_qty}</td><td>{entry.downtime_minutes} min{entry.losses.map((loss, index) => <div key={`${loss.kind}-${index}`}>{loss.reason}: {loss.kind === "REJECT" ? loss.quantity : `${loss.minutes} min`}</div>)}</td><td>{entry.mould_code ?? "—"}<br />{entry.shots ?? "—"}</td></tr>)}</tbody></table></div>
      <ul className="shift-entries__mobile" aria-label="Shift entry results">{visible.map(entry => <li key={entry.shift_entry_id} className="record-panel"><strong>{entry.work_order_no} · Shift {entry.shift_code}</strong><span>{entry.work_date} · {entry.machine_code}</span><span>{entry.operator}</span><span>Good {entry.good_qty} · Reject {entry.reject_qty}</span><span>Downtime {entry.downtime_minutes} min</span><span>{entry.mould_code ? `${entry.mould_code} · ${entry.shots ?? 0} shots` : "No mould recorded"}</span></li>)}</ul>
    </>}
  </RegisterPageTemplate>;
}

function parseDraft(raw: string | null): Draft | null { try { return raw ? JSON.parse(raw) as Draft : null; } catch { return null; } }

function Create({ me }: { me: Me }) {
  const [params] = useSearchParams();
  const markerKey = `shift-entry-unknown:${me.user_id}`;
  const draftKey = `shift-entry-draft:${me.user_id}`;
  const initialUnknown = (() => { try { return localStorage.getItem(markerKey) === "1" || sessionStorage.getItem(draftKey) !== null; } catch { return true; } })();
  const restored = (() => { try { return parseDraft(sessionStorage.getItem(draftKey)); } catch { return null; } })();
  const [catalog, setCatalog] = useState<Catalog | null>(null);
  const [loadError, setLoadError] = useState<Error | null>(null);
  const [attempt, setAttempt] = useState(0);
  const [workOrderId, setWorkOrderId] = useState(restored?.workOrderId ?? "");
  const [machineId, setMachineId] = useState(restored?.machineId ?? params.get("machine_id") ?? "");
  const [workDate, setWorkDate] = useState(restored?.workDate ?? params.get("work_date") ?? "");
  const [shiftCode, setShiftCode] = useState(restored?.shiftCode ?? params.get("shift_code") ?? "");
  const [goodQty, setGoodQty] = useState(restored?.goodQty ?? "");
  const [rejects, setRejects] = useState<RejectRow[]>(restored?.rejects ?? []);
  const [downtime, setDowntime] = useState<DowntimeRow[]>(restored?.downtime ?? []);
  const [mouldId, setMouldId] = useState(restored?.mouldId ?? "");
  const [shots, setShots] = useState(restored?.shots ?? "");
  const [unknown, setUnknown] = useState(initialUnknown);
  const [requestId, setRequestId] = useState<string>();
  const [validation, setValidation] = useState<string[]>([]);
  const [error, setError] = useState<Error | null>(null);
  const [busy, setBusy] = useState(false);
  const [created, setCreated] = useState<ShiftEntry | null>(null);
  const inFlight = useRef(false);
  const titleRef = useRef<HTMLHeadingElement>(null);

  useEffect(() => { titleRef.current?.focus(); }, []);
  useEffect(() => {
    const controller = new AbortController(); setLoadError(null);
    void loadCatalog(me, controller.signal).then(setCatalog).catch(value => { if (!controller.signal.aborted) setLoadError(value instanceof Error ? value : new Error("Shift-entry options could not be loaded.")); });
    return () => controller.abort();
  }, [me, attempt]);
  const selectedOrder = catalog?.orders.find(order => order.work_order_id === workOrderId);
  const machines = catalog?.machines.filter(machine => !selectedOrder || machine.unit_id === selectedOrder.unit_id) ?? [];
  const moulds = catalog?.moulds.filter(mould => !selectedOrder || mould.unit_id === selectedOrder.unit_id) ?? [];

  const clearSafety = () => { try { localStorage.removeItem(markerKey); sessionStorage.removeItem(draftKey); setUnknown(false); setRequestId(undefined); } catch { setUnknown(true); } };
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (busy || inFlight.current || unknown) return;
    const problems: string[] = [];
    if (!selectedOrder) problems.push("Select an eligible work order.");
    if (!machines.some(machine => machine.machine_id === machineId)) problems.push("Select an active machine in the work-order plant.");
    if (!workDate) problems.push("Work date is required.");
    else if (workDate > new Date().toISOString().slice(0, 10)) problems.push("Work date cannot be in the future.");
    if (!/[ABC]/.test(shiftCode)) problems.push("Select shift A, B or C.");
    if (!validDecimal(goodQty || "0", true)) problems.push("Good quantity must be between 0 and 1,000,000,000 with up to three decimal places.");
    rejects.forEach((row, index) => { if (row.reason.trim().length < 2) problems.push(`Reject reason ${index + 1} must contain at least 2 characters.`); if (!validDecimal(row.quantity)) problems.push(`Reject quantity ${index + 1} must be greater than 0.`); });
    downtime.forEach((row, index) => { if (row.reason.trim().length < 2) problems.push(`Downtime reason ${index + 1} must contain at least 2 characters.`); if (!/^\d+$/.test(row.minutes) || Number(row.minutes) < 1 || Number(row.minutes) > 480) problems.push(`Downtime minutes ${index + 1} must be between 1 and 480.`); });
    if (downtime.reduce((sum, row) => sum + Number(row.minutes || 0), 0) > 480) problems.push("Total downtime cannot exceed 480 minutes.");
    if (!validDecimal(goodQty || "0", true) || (goodQty === "0" && rejects.length === 0 && downtime.length === 0)) problems.push("Record good output, a reject, or downtime.");
    if (Boolean(mouldId) !== Boolean(shots)) problems.push("Mould and shots must be provided together; shots are required when a mould is selected.");
    if (shots && (!/^\d+$/.test(shots) || Number(shots) < 1 || Number(shots) > 10_000_000)) problems.push("Shots must be a positive whole number up to 10,000,000.");
    if (problems.length) { setValidation(problems); return; }
    const draft: Draft = { workOrderId, machineId, workDate, shiftCode, goodQty, rejects, downtime, mouldId, shots };
    try { sessionStorage.setItem(draftKey, JSON.stringify(draft)); localStorage.setItem(markerKey, "1"); } catch { setUnknown(true); return; }
    const body: ShiftEntryCreate = { work_order_id: workOrderId, machine_id: machineId, work_date: workDate, shift_code: shiftCode as "A" | "B" | "C", good_qty: goodQty, rejects: rejects.map(row => ({ reason: row.reason.trim(), quantity: row.quantity })), downtime: downtime.map(row => ({ reason: row.reason.trim(), minutes: Number(row.minutes) })), mould_id: mouldId || null, shots: shots ? Number(shots) : null };
    inFlight.current = true; setBusy(true); setValidation([]); setError(null);
    try { const result = await createShiftEntry(body); clearSafety(); setCreated(result); }
    catch (value) {
      if (value instanceof ApiError && value.status >= 400 && value.status < 500 && value.status !== 408) { clearSafety(); setError(value); }
      else { setRequestId(value instanceof ApiError ? value.requestId : undefined); setUnknown(true); }
    } finally { inFlight.current = false; setBusy(false); }
  }

  if (created) return <DetailPageTemplate title="Shift entry recorded" navigation={<ManufacturingTabs active="shifts" />} back={<Link to={PATH}><ArrowLeft size={16} />Back to shift entries</Link>}><section className="record-panel"><p role="status">Shift entry recorded for {created.work_order_no}.</p><p>{created.work_date} · Shift {created.shift_code} · {created.machine_code}</p><p>Good quantity: {created.good_qty}</p></section></DetailPageTemplate>;
  return <FormPageTemplate className="issue-planner shift-entry-create" title="New shift entry" headingRef={titleRef} navigation={<ManufacturingTabs active="shifts" />} back={<Link to={PATH}><ArrowLeft size={16} />Back to shift entries</Link>} description="Record actual output, rejects and downtime for one production shift." formTitle="Shift production details" onSubmit={event => void submit(event)}
    formActions={<><Link className="button button--secondary" to={PATH}>Cancel</Link><button className="button button--primary" type="submit" disabled={busy || unknown || !catalog}>Record shift entry</button></>}
    feedback={<>{validation.length > 0 && <div className="decision-error" role="alert"><ul>{validation.map(message => <li key={message}>{message}</li>)}</ul></div>}{error && errorNotice(error)}</>}
  >
    {unknown && <div className="decision-error" role="alert"><strong>Shift entry outcome unknown.</strong><p>Check the shift entry register before trying again. A repeated submission could create a duplicate.</p>{requestId && <p>Request ID: {requestId}</p>}<p><Link to={PATH} target="_blank" rel="noopener noreferrer">Open shift entry register</Link></p><button className="button button--secondary" type="button" onClick={clearSafety}>I checked the shift entry register</button></div>}
    {!catalog && !loadError && <p role="status">Loading shift-entry options…</p>}
    {loadError && <>{errorNotice(loadError)}<button className="button button--secondary" type="button" onClick={() => setAttempt(value => value + 1)}>Try again</button></>}
    {catalog && <fieldset className="issue-planner__fieldset" disabled={busy || unknown}><div className="record-form__grid">
      <div className="field"><label htmlFor="shift-order">Work order</label><select id="shift-order" required value={workOrderId} onChange={event => { setWorkOrderId(event.target.value); setMachineId(""); setMouldId(""); setShots(""); }}><option value="">Select work order</option>{catalog.orders.map(order => <option key={order.work_order_id} value={order.work_order_id}>{order.work_order_no} — {order.item_code}</option>)}</select></div>
      <div className="field"><label htmlFor="shift-machine">Machine</label><select id="shift-machine" required value={machineId} onChange={event => setMachineId(event.target.value)}><option value="">Select machine</option>{machines.map(machine => <option key={machine.machine_id} value={machine.machine_id}>{machine.code} — {machine.name}</option>)}</select></div>
      <div className="field"><label htmlFor="shift-date">Work date</label><input id="shift-date" type="date" required value={workDate} onChange={event => setWorkDate(event.target.value)} /></div>
      <div className="field"><label htmlFor="shift-code">Shift</label><select id="shift-code" required value={shiftCode} onChange={event => setShiftCode(event.target.value)}><option value="">Select shift</option><option>A</option><option>B</option><option>C</option></select></div>
      <div className="field"><label htmlFor="shift-good">Good quantity</label><input id="shift-good" inputMode="decimal" required value={goodQty} onChange={event => setGoodQty(event.target.value)} /></div>
      <div className="field"><label htmlFor="shift-mould">Mould</label><select id="shift-mould" value={mouldId} onChange={event => setMouldId(event.target.value)}><option value="">No mould</option>{moulds.map(mould => <option key={mould.mould_id} value={mould.mould_id}>{mould.code}</option>)}</select></div>
      <div className="field"><label htmlFor="shift-shots">Shots</label><input id="shift-shots" inputMode="numeric" value={shots} onChange={event => setShots(event.target.value)} /></div>
    </div>
    <section className="record-panel"><div className="record-panel__heading"><h3>Rejects</h3><button className="button button--secondary" type="button" onClick={() => setRejects(rows => [...rows, { reason: "", quantity: "" }])}>Add reject</button></div>{rejects.length === 0 && <p>No rejects recorded.</p>}{rejects.map((row, index) => <div className="record-form__grid" key={index}><div className="field"><label htmlFor={`reject-reason-${index}`}>Reject reason {index + 1}</label><input id={`reject-reason-${index}`} value={row.reason} onChange={event => setRejects(rows => rows.map((item, rowIndex) => rowIndex === index ? { ...item, reason: event.target.value } : item))} /></div><div className="field"><label htmlFor={`reject-qty-${index}`}>Reject quantity {index + 1}</label><input id={`reject-qty-${index}`} inputMode="decimal" value={row.quantity} onChange={event => setRejects(rows => rows.map((item, rowIndex) => rowIndex === index ? { ...item, quantity: event.target.value } : item))} /></div><button className="icon-button" type="button" aria-label={`Remove reject ${index + 1}`} onClick={() => setRejects(rows => rows.filter((_, rowIndex) => rowIndex !== index))}><Trash2 size={16} /></button></div>)}</section>
    <section className="record-panel"><div className="record-panel__heading"><h3>Downtime</h3><button className="button button--secondary" type="button" onClick={() => setDowntime(rows => [...rows, { reason: "", minutes: "" }])}>Add downtime</button></div>{downtime.length === 0 && <p>No downtime recorded.</p>}{downtime.map((row, index) => <div className="record-form__grid" key={index}><div className="field"><label htmlFor={`downtime-reason-${index}`}>Downtime reason {index + 1}</label><input id={`downtime-reason-${index}`} value={row.reason} onChange={event => setDowntime(rows => rows.map((item, rowIndex) => rowIndex === index ? { ...item, reason: event.target.value } : item))} /></div><div className="field"><label htmlFor={`downtime-minutes-${index}`}>Downtime minutes {index + 1}</label><input id={`downtime-minutes-${index}`} inputMode="numeric" value={row.minutes} onChange={event => setDowntime(rows => rows.map((item, rowIndex) => rowIndex === index ? { ...item, minutes: event.target.value } : item))} /></div><button className="icon-button" type="button" aria-label={`Remove downtime ${index + 1}`} onClick={() => setDowntime(rows => rows.filter((_, rowIndex) => rowIndex !== index))}><Trash2 size={16} /></button></div>)}</section>
    </fieldset>}
  </FormPageTemplate>;
}

export function ShiftEntries({ me }: { me: Me }) {
  const location = useLocation();
  if (!me.modules.includes("manufacturing")) return <section className="master-state"><h2>Manufacturing unavailable</h2><p>Access to the manufacturing module is required.</p></section>;
  if (location.pathname === `${PATH}/new`) {
    if (!me.permissions.includes("mfg.entry")) return <section className="master-state"><h2>Access denied</h2><p>The mfg.entry permission is required to record shift entries.</p></section>;
    return <Create me={me} />;
  }
  return <Register me={me} />;
}
