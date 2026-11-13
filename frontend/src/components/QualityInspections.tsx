import { useEffect, useRef, useState, type FormEvent } from "react";
import { ArrowLeft, ArrowUpRight, ChevronLeft, ChevronRight, Plus, RefreshCw } from "lucide-react";
import { Link, useNavigate, useNavigationType, useSearchParams } from "react-router-dom";
import {
  createInspection, fetchInspection, listInspectionsPage, listQualityInstrumentsPage,
  type Inspection, type InspectionCreate, type InspectionMeasurement, type InspectionPlanKind,
  type InspectionResult, type InspectionSubjectType, type InspectionSummary, type Me, type QualityInstrument,
} from "../api-client";
import { DetailPageTemplate, FormPageTemplate, RegisterPageTemplate } from "./PageTemplates";
import { QualityTabs } from "./QualityTabs";
import { RequestError } from "./RequestError";

const PAGE_SIZE = 50;
const subjectTypes: InspectionSubjectType[] = ["GRN_LINE", "COMPOUND_BATCH", "PROD_LOT", "STOCK_LOT"];
const kinds: InspectionPlanKind[] = ["INCOMING", "IN_PROCESS", "FINAL"];
const results: InspectionResult[] = ["ACCEPT", "PARTIAL", "REJECT", "HOLD"];
type Load<T> = { status: "loading" } | { status: "ready"; data: T } | { status: "error"; error: Error };
const failure = (value: unknown, message: string) => value instanceof Error ? value : new Error(message);
const label = (value: string | null) => value ? value.toLowerCase().split("_").map(word => word[0].toUpperCase() + word.slice(1)).join(" ") : "Not recorded";
const resultBadge = (value: string) => <span className={`status-badge status-badge--${value === "ACCEPT" ? "approved" : value === "PARTIAL" ? "pending" : "cancelled"}`}>{label(value)}</span>;

