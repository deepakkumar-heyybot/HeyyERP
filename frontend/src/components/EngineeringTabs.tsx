import { Link } from "react-router-dom";

export function EngineeringTabs({ active }: { active: "items" | "boms" | "routes" }) {
  return <nav className="organization-tabs" aria-label="Engineering masters">
    <Link className={active === "items" ? "active" : ""} aria-current={active === "items" ? "page" : undefined} to="/app/master-data/engineering">Items</Link>
    <Link className={active === "boms" ? "active" : ""} aria-current={active === "boms" ? "page" : undefined} to="/app/master-data/engineering/boms">Bills of material</Link>
    <Link className={active === "routes" ? "active" : ""} aria-current={active === "routes" ? "page" : undefined} to="/app/master-data/engineering/routes">Production routes</Link>
  </nav>;
}
