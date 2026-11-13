import { useEffect, useMemo, useRef, useState } from "react";
import { ArrowUpRight, RefreshCw } from "lucide-react";
import { Link, useNavigationType, useSearchParams } from "react-router-dom";
import { fetchAllOrganizationUnits, fetchQualityQueue, type Me, type QualityQueueKind, type QualityQueueRow, type Unit } from "../api-client";
import { RegisterPageTemplate } from "./PageTemplates";
import { QualityTabs } from "./QualityTabs";
import { RequestError } from "./RequestError";

type Load<T> = { status: "loading" } | { status: "ready"; data: T } | { status: "error"; error: Error };
const kinds: Array<{ value: QualityQueueKind; label: string }> = [
  { value: "GRN_LINE", label: "Incoming GRN" }, { value: "COMPOUND_BATCH", label: "Compound batch" }, { value: "PROD_LOT", label: "Production lot" },
];
const failure = (value: unknown, message: string) => value instanceof Error ? value : new Error(message);
const kindLabel = (value: QualityQueueKind) => kinds.find(kind => kind.value === value)?.label ?? value;
const ageHours = (value: string) => Math.max(0, Math.floor((Date.now() - new Date(value).getTime()) / 3_600_000));
const ageLabel = (value: string) => { const hours = ageHours(value); return hours >= 48 ? `${Math.floor(hours / 24)} days waiting` : `${hours} hours waiting`; };

export function QualityQueue({ me }: { me: Me }) {
  const [params, setParams] = useSearchParams();
  const navigationType = useNavigationType();
  const pending = useRef(new URLSearchParams(params));
  useEffect(() => { if (navigationType === "POP") pending.current = new URLSearchParams(params); }, [navigationType, params]);
  const typeValue = params.get("subject_type") ?? "";
  const subjectType = kinds.some(kind => kind.value === typeValue) ? typeValue as QualityQueueKind : null;
  const unitId = params.get("unit_id") ?? "";
  const age = params.get("age_hours") ?? "";
  const [units, setUnits] = useState<Load<Unit[]>>({ status: "loading" });
  const [queue, setQueue] = useState<Load<QualityQueueRow[]>>({ status: "loading" });
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    void fetchAllOrganizationUnits(controller.signal).then(data => { if (!controller.signal.aborted) setUnits({ status: "ready", data: data.filter(unit => me.all_plants || me.unit_ids.includes(unit.unit_id)) }); }).catch(value => { if (!controller.signal.aborted) setUnits({ status: "error", error: failure(value, "Plant options could not be loaded.") }); });
    return () => controller.abort();
  }, [me.all_plants, me.unit_ids]);
  useEffect(() => {
    const controller = new AbortController(); setQueue({ status: "loading" });
    void fetchQualityQueue(subjectType, controller.signal).then(data => { if (!controller.signal.aborted) setQueue({ status: "ready", data }); }).catch(value => { if (!controller.signal.aborted) setQueue({ status: "error", error: failure(value, "QA queue could not be loaded.") }); });
    return () => controller.abort();
  }, [attempt, subjectType]);

  function setFilter(key: string, value: string) {
    const next = new URLSearchParams(pending.current); value ? next.set(key, value) : next.delete(key); pending.current = next; setParams(next);
  }
  const filtered = useMemo(() => queue.status === "ready" ? queue.data.filter(row => (!unitId || row.unit_id === unitId) && (!age || ageHours(row.waiting_since) >= Number(age))) : [], [age, queue, unitId]);
  const plantLabel = (id: string) => units.status === "ready" ? units.data.find(unit => unit.unit_id === id)?.code ?? "Outside scope" : "Loading";
  if (!me.modules.includes("quality")) return <section className="master-state"><h2>Access denied</h2><p>The quality module is required.</p></section>;

  return <RegisterPageTemplate className="quality-queue" title="QA queue" navigation={<QualityTabs active="queue" />} description="Prioritize incoming, compound and production-stage work waiting for a quality decision."
    actions={<button className="button button--secondary" type="button" disabled={queue.status === "loading"} onClick={() => setAttempt(value => value + 1)}><RefreshCw size={16} />Refresh</button>}
    filters={<>
      <div className="field"><label htmlFor="qa-kind">Queue kind</label><select id="qa-kind" value={typeValue} onChange={event => setFilter("subject_type", event.target.value)}><option value="">All kinds</option>{kinds.map(kind => <option key={kind.value} value={kind.value}>{kind.label}</option>)}</select></div>
      <div className="field"><label htmlFor="qa-plant">Plant filter</label><select id="qa-plant" value={unitId} onChange={event => setFilter("unit_id", event.target.value)}><option value="">All authorized plants</option>{units.status === "ready" && units.data.map(unit => <option key={unit.unit_id} value={unit.unit_id}>{unit.code} — {unit.name}</option>)}</select></div>
      <div className="field"><label htmlFor="qa-age">Age filter</label><select id="qa-age" value={age} onChange={event => setFilter("age_hours", event.target.value)}><option value="">Any waiting time</option><option value="24">24+ hours</option><option value="48">48+ hours</option><option value="72">72+ hours</option></select></div>
    </>}
  >
    {units.status === "error" && <RequestError error={units.error} />}
    {queue.status === "loading" && <div className="master-state" role="status"><span className="spinner" /><p>Loading QA queue…</p></div>}
    {queue.status === "error" && <><RequestError error={queue.error} /><button className="button button--secondary" type="button" onClick={() => setAttempt(value => value + 1)}>Try again</button></>}
    {queue.status === "ready" && filtered.length === 0 && <div className="master-state"><h3>No QA work waiting</h3><p>Change the filters or refresh later.</p></div>}
    {queue.status === "ready" && filtered.length > 0 && <div className="master-table-wrap" role="region" aria-label="Scrollable QA queue results" tabIndex={0} style={{ display: "block" }}><table className="master-table" aria-label="QA queue results"><thead><tr><th>Kind</th><th>Reference</th><th>Item</th><th>Quantity</th><th>Stage</th><th>Plant</th><th>Waiting since</th><th>Inspection</th><th>Action</th></tr></thead><tbody>{filtered.map(row => <tr key={`${row.subject_type}:${row.subject_id}`}><td>{kindLabel(row.subject_type)}</td><td><strong>{row.reference}</strong></td><td>{row.item_code ?? "Not available"}</td><td>{row.quantity}</td><td>{row.stage ?? "Not applicable"}</td><td>{plantLabel(row.unit_id)}</td><td>{row.waiting_since.slice(0, 16).replace("T", " ")}<br /><span className="status-badge">{ageLabel(row.waiting_since)}</span></td><td>{row.inspected ? "Inspection recorded" : "Inspection required"}</td><td><Link className="icon-link" aria-label={`Inspect ${row.reference}`} to={`/app/quality/inspections/new?${new URLSearchParams({ subject_type: row.subject_type, subject_id: row.subject_id })}`}><ArrowUpRight size={17} /></Link></td></tr>)}</tbody></table></div>}
  </RegisterPageTemplate>;
}
