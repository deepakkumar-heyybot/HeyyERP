import { FormEvent, useEffect, useMemo, useRef, useState } from "react";
import { AlertCircle, ArrowLeft, ArrowUpRight, Building2, Network, Pencil, Plus, RefreshCw, X } from "lucide-react";
import { Link, useLocation, useNavigationType, useSearchParams } from "react-router-dom";
import {
  ApiError,
  Department,
  DepartmentCreate,
  DepartmentUpdate,
  Me,
  Unit,
  createDepartment,
  fetchAllDepartments,
  fetchAllOrganizationUnits,
  fetchDepartment,
  updateDepartment,
} from "../api-client";
import { OrganizationTabs } from "./OrganizationTabs";

type ListState = { status: "loading" } | { status: "ready"; items: Department[] } | { status: "error"; error: Error };
type DetailState = { status: "loading" } | { status: "ready"; item: Department } | { status: "error"; error: Error };

function StatusBadge({ active }: { active: boolean }) {
  return <span className={`status-badge status-badge--${active ? "approved" : "cancelled"}`}>{active ? "Active" : "Inactive"}</span>;
}

function RequestError({ error }: { error: Error }) {
  return <div className="decision-error" role="alert"><AlertCircle size={17} /><div><span>{error.message}</span>{error instanceof ApiError && error.status >= 500 && error.requestId && <span>Request ID: <code>{error.requestId}</code></span>}</div></div>;
}

function DepartmentForm({
  mode, item, plants, departments, busy, error, onCancel, onCreate, onUpdate,
}: {
  mode: "create" | "edit";
  item?: Department;
  plants: Unit[];
  departments: Department[];
  busy: boolean;
  error: Error | null;
  onCancel: () => void;
  onCreate?: (body: DepartmentCreate) => Promise<void>;
  onUpdate?: (body: DepartmentUpdate) => Promise<void>;
}) {
  const [code, setCode] = useState(item?.code ?? "");
  const [name, setName] = useState(item?.name ?? "");
  const [unitId, setUnitId] = useState(item?.unit_id ?? "");
  const [parentId, setParentId] = useState(item?.parent_department_id ?? "");
  const [headRole, setHeadRole] = useState(item?.head_role_code ?? "");
  const [active, setActive] = useState(item?.active ?? true);
  const parents = departments.filter(department => department.department_id !== item?.department_id && department.active && (!unitId || department.unit_id === unitId || department.unit_id === null));

  async function submit(event: FormEvent) {
    event.preventDefault();
    const common = { name: name.trim(), unit_id: unitId || null, parent_department_id: parentId || null, head_role_code: headRole.trim().toUpperCase() || null };
    if (mode === "create") await onCreate?.({ ...common, code: code.trim().toUpperCase() });
    else await onUpdate?.({ ...common, active });
  }

  return (
    <form className="record-form" onSubmit={submit}>
      <div className="record-form__heading"><div><p className="eyebrow">{mode === "create" ? "New department" : item?.code}</p><h3>{mode === "create" ? "Create department" : "Edit department"}</h3></div><button type="button" aria-label="Close department form" onClick={onCancel} disabled={busy}><X size={20} /></button></div>
      <div className="record-form__grid">
        <div className="field"><label htmlFor={`${mode}-department-code`}>Department code</label><input id={`${mode}-department-code`} value={code} onChange={event => setCode(event.target.value)} required maxLength={30} readOnly={mode === "edit"} /></div>
        <div className="field"><label htmlFor={`${mode}-department-name`}>Department name</label><input id={`${mode}-department-name`} value={name} onChange={event => setName(event.target.value)} required maxLength={120} /></div>
        <div className="field"><label htmlFor={`${mode}-department-plant`}>Plant</label><select id={`${mode}-department-plant`} value={unitId} onChange={event => { setUnitId(event.target.value); setParentId(""); }}><option value="">Company-wide</option>{plants.filter(plant => plant.active || plant.unit_id === item?.unit_id).map(plant => <option key={plant.unit_id} value={plant.unit_id}>{plant.name}</option>)}</select></div>
        <div className="field"><label htmlFor={`${mode}-department-parent`}>Parent department</label><select id={`${mode}-department-parent`} value={parentId} onChange={event => setParentId(event.target.value)}><option value="">No parent</option>{parents.map(department => <option key={department.department_id} value={department.department_id}>{department.code} · {department.name}</option>)}</select></div>
        <div className="field"><label htmlFor={`${mode}-department-role`}>Head role code</label><input id={`${mode}-department-role`} value={headRole} onChange={event => setHeadRole(event.target.value)} maxLength={50} placeholder="Optional role code" /></div>
      </div>
      {mode === "edit" && <label className="checkbox-field"><input type="checkbox" checked={active} onChange={event => setActive(event.target.checked)} /><span><strong>Active department</strong><small>Deactivate child departments first.</small></span></label>}
      {error && <RequestError error={error} />}
      <div className="record-form__actions"><button className="button button--secondary" type="button" onClick={onCancel} disabled={busy}>Cancel</button><button className="button button--primary" type="submit" disabled={busy}>{busy ? "Saving…" : mode === "create" ? "Save department" : "Save changes"}</button></div>
    </form>
  );
}

