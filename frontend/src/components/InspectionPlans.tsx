import { useEffect, useMemo, useRef, useState, type FormEvent } from "react";
import { ArrowLeft, ArrowUpRight, ChevronLeft, ChevronRight, Plus, RefreshCw, ShieldAlert, Trash2 } from "lucide-react";
import { Link, useNavigate, useNavigationType, useSearchParams } from "react-router-dom";
import {
  createInspectionPlan, fetchAllItems, fetchInspectionPlan, listInspectionPlansPage, retireInspectionPlan,
  type InspectionPlan, type InspectionPlanCreate, type InspectionPlanKind, type InspectionPlanParameter, type Item, type Me,
} from "../api-client";
import { DetailPageTemplate, FormPageTemplate, RegisterPageTemplate } from "./PageTemplates";
import { QualityTabs } from "./QualityTabs";
import { RequestError } from "./RequestError";

const PAGE_SIZE = 50;
const kinds: Array<{ value: InspectionPlanKind; label: string }> = [
  { value: "INCOMING", label: "Incoming" }, { value: "IN_PROCESS", label: "In process" }, { value: "FINAL", label: "Final" },
];
type Load<T> = { status: "loading" } | { status: "ready"; data: T } | { status: "error"; error: Error };
const failure = (value: unknown, message: string) => value instanceof Error ? value : new Error(message);
const kindLabel = (value: string) => kinds.find(kind => kind.value === value)?.label ?? value;
const status = (active: boolean) => <span className={`status-badge status-badge--${active ? "approved" : "cancelled"}`}>{active ? "Active" : "Retired"}</span>;
const decimalPattern = /^[-+]?\d{1,12}(?:\.\d{1,6})?$/;
function compareDecimals(left: string, right: string) {
  const parse = (value: string) => { const sign = value.startsWith("-") ? -1n : 1n; const [whole, fraction = ""] = value.replace(/^[+-]/, "").split("."); return { sign, whole: whole || "0", fraction }; };
  const a = parse(left), b = parse(right), scale = Math.max(a.fraction.length, b.fraction.length);
  const av = a.sign * BigInt(a.whole + a.fraction.padEnd(scale, "0"));
  const bv = b.sign * BigInt(b.whole + b.fraction.padEnd(scale, "0"));
  return av < bv ? -1 : av > bv ? 1 : 0;
}

