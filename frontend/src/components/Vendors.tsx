import { FormEvent, KeyboardEvent as ReactKeyboardEvent, useEffect, useRef, useState } from "react";
import { AlertCircle, ArrowLeft, ArrowUpRight, Building2, ChevronLeft, ChevronRight, Landmark, Pencil, Plus, RefreshCw, ShieldAlert } from "lucide-react";
import { Link, useLocation, useNavigate, useSearchParams } from "react-router-dom";
import { ApiError, Me, Page, PaymentTerm, Vendor, VendorBankAccount, VendorBankAccountUpdate, VendorCreate, VendorStatus, VendorUpdate, createVendor, deactivateVendor, fetchAllPaymentTerms, fetchVendor, fetchVendorBankAccount, listVendors, updateVendor, updateVendorBankAccount } from "../api-client";
import { PartyTabs } from "./PartyTabs";

type ListState = { status: "loading" } | { status: "ready"; page: Page<Vendor>; terms: PaymentTerm[] } | { status: "error"; error: Error };
type FormState = { status: "loading" } | { status: "ready"; terms: PaymentTerm[] } | { status: "error"; error: Error };
const PAGE_SIZE = 20;
const STATUSES: VendorStatus[] = ["PENDING_APPROVAL", "APPROVED", "REJECTED", "INACTIVE"];

function statusLabel(status: VendorStatus) { const label = status.toLowerCase().replaceAll("_", " "); return label[0].toUpperCase() + label.slice(1); }
function statusClass(status: VendorStatus) { return status === "APPROVED" ? "approved" : status === "PENDING_APPROVAL" ? "pending" : "cancelled"; }
function VendorBadge({ status }: { status: VendorStatus }) { return <span className={`status-badge status-badge--${statusClass(status)}`}>{statusLabel(status)}</span>; }
function RequestError({ error }: { error: Error }) { return <div className="decision-error" role="alert"><AlertCircle size={17} /><div><span>{error.message}</span>{error instanceof ApiError && error.status >= 500 && error.requestId && <span>Request ID: {error.requestId}</span>}</div></div>; }

