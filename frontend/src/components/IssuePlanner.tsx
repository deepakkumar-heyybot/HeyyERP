import { useEffect, useRef, useState, type FormEvent } from "react";
import { ArrowLeft, Plus, Trash2 } from "lucide-react";
import { Link } from "react-router-dom";
import { BusinessRule, Item, IssueInput, IssueOut, IssuePlan, Me, Store, fetchAllItems, fetchAllStores, planStockIssue, postStockIssue } from "../api-client";
import { useActivePlant } from "../plant-context";
import { FormPageTemplate, DetailPageTemplate } from "./PageTemplates";
import { InventoryTabs } from "./InventoryTabs";
import { RequestError } from "./RequestError";

type Load<T> = { status: "loading" } | { status: "ready"; data: T } | { status: "error"; error: Error };
type Entry = { key: number; itemId: string; quantity: string };
const failure = (value: unknown, fallback: string) => value instanceof Error ? value : new Error(fallback);
const scaled = (value: string) => { const [whole, fraction = ""] = value.split("."); return BigInt(whole) * 1000n + BigInt(fraction.padEnd(3, "0")); };
const validQuantity = (value: string) => /^\d{1,10}(?:\.\d{1,3})?$/.test(value) && scaled(value) > 0n && scaled(value) <= 1_000_000_000_000n;