export function InspectionPlanRegister({ me }: { me: Me }) {
  const [params, setParams] = useSearchParams();
  const navigationType = useNavigationType();
  const pending = useRef(new URLSearchParams(params));
  useEffect(() => { if (navigationType === "POP") pending.current = new URLSearchParams(params); }, [navigationType, params]);
  const itemId = params.get("item_id") ?? "";
  const kindValue = params.get("kind") ?? "";
  const kind = kinds.some(option => option.value === kindValue) ? kindValue as InspectionPlanKind : null;
  const activeValue = params.get("active") ?? "";
  const active = activeValue === "true" ? true : activeValue === "false" ? false : null;
  const offsetValue = Number(params.get("offset") ?? "0");
  const offset = Number.isSafeInteger(offsetValue) && offsetValue >= 0 ? offsetValue : 0;
  const [items, setItems] = useState<Load<Item[]>>({ status: "loading" });
  const [plans, setPlans] = useState<Load<{ items: InspectionPlan[]; total: number }>>({ status: "loading" });
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    void fetchAllItems(controller.signal).then(data => { if (!controller.signal.aborted) setItems({ status: "ready", data }); }).catch(value => { if (!controller.signal.aborted) setItems({ status: "error", error: failure(value, "Item options could not be loaded.") }); });
    return () => controller.abort();
  }, []);
  useEffect(() => {
    if (!me.modules.includes("quality")) return;
    const controller = new AbortController(); setPlans({ status: "loading" });
    void listInspectionPlansPage({ itemId: itemId || null, kind, active, limit: PAGE_SIZE, offset }, controller.signal)
      .then(page => { if (!controller.signal.aborted) setPlans({ status: "ready", data: { items: page.items, total: page.total } }); })
      .catch(value => { if (!controller.signal.aborted) setPlans({ status: "error", error: failure(value, "Inspection plans could not be loaded.") }); });
    return () => controller.abort();
  }, [active, attempt, itemId, kind, me.modules, offset]);
  function filter(key: string, value: string, keepOffset = false) {
    setPlans({ status: "loading" });
    const next = new URLSearchParams(pending.current); value ? next.set(key, value) : next.delete(key); if (!keepOffset) next.delete("offset"); pending.current = next; setParams(next, { replace: true });
  }
  if (!me.modules.includes("quality")) return <section className="master-state"><h2>Access denied</h2><p>The quality module is required.</p></section>;
  const total = plans.status === "ready" ? plans.data.total : 0;
  const first = total ? offset + 1 : 0;
  const last = Math.min(offset + PAGE_SIZE, total);
  return <RegisterPageTemplate title="Inspection plans" description="Measurement parameters and acceptance limits for incoming, in-process and final quality checks." navigation={<QualityTabs active="plans" />}
    actions={me.permissions.includes("qa.approve") && <Link className="button button--primary" to="/app/quality/plans/new"><Plus size={17} />Create inspection plan</Link>}
    filters={<>
      <div className="field"><label htmlFor="plan-item">Item filter</label><select id="plan-item" value={itemId} disabled={items.status !== "ready"} onChange={event => filter("item_id", event.target.value)}><option value="">All items</option>{items.status === "ready" && items.data.map(item => <option key={item.item_id} value={item.item_id}>{item.code} — {item.name}</option>)}</select></div>
      <div className="field"><label htmlFor="plan-kind">Inspection type</label><select id="plan-kind" value={kindValue} onChange={event => filter("kind", event.target.value)}><option value="">All types</option>{kinds.map(option => <option key={option.value} value={option.value}>{option.label}</option>)}</select></div>
      <div className="field"><label htmlFor="plan-active">Plan status</label><select id="plan-active" value={activeValue === "true" ? "active" : activeValue === "false" ? "retired" : ""} onChange={event => filter("active", event.target.value === "active" ? "true" : event.target.value === "retired" ? "false" : "")}><option value="">All statuses</option><option value="active">Active</option><option value="retired">Retired</option></select></div>
      <button className="button button--secondary" type="button" disabled={plans.status === "loading"} onClick={() => setAttempt(value => value + 1)}><RefreshCw size={16} />Refresh</button>
    </>}
  >
    {items.status === "error" && <RequestError error={items.error} />}
    {plans.status === "loading" && <div className="master-state" role="status"><span className="spinner" /><p>Loading inspection plans…</p></div>}
    {plans.status === "error" && <><RequestError error={plans.error} /><button className="button button--secondary" type="button" onClick={() => setAttempt(value => value + 1)}>Try again</button></>}
    {plans.status === "ready" && plans.data.items.length === 0 && <div className="master-state"><h3>No inspection plans</h3><p>Change the filters or create the first supported plan.</p></div>}
    {plans.status === "ready" && plans.data.items.length > 0 && <>
      <p className="compound-scroll-hint">Scroll horizontally to view all plan fields.</p>
      <div className="master-table-wrap" role="region" aria-label="Scrollable inspection plans" tabIndex={0}><table className="master-table" aria-label="Inspection plans" style={{ minWidth: 820 }}><thead><tr><th>Item</th><th>Plan</th><th>Type</th><th>Parameters</th><th>Created</th><th>Status</th><th>Action</th></tr></thead><tbody>{plans.data.items.map(plan => <tr key={plan.plan_id}><td><strong>{plan.item_code}</strong></td><td>{plan.name}</td><td>{kindLabel(plan.kind)}</td><td>{plan.params.length} {plan.params.length === 1 ? "parameter" : "parameters"}</td><td>{plan.created_at.slice(0, 10)}</td><td>{status(plan.active)}</td><td><Link className="icon-link" aria-label={`Open ${plan.name}`} to={`/app/quality/plans/${plan.plan_id}`}><ArrowUpRight size={17} /></Link></td></tr>)}</tbody></table></div>
      <ul className="master-cards" aria-label="Inspection plan cards">{plans.data.items.map(plan => <li key={plan.plan_id}><div><span>{plan.item_code}</span>{status(plan.active)}</div><h3>{plan.name}</h3><dl><div><dt>Type</dt><dd>{kindLabel(plan.kind)}</dd></div><div><dt>Parameters</dt><dd>{plan.params.length}</dd></div><div><dt>Created</dt><dd>{plan.created_at.slice(0, 10)}</dd></div></dl><Link className="button button--secondary" aria-label={`Open ${plan.name}`} to={`/app/quality/plans/${plan.plan_id}`}>Open plan</Link></li>)}</ul>
      <div className="reference-pagination"><p className="result-summary" aria-live="polite">Showing {first}–{last} of {total} records</p>{total > PAGE_SIZE && <div><button className="button button--secondary" type="button" aria-label="Previous page" disabled={offset === 0} onClick={() => filter("offset", String(Math.max(0, offset - PAGE_SIZE)), true)}><ChevronLeft size={16} /></button><span>Page {Math.floor(offset / PAGE_SIZE) + 1}</span><button className="button button--secondary" type="button" aria-label="Next page" disabled={offset + PAGE_SIZE >= total} onClick={() => filter("offset", String(offset + PAGE_SIZE), true)}><ChevronRight size={16} /></button></div>}</div>
    </>}
  </RegisterPageTemplate>;
}