function VendorList({ me }: { me: Me }) {
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const query = searchParams.get("q") ?? "";
  const statusParam = searchParams.get("status") ?? "";
  const vendorStatus = STATUSES.includes(statusParam as VendorStatus) ? statusParam as VendorStatus : null;
  const currentPage = Math.max(1, Number(searchParams.get("page") ?? "1") || 1);
  const [state, setState] = useState<ListState>({ status: "loading" });
  const [attempt, setAttempt] = useState(0);
  const canManage = me.permissions.includes("vendor.manage");

  useEffect(() => {
    const controller = new AbortController(); setState({ status: "loading" });
    void Promise.resolve().then(async () => {
      if (controller.signal.aborted) return;
      try {
        const [page, terms] = await Promise.all([listVendors({ q: query.trim(), vendorStatus, limit: PAGE_SIZE, offset: (currentPage - 1) * PAGE_SIZE }, controller.signal), fetchAllPaymentTerms(controller.signal)]);
        setState({ status: "ready", page, terms });
      } catch (problem) { if (!controller.signal.aborted) setState({ status: "error", error: problem instanceof Error ? problem : new Error("Vendors could not be loaded.") }); }
    });
    return () => controller.abort();
  }, [attempt, currentPage, query, vendorStatus]);

  function setParam(name: string, value: string, resetPage = true) {
    setSearchParams(current => { const next = new URLSearchParams(current); if (value) next.set(name, value); else next.delete(name); if (resetPage) next.delete("page"); return next; }, { replace: true });
  }
  const items = state.status === "ready" ? state.page.items : [];
  const total = state.status === "ready" ? state.page.total : 0;
  const termName = (id: string | null) => id ? state.status === "ready" ? state.terms.find(term => term.payment_term_id === id)?.name ?? "Unknown term" : "" : "Not assigned";
  const start = total ? (currentPage - 1) * PAGE_SIZE + 1 : 0;
  const end = Math.min(currentPage * PAGE_SIZE, total);
  const maxPage = Math.max(1, Math.ceil(total / PAGE_SIZE));

  return <section className="master-list" aria-labelledby="vendors-title"><PartyTabs active="vendors" /><div className="page-heading-row"><div><h2 id="vendors-title">Vendors</h2><p>Supplier legal identity, MSME registration, payment terms, and approval status.</p></div>{canManage && <button className="button button--primary" type="button" onClick={() => navigate("/app/master-data/parties/vendors/new")}><Plus size={17} />Create vendor</button>}</div><div className="master-filters vendor-filters"><div className="field"><label htmlFor="vendor-search">Search vendors</label><input id="vendor-search" value={query} onChange={event => setParam("q", event.target.value)} placeholder="Code or vendor name" /></div><div className="field"><label htmlFor="vendor-status">Approval status</label><select id="vendor-status" value={statusParam} onChange={event => setParam("status", event.target.value)}><option value="">All statuses</option>{STATUSES.map(status => <option key={status} value={status}>{statusLabel(status)}</option>)}</select></div><button className="button button--secondary" type="button" onClick={() => setAttempt(value => value + 1)} disabled={state.status === "loading"}><RefreshCw size={16} />Refresh</button></div>
    {state.status === "loading" && <div className="master-state"><span className="spinner" /><p>Loading vendors…</p></div>}
    {state.status === "error" && <div className="inline-error" role="alert"><AlertCircle size={20} /><div><h3>Vendors unavailable</h3><p>{state.error.message}</p>{state.error instanceof ApiError && state.error.status >= 500 && state.error.requestId && <p>Request ID: {state.error.requestId}</p>}</div><button className="button button--secondary" type="button" onClick={() => setAttempt(value => value + 1)}>Try again</button></div>}
    {state.status === "ready" && items.length === 0 && <div className="master-state"><Building2 size={28} /><h3>No vendors found</h3><p>{query || vendorStatus ? "Change the search term or approval status." : "Create the first vendor if you have permission."}</p></div>}
    {state.status === "ready" && items.length > 0 && <><div className="master-table-wrap"><table className="master-table vendor-table" aria-label="Vendor results"><thead><tr><th>Code</th><th>Vendor</th><th>GSTIN</th><th>MSME</th><th>Payment terms</th><th>Status</th><th><span className="sr-only">Actions</span></th></tr></thead><tbody>{items.map(vendor => <tr key={vendor.vendor_id}><td><strong>{vendor.code}</strong></td><td>{vendor.name}</td><td className="legal-id">{vendor.gstin ?? "Not registered"}</td><td>{vendor.msme_flag ? <span className="msme-badge">MSME</span> : "No"}</td><td>{termName(vendor.payment_term_id)}</td><td><VendorBadge status={vendor.status} /></td><td><Link className="icon-link" to={`/app/master-data/parties/vendors/${vendor.vendor_id}`} aria-label={`Open vendor ${vendor.code}`}><ArrowUpRight size={17} /></Link></td></tr>)}</tbody></table></div><ul className="master-cards" aria-label="Vendor results">{items.map(vendor => <li key={vendor.vendor_id}><div><span>{vendor.code}</span><VendorBadge status={vendor.status} /></div><h3>{vendor.name}</h3><dl><div><dt>GSTIN</dt><dd className="legal-id">{vendor.gstin ?? "Not registered"}</dd></div><div><dt>MSME</dt><dd>{vendor.msme_flag ? <span className="msme-badge">MSME</span> : "Not registered"}</dd></div><div><dt>Terms</dt><dd>{termName(vendor.payment_term_id)}</dd></div></dl><Link className="button button--secondary" to={`/app/master-data/parties/vendors/${vendor.vendor_id}`} aria-label={`Open vendor ${vendor.code}`}>Open vendor<ArrowUpRight size={16} /></Link></li>)}</ul><div className="reference-pagination"><p className="result-summary" aria-live="polite">Showing {start}–{end} of {total} records</p>{total > PAGE_SIZE && <div><button className="button button--secondary" type="button" aria-label="Previous page" disabled={currentPage === 1} onClick={() => setParam("page", String(currentPage - 1), false)}><ChevronLeft size={16} /></button><span>Page {currentPage} of {maxPage}</span><button className="button button--secondary" type="button" aria-label="Next page" disabled={currentPage === maxPage} onClick={() => setParam("page", String(currentPage + 1), false)}><ChevronRight size={16} /></button></div>}</div></>}
  </section>;
}

