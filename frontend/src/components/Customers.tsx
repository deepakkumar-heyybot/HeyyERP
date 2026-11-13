import { FormEvent, useEffect, useState } from "react";
import { AlertCircle, ArrowLeft, ArrowUpRight, Building2, ChevronLeft, ChevronRight, MapPin, Plus, RefreshCw } from "lucide-react";
import { Link, useLocation, useNavigate, useSearchParams } from "react-router-dom";
import {
  ApiError,
  Customer,
  CustomerCreate,
  Me,
  Page,
  PaymentTerm,
  createCustomer,
  fetchAllPaymentTerms,
  fetchCustomer,
  listCustomers,
} from "../api-client";
import { PartyTabs } from "./PartyTabs";

type ListState = { status: "loading" } | { status: "ready"; page: Page<Customer>; terms: PaymentTerm[] } | { status: "error"; error: Error };
type FormState = { status: "loading" } | { status: "ready"; terms: PaymentTerm[] } | { status: "error"; error: Error };
const PAGE_SIZE = 20;

function StatusBadge({ active }: { active: boolean }) {
  return <span className={`status-badge status-badge--${active ? "approved" : "cancelled"}`}>{active ? "Active" : "Inactive"}</span>;
}

function RequestError({ error }: { error: Error }) {
  return <div className="decision-error" role="alert"><AlertCircle size={17} /><div><span>{error.message}</span>{error instanceof ApiError && error.status >= 500 && error.requestId && <span>Request ID: {error.requestId}</span>}</div></div>;
}