function IssuePlannerBody({ me, settings }: { me: Me; settings: BusinessRule[] }) {
  const { activePlantId } = useActivePlant();
  const nextKey = useRef(2);
  const previewController = useRef<AbortController | null>(null);
  const [stores, setStores] = useState<Load<Store[]>>({ status: "loading" });
  const [items, setItems] = useState<Load<Item[]>>({ status: "loading" });
  const [storeId, setStoreId] = useState("");
  const [entries, setEntries] = useState<Entry[]>([{ key: 1, itemId: "", quantity: "" }]);
  const [purpose, setPurpose] = useState("");
  const [reference, setReference] = useState("");
  const [preview, setPreview] = useState<IssuePlan | null>(null);
  const [posted, setPosted] = useState<IssueOut | null>(null);
  const [previewBusy, setPreviewBusy] = useState(false);
  const [postBusy, setPostBusy] = useState(false);
  const [error, setError] = useState<Error | null>(null);
  const configuredRule = settings.find(rule => rule.key === "issueRule")?.value;

  useEffect(() => {
    const controller = new AbortController();
    void fetchAllItems(controller.signal).then(data => { if (!controller.signal.aborted) setItems({ status: "ready", data: data.filter(item => item.active) }); }).catch(value => { if (!controller.signal.aborted) setItems({ status: "error", error: failure(value, "Active items could not be loaded.") }); });
    return () => controller.abort();
  }, []);
  useEffect(() => {
    const controller = new AbortController();
    setStores({ status: "loading" }); setStoreId(""); setPreview(null);
    void fetchAllStores(true, activePlantId === "all" ? null : activePlantId || null, null, controller.signal).then(data => {
      if (!controller.signal.aborted) setStores({ status: "ready", data: data.filter(store => store.active && (me.all_plants || me.unit_ids.includes(store.unit_id))) });
    }).catch(value => { if (!controller.signal.aborted) setStores({ status: "error", error: failure(value, "Active stores could not be loaded.") }); });
    return () => controller.abort();
  }, [activePlantId, me.all_plants, me.unit_ids]);
  useEffect(() => () => previewController.current?.abort(), []);

  function invalidate() { previewController.current?.abort(); setPreview(null); setError(null); }
  function updateEntry(key: number, change: Partial<Entry>) { invalidate(); setEntries(current => current.map(entry => entry.key === key ? { ...entry, ...change } : entry)); }
  function input(): IssueInput { return { store_id: storeId, lines: entries.map(entry => ({ item_id: entry.itemId, quantity: entry.quantity })) }; }
  function validate(): string | null {
    if (stores.status !== "ready" || !stores.data.some(store => store.store_id === storeId)) return "Select an active store in an authorized plant.";
    if (items.status !== "ready" || entries.length < 1 || entries.length > 50 || entries.some(entry => !items.data.some(item => item.item_id === entry.itemId) || !validQuantity(entry.quantity))) return "Select an active item and a valid quantity for every line (up to three decimal places).";
    if (new Set(entries.map(entry => entry.itemId)).size !== entries.length) return "Choose distinct items for each issue line.";
    if (purpose.trim().length < 3 || purpose.trim().length > 200) return "Purpose must contain 3 to 200 characters.";
    if (reference.trim().length > 60) return "Reference must contain at most 60 characters.";
    return null;
  }
  async function previewIssue(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); invalidate();
    const problem = validate();
    if (problem) { setError(new Error(problem)); return; }
    const controller = new AbortController();
    previewController.current = controller;
    setPreviewBusy(true);
    try { const result = await planStockIssue(input(), controller.signal); if (!controller.signal.aborted) setPreview(result); }
    catch (value) { if (!controller.signal.aborted) setError(failure(value, "Issue preview could not be loaded.")); }
    finally { if (previewController.current === controller) { previewController.current = null; setPreviewBusy(false); } }
  }
  async function confirmIssue() {
    if (!preview?.can_issue || postBusy) return;
    const problem = validate();
    if (problem) { setError(new Error(problem)); setPreview(null); return; }
    setPostBusy(true); setError(null);
    try { setPosted(await postStockIssue({ ...input(), purpose: purpose.trim(), reference: reference.trim() || null, use_reservation: null })); }
    catch (value) { setError(failure(value, "Stock issue could not be posted.")); setPreview(null); }
    finally { setPostBusy(false); }
  }
  function reset() { invalidate(); setPosted(null); setStoreId(""); setEntries([{ key: nextKey.current++, itemId: "", quantity: "" }]); setPurpose(""); setReference(""); }

  if (posted) return <DetailPageTemplate className="issue-planner" title="Issue posted" navigation={<InventoryTabs active="issues" canIssue />} back={<Link to="/app/inventory/stock"><ArrowLeft size={16} />Back to stock</Link>} actions={<button className="button button--secondary" type="button" onClick={reset}>Start another issue</button>}>
    <section className="record-panel issue-planner__posted"><div className="record-panel__heading"><h3>{posted.issue_no}</h3><span className="status-badge">Posted</span></div><dl className="record-facts"><div><dt>Store</dt><dd>{posted.store_code}</dd></div><div><dt>Purpose</dt><dd>{posted.purpose}</dd></div><div><dt>Reference</dt><dd>{posted.reference ?? "Not provided"}</dd></div><div><dt>Issued by</dt><dd>{posted.issued_by_name ?? "Not available"}</dd></div><div><dt>Issued at</dt><dd>{posted.issued_at.slice(0, 16).replace("T", " ")}</dd></div><div><dt>Total cost</dt><dd>{posted.total_cost}</dd></div></dl><ul className="issue-planner__posted-lines" aria-label="Issued lots">{posted.lines.map((line, index) => <li key={`${line.lot_no}:${index}`}>{line.item_code} · {line.lot_no} · {line.quantity} · Cost {line.cost}</li>)}</ul></section>
  </DetailPageTemplate>;

  return <FormPageTemplate className="issue-planner" title="Issue planner" headingId="issue-planner-title" description="Preview backend-selected lots before posting. Stock and issue rules can change before confirmation." navigation={<InventoryTabs active="issues" canIssue />} back={<Link to="/app/inventory/stock"><ArrowLeft size={16} />Back to stock</Link>} formTitle="Issue details" onSubmit={previewIssue} formActions={<><button className="button button--secondary" type="submit" disabled={previewBusy || postBusy || stores.status !== "ready" || !stores.data.length || items.status !== "ready" || !items.data.length}>{previewBusy ? "Planning…" : "Preview issue"}</button><button className="button button--primary" type="button" disabled={!preview?.can_issue || previewBusy || postBusy} onClick={() => void confirmIssue()}>{postBusy ? "Posting…" : "Confirm issue"}</button></>}>
    {configuredRule === "FEFO" || configuredRule === "FIFO" ? <p className="indent-list__filter-note">Configured issue rule: {configuredRule}. The preview reports the rule actually used.</p> : <p className="indent-list__filter-note">The backend reports the current issue rule in the preview.</p>}
    <fieldset disabled={previewBusy || postBusy} className="issue-planner__fieldset"><div className="record-form__grid"><div className="field"><label htmlFor="issue-store">Store</label><select id="issue-store" value={storeId} onChange={event => { invalidate(); setStoreId(event.target.value); }}><option value="">Select active store</option>{stores.status === "ready" && stores.data.map(store => <option key={store.store_id} value={store.store_id}>{store.code} — {store.name}</option>)}</select></div><div className="field"><label htmlFor="issue-purpose">Purpose</label><input id="issue-purpose" value={purpose} maxLength={200} onChange={event => { invalidate(); setPurpose(event.target.value); }} /></div><div className="field"><label htmlFor="issue-reference">Reference (optional)</label><input id="issue-reference" value={reference} maxLength={60} onChange={event => { invalidate(); setReference(event.target.value); }} /></div></div>
      <div className="record-form__heading"><h3>Items to issue</h3><p>Choose 1–50 distinct active items. Quantities retain up to three decimal places.</p></div><div className="issue-planner__lines">{entries.map((entry, index) => <div className="issue-planner__line" key={entry.key}><div className="field"><label htmlFor={`issue-item-${entry.key}`}>Item for line {index + 1}</label><select id={`issue-item-${entry.key}`} value={entry.itemId} onChange={event => updateEntry(entry.key, { itemId: event.target.value })}><option value="">Select item</option>{items.status === "ready" && items.data.map(item => <option key={item.item_id} value={item.item_id}>{item.code} — {item.name}</option>)}</select></div><div className="field"><label htmlFor={`issue-qty-${entry.key}`}>Quantity for line {index + 1}</label><input id={`issue-qty-${entry.key}`} inputMode="decimal" value={entry.quantity} onChange={event => updateEntry(entry.key, { quantity: event.target.value })} /></div>{entries.length > 1 && <button className="button button--secondary" type="button" aria-label={`Remove line ${index + 1}`} onClick={() => { invalidate(); setEntries(current => current.filter(line => line.key !== entry.key)); }}><Trash2 size={16} /></button>}</div>)}</div><button className="button button--secondary" type="button" disabled={entries.length >= 50} onClick={() => { invalidate(); setEntries(current => [...current, { key: nextKey.current++, itemId: "", quantity: "" }]); }}><Plus size={16} />Add item</button>
    </fieldset>
    {stores.status === "loading" && <p role="status">Loading active stores…</p>}{stores.status === "error" && <RequestError error={stores.error} />}{stores.status === "ready" && !stores.data.length && <p>No active stores are available in the selected plant.</p>}
    {items.status === "loading" && <p role="status">Loading active items…</p>}{items.status === "error" && <RequestError error={items.error} />}{items.status === "ready" && !items.data.length && <p>No active items are available for an issue.</p>}
    {preview && <section className="issue-planner__preview" aria-labelledby="issue-preview-title"><div className="record-panel__heading"><h3 id="issue-preview-title">Issue preview</h3><span className="status-badge">{preview.can_issue ? "Ready to post" : "Insufficient stock"}</span></div><p>Selection rule: <strong>{preview.rule}</strong>. Posting rechecks stock and may still return a conflict.</p><div className="issue-planner__plan-lines">{preview.lines.map(line => <section key={line.item_id} className="issue-planner__plan-line"><h4>{line.item_code} · Requested {line.requested}</h4><div className="issue-planner__plan-columns"><div><h5>Selected lots</h5>{line.picks.length ? <ul>{line.picks.map(pick => <li key={pick.lot_id}><strong>{pick.lot_no}</strong><span>{pick.quantity}</span><small>Expiry {pick.expiry_at?.slice(0, 10) ?? "Not set"}</small></li>)}</ul> : <p>No valid lots selected.</p>}</div><div><h5>Blocked lots</h5>{line.blocked.length ? <ul>{line.blocked.map(blocked => <li key={blocked.lot_id}><strong>{blocked.lot_no}</strong><span>{blocked.quantity}</span><small>{blocked.reason}</small></li>)}</ul> : <p>No blocked lots reported.</p>}</div></div><p className="issue-planner__shortfall">Shortfall <strong>{line.shortfall}</strong></p></section>)}</div></section>}
    {error && <RequestError error={error} />}
  </FormPageTemplate>;
}

export function IssuePlanner({ me, settings }: { me: Me; settings: BusinessRule[] }) {
  if (!me.modules.includes("inventory") || !me.permissions.includes("inv.issue")) return <section className="module-pending"><h2>Issue planning unavailable</h2><p>Posting stock issues requires the inventory module and inv.issue permission.</p></section>;
  return <IssuePlannerBody me={me} settings={settings} />;
}
