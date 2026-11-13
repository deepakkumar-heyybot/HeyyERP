import { Link } from "react-router-dom";

export function ProcurementTabs({ active }: { active: "indents" | "rfqs" | "orders" }) {
  return <nav className="organization-tabs" aria-label="Procurement pages">
    <Link className={active === "indents" ? "active" : ""} aria-current={active === "indents" ? "page" : undefined} to="/app/procurement/indents">Indents</Link>
    <Link className={active === "rfqs" ? "active" : ""} aria-current={active === "rfqs" ? "page" : undefined} to="/app/procurement/rfqs">RFQs</Link>
    <Link className={active === "orders" ? "active" : ""} aria-current={active === "orders" ? "page" : undefined} to="/app/procurement/purchase-orders">Purchase orders</Link>
  </nav>;
}
