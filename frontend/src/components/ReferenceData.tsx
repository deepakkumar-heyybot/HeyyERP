import { FormEvent, KeyboardEvent, useEffect, useMemo, useState } from "react";
import { AlertCircle, BookOpen, ChevronLeft, ChevronRight, Pencil, Plus, RefreshCw, X } from "lucide-react";
import { useSearchParams } from "react-router-dom";
import {
  ApiError,
  ItemCategory,
  ItemCategoryCreate,
  Me,
  PaymentTerm,
  PaymentTermCreate,
  TaxCode,
  TaxCodeCreate,
  UnitOfMeasure,
  createItemCategory,
  createPaymentTerm,
  createTaxCode,
  fetchAllItemCategories,
  fetchAllPaymentTerms,
  fetchAllTaxCodes,
  fetchUnitsOfMeasure,
  updateItemCategory,
  updatePaymentTerm,
  updateTaxCode,
} from "../api-client";

type ReferenceType = "payment-terms" | "tax-codes" | "item-categories" | "uom";
type ReferenceItem = PaymentTerm | TaxCode | ItemCategory | UnitOfMeasure;
type ViewState = { status: "loading" } | { status: "ready"; items: ReferenceItem[] } | { status: "error"; error: Error };

const TYPES: Array<{ value: ReferenceType; label: string; singular: string }> = [
  { value: "payment-terms", label: "Payment terms", singular: "payment term" },
  { value: "tax-codes", label: "Tax codes", singular: "tax code" },
  { value: "item-categories", label: "Item categories", singular: "item category" },
  { value: "uom", label: "Units of measure", singular: "unit of measure" },
];
const PAGE_SIZE = 10;

function isReferenceType(value: string | null): value is ReferenceType {
  return TYPES.some(type => type.value === value);
}

function ErrorNotice({ error }: { error: Error }) {
  return <div className="decision-error" role="alert"><AlertCircle size={17} /><div><span>{error.message}</span>{error instanceof ApiError && error.status >= 500 && error.requestId && <span>Request ID: {error.requestId}</span>}</div></div>;
}

function PaymentTermForm({ item, busy, error, onCancel, onSaved }: {
  item?: PaymentTerm; busy: boolean; error: Error | null; onCancel: () => void; onSaved: (body: PaymentTermCreate | { name: string; due_days: number }) => Promise<void>;
}) {
  const [code, setCode] = useState(item?.code ?? "");
  const [name, setName] = useState(item?.name ?? "");
  const [dueDays, setDueDays] = useState(String(item?.due_days ?? ""));
  async function submit(event: FormEvent) {
    event.preventDefault();
    const body = { name: name.trim(), due_days: Number(dueDays) };
    await onSaved(item ? body : { ...body, code: code.trim().toUpperCase() });
  }
  return <form className="record-form" onSubmit={submit}><div className="record-form__heading"><div><h3>{item ? "Edit payment term" : "Create payment term"}</h3></div><button type="button" aria-label="Close payment term form" onClick={onCancel} disabled={busy}><X size={20} /></button></div><div className="record-form__grid">{!item && <div className="field"><label htmlFor="payment-term-code">Payment term code</label><input id="payment-term-code" required maxLength={50} value={code} onChange={event => setCode(event.target.value)} /></div>}<div className="field"><label htmlFor="payment-term-name">Payment term name</label><input id="payment-term-name" required maxLength={120} value={name} onChange={event => setName(event.target.value)} /></div><div className="field"><label htmlFor="payment-term-days">Due days</label><input id="payment-term-days" type="number" min="0" max="365" required value={dueDays} onChange={event => setDueDays(event.target.value)} /></div></div>{error && <ErrorNotice error={error} />}<div className="record-form__actions"><button className="button button--secondary" type="button" onClick={onCancel}>Cancel</button><button className="button button--primary" type="submit" disabled={busy}>{busy ? "Saving…" : item ? "Save changes" : "Save payment term"}</button></div></form>;
}