function VendorCreatePage() {
  const navigate = useNavigate();
  const [state, setState] = useState<FormState>({ status: "loading" });
  const [saving, setSaving] = useState(false); const [error, setError] = useState<Error | null>(null);
  useEffect(() => { const controller = new AbortController(); void fetchAllPaymentTerms(controller.signal).then(terms => setState({ status: "ready", terms })).catch(problem => { if (!controller.signal.aborted) setState({ status: "error", error: problem instanceof Error ? problem : new Error("Payment terms could not be loaded.") }); }); return () => controller.abort(); }, []);
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); const data = new FormData(event.currentTarget); setSaving(true); setError(null);
    const body: VendorCreate = { code: String(data.get("code") ?? "").trim().toUpperCase(), name: String(data.get("name") ?? "").trim(), gstin: String(data.get("gstin") ?? "").trim().toUpperCase() || null, pan: String(data.get("pan") ?? "").trim().toUpperCase() || null, msme_flag: data.get("msme_flag") === "on", payment_term_id: String(data.get("payment_term_id") ?? "") || null };
    try { const vendor = await createVendor(body); navigate(`/app/master-data/parties/vendors/${vendor.vendor_id}`); }
    catch (problem) { setError(problem instanceof Error ? problem : new Error("The vendor could not be created.")); }
    finally { setSaving(false); }
  }
  return <section className="master-detail vendor-create" aria-labelledby="vendor-create-title"><div className="master-detail__heading"><div><Link to="/app/master-data/parties/vendors"><ArrowLeft size={16} />Back to vendors</Link><h2 id="vendor-create-title">Create vendor</h2></div></div>{state.status === "loading" && <div className="master-state"><span className="spinner" /><p>Loading vendor form…</p></div>}{state.status === "error" && <div className="master-state"><AlertCircle size={28} /><h3>Vendor form unavailable</h3><p>{state.error.message}</p></div>}{state.status === "ready" && <div className="form-panel"><form className="record-form" onSubmit={submit}><div className="record-form__heading"><div><h3>Vendor master</h3><p>Enter legal registration and approved commercial terms. New vendors start pending approval.</p></div></div><div className="record-form__grid"><div className="field"><label htmlFor="vendor-code">Vendor code</label><input id="vendor-code" name="code" required maxLength={50} /></div><div className="field"><label htmlFor="vendor-name">Vendor name</label><input id="vendor-name" name="name" required maxLength={160} /></div><div className="field"><label htmlFor="vendor-gstin">GSTIN</label><input id="vendor-gstin" name="gstin" maxLength={15} /></div><div className="field"><label htmlFor="vendor-pan">PAN</label><input id="vendor-pan" name="pan" maxLength={10} /></div><div className="field"><label htmlFor="vendor-payment-term">Payment term</label><select id="vendor-payment-term" name="payment_term_id"><option value="">Not assigned</option>{state.terms.map(term => <option key={term.payment_term_id} value={term.payment_term_id}>{term.name}</option>)}</select></div><label className="checkbox-field"><input name="msme_flag" type="checkbox" />MSME registered</label></div>{error && <RequestError error={error} />}<div className="record-form__actions"><Link className="button button--secondary" to="/app/master-data/parties/vendors">Cancel</Link><button className="button button--primary" type="submit" disabled={saving}>{saving ? "Saving…" : "Save vendor"}</button></div></form></div>}</section>;
}

type DetailState = { status: "loading" } | { status: "ready"; vendor: Vendor; terms: PaymentTerm[] } | { status: "error"; error: Error };
type BankState = { status: "hidden" | "loading" | "empty" } | { status: "ready"; account: VendorBankAccount } | { status: "error"; error: Error };