export function InspectionRegister({ me }: { me: Me }) {
  const [params, setParams] = useSearchParams();
  const navigationType = useNavigationType();
  const pending = useRef(new URLSearchParams(params));
  useEffect(() => { if (navigationType === "POP") pending.current = new URLSearchParams(params); }, [navigationType, params]);
  const subjectValue = params.get("subject_type") ?? "";
  const subjectType = subjectTypes.includes(subjectValue as InspectionSubjectType) ? subjectValue as InspectionSubjectType : null;
  const kindValue = params.get("kind") ?? "";
  const kind = kinds.includes(kindValue as InspectionPlanKind) ? kindValue as InspectionPlanKind : null;
  const resultValue = params.get("result") ?? "";
  const result = results.includes(resultValue as InspectionResult) ? resultValue as InspectionResult : null;
  const offsetValue = Number(params.get("offset") ?? "0"), offset = Number.isSafeInteger(offsetValue) && offsetValue >= 0 ? offsetValue : 0;
  const [state, setState] = useState<Load<{ rows: InspectionSummary[]; total: number }>>({ status: "loading" });
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    if (!me.modules.includes("quality")) return;
    const controller = new AbortController(); setState({ status: "loading" });
    void listInspectionsPage({ subjectType, subjectId: null, kind, result, limit: PAGE_SIZE, offset }, controller.signal).then(page => { if (!controller.signal.aborted) setState({ status: "ready", data: { rows: page.items, total: page.total } }); }).catch(value => { if (!controller.signal.aborted) setState({ status: "error", error: failure(value, "Inspections could not be loaded.") }); });
    return () => controller.abort();
  }, [attempt, kind, me.modules, offset, result, subjectType]);
  function filter(key: string, value: string, keepOffset = false) { setState({ status: "loading" }); const next = new URLSearchParams(pending.current); value ? next.set(key, value) : next.delete(key); if (!keepOffset) next.delete("offset"); pending.current = next; setParams(next, { replace: true }); }
  if (!me.modules.includes("quality")) return <section className="master-state"><h2>Access denied</h2><p>The quality module is required.</p></section>;
  const total = state.status === "ready" ? state.data.total : 0;
  return <RegisterPageTemplate title="Inspections" description="Recorded incoming, in-process and final quality decisions with their measured values." navigation={<QualityTabs active="inspections" />} actions={me.permissions.includes("qa.approve") && <Link className="button button--primary" to="/app/quality/inspections/new"><Plus size={17} />Record inspection</Link>}
    filters={<><div className="field"><label htmlFor="inspection-subject">Subject type</label><select id="inspection-subject" value={subjectValue} onChange={event => filter("subject_type", event.target.value)}><option value="">All subjects</option>{subjectTypes.map(value => <option key={value} value={value}>{label(value)}</option>)}</select></div><div className="field"><label htmlFor="inspection-kind">Inspection type</label><select id="inspection-kind" value={kindValue} onChange={event => filter("kind", event.target.value)}><option value="">All types</option>{kinds.map(value => <option key={value} value={value}>{label(value)}</option>)}</select></div><div className="field"><label htmlFor="inspection-result">Result filter</label><select id="inspection-result" value={resultValue} onChange={event => filter("result", event.target.value)}><option value="">All results</option>{results.map(value => <option key={value} value={value}>{label(value)}</option>)}</select></div><button className="button button--secondary" type="button" disabled={state.status === "loading"} onClick={() => setAttempt(value => value + 1)}><RefreshCw size={16} />Refresh</button></>}
  >
    {state.status === "loading" && <div className="master-state" role="status"><span className="spinner" /><p>Loading inspections…</p></div>}
    {state.status === "error" && <><RequestError error={state.error} includeClientRequestId /><button className="button button--secondary" type="button" onClick={() => setAttempt(value => value + 1)}>Try again</button></>}
    {state.status === "ready" && state.data.rows.length === 0 && <div className="master-state"><h3>No inspections</h3><p>No quality records match the selected filters.</p></div>}
    {state.status === "ready" && state.data.rows.length > 0 && <><p className="compound-scroll-hint">Scroll horizontally to view all inspection fields.</p><div className="master-table-wrap" role="region" aria-label="Scrollable inspections" tabIndex={0}><table className="master-table" aria-label="Inspections" style={{ minWidth: 1120, tableLayout: "auto" }}><thead><tr>{["Inspection", "Type", "Subject", "Result", "Source", "Inspector", "Inspected at", "Action"].map(value => <th key={value} style={{ whiteSpace: "nowrap" }}>{value}</th>)}</tr></thead><tbody>{state.data.rows.map(row => <tr key={row.inspection_id}><td><strong>{row.inspection_no}</strong></td><td>{label(row.kind)}</td><td>{label(row.subject_type)}</td><td>{resultBadge(row.result)}</td><td>{label(row.source)}</td><td style={{ whiteSpace: "nowrap" }}>{row.inspected_by_name}</td><td style={{ whiteSpace: "nowrap" }}>{row.inspected_at.slice(0, 16).replace("T", " ")}</td><td><Link className="icon-link" aria-label={`Open inspection ${row.inspection_no}`} to={`/app/quality/inspections/${row.inspection_id}`}><ArrowUpRight size={17} /></Link></td></tr>)}</tbody></table></div><ul className="master-cards" aria-label="Inspection cards">{state.data.rows.map(row => <li key={row.inspection_id}><div><span>{row.inspection_no}</span>{resultBadge(row.result)}</div><h3>{label(row.kind)} inspection</h3><dl><div><dt>Subject</dt><dd>{label(row.subject_type)}</dd></div><div><dt>Inspector</dt><dd>{row.inspected_by_name}</dd></div><div><dt>Inspected</dt><dd>{row.inspected_at.slice(0, 10)}</dd></div></dl><Link className="button button--secondary" aria-label={`Open inspection ${row.inspection_no}`} to={`/app/quality/inspections/${row.inspection_id}`}>Open inspection</Link></li>)}</ul><div className="reference-pagination"><p className="result-summary" aria-live="polite">Showing {total ? offset + 1 : 0}–{Math.min(offset + PAGE_SIZE, total)} of {total} records</p>{total > PAGE_SIZE && <div><button className="button button--secondary" type="button" aria-label="Previous page" disabled={offset === 0} onClick={() => filter("offset", String(Math.max(0, offset - PAGE_SIZE)), true)}><ChevronLeft size={16} /></button><span>Page {Math.floor(offset / PAGE_SIZE) + 1}</span><button className="button button--secondary" type="button" aria-label="Next page" disabled={offset + PAGE_SIZE >= total} onClick={() => filter("offset", String(offset + PAGE_SIZE), true)}><ChevronRight size={16} /></button></div>}</div></>}
  </RegisterPageTemplate>;
}