function CustomerList({ me }: { me: Me }) {
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const query = searchParams.get("q") ?? "";
  const activeParam = searchParams.get("active") ?? "";
  const active = activeParam === "true" ? true : activeParam === "false" ? false : null;
  const currentPage = Math.max(1, Number(searchParams.get("page") ?? "1") || 1);
  const [state, setState] = useState<ListState>({ status: "loading" });
  const [attempt, setAttempt] = useState(0);
  const canManage = me.permissions.includes("customer.manage");

  useEffect(() => {
    const controller = new AbortController();
    setState({ status: "loading" });
    void Promise.resolve().then(async () => {
      if (controller.signal.aborted) return;
      try {
        const [page, terms] = await Promise.all([
          listCustomers({ q: query.trim(), active, limit: PAGE_SIZE, offset: (currentPage - 1) * PAGE_SIZE }, controller.signal),
          fetchAllPaymentTerms(controller.signal),
        ]);
        setState({ status: "ready", page, terms });
      } catch (problem) {
        if (!controller.signal.aborted) setState({ status: "error", error: problem instanceof Error ? problem : new Error("Customers could not be loaded.") });
      }
    });
    return () => controller.abort();
  }, [active, attempt, currentPage, query]);

  function setParam(name: string, value: string, resetPage = true) {
    setSearchParams(current => { const next = new URLSearchParams(current); if (value) next.set(name, value); else next.delete(name); if (resetPage) next.delete("page"); return next; }, { replace: true });
  }
  const items = state.status === "ready" ? state.page.items : [];
  const total = state.status === "ready" ? state.page.total : 0;
  const termName = (id: string | null) => id ? state.status === "ready" ? state.terms.find(term => term.payment_term_id === id)?.name ?? "Unknown term" : "" : "Not assigned";
  const start = total ? (currentPage - 1) * PAGE_SIZE + 1 : 0;
  const end = Math.min(currentPage * PAGE_SIZE, total);
  const maxPage = Math.max(1, Math.ceil(total / PAGE_SIZE));

  return <section className="master-list" aria-labelledby="customers-title">
    <PartyTabs active="customers" />
    <div className="page-heading-row"><div><h2 id="customers-title">Customers</h2><p>Customer identity, GST registration, commercial terms, credit limit, and operating status.</p></div>{canManage && <button className="button button--primary" type="button" onClick={() => navigate("/app/master-data/parties/customers/new")}><Plus size={17} />Create customer</button>}</div>
    <div className="master-filters customer-filters"><div className="field"><label htmlFor="customer-search">Search customers</label><input id="customer-search" value={query} onChange={event => setParam("q", event.target.value)} placeholder="Code or customer name" /></div><div className="field"><label htmlFor="customer-status">Status filter</label><select id="customer-status" value={activeParam} onChange={event => setParam("active", event.target.value)}><option value="">All statuses</option><option value="true">Active</option><option value="false">Inactive</option></select></div><button className="button button--secondary" type="button" onClick={() => setAttempt(value => value + 1)} disabled={state.status === "loading"}><RefreshCw size={16} />Refresh</button></div>
    {state.status === "loading" && <div className="master-state"><span className="spinner" /><p>Loading customers…</p></div>}
    {state.status === "error" && <div className="inline-error" role="alert"><AlertCircle size={20} /><div><h3>Customers unavailable</h3><p>{state.error.message}</p>{state.error instanceof ApiError && state.error.status >= 500 && state.error.requestId && <p>Request ID: {state.error.requestId}</p>}</div><button className="button button--secondary" type="button" onClick={() => setAttempt(value => value + 1)}>Try again</button></div>}
    {state.status === "ready" && items.length === 0 && <div className="master-state"><Building2 size={28} /><h3>No customers found</h3><p>{query || active !== null ? "Change the search term or status filter." : "Create the first customer if you have permission."}</p></div>}
    {state.status === "ready" && items.length > 0 && <>
      <div className="master-table-wrap"><table className="master-table customer-table" aria-label="Customer results"><thead><tr><th>Code</th><th>Customer</th><th>GSTIN</th><th>Payment terms</th><th>Credit limit</th><th>Status</th><th><span className="sr-only">Actions</span></th></tr></thead><tbody>{items.map(customer => <tr key={customer.customer_id}><td><strong>{customer.code}</strong></td><td>{customer.name}</td><td className="legal-id">{customer.gstin ?? "Not registered"}</td><td>{termName(customer.payment_term_id)}</td><td className="money-value">{customer.credit_limit === null ? "No credit limit" : `₹${customer.credit_limit}`}</td><td><StatusBadge active={customer.active} /></td><td><Link className="icon-link" to={`/app/master-data/parties/customers/${customer.customer_id}`} aria-label={`Open customer ${customer.code}`}><ArrowUpRight size={17} /></Link></td></tr>)}</tbody></table></div>
      <ul className="master-cards" aria-label="Customer results">{items.map(customer => <li key={customer.customer_id}><div><span>{customer.code}</span><StatusBadge active={customer.active} /></div><h3>{customer.name}</h3><dl><div><dt>GSTIN</dt><dd className="legal-id">{customer.gstin ?? "Not registered"}</dd></div><div><dt>Terms</dt><dd>{termName(customer.payment_term_id)}</dd></div><div><dt>Credit</dt><dd>{customer.credit_limit === null ? "No credit limit" : `₹${customer.credit_limit}`}</dd></div></dl><Link className="button button--secondary" to={`/app/master-data/parties/customers/${customer.customer_id}`} aria-label={`Open customer ${customer.code}`}>Open customer<ArrowUpRight size={16} /></Link></li>)}</ul>
      <div className="reference-pagination"><p className="result-summary" aria-live="polite">Showing {start}–{end} of {total} records</p>{total > PAGE_SIZE && <div><button className="button button--secondary" type="button" aria-label="Previous page" disabled={currentPage === 1} onClick={() => setParam("page", String(currentPage - 1), false)}><ChevronLeft size={16} /></button><span>Page {currentPage} of {maxPage}</span><button className="button button--secondary" type="button" aria-label="Next page" disabled={currentPage === maxPage} onClick={() => setParam("page", String(currentPage + 1), false)}><ChevronRight size={16} /></button></div>}</div>
    </>}
  </section>;
}