function VendorDetail({ vendorId, me }: { vendorId: string; me: Me }) {
  const [state, setState] = useState<DetailState>({ status: "loading" });
  const [bankState, setBankState] = useState<BankState>({ status: me.permissions.includes("fin.pay") ? "loading" : "hidden" });
  const [attempt, setAttempt] = useState(0);
  const [editing, setEditing] = useState(false);
  const [bankEditing, setBankEditing] = useState(false);
  const [confirmingDeactivate, setConfirmingDeactivate] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<Error | null>(null);
  const [notice, setNotice] = useState("");
  const cancelDeactivateRef = useRef<HTMLButtonElement>(null);
  const deactivateButtonRef = useRef<HTMLButtonElement>(null);
  const headingRef = useRef<HTMLHeadingElement>(null);
  const canManage = me.permissions.includes("vendor.manage");
  const canPay = me.permissions.includes("fin.pay");

  useEffect(() => {
    const controller = new AbortController(); setState({ status: "loading" });
    void Promise.resolve().then(async () => {
      if (controller.signal.aborted) return;
      try { const [vendor, terms] = await Promise.all([fetchVendor(vendorId, controller.signal), fetchAllPaymentTerms(controller.signal)]); setState({ status: "ready", vendor, terms }); }
      catch (problem) { if (!controller.signal.aborted) setState({ status: "error", error: problem instanceof Error ? problem : new Error("Vendor details could not be loaded.") }); }
    });
    return () => controller.abort();
  }, [attempt, vendorId]);

  useEffect(() => {
    if (!canPay) { setBankState({ status: "hidden" }); return; }
    const controller = new AbortController(); setBankState({ status: "loading" });
    void fetchVendorBankAccount(vendorId, controller.signal).then(account => setBankState({ status: "ready", account })).catch(problem => {
      if (controller.signal.aborted) return;
      if (problem instanceof ApiError && problem.status === 404) setBankState({ status: "empty" });
      else setBankState({ status: "error", error: problem instanceof Error ? problem : new Error("Bank details could not be loaded.") });
    });
    return () => controller.abort();
  }, [canPay, vendorId]);

  useEffect(() => { if (confirmingDeactivate) cancelDeactivateRef.current?.focus(); }, [confirmingDeactivate]);

  function closeConfirmation() {
    setConfirmingDeactivate(false);
    setError(null);
    queueMicrotask(() => deactivateButtonRef.current?.focus());
  }

  function confirmationKeys(event: ReactKeyboardEvent<HTMLElement>) {
    if (event.key === "Escape") { event.preventDefault(); closeConfirmation(); return; }
    if (event.key !== "Tab") return;
    const buttons = Array.from(event.currentTarget.querySelectorAll<HTMLButtonElement>("button:not([disabled])"));
    if (!buttons.length) return;
    if (event.shiftKey && document.activeElement === buttons[0]) { event.preventDefault(); buttons[buttons.length - 1].focus(); }
    else if (!event.shiftKey && document.activeElement === buttons[buttons.length - 1]) { event.preventDefault(); buttons[0].focus(); }
  }

  if (state.status === "loading") return <section className="master-detail"><div className="master-state"><span className="spinner" /><p>Loading vendor details…</p></div></section>;
  if (state.status === "error") return <section className="master-detail"><div className="inline-error" role="alert"><AlertCircle size={20} /><div><h3>Vendor unavailable</h3><p>{state.error.message}</p>{state.error instanceof ApiError && state.error.status >= 500 && state.error.requestId && <p>Request ID: {state.error.requestId}</p>}</div><button className="button button--secondary" type="button" onClick={() => setAttempt(value => value + 1)}>Try again</button></div></section>;
  const { vendor, terms } = state;
  const paymentTerm = terms.find(term => term.payment_term_id === vendor.payment_term_id)?.name ?? (vendor.payment_term_id ? "Unknown term" : "Not assigned");

  async function saveVendor(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); const data = new FormData(event.currentTarget);
    const body: VendorUpdate = { name: String(data.get("name") ?? "").trim(), gstin: String(data.get("gstin") ?? "").trim().toUpperCase() || null, pan: String(data.get("pan") ?? "").trim().toUpperCase() || null, msme_flag: data.get("msme_flag") === "on", payment_term_id: String(data.get("payment_term_id") ?? "") || null };
    setSaving(true); setError(null); setNotice("");
    try { const updated = await updateVendor(vendorId, body); setState({ status: "ready", vendor: updated, terms }); setEditing(false); setNotice("Vendor updated."); }
    catch (problem) { setError(problem instanceof Error ? problem : new Error("The vendor could not be updated.")); }
    finally { setSaving(false); }
  }

  async function confirmDeactivate() {
    setSaving(true); setError(null); setNotice("");
    try { const updated = await deactivateVendor(vendorId); setState({ status: "ready", vendor: updated, terms }); setConfirmingDeactivate(false); setNotice("Vendor deactivated."); queueMicrotask(() => headingRef.current?.focus()); }
    catch (problem) { setError(problem instanceof Error ? problem : new Error("The vendor could not be deactivated.")); }
    finally { setSaving(false); }
  }

  async function saveBank(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); const data = new FormData(event.currentTarget);
    const body: VendorBankAccountUpdate = { account_holder: String(data.get("account_holder") ?? "").trim(), account_no: String(data.get("account_no") ?? "").trim(), ifsc: String(data.get("ifsc") ?? "").trim().toUpperCase(), bank_name: String(data.get("bank_name") ?? "").trim() || null };
    setSaving(true); setError(null); setNotice("");
    try { const account = await updateVendorBankAccount(vendorId, body); setBankState({ status: "ready", account }); setBankEditing(false); setNotice("Bank account updated."); }
    catch (problem) { setError(problem instanceof Error ? problem : new Error("The bank account could not be updated.")); }
    finally { setSaving(false); }
  }

  return <section className="master-detail vendor-detail" aria-labelledby="vendor-detail-title"><PartyTabs active="vendors" /><div className="master-detail__heading"><div><Link to="/app/master-data/parties/vendors"><ArrowLeft size={16} />Back to vendors</Link><p className="eyebrow">{vendor.code}</p><h2 id="vendor-detail-title" tabIndex={-1} ref={headingRef}>{vendor.name}</h2><VendorBadge status={vendor.status} /></div>{canManage && !editing && <div><button className="button button--secondary" type="button" onClick={() => { setEditing(true); setError(null); }}><Pencil size={16} />Edit vendor</button>{vendor.status !== "INACTIVE" && <button className="button button--danger" type="button" ref={deactivateButtonRef} onClick={() => { setConfirmingDeactivate(true); setError(null); }}><ShieldAlert size={16} />Deactivate vendor</button>}</div>}</div>
    {notice && <p className="success-banner" role="status">{notice}</p>}
    {confirmingDeactivate && <div className="confirmation-backdrop"><section className="confirmation-panel" role="dialog" aria-modal="true" aria-labelledby="deactivate-vendor-title" onKeyDown={confirmationKeys}><div><ShieldAlert size={22} /><div><h3 id="deactivate-vendor-title">Deactivate this vendor?</h3><p>The vendor becomes inactive and cannot be selected for new purchasing activity. This is not a delete action.</p></div></div>{error && <RequestError error={error} />}<div><button className="button button--secondary" type="button" ref={cancelDeactivateRef} onClick={closeConfirmation}>Cancel</button><button className="button button--danger" type="button" disabled={saving} onClick={confirmDeactivate}>{saving ? "Deactivating…" : "Confirm deactivation"}</button></div></section></div>}
    {editing && <div className="form-panel"><form className="record-form" onSubmit={saveVendor}><div className="record-form__heading"><div><h3>Edit vendor</h3><p>Status changes use the dedicated backend action and are not part of this form.</p></div></div><div className="record-form__grid"><div className="field"><label htmlFor="detail-vendor-name">Vendor name</label><input id="detail-vendor-name" name="name" required defaultValue={vendor.name} /></div><div className="field"><label htmlFor="detail-vendor-gstin">GSTIN</label><input id="detail-vendor-gstin" name="gstin" maxLength={15} defaultValue={vendor.gstin ?? ""} /></div><div className="field"><label htmlFor="detail-vendor-pan">PAN</label><input id="detail-vendor-pan" name="pan" maxLength={10} defaultValue={vendor.pan ?? ""} /></div><div className="field"><label htmlFor="detail-vendor-term">Payment term</label><select id="detail-vendor-term" name="payment_term_id" defaultValue={vendor.payment_term_id ?? ""}><option value="">Not assigned</option>{terms.map(term => <option key={term.payment_term_id} value={term.payment_term_id}>{term.name}</option>)}</select></div><label className="checkbox-field"><input name="msme_flag" type="checkbox" defaultChecked={vendor.msme_flag} />MSME registered</label></div>{error && <RequestError error={error} />}<div className="record-form__actions"><button className="button button--secondary" type="button" onClick={() => setEditing(false)}>Cancel</button><button className="button button--primary" type="submit" disabled={saving}>{saving ? "Saving…" : "Save changes"}</button></div></form></div>}
    <div className={`master-detail__grid${canPay ? "" : " vendor-detail__single"}`}><section className="record-panel"><div className="record-panel__heading"><div><h3>Vendor master</h3><p>Legal identity, MSME registration, and commercial terms.</p></div><Building2 size={20} /></div><dl className="record-facts vendor-facts"><div><dt>Vendor code</dt><dd>{vendor.code}</dd></div><div><dt>GSTIN</dt><dd className="legal-id">{vendor.gstin ?? "Not registered"}</dd></div><div><dt>PAN</dt><dd className="legal-id">{vendor.pan ?? "Not supplied"}</dd></div><div><dt>MSME</dt><dd>{vendor.msme_flag ? "MSME registered" : "Not registered"}</dd></div><div><dt>Payment term</dt><dd>{paymentTerm}</dd></div><div><dt>Status</dt><dd>{statusLabel(vendor.status)}</dd></div></dl></section>{canPay && <section className="record-panel bank-panel"><div className="record-panel__heading"><div><h3>Bank account</h3><p>Sensitive account data is returned by the backend in masked form.</p></div><Landmark size={20} /></div>{bankState.status === "loading" && <div className="master-state"><span className="spinner" /><p>Loading bank account…</p></div>}{bankState.status === "error" && <RequestError error={bankState.error} />}{bankState.status === "empty" && !bankEditing && <div className="master-state bank-empty"><Landmark size={25} /><h3>No bank account saved</h3><p>Add verified payment details before including this vendor in payment runs.</p></div>}{bankState.status === "ready" && !bankEditing && <dl className="record-facts"><div><dt>Account holder</dt><dd>{bankState.account.account_holder}</dd></div><div><dt>Account number</dt><dd className="legal-id">{bankState.account.account_no_masked}</dd></div><div><dt>IFSC</dt><dd className="legal-id">{bankState.account.ifsc}</dd></div><div><dt>Bank</dt><dd>{bankState.account.bank_name ?? "Not supplied"}</dd></div></dl>}{!bankEditing && (bankState.status === "ready" || bankState.status === "empty") && <button className="button button--secondary" type="button" onClick={() => { setBankEditing(true); setError(null); }}>{bankState.status === "empty" ? "Add bank account" : "Update bank account"}</button>}{bankEditing && <form className="address-form bank-form" onSubmit={saveBank}><div className="field"><label htmlFor="bank-holder">Account holder</label><input id="bank-holder" name="account_holder" required minLength={2} maxLength={120} defaultValue={bankState.status === "ready" ? bankState.account.account_holder : vendor.name} /></div><div className="field"><label htmlFor="bank-account-number">Account number</label><input id="bank-account-number" name="account_no" required inputMode="numeric" pattern="[0-9]{6,20}" autoComplete="off" /></div><div className="record-form__grid"><div className="field"><label htmlFor="bank-ifsc">IFSC</label><input id="bank-ifsc" name="ifsc" required pattern="[A-Za-z]{4}0[A-Za-z0-9]{6}" defaultValue={bankState.status === "ready" ? bankState.account.ifsc : ""} /></div><div className="field"><label htmlFor="bank-name">Bank name</label><input id="bank-name" name="bank_name" maxLength={100} defaultValue={bankState.status === "ready" ? bankState.account.bank_name ?? "" : ""} /></div></div>{error && <RequestError error={error} />}<div className="record-form__actions"><button className="button button--secondary" type="button" onClick={() => setBankEditing(false)}>Cancel</button><button className="button button--primary" type="submit" disabled={saving}>{saving ? "Saving…" : "Save bank account"}</button></div></form>}</section>}</div>
  </section>;
}

export function Vendors({ me }: { me: Me }) {
  const location = useLocation(); const prefix = "/app/master-data/parties/vendors/";
  if (location.pathname === `${prefix}new`) return me.permissions.includes("vendor.manage") ? <VendorCreatePage /> : <VendorList me={me} />;
  if (location.pathname.startsWith(prefix)) return <VendorDetail vendorId={location.pathname.slice(prefix.length)} me={me} />;
  return <VendorList me={me} />;
}