export function InspectionDetail({ id }: { id: string }) {
  const [params] = useSearchParams();
  const [state, setState] = useState<Load<Inspection>>({ status: "loading" });
  useEffect(() => { const controller = new AbortController(); void fetchInspection(id, controller.signal).then(data => { if (!controller.signal.aborted) setState({ status: "ready", data }); }).catch(value => { if (!controller.signal.aborted) setState({ status: "error", error: failure(value, "Inspection could not be loaded.") }); }); return () => controller.abort(); }, [id]);
  if (state.status === "loading") return <section className="master-detail"><QualityTabs active="inspections" /><div className="master-state" role="status"><span className="spinner" /><p>Loading inspection…</p></div></section>;
  if (state.status === "error") return <section className="master-detail"><QualityTabs active="inspections" /><RequestError error={state.error} includeClientRequestId /></section>;
  const value = state.data;
  return <DetailPageTemplate className="inspection-detail" title={value.inspection_no} description={`${label(value.kind)} · ${label(value.subject_type)} · ${value.inspected_at.slice(0, 16).replace("T", " ")}`} navigation={<QualityTabs active="inspections" />} back={<Link className="back-link" to="/app/quality/inspections"><ArrowLeft size={16} />Inspections</Link>} status={resultBadge(value.result)}>
    {params.get("created") === "1" && <p className="success-banner" role="status">Inspection recorded.</p>}
    <section className="record-panel"><div className="record-panel__heading"><h3>Inspection context</h3></div><dl className="record-grid"><div><dt>Plan</dt><dd>{value.plan_name ?? "No active plan"}</dd></div><div><dt>Instrument</dt><dd>{value.instrument_code ?? "Not used"}</dd></div><div><dt>Inspector</dt><dd>{value.inspected_by_name}</dd></div><div><dt>Source</dt><dd>{label(value.source)}</dd></div><div><dt>Notes</dt><dd>{value.notes ?? "No notes"}</dd></div></dl></section>
    <section className="record-panel"><div className="record-panel__heading"><h3>Measurements</h3></div>{value.measurements.length ? <div className="master-table-wrap" role="region" aria-label="Scrollable inspection measurements" tabIndex={0} style={{ display: "block" }}><table className="master-table" aria-label="Inspection measurements" style={{ minWidth: 760, tableLayout: "auto" }}><thead><tr><th>Parameter</th><th>Measured</th><th>Unit</th><th>Minimum</th><th>Maximum</th><th>Result</th></tr></thead><tbody>{value.measurements.map(row => <tr key={row.parameter_code}><td><strong>{row.parameter_code}</strong></td><td>{row.measured_value ?? "Not measured"}</td><td>{row.unit ?? "Not set"}</td><td>{row.min_value ?? "No minimum"}</td><td>{row.max_value ?? "No maximum"}</td><td>{row.result ?? "Information"}</td></tr>)}</tbody></table></div> : <div className="master-state"><h4>No measurements</h4><p>This inspection contains a decision without measured parameters.</p></div>}</section>
  </DetailPageTemplate>;
}