export function InspectionPlanDetail({ id, me }: { id: string; me: Me }) {
  const [searchParams] = useSearchParams();
  const [plan, setPlan] = useState<Load<InspectionPlan>>({ status: "loading" });
  const [confirming, setConfirming] = useState(false);
  const [saving, setSaving] = useState(false);
  const [actionError, setActionError] = useState<Error | null>(null);
  useEffect(() => { const controller = new AbortController(); void fetchInspectionPlan(id, controller.signal).then(data => { if (!controller.signal.aborted) setPlan({ status: "ready", data }); }).catch(value => { if (!controller.signal.aborted) setPlan({ status: "error", error: failure(value, "Inspection plan could not be loaded.") }); }); return () => controller.abort(); }, [id]);
  async function retire() { setSaving(true); setActionError(null); try { const updated = await retireInspectionPlan(id); setPlan({ status: "ready", data: updated }); setConfirming(false); } catch (value) { setActionError(failure(value, "Inspection plan could not be retired.")); } finally { setSaving(false); } }
  if (plan.status === "loading") return <section className="master-detail"><QualityTabs active="plans" /><div className="master-state" role="status"><span className="spinner" /><p>Loading inspection plan…</p></div></section>;
  if (plan.status === "error") return <section className="master-detail"><QualityTabs active="plans" /><RequestError error={plan.error} /></section>;
  const value = plan.data;
  return <DetailPageTemplate className="inspection-plan-detail" title={value.name} description={`${value.item_code} · ${kindLabel(value.kind)} inspection`} navigation={<QualityTabs active="plans" />} back={<Link className="back-link" to="/app/quality/plans"><ArrowLeft size={16} />Inspection plans</Link>} status={status(value.active)} actions={value.active && me.permissions.includes("qa.approve") ? <button className="button button--danger" type="button" onClick={() => setConfirming(true)}><Trash2 size={16} />Retire plan</button> : undefined}>
    {searchParams.get("created") === "1" && <p className="success-banner" role="status">Inspection plan created.</p>}
    {!value.active && <p className="info-banner">Retired plans are read-only and are retained as quality history.</p>}
    <section className="record-panel"><div className="record-panel__heading"><h3>Inspection parameters</h3><p>{value.params.length} configured parameter{value.params.length === 1 ? "" : "s"}</p></div><div className="master-table-wrap" role="region" aria-label="Scrollable inspection parameters" tabIndex={0} style={{ display: "block" }}><table className="master-table" aria-label="Inspection parameters" style={{ minWidth: 760, tableLayout: "auto" }}><thead><tr><th>Code</th><th>Parameter</th><th>Unit</th><th>Minimum</th><th>Maximum</th><th>Characteristic</th></tr></thead><tbody>{value.params.map(parameter => <tr key={parameter.parameter_code}><td><strong style={{ whiteSpace: "nowrap" }}>{parameter.parameter_code}</strong></td><td>{parameter.name}</td><td>{parameter.unit ?? "Not set"}</td><td>{parameter.min_value ?? "No minimum"}</td><td>{parameter.max_value ?? "No maximum"}</td><td>{parameter.is_key ? "Key characteristic" : "Standard"}</td></tr>)}</tbody></table></div></section>
    {confirming && <div className="confirmation-backdrop"><section className="confirmation-panel" role="dialog" aria-modal="true" aria-labelledby="retire-plan-title"><div><ShieldAlert size={22} /><div><h3 id="retire-plan-title">Retire inspection plan?</h3><p>This plan cannot be used for new inspections after retirement. Existing inspection history is preserved.</p></div></div>{actionError && <RequestError error={actionError} />}<div><button className="button button--secondary" type="button" disabled={saving} onClick={() => setConfirming(false)}>Cancel</button><button className="button button--danger" type="button" disabled={saving} onClick={() => void retire()}>{saving ? "Retiring…" : "Confirm retirement"}</button></div></section></div>}
  </DetailPageTemplate>;
}

