import { FormEvent, KeyboardEvent, useEffect, useRef, useState } from "react";
import { AlertCircle, ArrowLeft, ArrowUpRight, Boxes, ChevronLeft, ChevronRight, Plus, RefreshCw } from "lucide-react";
import { Link, useLocation, useNavigate, useSearchParams } from "react-router-dom";
import {
  ApiError, Me, ProductionRoute, createProductionRoute, fetchProductionRoute,
  findActiveProductionRoute, listProductionRoutes, retireProductionRoute, reviseProductionRoute,
} from "../api-client";
import { EngineeringTabs } from "./EngineeringTabs";

const PAGE_SIZE = 20;
type RouteListState = { status: "loading" } | { status: "error"; error: Error } | { status: "ready"; routes: ProductionRoute[] };
type RouteResultState = { status: "idle" | "loading" } | { status: "found"; route: ProductionRoute } | { status: "missing" } | { status: "error"; error: Error };
type StageDraft = { key: number; code: string; name: string; qa: boolean };
const problem = (error: unknown, fallback: string) => error instanceof Error ? error : new Error(fallback);
const routeHref = (routeId: string, query = "") => `/app/master-data/engineering/routes/${routeId}${query ? `?${query}` : ""}`;
const codePattern = /^[A-Z0-9_-]{1,20}$/;

function RequestError({ error }: { error: Error }) {
  return <div className="decision-error" role="alert"><AlertCircle size={17} /><div><span>{error.message}</span>{error instanceof ApiError && error.status >= 500 && error.requestId && <span>Request ID: {error.requestId}</span>}</div></div>;
}