function CustomerCreatePage() {
  const navigate = useNavigate();
  const [state, setState] = useState<FormState>({ status: "loading" });
  const [code, setCode] = useState("");
  const [name, setName] = useState("");
  const [gstin, setGstin] = useState("");
  const [paymentTermId, setPaymentTermId] = useState("");
  const [creditLimit, setCreditLimit] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<Error | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    void Promise.resolve().then(async () => {
      if (controller.signal.aborted) return;
      try { setState({ status: "ready", terms: await fetchAllPaymentTerms(controller.signal) }); }
      catch (problem) { if (!controller.signal.aborted) setState({ status: "error", error: problem instanceof Error ? problem : new Error("Payment terms could not be loaded.") }); }
    });
    return () => controller.abort();
  }, []);

  async function submit(event: FormEvent) {
    event.preventDefault(); setSaving(true); setError(null);
    const body: CustomerCreate = { code: code.trim().toUpperCase(), name: name.trim(), gstin: gstin.trim().toUpperCase() || null, payment_term_id: paymentTermId || null, credit_limit: creditLimit || null };
    try { const customer = await createCustomer(body); navigate(`/app/master-data/parties/customers/${customer.customer_id}`); }
    catch (problem) { setError(problem instanceof Error ? problem : new Error("The customer could not be created.")); }
    finally { setSaving(false); }
  }

  return <section className="master-detail customer-create" aria-labelledby="customer-create-title"><div className="master-detail__heading"><div><Link to="/app/master-data/parties"><ArrowLeft size={16} />Back to customers</Link><h2 id="customer-create-title">Create customer</h2></div></div>{state.status === "loading" && <div className="master-state"><span className="spinner" /><p>Loading customer form…</p></div>}{state.status === "error" && <div className="master-state"><AlertCircle size={28} /><h3>Customer form unavailable</h3><p>{state.error.message}</p></div>}{state.status === "ready" && <div className="form-panel"><form className="record-form" onSubmit={submit}><div className="record-form__heading"><div><h3>Customer master</h3><p>Enter the legal identity and approved commercial terms.</p></div></div><div className="record-form__grid"><div className="field"><label htmlFor="customer-code">Customer code</label><input id="customer-code" required maxLength={50} value={code} onChange={event => setCode(event.target.value)} /></div><div className="field"><label htmlFor="customer-name">Customer name</label><input id="customer-name" required maxLength={160} value={name} onChange={event => setName(event.target.value)} /></div><div className="field"><label htmlFor="customer-gstin">GSTIN</label><input id="customer-gstin" maxLength={15} value={gstin} onChange={event => setGstin(event.target.value)} /></div><div className="field"><label htmlFor="customer-payment-term">Payment term</label><select id="customer-payment-term" value={paymentTermId} onChange={event => setPaymentTermId(event.target.value)}><option value="">Not assigned</option>{state.terms.map(term => <option key={term.payment_term_id} value={term.payment_term_id}>{term.name}</option>)}</select></div><div className="field"><label htmlFor="customer-credit-limit">Credit limit</label><input id="customer-credit-limit" inputMode="decimal" pattern="\d+(\.\d{1,2})?" value={creditLimit} onChange={event => setCreditLimit(event.target.value)} /></div></div>{error && <RequestError error={error} />}<div className="record-form__actions"><Link className="button button--secondary" to="/app/master-data/parties">Cancel</Link><button className="button button--primary" type="submit" disabled={saving}>{saving ? "Saving…" : "Save customer"}</button></div></form></div>}</section>;
}