type DraftParameter = InspectionPlanParameter;
const emptyParameter = (): DraftParameter => ({ parameter_code: "", name: "", unit: "", min_value: null, max_value: null, is_key: false });
export function InspectionPlanCreatePage({ me }: { me: Me }) {
  const navigate = useNavigate();
  const [items, setItems] = useState<Load<Item[]>>({ status: "loading" });
  const [itemId, setItemId] = useState("");
  const [kind, setKind] = useState<InspectionPlanKind>("INCOMING");
  const [name, setName] = useState("");
  const [parameters, setParameters] = useState<DraftParameter[]>([emptyParameter()]);
  const [error, setError] = useState<Error | null>(null);
  const [validation, setValidation] = useState("");
  const [saving, setSaving] = useState(false);
  useEffect(() => { const controller = new AbortController(); void fetchAllItems(controller.signal).then(data => { if (!controller.signal.aborted) setItems({ status: "ready", data: data.filter(item => item.active) }); }).catch(value => { if (!controller.signal.aborted) setItems({ status: "error", error: failure(value, "Item options could not be loaded.") }); }); return () => controller.abort(); }, []);
  const activeItems = useMemo(() => items.status === "ready" ? items.data : [], [items]);
  function change(index: number, field: keyof DraftParameter, value: string | boolean | null) { setParameters(current => current.map((parameter, row) => row === index ? { ...parameter, [field]: value } : parameter)); setValidation(""); }
  async function submit(event: FormEvent) {
    event.preventDefault(); setValidation(""); setError(null);
    const codes = parameters.map(parameter => parameter.parameter_code.trim().toUpperCase());
    if (parameters.some(parameter => parameter.min_value === null && parameter.max_value === null)) return setValidation("Every parameter needs a minimum or maximum limit.");
    if (parameters.some(parameter => (parameter.min_value !== null && !decimalPattern.test(parameter.min_value)) || (parameter.max_value !== null && !decimalPattern.test(parameter.max_value)))) return setValidation("Limits must be decimal values with no more than six decimal places.");
    if (parameters.some(parameter => parameter.min_value !== null && parameter.max_value !== null && compareDecimals(parameter.max_value, parameter.min_value) < 0)) return setValidation("A maximum cannot be below its minimum.");
    if (new Set(codes).size !== codes.length) return setValidation("Each parameter code may appear only once; remove the duplicate.");
    const body: InspectionPlanCreate = { item_id: itemId, kind, name, params: parameters.map(parameter => ({ ...parameter, unit: parameter.unit?.trim() || null })) };
    setSaving(true); try { const created = await createInspectionPlan(body); navigate(`/app/quality/plans/${created.plan_id}?created=1`); } catch (value) { setError(failure(value, "Inspection plan could not be created.")); } finally { setSaving(false); }
  }
  if (!me.permissions.includes("qa.approve")) return <section className="master-state"><h2>Permission required</h2><p>The qa.approve permission is required to create inspection plans.</p></section>;
  return <FormPageTemplate className="inspection-plan-create" title="Create inspection plan" description="Define ordered measurement parameters. Creating the same item/type combination retires the previous active plan." navigation={<QualityTabs active="plans" />} back={<Link className="back-link" to="/app/quality/plans"><ArrowLeft size={16} />Inspection plans</Link>} formTitle="Plan definition" onSubmit={submit} feedback={<>{validation && <p className="error-banner" role="alert">{validation}</p>}{error && <RequestError error={error} />}</>} formActions={<><Link className="button button--secondary" to="/app/quality/plans">Cancel</Link><button className="button button--primary" type="submit" disabled={saving || items.status !== "ready"}>{saving ? "Creating…" : "Create plan"}</button></>}>
    <div className="record-form__grid"><div className="field"><label htmlFor="plan-create-item">Item</label><select id="plan-create-item" required value={itemId} onChange={event => setItemId(event.target.value)}><option value="" disabled>Select an active item</option>{activeItems.map(item => <option key={item.item_id} value={item.item_id}>{item.code} — {item.name}</option>)}</select></div><div className="field"><label htmlFor="plan-create-kind">Inspection type</label><select id="plan-create-kind" value={kind} onChange={event => setKind(event.target.value as InspectionPlanKind)}>{kinds.map(option => <option key={option.value} value={option.value}>{option.label}</option>)}</select></div><div className="field field--wide"><label htmlFor="plan-create-name">Plan name</label><input id="plan-create-name" required minLength={2} maxLength={200} value={name} onChange={event => setName(event.target.value)} /></div></div>
    <div className="record-form__heading"><h3>Parameters</h3><div><button className="button button--secondary" type="button" disabled={parameters.length >= 60} onClick={() => setParameters(current => [...current, emptyParameter()])}><Plus size={16} />Add parameter</button></div></div>
    <div className="inspection-parameter-list">{parameters.map((parameter, index) => <fieldset className="bom-editor__line" key={index}><legend>Parameter {index + 1}</legend><div className="record-form__grid"><div className="field"><label htmlFor={`parameter-code-${index}`}>Parameter code {index + 1}</label><input id={`parameter-code-${index}`} required maxLength={40} value={parameter.parameter_code} onChange={event => change(index, "parameter_code", event.target.value)} /></div><div className="field"><label htmlFor={`parameter-name-${index}`}>Parameter name {index + 1}</label><input id={`parameter-name-${index}`} required minLength={2} maxLength={120} value={parameter.name} onChange={event => change(index, "name", event.target.value)} /></div><div className="field"><label htmlFor={`parameter-unit-${index}`}>Unit {index + 1}</label><input id={`parameter-unit-${index}`} maxLength={20} value={parameter.unit ?? ""} onChange={event => change(index, "unit", event.target.value)} /></div><div className="field"><label htmlFor={`parameter-min-${index}`}>Minimum {index + 1}</label><input id={`parameter-min-${index}`} inputMode="decimal" value={parameter.min_value ?? ""} onChange={event => change(index, "min_value", event.target.value || null)} /></div><div className="field"><label htmlFor={`parameter-max-${index}`}>Maximum {index + 1}</label><input id={`parameter-max-${index}`} inputMode="decimal" value={parameter.max_value ?? ""} onChange={event => change(index, "max_value", event.target.value || null)} /></div><div className="field checkbox-field"><label><input aria-label={`Key characteristic ${index + 1}`} type="checkbox" checked={parameter.is_key} onChange={event => change(index, "is_key", event.target.checked)} />Key characteristic</label></div></div>{parameters.length > 1 && <button className="button button--secondary" type="button" onClick={() => setParameters(current => current.filter((_, row) => row !== index))}>Remove parameter {index + 1}</button>}</fieldset>)}</div>
  </FormPageTemplate>;
}