type DraftMeasurement = { parameter_code: string; value: string; unit: string };
const emptyMeasurement = (): DraftMeasurement => ({ parameter_code: "", value: "", unit: "" });
export function InspectionCreatePage({ me }: { me: Me }) {
  const navigate = useNavigate();
  const [params] = useSearchParams();
  const initialType = params.get("subject_type") ?? "";
  const [subjectType, setSubjectType] = useState<InspectionSubjectType>(subjectTypes.includes(initialType as InspectionSubjectType) ? initialType as InspectionSubjectType : "GRN_LINE");
  const [subjectId, setSubjectId] = useState(params.get("subject_id") ?? "");
  const [kind, setKind] = useState<InspectionPlanKind>("INCOMING");
  const [result, setResult] = useState<InspectionResult>("ACCEPT");
  const [instrumentId, setInstrumentId] = useState("");
  const [notes, setNotes] = useState("");
  const [measurements, setMeasurements] = useState<DraftMeasurement[]>([emptyMeasurement()]);
  const [instruments, setInstruments] = useState<Load<QualityInstrument[]>>({ status: "loading" });
  const [error, setError] = useState<Error | null>(null), [saving, setSaving] = useState(false);
  useEffect(() => { const controller = new AbortController(); void listQualityInstrumentsPage({ unitId: null, calibrationStatus: null, active: true, limit: 200, offset: 0 }, controller.signal).then(page => { if (!controller.signal.aborted) setInstruments({ status: "ready", data: page.items }); }).catch(value => { if (!controller.signal.aborted) setInstruments({ status: "error", error: failure(value, "Instrument options could not be loaded.") }); }); return () => controller.abort(); }, []);
  function change(index: number, field: keyof DraftMeasurement, value: string) { setMeasurements(current => current.map((row, position) => position === index ? { ...row, [field]: value } : row)); }
  async function submit(event: FormEvent) {
    event.preventDefault(); setError(null); setSaving(true);
    const body: InspectionCreate = { kind, subject_type: subjectType, subject_id: subjectId, result, notes: notes.trim() || null, instrument_id: instrumentId || null, measurements: measurements.filter(row => row.parameter_code.trim() || row.value.trim()).map(row => ({ parameter_code: row.parameter_code.trim(), value: row.value.trim(), unit: row.unit.trim() || null })) };
    try { const created = await createInspection(body); navigate(`/app/quality/inspections/${created.inspection_id}?created=1`); } catch (value) { setError(failure(value, "Inspection could not be recorded.")); } finally { setSaving(false); }
  }
  if (!me.permissions.includes("qa.approve")) return <section className="master-state"><h2>Permission required</h2><p>The qa.approve permission is required to record inspections.</p></section>;
  return <FormPageTemplate className="inspection-create" title="Record inspection" description="Record the measured values exactly. The backend resolves the current plan, checks every required parameter and prevents an unsafe result." navigation={<QualityTabs active="inspections" />} back={<Link className="back-link" to="/app/quality/inspections"><ArrowLeft size={16} />Inspections</Link>} formTitle="Inspection context" onSubmit={submit} feedback={<>{instruments.status === "error" && <RequestError error={instruments.error} includeClientRequestId />}{error && <RequestError error={error} includeClientRequestId />}</>} formActions={<><Link className="button button--secondary" to="/app/quality/inspections">Cancel</Link><button className="button button--primary" type="submit" disabled={saving}>{saving ? "Recording…" : "Record inspection"}</button></>}>
    <div className="record-form__grid"><div className="field"><label htmlFor="inspection-create-subject-type">Subject type</label><select id="inspection-create-subject-type" value={subjectType} onChange={event => setSubjectType(event.target.value as InspectionSubjectType)}>{subjectTypes.map(value => <option key={value} value={value}>{label(value)}</option>)}</select></div><div className="field"><label htmlFor="inspection-create-subject-id">Subject ID</label><input id="inspection-create-subject-id" required value={subjectId} onChange={event => setSubjectId(event.target.value)} /></div><div className="field"><label htmlFor="inspection-create-kind">Inspection type</label><select id="inspection-create-kind" value={kind} onChange={event => setKind(event.target.value as InspectionPlanKind)}>{kinds.map(value => <option key={value} value={value}>{label(value)}</option>)}</select></div><div className="field"><label htmlFor="inspection-create-result">Result</label><select id="inspection-create-result" value={result} onChange={event => setResult(event.target.value as InspectionResult)}>{results.map(value => <option key={value} value={value}>{label(value)}</option>)}</select></div><div className="field"><label htmlFor="inspection-create-instrument">Instrument</label><select id="inspection-create-instrument" value={instrumentId} onChange={event => setInstrumentId(event.target.value)}><option value="">No instrument</option>{instruments.status === "ready" && instruments.data.map(instrument => { const usable = instrument.active && !["NEVER", "FAILED", "OVERDUE"].includes(instrument.calibration_status); return <option key={instrument.instrument_id} value={instrument.instrument_id} disabled={!usable}>{instrument.code} — {instrument.name} · {instrument.calibration_status}</option>; })}</select></div><div className="field field--wide"><label htmlFor="inspection-create-notes">Notes</label><textarea id="inspection-create-notes" maxLength={500} value={notes} onChange={event => setNotes(event.target.value)} /></div></div>
    <div className="record-form__heading"><h3>Measurements</h3><div><button className="button button--secondary" type="button" disabled={measurements.length >= 60} onClick={() => setMeasurements(current => [...current, emptyMeasurement()])}><Plus size={16} />Add measurement</button></div></div>
    <div className="inspection-parameter-list">{measurements.map((measurement, index) => <fieldset className="bom-editor__line" key={index}><legend>Measurement {index + 1}</legend><div className="record-form__grid"><div className="field"><label htmlFor={`measurement-code-${index}`}>Parameter code {index + 1}</label><input id={`measurement-code-${index}`} required value={measurement.parameter_code} onChange={event => change(index, "parameter_code", event.target.value)} /></div><div className="field"><label htmlFor={`measurement-value-${index}`}>Measured value {index + 1}</label><input id={`measurement-value-${index}`} required inputMode="decimal" value={measurement.value} onChange={event => change(index, "value", event.target.value)} /></div><div className="field"><label htmlFor={`measurement-unit-${index}`}>Unit {index + 1}</label><input id={`measurement-unit-${index}`} value={measurement.unit} onChange={event => change(index, "unit", event.target.value)} /></div></div>{measurements.length > 1 && <button className="button button--secondary" type="button" onClick={() => setMeasurements(current => current.filter((_, row) => row !== index))}>Remove measurement {index + 1}</button>}</fieldset>)}</div>
  </FormPageTemplate>;
}