function RouteList({ me }: { me: Me }) {
  const navigate = useNavigate();
  const location = useLocation();
  const [searchParams, setSearchParams] = useSearchParams();
  const rawStatus = searchParams.get("status");
  const status = rawStatus === "all" || rawStatus === "inactive" ? rawStatus : "active";
  const q = searchParams.get("q") ?? "";
  const exactCode = searchParams.get("code") ?? "";
  const currentPage = Math.max(1, Number(searchParams.get("page") ?? "1") || 1);
  const [lookupInput, setLookupInput] = useState(exactCode);
  const [listState, setListState] = useState<RouteListState>({ status: "loading" });
  const [result, setResult] = useState<RouteResultState>({ status: "idle" });
  const [listAttempt, setListAttempt] = useState(0);
  const [lookupAttempt, setLookupAttempt] = useState(0);
  const canManage = me.permissions.includes("route.manage");
  const createdCode = (location.state as { createdCode?: string } | null)?.createdCode;

  useEffect(() => {
    const controller = new AbortController();
    setListState({ status: "loading" });
    const requested = status === "all" ? [true, false] : [status === "active"];
    void Promise.all(requested.map(active => listProductionRoutes(active, controller.signal)))
      .then(groups => { if (!controller.signal.aborted) setListState({ status: "ready", routes: groups.flat() }); })
      .catch(error => { if (!controller.signal.aborted) setListState({ status: "error", error: problem(error, "Routes could not be loaded.") }); });
    return () => controller.abort();
  }, [listAttempt, status]);

  useEffect(() => {
    setLookupInput(exactCode);
    if (!exactCode) { setResult({ status: "idle" }); return; }
    const controller = new AbortController();
    setResult({ status: "loading" });
    void findActiveProductionRoute(exactCode, controller.signal)
      .then(route => { if (!controller.signal.aborted) setResult({ status: "found", route }); })
      .catch(error => {
        if (controller.signal.aborted) return;
        setResult(error instanceof ApiError && error.status === 404 ? { status: "missing" } : { status: "error", error: problem(error, "Route code could not be checked.") });
      });
    return () => controller.abort();
  }, [exactCode, lookupAttempt]);

  function setParam(name: string, value: string, resetPage = true) {
    const next = new URLSearchParams(window.location.search);
    if (value) next.set(name, value); else next.delete(name);
    if (resetPage) next.delete("page");
    setSearchParams(next, { replace: true });
  }
  function findCode(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const code = lookupInput.trim().toUpperCase();
    setParam("code", code, false);
    setLookupAttempt(value => value + 1);
  }

  const routes = listState.status === "ready" ? listState.routes
    .filter(route => `${route.code} ${route.name}`.toLowerCase().includes(q.trim().toLowerCase()))
    .sort((a, b) => a.code.localeCompare(b.code) || b.version - a.version) : [];
  const maxPage = Math.max(1, Math.ceil(routes.length / PAGE_SIZE));
  const page = Math.min(currentPage, maxPage);
  const visible = routes.slice((page - 1) * PAGE_SIZE, page * PAGE_SIZE);
  const query = searchParams.toString();

  return <section className="master-list route-list" aria-labelledby="routes-title">
    <EngineeringTabs active="routes" />
    <div className="page-heading-row"><div><h2 id="routes-title">Production routes</h2><p>Ordered manufacturing stages and their active or earlier versions.</p></div>{canManage && <button className="button button--primary" type="button" onClick={() => navigate(`/app/master-data/engineering/routes/new${query ? `?${query}` : ""}`)}><Plus size={17} />Create route</button>}</div>
    {createdCode && <p className="success-banner" role="status" aria-label="Route creation status">Route {createdCode} created.</p>}
    <div className="master-filters route-filters"><div className="field"><label htmlFor="route-search">Route search</label><input id="route-search" type="search" placeholder="Code or name" value={q} onChange={event => setParam("q", event.target.value)} /></div><div className="field"><label htmlFor="route-status">Route status</label><select id="route-status" value={status} onChange={event => setParam("status", event.target.value === "active" ? "" : event.target.value)}><option value="active">Active versions</option><option value="inactive">Inactive versions</option><option value="all">All versions</option></select></div><button className="button button--secondary" type="button" disabled={listState.status === "loading"} onClick={() => setListAttempt(value => value + 1)}><RefreshCw size={16} />Refresh</button></div>
    <form className="route-lookup" onSubmit={findCode}><div className="field"><label htmlFor="route-code-lookup">Exact route code</label><input id="route-code-lookup" value={lookupInput} maxLength={20} onChange={event => setLookupInput(event.target.value)} placeholder="Find the active version by code" /></div><button className="button button--secondary" type="submit" disabled={!lookupInput.trim()}>Find active route</button></form>
    {result.status === "loading" && <p className="route-lookup__message" role="status">Checking active route…</p>}
    {result.status === "found" && <section className="route-lookup__result" role="region" aria-label="Exact code result"><div><strong>{result.route.code}</strong><span>{result.route.name} · Revision {result.route.version}</span></div><Link className="button button--secondary" to={routeHref(result.route.route_id, query)} aria-label={`Open route ${result.route.code} revision ${result.route.version}`}>Open route<ArrowUpRight size={16} /></Link></section>}
    {result.status === "missing" && <div className="master-state route-lookup__empty"><h3>No active route found</h3><p>Check the exact code. Earlier or retired versions can still appear in the register below.</p></div>}
    {result.status === "error" && <RequestError error={result.error} />}
    {listState.status === "loading" && <div className="master-state"><span className="spinner" /><p>Loading routes…</p></div>}
    {listState.status === "error" && <div className="inline-error" role="alert"><AlertCircle size={20} /><div><h3>Route register unavailable</h3><p>{listState.error.message}</p>{listState.error instanceof ApiError && listState.error.status >= 500 && listState.error.requestId && <p>Request ID: {listState.error.requestId}</p>}</div><button className="button button--secondary" type="button" onClick={() => setListAttempt(value => value + 1)}>Try again</button></div>}
    {listState.status === "ready" && routes.length === 0 && <div className="master-state"><Boxes size={28} /><h3>No routes found</h3><p>{q || status !== "active" ? "Adjust the search or status to see other versions." : "No active routes were returned."}</p></div>}
    {listState.status === "ready" && routes.length > 0 && <><p className="route-scroll-hint">Scroll table horizontally to view more columns.</p><div className="master-table-wrap route-table-wrap" role="region" aria-label="Scrollable route results" tabIndex={0}><table className="master-table route-table" aria-label="Route results"><thead><tr><th>Code</th><th>Route</th><th>Revision</th><th>Status</th><th>Stages</th><th>QA gates</th><th><span className="sr-only">Actions</span></th></tr></thead><tbody>{visible.map(route => <tr key={route.route_id}><td><strong>{route.code}</strong></td><td>{route.name}</td><td>Revision {route.version}</td><td><span className={`status-badge status-badge--${route.active ? "approved" : "cancelled"}`}>{route.active ? "Active" : "Inactive"}</span></td><td>{route.stages.length} {route.stages.length === 1 ? "stage" : "stages"}</td><td>{route.stages.filter(stage => stage.is_qa_gate).length}</td><td><Link className="icon-link" to={routeHref(route.route_id, query)} aria-label={`Open route ${route.code} revision ${route.version}`}><ArrowUpRight size={17} /></Link></td></tr>)}</tbody></table></div><ul className="master-cards" aria-label="Route results">{visible.map(route => <li key={route.route_id}><div><span>{route.code}</span><span className={`status-badge status-badge--${route.active ? "approved" : "cancelled"}`}>{route.active ? "Active" : "Inactive"}</span></div><h3>{route.name}</h3><dl><div><dt>Revision</dt><dd>Revision {route.version}</dd></div><div><dt>Stages</dt><dd>{route.stages.length} {route.stages.length === 1 ? "stage" : "stages"}</dd></div><div><dt>QA gates</dt><dd>{route.stages.filter(stage => stage.is_qa_gate).length}</dd></div></dl><Link className="button button--secondary" to={routeHref(route.route_id, query)} aria-label={`Open route ${route.code} revision ${route.version}`}>Open route<ArrowUpRight size={16} /></Link></li>)}</ul><div className="reference-pagination"><p className="result-summary" aria-live="polite">Showing {(page - 1) * PAGE_SIZE + 1}–{Math.min(page * PAGE_SIZE, routes.length)} of {routes.length} records</p>{routes.length > PAGE_SIZE && <div><button className="button button--secondary" type="button" aria-label="Previous page" disabled={page === 1} onClick={() => setParam("page", String(page - 1), false)}><ChevronLeft size={16} /></button><span>Page {page} of {maxPage}</span><button className="button button--secondary" type="button" aria-label="Next page" disabled={page === maxPage} onClick={() => setParam("page", String(page + 1), false)}><ChevronRight size={16} /></button></div>}</div></>}
  </section>;
}