function DepartmentList({ me }: { me: Me }) {
  const [searchParams, setSearchParams] = useSearchParams();
  const navigationType = useNavigationType();
  const pendingParams = useRef(new URLSearchParams(searchParams));
  useEffect(() => { if (navigationType !== "REPLACE") pendingParams.current = new URLSearchParams(searchParams); }, [navigationType, searchParams]);
  const [state, setState] = useState<ListState>({ status: "loading" });
  const [plants, setPlants] = useState<Unit[]>([]);
  const [attempt, setAttempt] = useState(0);
  const [creating, setCreating] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<Error | null>(null);
  const [notice, setNotice] = useState("");
  const canManage = me.permissions.includes("org.manage");
  const activeParam = searchParams.get("active") ?? "";
  const unitId = searchParams.get("unit_id") ?? "";
  const query = searchParams.get("q") ?? "";
  const active = activeParam === "true" ? true : activeParam === "false" ? false : null;

  useEffect(() => {
    const controller = new AbortController();
    setState({ status: "loading" });
    Promise.all([fetchAllDepartments(active, unitId || null, controller.signal), fetchAllOrganizationUnits(controller.signal)]).then(([items, allPlants]) => {
      setPlants(allPlants); setState({ status: "ready", items });
    }).catch(problem => { if (!controller.signal.aborted) setState({ status: "error", error: problem instanceof Error ? problem : new Error("Departments could not be loaded.") }); });
    return () => controller.abort();
  }, [active, attempt, unitId]);

  function setFilter(key: string, value: string) {
    const next = new URLSearchParams(pendingParams.current);
    if (value) next.set(key, value); else next.delete(key);
    pendingParams.current = next;
    setSearchParams(next, { replace: true });
  }
  const items = state.status === "ready" ? state.items : [];
  const filtered = useMemo(() => {
    const normalized = query.trim().toLowerCase();
    return items.filter(item => {
      if (unitId && item.unit_id !== unitId) return false;
      if (active !== null && item.active !== active) return false;
      return !normalized || `${item.code} ${item.name}`.toLowerCase().includes(normalized);
    });
  }, [active, items, query, unitId]);
  const plantName = (id: string | null) => id ? plants.find(plant => plant.unit_id === id)?.name ?? "Unknown plant" : "Company-wide";
  const departmentName = (id: string | null) => id ? items.find(item => item.department_id === id)?.name ?? "Unknown department" : "No parent";

  async function submitCreate(body: DepartmentCreate) {
    setSaving(true); setError(null);
    try { await createDepartment(body); setCreating(false); setNotice("Department created."); setAttempt(value => value + 1); }
    catch (problem) { setError(problem instanceof Error ? problem : new Error("The department could not be created.")); }
    finally { setSaving(false); }
  }

  return (
    <section className="master-list" aria-labelledby="departments-title">
      <OrganizationTabs active="departments" />
      <div className="page-heading-row"><div><p className="eyebrow">Organization master</p><h2 id="departments-title">Departments</h2><p>Plant and company-wide departments, hierarchy, approval head, and status.</p></div>{canManage && <button className="button button--primary" type="button" onClick={() => { setCreating(true); setError(null); }}><Plus size={17} />Create department</button>}</div>
      {notice && <p className="success-banner" role="status">{notice}</p>}
      {creating && <div className="form-panel"><DepartmentForm mode="create" plants={plants} departments={items} busy={saving} error={error} onCancel={() => setCreating(false)} onCreate={submitCreate} /></div>}
      <div className="master-filters">
        <div className="field"><label htmlFor="department-search">Search departments</label><input id="department-search" value={query} onChange={event => setFilter("q", event.target.value)} placeholder="Code or name" /></div>
        <div className="field"><label htmlFor="department-plant-filter">Plant filter</label><select id="department-plant-filter" value={unitId} onChange={event => setFilter("unit_id", event.target.value)}><option value="">All plants</option>{plants.map(plant => <option key={plant.unit_id} value={plant.unit_id}>{plant.name}</option>)}</select></div>
        <div className="field"><label htmlFor="department-status-filter">Status filter</label><select id="department-status-filter" value={activeParam} onChange={event => setFilter("active", event.target.value)}><option value="">All statuses</option><option value="true">Active</option><option value="false">Inactive</option></select></div>
        <button className="button button--secondary" type="button" onClick={() => setAttempt(value => value + 1)} disabled={state.status === "loading"}><RefreshCw size={16} />Refresh</button>
      </div>
      {state.status === "loading" && <div className="master-state"><span className="spinner" /><p>Loading departments…</p></div>}
      {state.status === "error" && <div className="inline-error"><AlertCircle size={20} /><div><h3>Departments unavailable</h3><p>{state.error.message}</p></div><button className="button button--secondary" onClick={() => setAttempt(value => value + 1)}>Try again</button></div>}
      {state.status === "ready" && filtered.length === 0 && <div className="master-state"><Network size={28} /><h3>No departments found</h3><p>Change the filters or search term.</p></div>}
      {state.status === "ready" && filtered.length > 0 && <>
        <div className="master-table-wrap"><table className="master-table department-table" aria-label="Department results"><thead><tr><th>Code</th><th>Department</th><th>Plant</th><th>Parent</th><th>Head role</th><th>Status</th><th><span className="sr-only">Actions</span></th></tr></thead><tbody>{filtered.map(item => <tr key={item.department_id}><td><strong>{item.code}</strong></td><td>{item.name}</td><td>{plantName(item.unit_id)}</td><td>{departmentName(item.parent_department_id)}</td><td>{item.head_role_code || "—"}</td><td><StatusBadge active={item.active} /></td><td><Link className="icon-link" to={`/app/master-data/organization/departments/${item.department_id}`} aria-label={`Open department ${item.name}`}><ArrowUpRight size={17} /></Link></td></tr>)}</tbody></table></div>
        <ul className="master-cards" aria-label="Department results">{filtered.map(item => <li key={item.department_id}><div><span>{item.code}</span><StatusBadge active={item.active} /></div><h3>{item.name}</h3><dl><div><dt>Plant</dt><dd>{plantName(item.unit_id)}</dd></div><div><dt>Parent</dt><dd>{departmentName(item.parent_department_id)}</dd></div><div><dt>Head role</dt><dd>{item.head_role_code || "—"}</dd></div></dl><Link className="button button--secondary" to={`/app/master-data/organization/departments/${item.department_id}`} aria-label={`Open department ${item.name}`}>Open department<ArrowUpRight size={16} /></Link></li>)}</ul>
        <p className="result-summary" aria-live="polite">Showing {filtered.length} of {items.length} departments</p>
      </>}
    </section>
  );
}