function TaxCodeForm({ item, busy, error, onCancel, onSaved }: {
  item?: TaxCode; busy: boolean; error: Error | null; onCancel: () => void; onSaved: (body: TaxCodeCreate | { description: string; gst_rate: string; active: boolean }) => Promise<void>;
}) {
  const [code, setCode] = useState(item?.code ?? "");
  const [description, setDescription] = useState(item?.description ?? "");
  const [rate, setRate] = useState(item?.gst_rate ?? "");
  const [active, setActive] = useState(item?.active ?? true);
  async function submit(event: FormEvent) {
    event.preventDefault();
    const body = { description: description.trim(), gst_rate: rate };
    await onSaved(item ? { ...body, active } : { ...body, code: code.trim().toUpperCase() });
  }
  return <form className="record-form" onSubmit={submit}><div className="record-form__heading"><div><h3>{item ? "Edit tax code" : "Create tax code"}</h3></div><button type="button" aria-label="Close tax code form" onClick={onCancel} disabled={busy}><X size={20} /></button></div><div className="record-form__grid">{!item && <div className="field"><label htmlFor="tax-code">Tax code</label><input id="tax-code" required maxLength={50} value={code} onChange={event => setCode(event.target.value)} /></div>}<div className="field"><label htmlFor="tax-description">Description</label><input id="tax-description" required maxLength={200} value={description} onChange={event => setDescription(event.target.value)} /></div><div className="field"><label htmlFor="gst-rate">GST rate</label><input id="gst-rate" inputMode="decimal" required pattern="\d+(\.\d{1,4})?" value={rate} onChange={event => setRate(event.target.value)} /></div>{item && <label className="checkbox-field"><input type="checkbox" checked={active} onChange={event => setActive(event.target.checked)} />Active tax code</label>}</div>{error && <ErrorNotice error={error} />}<div className="record-form__actions"><button className="button button--secondary" type="button" onClick={onCancel}>Cancel</button><button className="button button--primary" type="submit" disabled={busy}>{busy ? "Saving…" : item ? "Save changes" : "Save tax code"}</button></div></form>;
}

function CategoryForm({ item, categories, busy, error, onCancel, onSaved }: {
  item?: ItemCategory; categories: ItemCategory[]; busy: boolean; error: Error | null; onCancel: () => void; onSaved: (body: ItemCategoryCreate | { name: string; parent_category_id: string | null }) => Promise<void>;
}) {
  const [code, setCode] = useState(item?.code ?? "");
  const [name, setName] = useState(item?.name ?? "");
  const [parentId, setParentId] = useState(item?.parent_category_id ?? "");
  async function submit(event: FormEvent) {
    event.preventDefault();
    const body = { name: name.trim(), parent_category_id: parentId || null };
    await onSaved(item ? body : { ...body, code: code.trim().toUpperCase() });
  }
  return <form className="record-form" onSubmit={submit}><div className="record-form__heading"><div><h3>{item ? "Edit item category" : "Create item category"}</h3></div><button type="button" aria-label="Close item category form" onClick={onCancel} disabled={busy}><X size={20} /></button></div><div className="record-form__grid">{!item && <div className="field"><label htmlFor="category-code">Category code</label><input id="category-code" required maxLength={50} value={code} onChange={event => setCode(event.target.value)} /></div>}<div className="field"><label htmlFor="category-name">Category name</label><input id="category-name" required maxLength={120} value={name} onChange={event => setName(event.target.value)} /></div><div className="field"><label htmlFor="parent-category">Parent category</label><select id="parent-category" value={parentId} onChange={event => setParentId(event.target.value)}><option value="">No parent</option>{categories.filter(category => category.category_id !== item?.category_id).map(category => <option key={category.category_id} value={category.category_id}>{category.code} — {category.name}</option>)}</select></div></div>{error && <ErrorNotice error={error} />}<div className="record-form__actions"><button className="button button--secondary" type="button" onClick={onCancel}>Cancel</button><button className="button button--primary" type="submit" disabled={busy}>{busy ? "Saving…" : item ? "Save changes" : "Save item category"}</button></div></form>;
}