function RouteCreate() {
  const navigate = useNavigate();
  const location = useLocation();
  const [code, setCode] = useState("");
  const [name, setName] = useState("");
  const [stages, setStages] = useState<StageDraft[]>([{ key: 0, code: "", name: "", qa: false }]);
  const nextKey = useRef(1);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<Error | null>(null);
  const back = `/app/master-data/engineering/routes${location.search}`;
  function updateStage(key: number, change: Partial<StageDraft>) { setStages(current => current.map(stage => stage.key === key ? { ...stage, ...change } : stage)); }
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const normalized = code.trim().toUpperCase();
    const normalizedStages = stages.map((stage, index) => ({ stage_code: stage.code.trim().toUpperCase(), stage_name: stage.name.trim(), is_qa_gate: stage.qa, is_end: index === stages.length - 1 }));
    if (!codePattern.test(normalized) || normalizedStages.some(stage => !codePattern.test(stage.stage_code) || !stage.stage_name)) { setError(new Error("Use valid route and stage codes and give every stage a name.")); return; }
    if (new Set(normalizedStages.map(stage => stage.stage_code)).size !== normalizedStages.length) { setError(new Error("Stage codes must be unique within a route.")); return; }
    setSaving(true); setError(null);
    try {
      const created = await createProductionRoute({ code: normalized, name: name.trim(), stages: normalizedStages });
      navigate(`/app/master-data/engineering/routes?q=${encodeURIComponent(created.code)}`, { state: { createdCode: created.code } });
    } catch (error) { setError(problem(error, "Route could not be created.")); }
    finally { setSaving(false); }
  }
  return <section className="master-detail route-create" aria-labelledby="route-create-title"><div className="master-detail__heading"><div><Link to={back}><ArrowLeft size={16} />Back to routes</Link><h2 id="route-create-title">Create route</h2><p>Define the stage order before this route is used by manufactured items.</p></div></div><div className="form-panel"><form className="record-form" onSubmit={submit}><div className="record-form__heading"><div><h3>Route identity</h3><p>Codes are saved in uppercase. A route version is immutable after creation.</p></div></div><div className="record-form__grid"><div className="field"><label htmlFor="new-route-code">Route code</label><input id="new-route-code" required maxLength={20} pattern="[A-Za-z0-9_-]{1,20}" value={code} onChange={event => setCode(event.target.value)} /></div><div className="field"><label htmlFor="new-route-name">Route name</label><input id="new-route-name" required maxLength={120} value={name} onChange={event => setName(event.target.value)} /></div></div><div className="route-create__stages"><div className="record-form__heading"><div><h3>Ordered stages</h3><p>The final stage is the route end. Mark any quality gates that production must pass.</p></div></div>{stages.map((stage, index) => <fieldset className="route-create__stage" key={stage.key}><legend>Stage {index + 1}{index === stages.length - 1 ? " · Final stage" : ""}</legend><div className="record-form__grid"><div className="field"><label htmlFor={`stage-code-${stage.key}`}>Stage code</label><input id={`stage-code-${stage.key}`} required maxLength={20} pattern="[A-Za-z0-9_-]{1,20}" value={stage.code} onChange={event => updateStage(stage.key, { code: event.target.value })} /></div><div className="field"><label htmlFor={`stage-name-${stage.key}`}>Stage name</label><input id={`stage-name-${stage.key}`} required maxLength={120} value={stage.name} onChange={event => updateStage(stage.key, { name: event.target.value })} /></div><label className="route-create__qa"><input type="checkbox" checked={stage.qa} onChange={event => updateStage(stage.key, { qa: event.target.checked })} />QA gate</label></div>{stages.length > 1 && <button className="button button--secondary" type="button" onClick={() => setStages(current => current.filter(candidate => candidate.key !== stage.key))}>Remove stage {index + 1}</button>}</fieldset>)}<button className="button button--secondary" type="button" disabled={stages.length >= 30} onClick={() => setStages(current => [...current, { key: nextKey.current++, code: "", name: "", qa: false }])}><Plus size={16} />Add stage</button></div>{error && <RequestError error={error} />}<div className="record-form__actions"><Link className="button button--secondary" to={back}>Cancel</Link><button className="button button--primary" type="submit" disabled={saving}>{saving ? "Saving…" : "Save route"}</button></div></form></div></section>;
}