function DepartmentDetail({ me, departmentId }: { me: Me; departmentId: string }) {
  const [state, setState] = useState<DetailState>({ status: "loading" });
  const [departments, setDepartments] = useState<Department[]>([]);
  const [plants, setPlants] = useState<Unit[]>([]);
  const [attempt, setAttempt] = useState(0);
  const [editing, setEditing] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<Error | null>(null);
  const [notice, setNotice] = useState("");
  const canManage = me.permissions.includes("org.manage");
  const headingRef = useRef<HTMLHeadingElement>(null);
  useEffect(() => {
    const controller = new AbortController();
    setState({ status: "loading" });
    Promise.all([fetchDepartment(departmentId, controller.signal), fetchAllDepartments(null, null, controller.signal), fetchAllOrganizationUnits(controller.signal)]).then(([item, allDepartments, allPlants]) => {
      setDepartments(allDepartments); setPlants(allPlants); setState({ status: "ready", item });
    }).catch(problem => { if (!controller.signal.aborted) setState({ status: "error", error: problem instanceof Error ? problem : new Error("The department could not be loaded.") }); });
    return () => controller.abort();
  }, [attempt, departmentId]);
  useEffect(() => { if (state.status !== "loading") headingRef.current?.focus(); }, [state.status]);
  async function submitUpdate(body: DepartmentUpdate) {
    setSaving(true); setError(null);
    try { const item = await updateDepartment(departmentId, body); setState({ status: "ready", item }); setEditing(false); setNotice("Department updated."); }
    catch (problem) { setError(problem instanceof Error ? problem : new Error("The department could not be updated.")); }
    finally { setSaving(false); }
  }
  if (state.status === "loading") return <div className="master-state"><span className="spinner" /><p>Loading department…</p></div>;
  if (state.status === "error") return <div className="master-state"><AlertCircle size={28} /><h2 ref={headingRef} tabIndex={-1}>Department unavailable</h2><p>{state.error.message}</p><button className="button button--secondary" onClick={() => setAttempt(value => value + 1)}>Try again</button></div>;
  const item = state.item;
  const plant = item.unit_id ? plants.find(candidate => candidate.unit_id === item.unit_id)?.name ?? "Unknown plant" : "Company-wide";
  const parent = item.parent_department_id ? departments.find(candidate => candidate.department_id === item.parent_department_id)?.name ?? "Unknown department" : "No parent";
  return <section className="master-detail" aria-labelledby="department-detail-title"><OrganizationTabs active="departments" /><div className="master-detail__heading"><div><Link to="/app/master-data/organization/departments"><ArrowLeft size={16} />Back to departments</Link><p className="eyebrow">{item.code}</p><h2 id="department-detail-title" ref={headingRef} tabIndex={-1}>{item.name}</h2><StatusBadge active={item.active} /></div>{canManage && <div><button className="button button--secondary" type="button" onClick={() => { setEditing(true); setError(null); }}><Pencil size={16} />Edit department</button></div>}</div>{notice && <p className="success-banner" role="status">{notice}</p>}{editing && <div className="form-panel"><DepartmentForm mode="edit" item={item} plants={plants} departments={departments} busy={saving} error={error} onCancel={() => setEditing(false)} onUpdate={submitUpdate} /></div>}<section className="record-panel"><div className="record-panel__heading"><div><h3>Department details</h3><p>Organization scope and approval hierarchy.</p></div><Building2 size={20} /></div><dl className="record-facts department-facts"><div><dt>Code</dt><dd>{item.code}</dd></div><div><dt>Plant</dt><dd>{plant}</dd></div><div><dt>Parent</dt><dd>{parent}</dd></div><div><dt>Head role</dt><dd>{item.head_role_code || "Not assigned"}</dd></div><div><dt>Status</dt><dd>{item.active ? "Active" : "Inactive"}</dd></div></dl></section></section>;
}

export function OrganizationDepartments({ me }: { me: Me }) {
  const location = useLocation();
  const prefix = "/app/master-data/organization/departments/";
  const departmentId = location.pathname.startsWith(prefix) ? location.pathname.slice(prefix.length) : "";
  return departmentId ? <DepartmentDetail me={me} departmentId={departmentId} /> : <DepartmentList me={me} />;
}
