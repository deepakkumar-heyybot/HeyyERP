import { FormEvent, KeyboardEvent, useEffect, useMemo, useRef, useState } from "react";
import { AlertCircle, ArrowLeft, ArrowUpRight, ChevronLeft, ChevronRight, ClipboardList, Plus, RefreshCw, X } from "lucide-react";
import { Link, useLocation, useNavigate, useSearchParams } from "react-router-dom";
import {
  ApiError, Department, Indent, IndentCreate as IndentCreateBody, IndentSummary, IndentType, Item, ItemType, Me, Unit,
  cancelIndent, createIndent, fetchAllActiveItemsOfTypes, fetchAllDepartments, fetchAllIndents, fetchAllOrganizationUnits,
  fetchIndent, listIndentsPage, submitIndent, updateIndent,
} from "../api-client";
import { useActivePlant } from "../plant-context";
import { ProcurementTabs } from "./ProcurementTabs";

const PATH = "/app/procurement/indents";
const PAGE_SIZE = 20;
const TYPES: IndentType[] = ["BOM", "CONSUMABLE", "CAPITAL"];
const STATUSES = ["DRAFT", "PENDING_APPROVAL", "APPROVED", "REJECTED", "ORDERED", "CANCELLED"];
const ITEM_TYPES: Record<IndentType, ItemType[]> = { BOM: ["RM"], CONSUMABLE: ["CONSUMABLE", "SPARE", "OTHER"], CAPITAL: ["CAPITAL"] };
type ListState = { status: "loading" } | { status: "error"; error: Error } | { status: "ready"; items: IndentSummary[]; total: number };
type Resource<T> = { status: "loading" } | { status: "error"; error: Error } | { status: "ready"; data: T };
type LineDraft = { key: number; itemId: string; quantity: string; purpose: string; description: string; value: string };
const blankLine = (key: number): LineDraft => ({ key, itemId: "", quantity: "", purpose: "", description: "", value: "" });
const problem = (error: unknown, fallback: string) => error instanceof Error ? error : new Error(fallback);
const statusLabel = (value: string) => value.toLowerCase().split("_").map(word => word[0].toUpperCase() + word.slice(1)).join(" ");
const statusTone = (value: string) => value === "APPROVED" || value === "ORDERED" ? "approved" : value === "PENDING_APPROVAL" ? "pending" : value === "REJECTED" ? "rejected" : "cancelled";
const requestId = (error: Error) => error instanceof ApiError && error.status >= 500 && error.requestId ? <span>Request ID: {error.requestId}</span> : null;
const validDecimal = (value: string, places: number) => new RegExp(`^\\d{1,9}(?:\\.\\d{1,${places}})?$`).test(value) && !/^0+(?:\.0+)?$/.test(value);
const todayLocal = () => { const now = new Date(); return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}-${String(now.getDate()).padStart(2, "0")}`; };

function RequestError({ error }: { error: Error }) {
  return <div className="decision-error" role="alert"><AlertCircle size={17} /><div><span>{error.message}</span>{error instanceof ApiError && error.fieldErrors.map((field, index) => <span key={`${field.field}-${index}`}>{field.field ? `${field.field}: ` : ""}{field.message}</span>)}{requestId(error)}</div></div>;
}

function IndentList({ me }: { me: Me }) {
  const navigate = useNavigate();
  const location = useLocation();
  const { activePlantId } = useActivePlant();
  const unitId = activePlantId === "all" ? null : activePlantId;
  const [searchParams, setSearchParams] = useSearchParams();
  const rawStatus = searchParams.get("status") ?? "";
  const status = STATUSES.includes(rawStatus) ? rawStatus : null;
  const rawType = searchParams.get("type") ?? "";
  const indentType = TYPES.includes(rawType as IndentType) ? rawType as IndentType : null;
  const mine = searchParams.get("mine") === "true";
  const departmentId = searchParams.get("department") ?? "";
  const requester = searchParams.get("requester") ?? "";
  const from = searchParams.get("from") ?? "";
  const through = searchParams.get("through") ?? "";
  const clientFilter = Boolean(departmentId || requester || from || through);
  const currentPage = Math.max(1, Number(searchParams.get("page") ?? "1") || 1);
  const [state, setState] = useState<ListState>({ status: "loading" });
  const [departments, setDepartments] = useState<Resource<Department[]>>({ status: "loading" });
  const [attempt, setAttempt] = useState(0);
  const canRaise = me.permissions.includes("indent.raise");
  const created = (location.state as { created?: string } | null)?.created;

  useEffect(() => {
    const controller = new AbortController();
    void fetchAllDepartments(null, null, controller.signal)
      .then(data => { if (!controller.signal.aborted) setDepartments({ status: "ready", data: data.filter(department => !unitId || department.unit_id === null || department.unit_id === unitId) }); })
      .catch(error => { if (!controller.signal.aborted) setDepartments({ status: "error", error: problem(error, "Departments could not be loaded.") }); });
    return () => controller.abort();
  }, [unitId]);

  useEffect(() => {
    const controller = new AbortController();
    setState({ status: "loading" });
    const query = { status, indentType, unitId, mine };
    const load = clientFilter
      ? fetchAllIndents(query, controller.signal).then(items => ({ items, total: items.length }))
      : listIndentsPage({ ...query, limit: PAGE_SIZE, offset: (currentPage - 1) * PAGE_SIZE }, controller.signal).then(page => ({ items: page.items, total: page.total }));
    void load.then(data => { if (!controller.signal.aborted) setState({ status: "ready", ...data }); })
      .catch(error => { if (!controller.signal.aborted) setState({ status: "error", error: problem(error, "Indents could not be loaded.") }); });
    return () => controller.abort();
  }, [attempt, clientFilter, currentPage, indentType, mine, status, unitId]);

  const filtered = useMemo(() => state.status !== "ready" ? [] : clientFilter ? state.items.filter(item =>
    (!departmentId || item.department_id === departmentId)
    && (!requester || item.requester_name.toLocaleLowerCase().includes(requester.trim().toLocaleLowerCase()))
    && (!from || (item.needed_by !== null && item.needed_by >= from))
    && (!through || (item.needed_by !== null && item.needed_by <= through))) : state.items,
  [clientFilter, departmentId, from, requester, state, through]);
  const total = state.status === "ready" ? clientFilter ? filtered.length : state.total : 0;
  const visible = clientFilter ? filtered.slice((currentPage - 1) * PAGE_SIZE, currentPage * PAGE_SIZE) : filtered;
  const first = total ? (currentPage - 1) * PAGE_SIZE + 1 : 0;
  const last = Math.min(currentPage * PAGE_SIZE, total);
  const maxPage = Math.max(1, Math.ceil(total / PAGE_SIZE));
  const backQuery = searchParams.toString();
  const detailHref = (id: string) => `${PATH}/${id}${backQuery ? `?${backQuery}` : ""}`;

  function setParam(key: string, value: string, resetPage = true) {
    const next = new URLSearchParams(window.location.search);
    if (value) next.set(key, value); else next.delete(key);
    if (resetPage) next.delete("page");
    setSearchParams(next, { replace: true });
  }

  return <section className="master-list indent-list" aria-labelledby="indents-title">
    <ProcurementTabs active="indents" />
    <div className="page-heading-row"><div><h2 id="indents-title">Purchase indents</h2><p>Requests for raw materials, consumables, spares, and capital items.</p></div>{canRaise && <button className="button button--primary" type="button" onClick={() => navigate(`${PATH}/new${backQuery ? `?${backQuery}` : ""}`)}><Plus size={17} />New indent</button>}</div>
    {created && <p className="form-success" role="status">Indent {created} created as a draft.</p>}
    <div className="master-filters indent-filters">
      <div className="field"><label htmlFor="indent-status">Indent status</label><select id="indent-status" value={status ?? ""} onChange={event => setParam("status", event.target.value)}><option value="">All statuses</option>{STATUSES.map(value => <option key={value} value={value}>{statusLabel(value)}</option>)}</select></div>
      <div className="field"><label htmlFor="indent-type">Indent type</label><select id="indent-type" value={indentType ?? ""} onChange={event => setParam("type", event.target.value)}><option value="">All types</option>{TYPES.map(value => <option key={value} value={value}>{value === "BOM" ? "BOM materials" : statusLabel(value)}</option>)}</select></div>
      <div className="field"><label htmlFor="indent-department">Department</label><select id="indent-department" value={departmentId} onChange={event => setParam("department", event.target.value)}><option value="">All departments</option>{departments.status === "ready" && departments.data.map(department => <option key={department.department_id} value={department.department_id}>{department.code} — {department.name}</option>)}</select></div>
      <div className="field"><label htmlFor="indent-requester">Requester name</label><input id="indent-requester" type="search" value={requester} onChange={event => setParam("requester", event.target.value)} placeholder="Search requester" /></div>
      <div className="field"><label htmlFor="indent-from">Needed by from</label><input id="indent-from" type="date" value={from} onChange={event => setParam("from", event.target.value)} /></div>
      <div className="field"><label htmlFor="indent-through">Needed by through</label><input id="indent-through" type="date" value={through} min={from || undefined} onChange={event => setParam("through", event.target.value)} /></div>
      <div className="field"><label htmlFor="indent-mine">Requester scope</label><select id="indent-mine" value={mine ? "true" : ""} onChange={event => setParam("mine", event.target.value)}><option value="">All visible requesters</option><option value="true">My indents</option></select></div>
      <button className="button button--secondary" type="button" disabled={state.status === "loading"} onClick={() => setAttempt(value => value + 1)}><RefreshCw size={16} />Refresh</button>
    </div>
    {departments.status === "error" && <RequestError error={departments.error} />}
    {clientFilter && <p className="indent-list__filter-note">Department, requester name, and needed-by dates are filtered across all matching backend pages.</p>}
    {state.status === "loading" && <div className="master-state"><span className="spinner" /><p>Loading indents…</p></div>}
    {state.status === "error" && <div className="inline-error" role="alert"><AlertCircle size={20} /><div><h3>Indent register unavailable</h3><p>{state.error.message}</p>{requestId(state.error)}</div><button className="button button--secondary" type="button" onClick={() => setAttempt(value => value + 1)}>Try again</button></div>}
    {state.status === "ready" && visible.length === 0 && <div className="master-state"><ClipboardList size={28} /><h3>No indents found</h3><p>{status || indentType || clientFilter || mine ? "Adjust the filters to see other indents." : "No purchase indents were returned."}</p></div>}
    {state.status === "ready" && visible.length > 0 && <><p className="indent-scroll-hint">Scroll table horizontally to view more columns.</p><div className="master-table-wrap indent-table-wrap" role="region" aria-label="Scrollable indent results" tabIndex={0}><table className="master-table indent-table" aria-label="Indent results"><thead><tr><th>Indent no.</th><th>Plant</th><th>Type</th><th>Requester</th><th>Needed by</th><th>Status</th><th><span className="sr-only">Actions</span></th></tr></thead><tbody>{visible.map(item => <tr key={item.indent_id}><td><strong>{item.indent_no}</strong></td><td>{item.unit_code}</td><td>{item.indent_type}</td><td>{item.requester_name}</td><td>{item.needed_by ?? "Not set"}</td><td><span className={`status-badge status-badge--${statusTone(item.status)}`}>{statusLabel(item.status)}</span></td><td><Link className="icon-link" to={detailHref(item.indent_id)} aria-label={`Open indent ${item.indent_no}`}><ArrowUpRight size={17} /></Link></td></tr>)}</tbody></table></div><ul className="master-cards indent-cards" aria-label="Indent results">{visible.map(item => <li key={item.indent_id}><div><span>{item.indent_no}</span><span className={`status-badge status-badge--${statusTone(item.status)}`}>{statusLabel(item.status)}</span></div><h3>{item.indent_type} · {item.unit_code}</h3><dl><div><dt>Requester</dt><dd>{item.requester_name}</dd></div><div><dt>Needed by</dt><dd>{item.needed_by ?? "Not set"}</dd></div></dl><Link className="button button--secondary" to={detailHref(item.indent_id)} aria-label={`Open indent ${item.indent_no}`}>Open indent<ArrowUpRight size={16} /></Link></li>)}</ul><div className="reference-pagination"><p className="result-summary" aria-live="polite">Showing {first}–{last} of {total} records</p>{total > PAGE_SIZE && <div><button className="button button--secondary" type="button" aria-label="Previous page" disabled={currentPage === 1} onClick={() => setParam("page", String(currentPage - 1), false)}><ChevronLeft size={16} /></button><span>Page {currentPage} of {maxPage}</span><button className="button button--secondary" type="button" aria-label="Next page" disabled={currentPage >= maxPage} onClick={() => setParam("page", String(currentPage + 1), false)}><ChevronRight size={16} /></button></div>}</div></>}
  </section>;
}

function IndentCreate({ me }: { me: Me }) {
  const navigate = useNavigate();
  const location = useLocation();
  const { activeUnit } = useActivePlant();
  const [units, setUnits] = useState<Resource<Unit[]>>({ status: "loading" });
  const [departments, setDepartments] = useState<Resource<Department[]>>({ status: "loading" });
  const [items, setItems] = useState<Resource<Item[]>>({ status: "loading" });
  const [unitId, setUnitId] = useState(activeUnit?.unit_id ?? "");
  const [departmentId, setDepartmentId] = useState("");
  const [indentType, setIndentType] = useState<IndentType>("BOM");
  const [neededBy, setNeededBy] = useState("");
  const [lines, setLines] = useState<LineDraft[]>([blankLine(1)]);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<Error | null>(null);
  const back = `${PATH}${location.search}`;

  useEffect(() => {
    const controller = new AbortController();
    void fetchAllOrganizationUnits(controller.signal)
      .then(data => { if (!controller.signal.aborted) setUnits({ status: "ready", data: data.filter(unit => unit.active && (me.all_plants || me.unit_ids.includes(unit.unit_id))) }); })
      .catch(failure => { if (!controller.signal.aborted) setUnits({ status: "error", error: problem(failure, "Plants could not be loaded.") }); });
    return () => controller.abort();
  }, [me.all_plants, me.unit_ids]);
  useEffect(() => {
    if (!unitId) return;
    const controller = new AbortController();
    setDepartments({ status: "loading" });
    void fetchAllDepartments(true, null, controller.signal)
      .then(data => { if (!controller.signal.aborted) setDepartments({ status: "ready", data: data.filter(department => department.unit_id === null || department.unit_id === unitId) }); })
      .catch(failure => { if (!controller.signal.aborted) setDepartments({ status: "error", error: problem(failure, "Departments could not be loaded.") }); });
    return () => controller.abort();
  }, [unitId]);
  useEffect(() => {
    const controller = new AbortController();
    setItems({ status: "loading" });
    void fetchAllActiveItemsOfTypes(ITEM_TYPES[indentType], controller.signal)
      .then(data => { if (!controller.signal.aborted) setItems({ status: "ready", data }); })
      .catch(failure => { if (!controller.signal.aborted) setItems({ status: "error", error: problem(failure, "Items could not be loaded.") }); });
    return () => controller.abort();
  }, [indentType]);

  function updateLine(key: number, patch: Partial<LineDraft>) { setLines(current => current.map(line => line.key === key ? { ...line, ...patch } : line)); }
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(null);
    if (!unitId || lines.length < 1 || lines.length > 100 || lines.some(line => !line.itemId || !validDecimal(line.quantity, 3)
      || (indentType === "CAPITAL" && (!line.description.trim() || !validDecimal(line.value, 2)))
      || line.purpose.length > 200 || line.description.length > 200)
      || new Set(lines.map(line => line.itemId)).size !== lines.length) {
      setError(new Error("Select eligible, distinct items and enter valid quantities. Capital lines also need a description and positive value."));
      return;
    }
    if (neededBy && neededBy < todayLocal()) { setError(new Error("The needed-by date cannot be in the past.")); return; }
    const body: IndentCreateBody = {
      unit_id: unitId, department_id: departmentId || null, indent_type: indentType, needed_by: neededBy || null,
      lines: lines.map(line => ({ item_id: line.itemId, requested_qty: line.quantity, purpose: line.purpose.trim() || null,
        capital_description: indentType === "CAPITAL" ? line.description.trim() : null,
        capital_value: indentType === "CAPITAL" ? line.value : null })),
    };
    setSaving(true);
    try {
      const created = await createIndent(body);
      navigate(`${PATH}/${created.indent_id}${location.search}`, { state: { created: created.indent_no } });
    } catch (failure) { setError(problem(failure, "The indent could not be created.")); }
    finally { setSaving(false); }
  }

  return <section className="master-detail indent-create" aria-labelledby="indent-create-title"><ProcurementTabs active="indents" /><div className="master-detail__heading"><div><Link to={back}><ArrowLeft size={16} />Back to indents</Link><h2 id="indent-create-title">New indent</h2><p>Create a draft with at least one eligible item.</p></div></div>
    <div className="form-panel"><form className="record-form" onSubmit={submit}><div className="record-form__heading"><h3>Request details</h3></div>
      <div className="record-form__grid"><div className="field"><label htmlFor="new-indent-plant">Plant</label><select id="new-indent-plant" value={unitId} required onChange={event => { setUnitId(event.target.value); setDepartmentId(""); }}><option value="" disabled>Select plant</option>{units.status === "ready" && units.data.map(unit => <option key={unit.unit_id} value={unit.unit_id}>{unit.code} — {unit.name}</option>)}</select></div>
      <div className="field"><label htmlFor="new-indent-department">Department</label><select id="new-indent-department" value={departmentId} onChange={event => setDepartmentId(event.target.value)}><option value="">No department</option>{departments.status === "ready" && departments.data.map(department => <option key={department.department_id} value={department.department_id}>{department.code} — {department.name}</option>)}</select></div>
      <div className="field"><label htmlFor="new-indent-type">Indent type</label><select id="new-indent-type" value={indentType} onChange={event => { setIndentType(event.target.value as IndentType); setLines([blankLine(1)]); }}><option value="BOM">BOM materials</option><option value="CONSUMABLE">Consumables and spares</option><option value="CAPITAL">Capital</option></select></div>
      <div className="field"><label htmlFor="new-indent-needed">Needed by</label><input id="new-indent-needed" type="date" value={neededBy} min={todayLocal()} onChange={event => setNeededBy(event.target.value)} /></div></div>
      <div className="indent-create__lines"><div className="record-form__heading"><div><h3>Requested items</h3><p>Each item can appear once. Quantities use up to three decimal places.</p></div></div>
        {lines.map((line, index) => <div className="record-panel indent-create__line" key={line.key}><div className="record-panel__heading"><h4>Line {index + 1}</h4>{lines.length > 1 && <button className="button button--secondary" type="button" onClick={() => setLines(current => current.filter(item => item.key !== line.key))}>Remove line</button>}</div><div className="record-form__grid"><div className="field"><label htmlFor={`indent-item-${line.key}`}>Item</label><select id={`indent-item-${line.key}`} value={line.itemId} required onChange={event => updateLine(line.key, { itemId: event.target.value })}><option value="" disabled>Select item</option>{items.status === "ready" && items.data.map(item => <option key={item.item_id} value={item.item_id}>{item.code} — {item.name} ({item.uom_code})</option>)}</select></div><div className="field"><label htmlFor={`indent-qty-${line.key}`}>Requested quantity</label><input id={`indent-qty-${line.key}`} inputMode="decimal" required value={line.quantity} onChange={event => updateLine(line.key, { quantity: event.target.value })} /></div><div className="field"><label htmlFor={`indent-purpose-${line.key}`}>Purpose</label><input id={`indent-purpose-${line.key}`} maxLength={200} value={line.purpose} onChange={event => updateLine(line.key, { purpose: event.target.value })} /></div>{indentType === "CAPITAL" && <><div className="field"><label htmlFor={`indent-description-${line.key}`}>Capital description</label><input id={`indent-description-${line.key}`} maxLength={200} required value={line.description} onChange={event => updateLine(line.key, { description: event.target.value })} /></div><div className="field"><label htmlFor={`indent-value-${line.key}`}>Capital value</label><input id={`indent-value-${line.key}`} inputMode="decimal" required value={line.value} onChange={event => updateLine(line.key, { value: event.target.value })} /></div></>}</div></div>)}
        <button className="button button--secondary" type="button" disabled={lines.length >= 100} onClick={() => setLines(current => [...current, blankLine(Math.max(...current.map(line => line.key)) + 1)])}><Plus size={16} />Add line</button>
      </div>
      {units.status === "error" && <RequestError error={units.error} />}{departments.status === "error" && <RequestError error={departments.error} />}{items.status === "error" && <RequestError error={items.error} />}{items.status === "ready" && items.data.length === 0 && <div className="master-state"><ClipboardList size={24} /><h3>No eligible items</h3><p>Choose another type or ask a manager to create an active item.</p></div>}{error && <RequestError error={error} />}
      <div className="record-form__actions"><Link className="button button--secondary" to={back}>Cancel</Link><button className="button button--primary" type="submit" disabled={saving || units.status !== "ready" || items.status !== "ready" || items.data.length === 0}>{saving ? "Creating…" : "Create draft indent"}</button></div>
    </form></div></section>;
}

function IndentDetail({ id, me }: { id: string; me: Me }) {
  const location = useLocation();
  const [state, setState] = useState<Resource<Indent>>({ status: "loading" });
  const [attempt, setAttempt] = useState(0);
  const [owner, setOwner] = useState<Resource<boolean>>({ status: "loading" });
  const [editorOpen, setEditorOpen] = useState(false);
  const [cancelOpen, setCancelOpen] = useState(false);
  const [departments, setDepartments] = useState<Resource<Department[]>>({ status: "loading" });
  const [items, setItems] = useState<Resource<Item[]>>({ status: "loading" });
  const [departmentId, setDepartmentId] = useState("");
  const [neededBy, setNeededBy] = useState("");
  const [lines, setLines] = useState<LineDraft[]>([]);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<Error | null>(null);
  const [notice, setNotice] = useState("");
  const editRef = useRef<HTMLButtonElement>(null);
  const cancelRef = useRef<HTMLButtonElement>(null);
  const editorCloseRef = useRef<HTMLButtonElement>(null);
  const keepRef = useRef<HTMLButtonElement>(null);
  const returnEditFocus = useRef(false);
  const returnCancelFocus = useRef(false);
  useEffect(() => {
    const controller = new AbortController();
    setState({ status: "loading" }); setOwner({ status: "loading" }); setError(null);
    void fetchIndent(id, controller.signal)
      .then(data => { if (!controller.signal.aborted) setState({ status: "ready", data }); })
      .catch(error => { if (!controller.signal.aborted) setState({ status: "error", error: problem(error, "Indent could not be loaded.") }); });
    return () => controller.abort();
  }, [attempt, id]);

  useEffect(() => {
    if (state.status !== "ready") return;
    if (!me.permissions.includes("indent.raise")) { setOwner({ status: "ready", data: false }); return; }
    if (me.permissions.includes("admin")) { setOwner({ status: "ready", data: true }); return; }
    const controller = new AbortController();
    const indent = state.data;
    setOwner({ status: "loading" });
    void fetchAllIndents({ status: indent.status, indentType: indent.indent_type, unitId: indent.unit_id, mine: true }, controller.signal)
      .then(data => { if (!controller.signal.aborted) setOwner({ status: "ready", data: data.some(item => item.indent_id === id) }); })
      .catch(failure => { if (!controller.signal.aborted) setOwner({ status: "error", error: problem(failure, "Edit access could not be checked.") }); });
    return () => controller.abort();
  }, [attempt, id, me.permissions, state.status === "ready" ? state.data.indent_id : null]);

  useEffect(() => {
    if (!editorOpen || state.status !== "ready") return;
    const controller = new AbortController();
    setItems({ status: "loading" }); setDepartments({ status: "loading" });
    void fetchAllActiveItemsOfTypes(ITEM_TYPES[state.data.indent_type], controller.signal)
      .then(data => { if (!controller.signal.aborted) setItems({ status: "ready", data }); })
      .catch(failure => { if (!controller.signal.aborted) setItems({ status: "error", error: problem(failure, "Eligible items could not be loaded.") }); });
    void fetchAllDepartments(true, null, controller.signal)
      .then(data => { if (!controller.signal.aborted) setDepartments({ status: "ready", data: data.filter(department => department.unit_id === null || department.unit_id === state.data.unit_id) }); })
      .catch(failure => { if (!controller.signal.aborted) setDepartments({ status: "error", error: problem(failure, "Departments could not be loaded.") }); });
    return () => controller.abort();
  }, [editorOpen, state.status]);

  useEffect(() => {
    if (!editorOpen && !cancelOpen) return;
    const previous = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    if (editorOpen) editorCloseRef.current?.focus();
    if (cancelOpen) keepRef.current?.focus();
    return () => { document.body.style.overflow = previous; };
  }, [cancelOpen, editorOpen]);

  useEffect(() => {
    if (!editorOpen && returnEditFocus.current) { returnEditFocus.current = false; editRef.current?.focus(); }
    if (!cancelOpen && returnCancelFocus.current) { returnCancelFocus.current = false; cancelRef.current?.focus(); }
  }, [cancelOpen, editorOpen]);

  function dialogKeys(event: KeyboardEvent<HTMLElement>, close: () => void) {
    if (event.key === "Escape") { event.preventDefault(); close(); return; }
    if (event.key !== "Tab") return;
    const focusable = Array.from(event.currentTarget.querySelectorAll<HTMLElement>('button:not(:disabled), input:not(:disabled), select:not(:disabled), textarea:not(:disabled)'));
    if (!focusable.length) return;
    const first = focusable[0], last = focusable[focusable.length - 1];
    if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
    if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
  }
  function openEditor() {
    if (state.status !== "ready") return;
    setDepartmentId(state.data.department_id ?? ""); setNeededBy(state.data.needed_by ?? "");
    setLines(state.data.lines.map(line => ({ key: line.line_no, itemId: line.item_id, quantity: line.requested_qty,
      purpose: line.purpose ?? "", description: line.capital_description ?? "", value: line.capital_value ?? "" })));
    setError(null); setEditorOpen(true);
  }
  function closeEditor() { returnEditFocus.current = true; setEditorOpen(false); setError(null); }
  function closeCancel() { returnCancelFocus.current = true; setCancelOpen(false); setError(null); }
  function updateLine(key: number, patch: Partial<LineDraft>) { setLines(current => current.map(line => line.key === key ? { ...line, ...patch } : line)); }

  async function saveDraft(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (state.status !== "ready") return;
    const indentType = state.data.indent_type;
    setError(null);
    if (lines.length < 1 || lines.length > 100 || lines.some(line => !line.itemId || !validDecimal(line.quantity, 3)
      || (indentType === "CAPITAL" && (!line.description.trim() || !validDecimal(line.value, 2)))
      || line.purpose.length > 200 || line.description.length > 200)
      || new Set(lines.map(line => line.itemId)).size !== lines.length) {
      setError(new Error("Select eligible, distinct items and enter valid quantities. Capital lines also need a description and positive value."));
      return;
    }
    if (neededBy && neededBy < todayLocal()) { setError(new Error("The needed-by date cannot be in the past.")); return; }
    setSaving(true);
    try {
      const data = await updateIndent(id, { department_id: departmentId || null, needed_by: neededBy || null,
        lines: lines.map(line => ({ item_id: line.itemId, requested_qty: line.quantity, purpose: line.purpose.trim() || null,
          capital_description: indentType === "CAPITAL" ? line.description.trim() : null,
          capital_value: indentType === "CAPITAL" ? line.value : null })) });
      returnEditFocus.current = true; setState({ status: "ready", data }); setEditorOpen(false); setNotice("Draft indent saved.");
    } catch (failure) { setError(problem(failure, "Draft indent could not be saved.")); }
    finally { setSaving(false); }
  }
  async function submit() {
    setSaving(true); setError(null);
    try { const data = await submitIndent(id); setState({ status: "ready", data }); setNotice("Indent submitted for approval."); }
    catch (failure) { setError(problem(failure, "Indent could not be submitted.")); }
    finally { setSaving(false); }
  }
  async function confirmCancel() {
    setSaving(true); setError(null);
    try { const data = await cancelIndent(id); setState({ status: "ready", data }); setCancelOpen(false); setNotice("Indent cancelled."); }
    catch (failure) { setError(problem(failure, "Indent could not be cancelled.")); }
    finally { setSaving(false); }
  }

  const indent = state.status === "ready" ? state.data : null;
  const mayWrite = owner.status === "ready" && owner.data && me.permissions.includes("indent.raise");
  const canEdit = Boolean(indent && mayWrite && indent.status === "DRAFT");
  const canCancel = Boolean(indent && mayWrite && (indent.status === "DRAFT" || indent.status === "APPROVED"));
  const available = items.status === "ready" ? items.data : [];

  return <section className="master-detail indent-detail" aria-labelledby="indent-detail-title">
    <ProcurementTabs active="indents" />
    <div className="master-detail__heading"><div><Link to={`${PATH}${location.search}`}><ArrowLeft size={16} />Back to indents</Link><h2 id="indent-detail-title">{indent?.indent_no ?? "Indent detail"}</h2></div>{indent && <div className="indent-detail__actions">{canEdit && <><button className="button button--secondary" type="button" ref={editRef} disabled={saving} onClick={openEditor}>Edit draft</button><button className="button button--primary" type="button" disabled={saving || indent.lines.length === 0} onClick={submit}>Submit for approval</button></>}{canCancel && <button className="button button--secondary" type="button" ref={cancelRef} disabled={saving} onClick={() => { setError(null); setCancelOpen(true); }}>Cancel indent</button>}</div>}</div>
    {state.status === "loading" && <div className="master-state"><span className="spinner" /><p>Loading indent…</p></div>}
    {state.status === "error" && <div className="inline-error" role="alert"><AlertCircle size={20} /><div><h3>Indent unavailable</h3><p>{state.error.message}</p>{requestId(state.error)}</div><button className="button button--secondary" type="button" onClick={() => setAttempt(value => value + 1)}>Try again</button></div>}
    {owner.status === "error" && <RequestError error={owner.error} />}
    {notice && <p className="success-banner" role="status" aria-label="Indent status update">{notice}</p>}
    {error && !editorOpen && !cancelOpen && <RequestError error={error} />}
    {indent && <><section className="record-panel indent-detail__summary"><div className="record-panel__heading"><h3>Request summary</h3><span className={`status-badge status-badge--${statusTone(indent.status)}`}>{statusLabel(indent.status)}</span></div><dl className="record-facts"><div><dt>Plant</dt><dd>{indent.unit_code}</dd></div><div><dt>Type</dt><dd>{indent.indent_type}</dd></div><div><dt>Requester</dt><dd>{indent.requester_name}</dd></div><div><dt>Needed by</dt><dd>{indent.needed_by ?? "Not set"}</dd></div><div><dt>Created</dt><dd>{indent.created_at.slice(0, 10)}</dd></div><div><dt>Capital total</dt><dd>{indent.capital_total ?? "Not applicable"}</dd></div></dl></section>
      <section className="record-panel indent-detail__items"><div className="record-panel__heading"><div><h3>Requested items</h3><p>{indent.status === "DRAFT" ? "Draft lines can be updated by the requester." : "Submitted lines are read only."}</p></div></div><ol className="indent-detail__lines">{indent.lines.map(line => <li key={line.line_no}><div><strong>{line.item_code} — {line.item_name}</strong>{line.purpose && <span>{line.purpose}</span>}{line.capital_description && <span>{line.capital_description}</span>}</div><div><strong>{line.requested_qty} {line.uom_code}</strong>{line.capital_value !== null && <span>Value {line.capital_value}</span>}</div></li>)}</ol></section>
      <section className="record-panel indent-detail__approval"><div className="record-panel__heading"><h3>Approval and history</h3></div>{indent.approval_id ? me.modules.includes("approvals") ? <Link className="button button--secondary" to={`/app/approvals/${indent.approval_id}`}>View approval history<ArrowUpRight size={16} /></Link> : <p>Approval linked. Ask an authorized reviewer for the workflow history.</p> : <p>No approval is linked to this indent yet.</p>}</section></>}
    {editorOpen && indent && canEdit && <div className="bom-editor-backdrop"><section className="bom-editor indent-editor" role="dialog" aria-modal="true" aria-labelledby="indent-editor-title" onKeyDown={event => dialogKeys(event, closeEditor)}><div className="bom-editor__header"><div><p className="eyebrow">{indent.indent_no} · {indent.indent_type}</p><h3 id="indent-editor-title">Edit draft indent</h3><p>Save the complete set of lines in their displayed order.</p></div><button className="button button--secondary" type="button" ref={editorCloseRef} onClick={closeEditor} aria-label="Close line editor"><X size={18} /></button></div><form onSubmit={saveDraft}><div className="bom-editor__body"><div className="record-form__grid"><div className="field"><label htmlFor="edit-indent-department">Department</label><select id="edit-indent-department" value={departmentId} onChange={event => setDepartmentId(event.target.value)}><option value="">No department</option>{departments.status === "ready" && departments.data.map(department => <option key={department.department_id} value={department.department_id}>{department.code} — {department.name}</option>)}{departmentId && departments.status === "ready" && !departments.data.some(department => department.department_id === departmentId) && <option value={departmentId}>Current department (unavailable)</option>}</select></div><div className="field"><label htmlFor="edit-indent-needed">Needed by</label><input id="edit-indent-needed" type="date" value={neededBy} min={todayLocal()} onChange={event => setNeededBy(event.target.value)} /></div></div>
        {lines.map((line, index) => <fieldset key={line.key} className="bom-editor__line indent-editor__line"><legend>Line {index + 1}</legend><div className="record-form__grid"><div className="field"><label htmlFor={`edit-indent-item-${line.key}`}>Item</label><select id={`edit-indent-item-${line.key}`} required value={line.itemId} onChange={event => updateLine(line.key, { itemId: event.target.value })}><option value="" disabled>Select item</option>{available.map(item => <option key={item.item_id} value={item.item_id}>{item.code} — {item.name} ({item.uom_code})</option>)}{line.itemId && !available.some(item => item.item_id === line.itemId) && <option value={line.itemId}>{indent.lines.find(item => item.item_id === line.itemId)?.item_code ?? "Existing item"} (unavailable)</option>}</select></div><div className="field"><label htmlFor={`edit-indent-qty-${line.key}`}>Requested quantity</label><input id={`edit-indent-qty-${line.key}`} inputMode="decimal" required value={line.quantity} onChange={event => updateLine(line.key, { quantity: event.target.value })} /></div><div className="field"><label htmlFor={`edit-indent-purpose-${line.key}`}>Purpose</label><input id={`edit-indent-purpose-${line.key}`} maxLength={200} value={line.purpose} onChange={event => updateLine(line.key, { purpose: event.target.value })} /></div>{indent.indent_type === "CAPITAL" && <><div className="field"><label htmlFor={`edit-indent-description-${line.key}`}>Capital description</label><input id={`edit-indent-description-${line.key}`} maxLength={200} required value={line.description} onChange={event => updateLine(line.key, { description: event.target.value })} /></div><div className="field"><label htmlFor={`edit-indent-value-${line.key}`}>Capital value</label><input id={`edit-indent-value-${line.key}`} inputMode="decimal" required value={line.value} onChange={event => updateLine(line.key, { value: event.target.value })} /></div></>}</div>{lines.length > 1 && <button className="button button--secondary" type="button" onClick={() => setLines(current => current.filter(item => item.key !== line.key))}>Remove line</button>}</fieldset>)}
        <button className="button button--secondary" type="button" disabled={lines.length >= 100} onClick={() => setLines(current => [...current, blankLine(current.length ? Math.max(...current.map(line => line.key)) + 1 : 1)])}><Plus size={16} />Add line</button>{items.status === "error" && <RequestError error={items.error} />}{departments.status === "error" && <RequestError error={departments.error} />}{error && <RequestError error={error} />}</div><div className="bom-editor__actions"><button className="button button--secondary" type="button" onClick={closeEditor}>Cancel</button><button className="button button--primary" type="submit" disabled={saving || items.status !== "ready" || departments.status !== "ready"}>{saving ? "Saving…" : "Save draft"}</button></div></form></section></div>}
    {cancelOpen && indent && canCancel && <div className="bom-editor-backdrop"><section className="route-retire-dialog" role="dialog" aria-modal="true" aria-labelledby="indent-cancel-title" onKeyDown={event => dialogKeys(event, closeCancel)}><h3 id="indent-cancel-title">Cancel this indent?</h3><p>{indent.status === "APPROVED" ? "This approved indent will be cancelled if the backend allows it." : "This draft will be cancelled."} This action cannot be undone.</p>{error && <RequestError error={error} />}<div className="record-form__actions"><button className="button button--secondary" type="button" ref={keepRef} disabled={saving} onClick={closeCancel}>Keep indent</button><button className="button button--primary" type="button" disabled={saving} onClick={confirmCancel}>{saving ? "Cancelling…" : "Confirm cancellation"}</button></div></section></div>}
  </section>;
}

export function Indents({ me }: { me: Me }) {
  const location = useLocation();
  if (location.pathname === `${PATH}/new`) return me.permissions.includes("indent.raise") ? <IndentCreate me={me} /> : <IndentList me={me} />;
  if (location.pathname.startsWith(`${PATH}/`)) return <IndentDetail id={location.pathname.slice(PATH.length + 1)} me={me} />;
  return <IndentList me={me} />;
}