function RouteRead({ routeId, me }: { routeId: string; me: Me }) {
  const location = useLocation();
  const navigate = useNavigate();
  const [state, setState] = useState<{ status: "loading" } | { status: "ready"; route: ProductionRoute } | { status: "error"; error: Error }>({ status: "loading" });
  const [attempt, setAttempt] = useState(0);
  const [revisionOpen, setRevisionOpen] = useState(false);
  const [revisionName, setRevisionName] = useState("");
  const [stages, setStages] = useState<StageDraft[]>([]);
  const nextKey = useRef(0);
  const [retireOpen, setRetireOpen] = useState(false);
  const [saving, setSaving] = useState(false);
  const [actionError, setActionError] = useState<Error | null>(null);
  const [notice, setNotice] = useState("");
  const reviseRef = useRef<HTMLButtonElement>(null);
  const retireRef = useRef<HTMLButtonElement>(null);
  const cancelRef = useRef<HTMLButtonElement>(null);
  const nameRef = useRef<HTMLInputElement>(null);
  const titleRef = useRef<HTMLHeadingElement>(null);
  const canManage = me.permissions.includes("route.manage");
  useEffect(() => {
    const controller = new AbortController();
    setState({ status: "loading" });
    setRevisionOpen(false); setRetireOpen(false); setActionError(null); setNotice("");
    void fetchProductionRoute(routeId, controller.signal)
      .then(route => { if (!controller.signal.aborted) setState({ status: "ready", route }); })
      .catch(error => { if (!controller.signal.aborted) setState({ status: "error", error: problem(error, "Route could not be loaded.") }); });
    return () => controller.abort();
  }, [attempt, routeId]);
  useEffect(() => { if (revisionOpen) nameRef.current?.focus(); }, [revisionOpen]);
  useEffect(() => {
    if (!retireOpen) return;
    const overflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    cancelRef.current?.focus();
    return () => { document.body.style.overflow = overflow; };
  }, [retireOpen]);
  function openRevision() {
    if (state.status !== "ready") return;
    setRevisionName(state.route.name);
    setStages([...state.route.stages].sort((a, b) => a.sequence_no - b.sequence_no).map((stage, index) => ({ key: index, code: stage.stage_code, name: stage.stage_name, qa: stage.is_qa_gate })));
    nextKey.current = state.route.stages.length;
    setActionError(null); setRevisionOpen(true);
  }
  function closeRevision() { setRevisionOpen(false); setActionError(null); requestAnimationFrame(() => reviseRef.current?.focus()); }
  function updateStage(key: number, change: Partial<StageDraft>) { setStages(current => current.map(stage => stage.key === key ? { ...stage, ...change } : stage)); }
  function moveStage(index: number, direction: -1 | 1) {
    setStages(current => {
      const next = [...current];
      const target = index + direction;
      if (target < 0 || target >= next.length) return current;
      [next[index], next[target]] = [next[target], next[index]];
      return next;
    });
  }
  async function saveRevision(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const normalizedStages = stages.map((stage, index) => ({ stage_code: stage.code.trim().toUpperCase(), stage_name: stage.name.trim(), is_qa_gate: stage.qa, is_end: index === stages.length - 1 }));
    if (normalizedStages.some(stage => !codePattern.test(stage.stage_code) || !stage.stage_name)) { setActionError(new Error("Give every stage a valid code and name.")); return; }
    if (new Set(normalizedStages.map(stage => stage.stage_code)).size !== normalizedStages.length) { setActionError(new Error("Stage codes must be unique within a route.")); return; }
    setSaving(true); setActionError(null);
    try {
      const revised = await reviseProductionRoute(routeId, { name: revisionName.trim(), stages: normalizedStages });
      navigate(routeHref(revised.route_id, location.search.slice(1)));
    } catch (error) { setActionError(problem(error, "Route revision could not be saved.")); }
    finally { setSaving(false); }
  }
  function closeRetire() { setRetireOpen(false); setActionError(null); retireRef.current?.focus(); }
  function retireKeys(event: KeyboardEvent<HTMLElement>) {
    if (event.key === "Escape") { event.preventDefault(); closeRetire(); return; }
    if (event.key !== "Tab") return;
    const focusable = Array.from(event.currentTarget.querySelectorAll<HTMLElement>('button:not(:disabled)'));
    if (!focusable.length) return;
    const first = focusable[0], last = focusable[focusable.length - 1];
    if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
    if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
  }
  async function confirmRetire() {
    setSaving(true); setActionError(null);
    try {
      const retired = await retireProductionRoute(routeId);
      setState({ status: "ready", route: retired });
      setRetireOpen(false); setNotice(`Route ${retired.code} retired.`);
      titleRef.current?.focus();
    } catch (error) { setRetireOpen(false); setActionError(problem(error, "Route could not be retired.")); retireRef.current?.focus(); }
    finally { setSaving(false); }
  }
  const back = `/app/master-data/engineering/routes${location.search}`;
  if (state.status === "loading") return <section className="master-detail"><div className="master-state"><span className="spinner" /><p>Loading route…</p></div></section>;
  if (state.status === "error") return <section className="master-detail"><Link to={back}><ArrowLeft size={16} />Back to routes</Link><RequestError error={state.error} /><button className="button button--secondary" type="button" onClick={() => setAttempt(value => value + 1)}>Try again</button></section>;
  const route = state.route;
  return <section className="master-detail route-detail" aria-labelledby="route-detail-title">
    <div className="master-detail__heading"><div><Link to={back}><ArrowLeft size={16} />Back to routes</Link><p className="eyebrow">{route.code} · Revision {route.version}</p><h2 id="route-detail-title" tabIndex={-1} ref={titleRef}>{route.name}</h2><span className={`status-badge status-badge--${route.active ? "approved" : "cancelled"}`}>{route.active ? "Active" : "Inactive"}</span></div>{canManage && route.active && <div className="route-detail__actions"><button className="button button--secondary" type="button" ref={reviseRef} disabled={revisionOpen || saving} onClick={openRevision}>Revise route</button><button className="button button--secondary" type="button" ref={retireRef} disabled={revisionOpen || saving} onClick={() => { setActionError(null); setRetireOpen(true); }}>Retire route</button></div>}</div>
    {notice && <p className="success-banner" role="status" aria-label="Route status update">{notice}</p>}
    {actionError && !revisionOpen && <RequestError error={actionError} />}
    {revisionOpen && <section className="form-panel route-revision"><form className="record-form" onSubmit={saveRevision}><div className="record-form__heading"><div><h3>Revise route</h3><p>A revision creates a new active version. The current version remains in history.</p></div></div><div className="record-form__grid"><div className="field"><label htmlFor="route-revision-name">Route name</label><input id="route-revision-name" ref={nameRef} required maxLength={120} value={revisionName} onChange={event => setRevisionName(event.target.value)} /></div></div><div className="route-create__stages"><div className="record-form__heading"><div><h3>Ordered stages</h3><p>Move stages into their production order. The final row becomes the end stage.</p></div></div>{stages.map((stage, index) => <fieldset className="route-create__stage" key={stage.key}><legend>Stage {index + 1}{index === stages.length - 1 ? " · Final stage" : ""}</legend><div className="record-form__grid"><div className="field"><label htmlFor={`revision-stage-code-${stage.key}`}>Stage code</label><input id={`revision-stage-code-${stage.key}`} required maxLength={20} pattern="[A-Za-z0-9_-]{1,20}" value={stage.code} onChange={event => updateStage(stage.key, { code: event.target.value })} /></div><div className="field"><label htmlFor={`revision-stage-name-${stage.key}`}>Stage name</label><input id={`revision-stage-name-${stage.key}`} required maxLength={120} value={stage.name} onChange={event => updateStage(stage.key, { name: event.target.value })} /></div><label className="route-create__qa"><input type="checkbox" checked={stage.qa} onChange={event => updateStage(stage.key, { qa: event.target.checked })} />QA gate</label></div><div className="route-revision__row-actions"><button className="button button--secondary" type="button" disabled={index === 0} onClick={() => moveStage(index, -1)}>Move stage {index + 1} up</button><button className="button button--secondary" type="button" disabled={index === stages.length - 1} onClick={() => moveStage(index, 1)}>Move stage {index + 1} down</button>{stages.length > 1 && <button className="button button--secondary" type="button" onClick={() => setStages(current => current.filter(candidate => candidate.key !== stage.key))}>Remove stage {index + 1}</button>}</div></fieldset>)}<button className="button button--secondary" type="button" disabled={stages.length >= 30} onClick={() => setStages(current => [...current, { key: nextKey.current++, code: "", name: "", qa: false }])}><Plus size={16} />Add stage</button></div>{actionError && <RequestError error={actionError} />}<div className="record-form__actions"><button className="button button--secondary" type="button" onClick={closeRevision}>Cancel revision</button><button className="button button--primary" type="submit" disabled={saving}>{saving ? "Saving…" : "Save revision"}</button></div></form></section>}
    <section className="record-panel"><div className="record-panel__heading"><div><h3>Route stages</h3><p>Ordered stages and quality gates from this route version.</p></div></div><ol className="route-detail__stages">{[...route.stages].sort((a, b) => a.sequence_no - b.sequence_no).map(stage => <li key={stage.sequence_no}><span>{stage.sequence_no}</span><div><strong>{stage.stage_code}</strong><p>{stage.stage_name}</p></div><div>{stage.is_qa_gate && <span className="status-badge status-badge--pending">QA gate</span>}{stage.is_end && <span className="status-badge status-badge--approved">End stage</span>}</div></li>)}</ol></section>
    {retireOpen && <div className="bom-editor-backdrop"><section className="route-retire-dialog" role="dialog" aria-modal="true" aria-labelledby="route-retire-title" onKeyDown={retireKeys}><h3 id="route-retire-title">Retire this route?</h3><p>This active version will no longer be available for new items. The backend may refuse while active items still follow its code.</p><div className="record-form__actions"><button className="button button--secondary" type="button" ref={cancelRef} onClick={closeRetire}>Cancel</button><button className="button button--primary" type="button" disabled={saving} onClick={confirmRetire}>{saving ? "Retiring…" : "Confirm retirement"}</button></div></section></div>}
  </section>;
}

export function Routes({ me }: { me: Me }) {
  const location = useLocation();
  const prefix = "/app/master-data/engineering/routes";
  if (location.pathname === `${prefix}/new`) return me.permissions.includes("route.manage") ? <RouteCreate /> : <RouteList me={me} />;
  if (location.pathname.startsWith(`${prefix}/`)) return <RouteRead routeId={location.pathname.slice(prefix.length + 1)} me={me} />;
  return <RouteList me={me} />;
}
