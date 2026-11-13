import { Link } from "react-router-dom";

export function PartyTabs({ active }: { active: "customers" | "vendors" }) {
  return <nav className="organization-tabs" aria-label="Party types">
    <Link className={active === "customers" ? "active" : ""} to="/app/master-data/parties">Customers</Link>
    <Link className={active === "vendors" ? "active" : ""} to="/app/master-data/parties/vendors">Vendors</Link>
  </nav>;
}