export function ReferenceData({ me }: { me: Me }) {
  const [searchParams, setSearchParams] = useSearchParams();
  const type: ReferenceType = isReferenceType(searchParams.get("type")) ? searchParams.get("type") as ReferenceType : "payment-terms";
  const query = searchParams.get("q") ?? "";
  const activeParam = searchParams.get("active") ?? "";
  const active = activeParam === "true" ? true : activeParam === "false" ? false : null;
  const page = Math.max(1, Number(searchParams.get("page") ?? "1") || 1);
  const [state, setState] = useState<ViewState>({ status: "loading" });
  const [attempt, setAttempt] = useState(0);
  const [form, setForm] = useState<{ mode: "create" | "edit"; item?: ReferenceItem } | null>(null);
  const [saving, setSaving] = useState(false);
  const [formError, setFormError] = useState<Error | null>(null);
  const [notice, setNotice] = useState("");
  const canManage = me.permissions.includes("refdata.manage") && type !== "uom";
  const typeInfo = TYPES.find(candidate => candidate.value === type)!;

  useEffect(() => {
    const controller = new AbortController();
    setState({ status: "loading" }); setForm(null); setFormError(null);
    void Promise.resolve().then(async () => {
      if (controller.signal.aborted) return;
      const request = type === "payment-terms" ? fetchAllPaymentTerms(controller.signal)
        : type === "tax-codes" ? fetchAllTaxCodes(active, controller.signal)
          : type === "item-categories" ? fetchAllItemCategories(controller.signal)
            : fetchUnitsOfMeasure(controller.signal);
      try { setState({ status: "ready", items: await request }); }
      catch (problem) { if (!controller.signal.aborted) setState({ status: "error", error: problem instanceof Error ? problem : new Error("Reference data could not be loaded.") }); }
    });
    return () => controller.abort();
  }, [active, attempt, type]);

  function setParam(name: string, value: string, resetPage = true) {
    setSearchParams(current => { const next = new URLSearchParams(current); if (value) next.set(name, value); else next.delete(name); if (resetPage) next.delete("page"); return next; }, { replace: true });
  }
  function selectType(nextType: ReferenceType) {
    setNotice("");
    setSearchParams(current => { const next = new URLSearchParams(current); if (nextType === "payment-terms") next.delete("type"); else next.set("type", nextType); next.delete("q"); next.delete("active"); next.delete("page"); return next; }, { replace: true });
  }
  function tabKey(event: KeyboardEvent<HTMLButtonElement>, index: number) {
    let target = index;
    if (event.key === "ArrowRight") target = (index + 1) % TYPES.length;
    else if (event.key === "ArrowLeft") target = (index - 1 + TYPES.length) % TYPES.length;
    else if (event.key === "Home") target = 0;
    else if (event.key === "End") target = TYPES.length - 1;
    else return;
    event.preventDefault(); selectType(TYPES[target].value);
    requestAnimationFrame(() => document.querySelector<HTMLButtonElement>(`[data-reference-tab="${TYPES[target].value}"]`)?.focus());
  }

  const items = state.status === "ready" ? state.items : [];
  const filtered = useMemo(() => {
    const normalized = query.trim().toLowerCase();
    return items.filter(item => Object.values(item).some(value => String(value ?? "").toLowerCase().includes(normalized)));
  }, [items, query]);
  const maxPage = Math.max(1, Math.ceil(filtered.length / PAGE_SIZE));
  const currentPage = Math.min(page, maxPage);
  const visible = filtered.slice((currentPage - 1) * PAGE_SIZE, currentPage * PAGE_SIZE);
  const categories = type === "item-categories" ? items as ItemCategory[] : [];
  const categoryName = (id: string | null) => id ? categories.find(category => category.category_id === id)?.name ?? "Unknown category" : "No parent";

  async function save(run: () => Promise<ReferenceItem>, success: string) {
    setSaving(true); setFormError(null);
    try { await run(); setForm(null); setNotice(success); setAttempt(value => value + 1); }
    catch (problem) { setFormError(problem instanceof Error ? problem : new Error("The reference record could not be saved.")); }
    finally { setSaving(false); }
  }

  function renderForm() {
    if (!form) return null;
    if (type === "payment-terms") {
      const item = form.item as PaymentTerm | undefined;
      return <PaymentTermForm item={item} busy={saving} error={formError} onCancel={() => setForm(null)} onSaved={body => save(() => item ? updatePaymentTerm(item.payment_term_id, body as { name: string; due_days: number }) : createPaymentTerm(body as PaymentTermCreate), item ? "Payment term updated." : "Payment term created.")} />;
    }
    if (type === "tax-codes") {
      const item = form.item as TaxCode | undefined;
      return <TaxCodeForm item={item} busy={saving} error={formError} onCancel={() => setForm(null)} onSaved={body => save(() => item ? updateTaxCode(item.tax_code_id, body as { description: string; gst_rate: string; active: boolean }) : createTaxCode(body as TaxCodeCreate), item ? "Tax code updated." : "Tax code created.")} />;
    }
    const item = form.item as ItemCategory | undefined;
    return <CategoryForm item={item} categories={categories} busy={saving} error={formError} onCancel={() => setForm(null)} onSaved={body => save(() => item ? updateItemCategory(item.category_id, body as { name: string; parent_category_id: string | null }) : createItemCategory(body as ItemCategoryCreate), item ? "Item category updated." : "Item category created.")} />;
  }

  const editButton = (item: ReferenceItem, code: string) => canManage && <button className="icon-link" type="button" aria-label={`Edit ${typeInfo.singular} ${code}`} onClick={() => { setForm({ mode: "edit", item }); setFormError(null); }}><Pencil size={16} /></button>;
  const tableLabel = `${typeInfo.singular.replace(/^./, value => value.toUpperCase())} results`;

  return <section className="master-list reference-data" aria-labelledby="reference-title">
    <fieldset className="reference-type-tabs" role="tablist"><legend className="sr-only">Reference types</legend>{TYPES.map((candidate, index) => <button key={candidate.value} type="button" role="tab" aria-selected={type === candidate.value} tabIndex={type === candidate.value ? 0 : -1} data-reference-tab={candidate.value} onKeyDown={event => tabKey(event, index)} onClick={() => selectType(candidate.value)}>{candidate.label}</button>)}</fieldset>
    <div className="reference-type-select field"><label htmlFor="reference-type">Reference type</label><select id="reference-type" value={type} onChange={event => selectType(event.target.value as ReferenceType)}>{TYPES.map(candidate => <option key={candidate.value} value={candidate.value}>{candidate.label}</option>)}</select></div>
    <div className="page-heading-row"><div><h2 id="reference-title">Reference records</h2><p>Shared terms, tax classifications, item categories, and measurement units used across the ERP.</p></div>{canManage && <button className="button button--primary" type="button" onClick={() => { setForm({ mode: "create" }); setFormError(null); }}><Plus size={17} />Create {typeInfo.singular}</button>}</div>
    {type === "uom" && <p className="scope-notice"><BookOpen size={17} />Units of measure are seeded globally and are read-only.</p>}
    {notice && <p className="success-banner" role="status">{notice}</p>}
    {form && <div className="form-panel">{renderForm()}</div>}
    <div className={`master-filters ${type === "tax-codes" ? "reference-filters--tax" : "reference-filters"}`}><div className="field"><label htmlFor="reference-search">Search reference data</label><input id="reference-search" value={query} onChange={event => setParam("q", event.target.value)} placeholder={`Search ${typeInfo.label.toLowerCase()}`} /></div>{type === "tax-codes" && <div className="field"><label htmlFor="reference-status">Status filter</label><select id="reference-status" value={activeParam} onChange={event => setParam("active", event.target.value)}><option value="">All statuses</option><option value="true">Active</option><option value="false">Inactive</option></select></div>}<button className="button button--secondary" type="button" onClick={() => setAttempt(value => value + 1)} disabled={state.status === "loading"}><RefreshCw size={16} />Refresh</button></div>
    {state.status === "loading" && <div className="master-state"><span className="spinner" /><p>Loading {typeInfo.label.toLowerCase()}…</p></div>}
    {state.status === "error" && <div className="inline-error" role="alert"><AlertCircle size={20} /><div><h3>Reference data unavailable</h3><p>{state.error.message}</p>{state.error instanceof ApiError && state.error.status >= 500 && state.error.requestId && <p>Request ID: {state.error.requestId}</p>}</div><button className="button button--secondary" type="button" onClick={() => setAttempt(value => value + 1)}>Try again</button></div>}
    {state.status === "ready" && filtered.length === 0 && <div className="master-state"><BookOpen size={28} /><h3>No {typeInfo.label.toLowerCase()} found</h3><p>{query ? "Change the search term or filters." : type === "uom" ? "No units of measure are available." : `Create the first ${typeInfo.singular} if you have permission.`}</p></div>}
    {state.status === "ready" && filtered.length > 0 && <>
      <div className="master-table-wrap"><table className="master-table reference-table" aria-label={tableLabel}><thead><tr>{type === "payment-terms" ? <><th>Code</th><th>Name</th><th>Due days</th></> : type === "tax-codes" ? <><th>Code</th><th>Description</th><th>GST rate</th><th>Status</th></> : type === "item-categories" ? <><th>Code</th><th>Name</th><th>Parent category</th></> : <><th>Code</th><th>Name</th><th>Decimal places</th></>}{canManage && <th className="reference-actions"><span className="sr-only">Actions</span></th>}</tr></thead><tbody>{visible.map(item => {
        if (type === "payment-terms") { const row = item as PaymentTerm; return <tr key={row.payment_term_id}><td><strong>{row.code}</strong></td><td>{row.name}</td><td>{row.due_days}</td>{canManage && <td>{editButton(row, row.code)}</td>}</tr>; }
        if (type === "tax-codes") { const row = item as TaxCode; return <tr key={row.tax_code_id}><td><strong>{row.code}</strong></td><td>{row.description}</td><td>{row.gst_rate}%</td><td><span className={`status-badge status-badge--${row.active ? "approved" : "cancelled"}`}>{row.active ? "Active" : "Inactive"}</span></td>{canManage && <td>{editButton(row, row.code)}</td>}</tr>; }
        if (type === "item-categories") { const row = item as ItemCategory; return <tr key={row.category_id}><td><strong>{row.code}</strong></td><td>{row.name}</td><td>{categoryName(row.parent_category_id)}</td>{canManage && <td>{editButton(row, row.code)}</td>}</tr>; }
        const row = item as UnitOfMeasure; return <tr key={row.uom_id}><td><strong>{row.code}</strong></td><td>{row.name}</td><td>{row.decimal_places}</td></tr>;
      })}</tbody></table></div>
      <ul className="master-cards" aria-label={tableLabel}>{visible.map(item => {
        const code = item.code; const id = "payment_term_id" in item ? item.payment_term_id : "tax_code_id" in item ? item.tax_code_id : "category_id" in item ? item.category_id : item.uom_id;
        const name = "name" in item ? item.name : item.description;
        return <li key={id}><div><span>{code}</span>{"active" in item && <span className={`status-badge status-badge--${item.active ? "approved" : "cancelled"}`}>{item.active ? "Active" : "Inactive"}</span>}</div><h3>{name}</h3><dl>{"due_days" in item && <div><dt>Due days</dt><dd>{item.due_days}</dd></div>}{"gst_rate" in item && <div><dt>GST rate</dt><dd>{item.gst_rate}%</dd></div>}{"parent_category_id" in item && <div><dt>Parent</dt><dd>{categoryName(item.parent_category_id)}</dd></div>}{"decimal_places" in item && <div><dt>Decimals</dt><dd>{item.decimal_places}</dd></div>}</dl>{editButton(item, code)}</li>;
      })}</ul>
      <div className="reference-pagination"><p className="result-summary" aria-live="polite">Showing {(currentPage - 1) * PAGE_SIZE + 1}–{Math.min(currentPage * PAGE_SIZE, filtered.length)} of {filtered.length} records</p>{filtered.length > PAGE_SIZE && <div><button className="button button--secondary" type="button" aria-label="Previous page" disabled={currentPage === 1} onClick={() => setParam("page", String(currentPage - 1), false)}><ChevronLeft size={16} /></button><span>Page {currentPage} of {maxPage}</span><button className="button button--secondary" type="button" aria-label="Next page" disabled={currentPage === maxPage} onClick={() => setParam("page", String(currentPage + 1), false)}><ChevronRight size={16} /></button></div>}</div>
    </>}
  </section>;
}