type DetailState = { status: "loading" } | { status: "ready"; customer: Customer; terms: PaymentTerm[] } | { status: "error"; error: Error };
function CustomerDetail({ customerId }: { customerId: string }) {
  const [state, setState] = useState<DetailState>({ status: "loading" });
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    setState({ status: "loading" });
    void Promise.resolve().then(async () => {
      if (controller.signal.aborted) return;
      try {
        const [customer, terms] = await Promise.all([fetchCustomer(customerId, controller.signal), fetchAllPaymentTerms(controller.signal)]);
        setState({ status: "ready", customer, terms });
      } catch (problem) {
        if (!controller.signal.aborted) setState({ status: "error", error: problem instanceof Error ? problem : new Error("Customer details could not be loaded.") });
      }
    });
    return () => controller.abort();
  }, [attempt, customerId]);

  if (state.status === "loading") return <section className="master-detail"><div className="master-state"><span className="spinner" /><p>Loading customer details…</p></div></section>;
  if (state.status === "error") return <section className="master-detail"><div className="inline-error" role="alert"><AlertCircle size={20} /><div><h3>Customer unavailable</h3><p>{state.error.message}</p>{state.error instanceof ApiError && state.error.status >= 500 && state.error.requestId && <p>Request ID: {state.error.requestId}</p>}</div><button className="button button--secondary" type="button" onClick={() => setAttempt(value => value + 1)}>Try again</button></div></section>;

  const { customer, terms } = state;
  const paymentTerm = terms.find(term => term.payment_term_id === customer.payment_term_id)?.name ?? (customer.payment_term_id ? "Unknown term" : "Not assigned");

  return <section className="master-detail customer-detail" aria-labelledby="customer-detail-title">
    <nav className="customer-sections" aria-label="Customer sections">
      <Link className="active" to={`/app/master-data/parties/customers/${customerId}`}>Master details</Link>
    </nav>
    <div className="master-detail__heading"><div><Link to="/app/master-data/parties"><ArrowLeft size={16} />Back to customers</Link><p className="eyebrow">{customer.code}</p><h2 id="customer-detail-title">{customer.name}</h2><StatusBadge active={customer.active} /></div></div>
    <div className="master-detail__grid"><section className="record-panel"><div className="record-panel__heading"><div><h3>Customer master</h3><p>Legal identity and approved commercial terms.</p></div><Building2 size={20} /></div><dl className="record-facts"><div><dt>Customer code</dt><dd>{customer.code}</dd></div><div><dt>GSTIN</dt><dd className="legal-id">{customer.gstin ?? "Not registered"}</dd></div><div><dt>Payment term</dt><dd>{paymentTerm}</dd></div><div><dt>Credit limit</dt><dd className="money-value">{customer.credit_limit === null ? "No credit limit" : `₹${customer.credit_limit}`}</dd></div></dl></section><section className="record-panel"><div className="record-panel__heading"><div><h3>Billing address</h3><p>Saved billing address details are unavailable.</p></div><MapPin size={20} /></div><div className="address-gap"><AlertCircle size={18} /><div><strong>Address read-back unavailable</strong><p>The backend does not provide the saved address on read.</p></div></div></section></div>
  </section>;
}

export function Customers({ me }: { me: Me }) {
  const location = useLocation();
  const prefix = "/app/master-data/parties/customers/";
  if (location.pathname === `${prefix}new`) return me.permissions.includes("customer.manage") ? <CustomerCreatePage /> : <CustomerList me={me} />;
  if (location.pathname.startsWith(prefix)) {
    const [customerId, child] = location.pathname.slice(prefix.length).split("/");
    if (child === "credit" || child === "statement") return <section className="master-detail" aria-labelledby="customer-excluded-title"><div className="master-detail__heading"><div><Link to="/app/master-data/parties"><ArrowLeft size={16} />Back to customers</Link><h2 id="customer-excluded-title">Customer {child} not included</h2><p>This workflow is not included in this handover.</p></div></div><Link className="button button--secondary" to={`/app/master-data/parties/customers/${customerId}`}>View customer master</Link></section>;
    return <CustomerDetail customerId={customerId} />;
  }
  return <CustomerList me={me} />;
}
