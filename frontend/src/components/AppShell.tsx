import { OrganizationTabs } from "./OrganizationTabs";
import { BatchRegister } from "./BatchRegister";
import { RefObject, useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import {
  Bell,
  Boxes,
  CheckSquare2,
  ChevronRight,
  ClipboardCheck,
  FileBarChart2,
  Gauge,
  Hammer,
  HeartHandshake,
  Home,
  Landmark,
  LogOut,
  Menu,
  PackageSearch,
  Search,
  Settings,
  ShieldAlert,
  ShoppingCart,
  Sparkles,
  Users,
  Warehouse,
  Wrench,
  X,
} from "lucide-react";
import { Link, NavLink, useLocation, useNavigate } from "react-router-dom";
import {
  ApiError,
  ApprovalCount,
  BusinessRule,
  Me,
  NotificationCount,
  Unit,
  fetchApprovalCount,
  fetchMe,
  fetchNotificationCount,
  fetchSettings,
  fetchAllUnits,
} from "../api-client";
import { authClient } from "../auth-client";
import { ActivePlantContext } from "../plant-context";
import { ApprovalInbox } from "./ApprovalInbox";
import { ApprovalDetail } from "./ApprovalDetail";
import { NotificationInbox } from "./NotificationInbox";
import { OrganizationDepartments } from "./OrganizationDepartments";
import { OrganizationStores } from "./OrganizationStores";
import { ReferenceData } from "./ReferenceData";
import { Customers } from "./Customers";
import { Vendors } from "./Vendors";
import { Items } from "./Items";
import { Boms } from "./Boms";
import { Routes } from "./Routes";
import { Indents } from "./Indents";
import { Rfqs } from "./Rfqs";
import { PurchaseOrders } from "./PurchaseOrders";
import { Grns } from "./Grns";
import { StockExplorer } from "./StockExplorer";
import { IssuePlanner } from "./IssuePlanner";
import { Reservations } from "./Reservations";
import { BatchDetail } from "./BatchDetail";
import { ProductionLotRegister } from "./ProductionLotRegister";
import { ProductionLotCreate } from "./ProductionLotCreate";
import { ProductionLotDetail } from "./ProductionLotDetail";
import { ShiftEntries } from "./ShiftEntries";
import { ManufacturingPerformance } from "./ManufacturingPerformance";
import { QualityQueue } from "./QualityQueue";
import { InspectionPlanCreatePage, InspectionPlanDetail, InspectionPlanRegister } from "./InspectionPlans";
import { InspectionCreatePage, InspectionDetail, InspectionRegister } from "./QualityInspections";
import { QualityHoldCreate, QualityHoldDetail, QualityHoldRegister } from "./QualityHolds";
import { BrandMark } from "./Brand";

type NavItem = {
  label: string;
  href: string;
  icon: typeof Home;
  module?: string | string[];
  permission?: string;
};

type NavGroup = { label: string; items: NavItem[] };

const navigation: NavGroup[] = [
  {
    label: "Main",
    items: [
      { label: "Dashboard", href: "/app", icon: Home, module: "home" },
      { label: "Approvals", href: "/app/approvals", icon: CheckSquare2, module: "approvals" },
      { label: "Notifications", href: "/app/notifications", icon: Bell },
    ],
  },
  {
    label: "Master data",
    items: [
      { label: "Organization", href: "/app/master-data/organization", icon: Boxes },
      { label: "Parties", href: "/app/master-data/parties", icon: Users },
      { label: "Engineering", href: "/app/master-data/engineering", icon: PackageSearch },
      { label: "Reference data", href: "/app/master-data/reference-data", icon: Settings },
    ],
  },
  {
    label: "Operations",
    items: [
      { label: "Sales and Dispatch", href: "/app/sales", icon: ShoppingCart, module: "sales" },
      { label: "Procurement", href: "/app/procurement", icon: PackageSearch, module: "procurement" },
      { label: "Inventory", href: "/app/inventory", icon: Warehouse, module: "inventory" },
      { label: "Manufacturing", href: "/app/manufacturing", icon: Hammer, module: "manufacturing" },
      { label: "Quality", href: "/app/quality", icon: ClipboardCheck, module: "quality" },
      { label: "Maintenance", href: "/app/maintenance", icon: Wrench, module: "maintenance" },
    ],
  },
  {
    label: "Business",
    items: [
      { label: "Finance and GST", href: "/app/finance", icon: Landmark, module: ["finance", "gst"] },
      { label: "HR", href: "/app/hr", icon: Users, module: "hr" },
      { label: "Kaizen", href: "/app/kaizen", icon: Sparkles, module: "kaizen" },
      { label: "Reports", href: "/app/reports", icon: FileBarChart2, module: "reports" },
    ],
  },
  {
    label: "Management",
    items: [
      { label: "Administration", href: "/app/administration", icon: Settings, module: "admin", permission: "admin" },
    ],
  },
];

function canSee(me: Me, item: NavItem) {
  const modules = item.module ? (Array.isArray(item.module) ? item.module : [item.module]) : [];
  const moduleAllowed = modules.length === 0 || modules.some(module => me.modules.includes(module))
    || (item.href === "/app/procurement" && me.permissions.includes("indent.raise"));
  const permissionAllowed = !item.permission || me.permissions.includes(item.permission);
  return moduleAllowed && permissionAllowed;
}

function useStateHeadingFocus() {
  const headingRef = useRef<HTMLHeadingElement>(null);
  useEffect(() => { headingRef.current?.focus(); }, []);
  return headingRef;
}

function ErrorState({ error, onRetry }: { error: ApiError | Error; onRetry: () => void }) {
  const headingRef = useStateHeadingFocus();
  const [copyStatus, setCopyStatus] = useState<"idle" | "copied" | "failed">("idle");
  const status = error instanceof ApiError ? error.status : 0;
  const requestId = status >= 500 && error instanceof ApiError ? error.requestId : undefined;
  const heading = status === 409
    ? "Workspace changed"
    : status === 404
      ? "Page not found"
      : status >= 500
        ? "Service temporarily unavailable"
        : "Workspace access could not be confirmed";
  const retryLabel = status === 409 ? "Refresh workspace" : "Try again";
  const message = error instanceof ApiError
    ? error.message
    : "The workspace could not be reached. Check your connection and try again.";

  const copyRequestId = async () => {
    if (!requestId) return;
    try {
      await navigator.clipboard.writeText(requestId);
      setCopyStatus("copied");
    } catch {
      setCopyStatus("failed");
    }
  };

  return (
    <main className="system-state">
      <div className="system-state__icon"><ShieldAlert size={26} /></div>
      <h1 ref={headingRef} tabIndex={-1}>{heading}</h1>
      <p role="alert">{message}</p>
      {requestId && (
        <div className="request-id">
          <span>Request ID</span>
          <code>{requestId}</code>
          <button type="button" aria-label="Copy request ID" onClick={copyRequestId}>{copyStatus === "copied" ? "Copied" : "Copy"}</button>
          {copyStatus === "failed" && <span role="alert">Copy failed. Select the request ID and copy it manually.</span>}
        </div>
      )}
      <div className="system-state__actions">
        <button className="button button--primary" type="button" onClick={onRetry}>{retryLabel}</button>
        <button className="button button--secondary" type="button" onClick={async () => { await authClient.signOut(); location.assign("/"); }}>Sign out</button>
      </div>
    </main>
  );
}

function blockedAccessKind(message: string) {
  if (/ERP account is\s+(inactive|deactivated|locked|suspended|disabled)/i.test(message)) return "unavailable" as const;
  if (/No ERP profile/i.test(message)) return "unprovisioned" as const;
  return "denied" as const;
}

function AccessBlockedState({ error, onRetry }: { error: ApiError; onRetry: () => void }) {
  const headingRef = useStateHeadingFocus();
  const kind = blockedAccessKind(error.message);
  const heading = kind === "unavailable"
    ? "ERP account unavailable"
    : kind === "unprovisioned"
      ? "ERP access not provisioned"
      : "Workspace access denied";
  const guidance = kind === "unavailable"
    ? "Ask your ERP administrator to review the status of your account."
    : kind === "unprovisioned"
      ? "An ERP administrator must assign your role and access scope before you can continue."
      : "Use an authorized V-Rub account, or ask an administrator to review your organization access.";

  return (
    <main className="system-state">
      <div className="system-state__icon"><ShieldAlert size={26} /></div>
      <h1 ref={headingRef} tabIndex={-1}>{heading}</h1>
      <p role="alert">{error.message}</p>
      <p>{guidance}</p>
      <div className="system-state__actions">
        <button className="button button--primary" type="button" onClick={onRetry}>Check access again</button>
        <button className="button button--secondary" type="button" onClick={async () => { await authClient.signOut(); location.assign("/"); }}>Sign out</button>
      </div>
    </main>
  );
}

function NoPlantState({ onRetry }: { onRetry: () => void }) {
  const headingRef = useStateHeadingFocus();
  return (
    <main className="system-state">
      <div className="system-state__icon"><Boxes size={26} /></div>
      <h1 ref={headingRef} tabIndex={-1}>No plant access assigned</h1>
      <p role="alert">Your account is active, but it is not assigned to a V-Rub plant. Ask an administrator to update your access.</p>
      <div className="system-state__actions">
        <button className="button button--primary" type="button" onClick={onRetry}>Check access again</button>
        <button className="button button--secondary" type="button" onClick={async () => { await authClient.signOut(); location.assign("/"); }}>Sign out</button>
      </div>
    </main>
  );
}

function PlantSelect({ me, units, activePlantId, onChange, className = "" }: { me: Me; units: Unit[]; activePlantId: string; onChange: (plantId: string) => void; className?: string }) {
  return (
    <label className={className}>
      <span className={className === "plant-selector" ? "sr-only" : undefined}>Active plant</span>
      <select aria-label="Active plant" value={activePlantId} onChange={event => onChange(event.target.value)}>
        {!me.all_plants && <option value="">Select plant</option>}
        {me.all_plants && <option value="all">All plants</option>}
        {units.map(unit => <option key={unit.unit_id} value={unit.unit_id}>{unit.code} · {unit.name}</option>)}
      </select>
    </label>
  );
}

function AppSidebar({ me, units, activePlantId, onPlantChange, open, mobile, onClose, sidebarRef }: { me: Me; units: Unit[]; activePlantId: string; onPlantChange: (plantId: string) => void; open: boolean; mobile: boolean; onClose: () => void; sidebarRef: RefObject<HTMLElement | null> }) {
  const groups = useMemo(() => navigation
    .map(group => ({ ...group, items: group.items.filter(item => canSee(me, item)) }))
    .filter(group => group.items.length > 0), [me]);

  return (
    <>
      {mobile && open && <button className="sidebar-scrim sidebar-scrim--open" aria-label="Close navigation" type="button" onClick={onClose} />}
      <aside
        ref={sidebarRef}
        className={open ? "app-sidebar app-sidebar--open" : "app-sidebar"}
        aria-hidden={mobile && !open ? true : undefined}
        aria-modal={mobile && open ? true : undefined}
        aria-label={mobile && open ? "Application navigation" : undefined}
        role={mobile && open ? "dialog" : undefined}
        inert={mobile && !open ? true : undefined}
      >
        <div className="app-sidebar__brand">
          <BrandMark />
          <button className="sidebar-close" type="button" aria-label="Close navigation" onClick={onClose}><X size={20} /></button>
        </div>
        <nav aria-label="Primary navigation" className="app-nav">
          {groups.map(group => (
            <div className="app-nav__group" key={group.label}>
              <span className="app-nav__label">{group.label}</span>
              {group.items.map(item => {
                const Icon = item.icon;
                return (
                  <NavLink key={item.href} to={item.href} end={item.href === "/app"} onClick={onClose} aria-label={item.label} title={item.label}>
                    <Icon size={19} aria-hidden="true" />
                    <span>{item.label}</span>
                  </NavLink>
                );
              })}
            </div>
          ))}
        </nav>
        {(me.all_plants || units.length > 1) && <PlantSelect me={me} units={units} activePlantId={activePlantId} onChange={onPlantChange} className="sidebar-plant" />}
        <div className="sidebar-profile">
          <div className="avatar" aria-hidden="true">{me.display_name.trim().charAt(0).toUpperCase() || "U"}</div>
          <div><strong>{me.display_name}</strong><span>{me.role.replaceAll("_", " ")}</span></div>
          <button type="button" aria-label="Sign out" onClick={async () => { await authClient.signOut(); location.assign("/"); }}><LogOut size={18} /></button>
        </div>
      </aside>
    </>
  );
}

function Dashboard({ approvalCount, notificationCount, showApprovals }: { approvalCount: ApprovalCount | null; notificationCount: NotificationCount | null; showApprovals: boolean }) {
  return (
    <div className="dashboard">
      <section className="summary-grid" aria-label="Work summary">
        {showApprovals && <Link className="summary-card" to="/app/approvals">
          <div className="summary-card__icon"><CheckSquare2 size={22} /></div>
          <div><span>Pending approvals</span><strong>{approvalCount?.pending ?? "—"}</strong><small>Waiting for your action</small></div>
          <ChevronRight size={18} aria-hidden="true" />
        </Link>}
        {showApprovals && <Link className="summary-card" to="/app/approvals">
          <div className="summary-card__icon summary-card__icon--warning"><ShieldAlert size={22} /></div>
          <div><span>Overdue approvals</span><strong>{approvalCount?.overdue ?? "—"}</strong><small>Past the configured SLA</small></div>
          <ChevronRight size={18} aria-hidden="true" />
        </Link>}
        <Link className="summary-card" to="/app/notifications">
          <div className="summary-card__icon"><Bell size={22} /></div>
          <div><span>Unread notifications</span><strong>{notificationCount?.unread ?? "—"}</strong><small>New in-app messages</small></div>
          <ChevronRight size={18} aria-hidden="true" />
        </Link>
      </section>

      <section className="workspace-panel">
        <div className="workspace-panel__heading">
          <div><h2>Work requiring attention</h2><p>Live counts from the approvals and notifications services.</p></div>
          {showApprovals && <Link className="button button--secondary" to="/app/approvals">Open approvals</Link>}
        </div>
        <div className="attention-list">
          {showApprovals && <Link to="/app/approvals">
            <span className="attention-list__icon"><CheckSquare2 size={20} /></span>
            <span><strong>Approval inbox</strong><small>{approvalCount ? `${approvalCount.pending} pending · ${approvalCount.overdue} overdue` : "Count temporarily unavailable"}</small></span>
            <ChevronRight size={19} />
          </Link>}
          <Link to="/app/notifications">
            <span className="attention-list__icon"><Bell size={20} /></span>
            <span><strong>Notifications</strong><small>{notificationCount ? `${notificationCount.unread} unread messages` : "Count temporarily unavailable"}</small></span>
            <ChevronRight size={19} />
          </Link>
        </div>
      </section>
    </div>
  );
}

export function AppShell({ me, settings, units }: { me: Me; settings: BusinessRule[]; units: Unit[] }) {
  const navigate = useNavigate();
  const location = useLocation();
  const [menuOpen, setMenuOpen] = useState(false);
  const [mobileNavigation, setMobileNavigation] = useState(() => window.matchMedia("(max-width: 860px)").matches);
  const sidebarRef = useRef<HTMLElement>(null);
  const pageHeadingRef = useRef<HTMLHeadingElement>(null);
  const [search, setSearch] = useState("");
  const [approvalCount, setApprovalCount] = useState<ApprovalCount | null>(null);
  const [notificationCount, setNotificationCount] = useState<NotificationCount | null>(null);
  const updateNotificationCount = useCallback((unread: number) => setNotificationCount({ unread }), []);
  const lastCountRefreshAt = useRef(0);
  const authorizedUnits = useMemo(() => units.filter(unit => me.all_plants || me.unit_ids.includes(unit.unit_id)), [me, units]);
  const [activePlantId, setActivePlantId] = useState(() => me.all_plants ? "all" : authorizedUnits.length === 1 ? authorizedUnits[0].unit_id : "");

  const allItems = useMemo(() => navigation.flatMap(group => group.items), []);
  const visibleItems = useMemo(() => allItems.filter(item => canSee(me, item)), [allItems, me]);
  const matchesRoute = (item: NavItem) => item.href === location.pathname || (item.href !== "/app" && location.pathname.startsWith(`${item.href}/`));
  const knownItem = allItems.find(matchesRoute);
  const activeItem = visibleItems.find(matchesRoute);
  const routeForbidden = Boolean(knownItem && !activeItem);
  const pageTitle = routeForbidden ? "Access denied" : knownItem?.label ?? "Page not found";
  const pageSubtitle = location.pathname === "/app"
    ? "Overview of your authorized operational work"
    : location.pathname === "/app/approvals"
      ? "Review assigned workflow decisions and their current state"
      : location.pathname.startsWith("/app/approvals/")
        ? "Inspect the approval route and record a permitted decision"
        : location.pathname === "/app/notifications"
          ? "Review operational alerts and open supported records"
        : location.pathname === "/app/master-data/reference-data"
          ? "Maintain shared commercial, tax, category, and measurement references"
        : location.pathname.startsWith("/app/master-data/parties")
          ? "Maintain customer and vendor identities and commercial terms"
        : location.pathname.startsWith("/app/master-data/organization")
          ? "Maintain legal plants, GST registrations, and organization structure"
        : location.pathname.startsWith("/app/procurement")
          ? "Track material and capital requests by plant, requester, and approval state"
      : "Authorized workspace · implementation in progress";
  const hasApprovals = me.modules.includes("approvals");
  const hasActivePlant = Boolean(activePlantId);
  const recoveryHref = visibleItems[0]?.href ?? "/";
  const plantContext = useMemo(() => ({
    activePlantId,
    activeUnit: activePlantId === "all" ? null : authorizedUnits.find(unit => unit.unit_id === activePlantId) ?? null,
    allPlants: activePlantId === "all",
    selectActivePlant: setActivePlantId,
  }), [activePlantId, authorizedUnits]);

  useEffect(() => {
    const media = window.matchMedia("(max-width: 860px)");
    const update = () => {
      setMobileNavigation(media.matches);
      if (!media.matches) setMenuOpen(false);
    };
    update();
    media.addEventListener("change", update);
    return () => media.removeEventListener("change", update);
  }, []);

  useEffect(() => {
    let active = true;
    const refresh = (force = false) => {
      const now = Date.now();
      if (!force && now - lastCountRefreshAt.current < 250) return;
      lastCountRefreshAt.current = now;
      if (hasApprovals) {
        fetchApprovalCount().then(value => { if (active) setApprovalCount(value); }, () => { if (active) setApprovalCount(null); });
      } else {
        setApprovalCount(null);
      }
      fetchNotificationCount().then(value => { if (active) setNotificationCount(value); }, () => { if (active) setNotificationCount(null); });
    };
    if (!hasActivePlant) {
      setApprovalCount(null);
      setNotificationCount(null);
      return () => { active = false; };
    }
    refresh(true);
    const interval = window.setInterval(() => { if (document.visibilityState === "visible") refresh(); }, 60_000);
    const onFocus = () => { if (document.visibilityState === "visible") refresh(); };
    const onVisibilityChange = () => { if (document.visibilityState === "visible") refresh(); };
    window.addEventListener("focus", onFocus);
    document.addEventListener("visibilitychange", onVisibilityChange);
    return () => {
      active = false;
      window.clearInterval(interval);
      window.removeEventListener("focus", onFocus);
      document.removeEventListener("visibilitychange", onVisibilityChange);
    };
  }, [hasApprovals, hasActivePlant, activePlantId]);

  useEffect(() => {
    if (!menuOpen) return;
    const previouslyFocused = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    const sidebar = sidebarRef.current;
    const focusable = Array.from(sidebar?.querySelectorAll<HTMLElement>("a[href], button:not([disabled])") ?? []);
    const preferred = sidebar?.querySelector<HTMLElement>('a[aria-current="page"]') ?? focusable[0];
    preferred?.focus();

    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.preventDefault();
        setMenuOpen(false);
        return;
      }
      if (event.key !== "Tab" || focusable.length === 0) return;
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    };

    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("keydown", onKeyDown);
      previouslyFocused?.focus();
    };
  }, [menuOpen]);

  useEffect(() => {
    pageHeadingRef.current?.focus();
  }, [location.pathname]);

  const submitSearch = (event: React.FormEvent) => {
    event.preventDefault();
    const normalized = search.trim().toLowerCase();
    const destination = visibleItems.find(item => item.label.toLowerCase().includes(normalized));
    if (destination) {
      navigate(destination.href);
      setSearch("");
    }
  };

  return (
    <ActivePlantContext.Provider value={plantContext}>
    <div className="app-layout" data-settings-count={settings.length}>
      <AppSidebar me={me} units={authorizedUnits} activePlantId={activePlantId} onPlantChange={setActivePlantId} open={menuOpen} mobile={mobileNavigation} onClose={() => setMenuOpen(false)} sidebarRef={sidebarRef} />
      <div className="app-main" inert={mobileNavigation && menuOpen ? true : undefined} aria-hidden={mobileNavigation && menuOpen ? true : undefined}>
        <header className="app-header">
          <div className="app-header__title">
            <button className="mobile-menu" type="button" aria-label="Open navigation" onClick={() => setMenuOpen(true)}><Menu size={21} /></button>
            <div><h1 ref={pageHeadingRef} tabIndex={-1}>{pageTitle}</h1><p>{pageSubtitle}</p></div>
          </div>
          <form className="nav-search" role="search" onSubmit={submitSearch}>
            <Search size={18} aria-hidden="true" />
            <label className="sr-only" htmlFor="navigation-search">Search navigation</label>
            <input id="navigation-search" value={search} onChange={event => setSearch(event.target.value)} placeholder="Search navigation…" />
          </form>
          <div className="app-header__utilities">
            <Link className="header-icon" aria-label={notificationCount ? `${notificationCount.unread} unread notifications` : "Notification count unavailable"} to="/app/notifications">
              <Bell size={20} />
              {!!notificationCount?.unread && <span>{notificationCount.unread > 99 ? "99+" : notificationCount.unread}</span>}
            </Link>
            <span className="sr-only" role="status" aria-label="Notification count status" aria-atomic="true">{notificationCount ? `${notificationCount.unread} unread notifications` : "Notification count unavailable"}</span>
            <div className="header-identity"><span>{me.display_name.charAt(0).toUpperCase()}</span><div><strong>{me.display_name}</strong><small>{authorizedUnits.length === 1 && !me.all_plants ? authorizedUnits[0].name : me.role.replaceAll("_", " ")}</small></div></div>
            {(me.all_plants || authorizedUnits.length > 1) && (
              <PlantSelect me={me} units={authorizedUnits} activePlantId={activePlantId} onChange={setActivePlantId} className="plant-selector" />
            )}
          </div>
        </header>
        <main className="app-content">
          {routeForbidden ? (
            <section className="module-pending">
              <div className="module-pending__icon"><ShieldAlert size={26} /></div>
              <p>This route is outside your current module access.</p>
              <Link className="button button--primary" to={recoveryHref}>Open an authorized workspace</Link>
            </section>
          ) : !knownItem ? (
            <section className="module-pending">
              <div className="module-pending__icon"><ShieldAlert size={26} /></div>
              <p>This private page could not be found.</p>
              <Link className="button button--primary" to={recoveryHref}>Open an authorized workspace</Link>
            </section>
          ) : !hasActivePlant && location.pathname !== "/app/manufacturing/shift-entries" ? (
            <section className="module-pending">
              <div className="module-pending__icon"><Boxes size={26} /></div>
              <h2>Select an active plant</h2>
              <p>Choose the plant you are working with before opening operational data.</p>
            </section>
          ) : location.pathname === "/app" ? (
            <Dashboard approvalCount={approvalCount} notificationCount={notificationCount} showApprovals={hasApprovals} />
          ) : location.pathname === "/app/approvals" && activeItem ? (
            <ApprovalInbox me={me} />
          ) : location.pathname.startsWith("/app/approvals/") && activeItem ? (
            <ApprovalDetail me={me} approvalId={location.pathname.slice("/app/approvals/".length)} />
          ) : location.pathname === "/app/notifications" && activeItem ? (
            <NotificationInbox onUnreadCountChange={updateNotificationCount} />
          ) : location.pathname.startsWith("/app/master-data/organization/departments") && activeItem ? (
            <OrganizationDepartments me={me} />
          ) : location.pathname.startsWith("/app/master-data/organization/stores") && activeItem ? (
            <OrganizationStores me={me} />
          ) : location.pathname.startsWith("/app/master-data/organization") && activeItem ? (
            <UnavailableForHandover />
          ) : location.pathname === "/app/master-data/reference-data" && activeItem ? (
            <ReferenceData me={me} />
          ) : location.pathname.startsWith("/app/master-data/parties/vendors") && activeItem ? (
            <Vendors me={me} />
          ) : location.pathname.startsWith("/app/master-data/parties") && activeItem ? (
            <Customers me={me} />
          ) : location.pathname.startsWith("/app/master-data/engineering/routes") && activeItem ? (
            <Routes me={me} />
          ) : location.pathname.startsWith("/app/master-data/engineering/boms") && activeItem ? (
            <Boms me={me} />
          ) : location.pathname.startsWith("/app/master-data/engineering") && activeItem ? (
            <Items me={me} />
          ) : location.pathname.startsWith("/app/procurement/purchase-orders") && activeItem ? (
            <PurchaseOrders me={me} settings={settings} />
          ) : location.pathname.startsWith("/app/procurement/rfqs") && activeItem ? (
            <Rfqs me={me} settings={settings} />
          ) : location.pathname.startsWith("/app/procurement") && activeItem ? (
            <Indents me={me} />
          ) : location.pathname === "/app/inventory/issues/new" && activeItem ? (
            <IssuePlanner me={me} settings={settings} />
          ) : location.pathname.startsWith("/app/inventory/issues") && activeItem ? (
            <UnavailableForHandover />
          ) : (location.pathname === "/app/inventory/reservations" || location.pathname === "/app/inventory/reservations/new") && activeItem ? (
            <Reservations me={me} />
          ) : (location.pathname === "/app/inventory/transfers" || location.pathname.startsWith("/app/inventory/transfers/")) && activeItem ? (
            <UnavailableForHandover />
          ) : (location.pathname === "/app/inventory/counts" || location.pathname.startsWith("/app/inventory/counts/")) && activeItem ? (
            <UnavailableForHandover />
          ) : location.pathname === "/app/inventory/reports" && activeItem ? (
            <UnavailableForHandover />
          ) : (location.pathname.startsWith("/app/inventory/stock") || location.pathname.startsWith("/app/inventory/lots/")) && activeItem ? (
            <StockExplorer me={me} />
          ) : location.pathname.startsWith("/app/inventory") && activeItem ? (
            <Grns me={me} />
          ) : location.pathname === "/app/manufacturing/batches/new" && activeItem ? (
            me.permissions.includes("route.move") ? <UnavailableForHandover /> : <section className="master-state"><h2>Access denied</h2><p>The route.move permission is required to start weighing.</p></section>
          ) : location.pathname.startsWith("/app/manufacturing/batches/") && activeItem ? (
            <BatchDetail id={location.pathname.slice("/app/manufacturing/batches/".length)} me={me} />
          ) : location.pathname === "/app/manufacturing/batches" && activeItem ? (
            <BatchRegister me={me} />
          ) : location.pathname === "/app/manufacturing/equipment" && activeItem ? (
            <UnavailableForHandover />
          ) : location.pathname === "/app/manufacturing/work-orders/new" && activeItem ? (
            <UnavailableForHandover />
          ) : location.pathname.startsWith("/app/manufacturing/work-orders/") && activeItem ? (
            <UnavailableForHandover />
          ) : location.pathname === "/app/manufacturing/work-orders" && activeItem ? (
            <UnavailableForHandover />
          ) : location.pathname === "/app/manufacturing/lots/new" && activeItem ? (
            <ProductionLotCreate me={me} />
          ) : location.pathname.startsWith("/app/manufacturing/lots/") && activeItem ? (
            <ProductionLotDetail id={location.pathname.slice("/app/manufacturing/lots/".length)} me={me} />
          ) : location.pathname === "/app/manufacturing/lots" && activeItem ? (
            <ProductionLotRegister me={me} />
          ) : location.pathname.startsWith("/app/manufacturing/shift-entries") && activeItem ? (
            <ShiftEntries me={me} />
          ) : location.pathname === "/app/manufacturing/performance" && activeItem ? (
            <ManufacturingPerformance me={me} />
          ) : (location.pathname === "/app/manufacturing" || location.pathname === "/app/manufacturing/compounds" || location.pathname.startsWith("/app/manufacturing/compounds/")) && activeItem ? (
            location.pathname === "/app/manufacturing" ? <BatchRegister me={me} /> : <UnavailableForHandover />
          ) : location.pathname === "/app/quality/plans/new" && activeItem ? (
            <InspectionPlanCreatePage me={me} />
          ) : location.pathname.startsWith("/app/quality/plans/") && activeItem ? (
            <InspectionPlanDetail id={location.pathname.slice("/app/quality/plans/".length)} me={me} />
          ) : location.pathname === "/app/quality/plans" && activeItem ? (
            <InspectionPlanRegister me={me} />
          ) : location.pathname === "/app/quality/inspections/new" && activeItem ? (
            <InspectionCreatePage me={me} />
          ) : location.pathname.startsWith("/app/quality/inspections/") && activeItem ? (
            <InspectionDetail id={location.pathname.slice("/app/quality/inspections/".length)} />
          ) : location.pathname === "/app/quality/inspections" && activeItem ? (
            <InspectionRegister me={me} />
          ) : location.pathname === "/app/quality/holds/new" && activeItem ? (
            <QualityHoldCreate me={me} />
          ) : location.pathname.startsWith("/app/quality/holds/") && activeItem ? (
            <QualityHoldDetail id={location.pathname.slice("/app/quality/holds/".length)} me={me} />
          ) : location.pathname === "/app/quality/holds" && activeItem ? (
            <QualityHoldRegister me={me} />
          ) : location.pathname === "/app/quality/ncrs/new" && activeItem ? (
            <UnavailableForHandover />
          ) : location.pathname.startsWith("/app/quality/ncrs/") && activeItem ? (
            <UnavailableForHandover />
          ) : location.pathname === "/app/quality/ncrs" && activeItem ? (
            <UnavailableForHandover />
          ) : location.pathname === "/app/quality/instruments/new" && activeItem ? (
            <UnavailableForHandover />
          ) : location.pathname.startsWith("/app/quality/instruments/") && activeItem ? (
            <UnavailableForHandover />
          ) : location.pathname === "/app/quality/instruments" && activeItem ? (
            <UnavailableForHandover />
          ) : location.pathname === "/app/sales/prices/new" && activeItem ? (
            <UnavailableForHandover />
          ) : (location.pathname === "/app/sales" || location.pathname === "/app/sales/prices") && activeItem ? (
            <UnavailableForHandover />
          ) : location.pathname === "/app/sales/quotes/new" && activeItem ? (
            <UnavailableForHandover />
          ) : location.pathname.startsWith("/app/sales/quotes/") && activeItem ? (
            <UnavailableForHandover />
          ) : location.pathname === "/app/sales/quotes" && activeItem ? (
            <UnavailableForHandover />
          ) : location.pathname === "/app/sales/orders/new" && activeItem ? (
            <UnavailableForHandover />
          ) : location.pathname.startsWith("/app/sales/orders/") && activeItem ? (
            <UnavailableForHandover />
          ) : location.pathname === "/app/sales/orders" && activeItem ? (
            <UnavailableForHandover />
          ) : (location.pathname === "/app/quality" || location.pathname === "/app/quality/queue") && activeItem ? (
            <QualityQueue me={me} />
          ) : activeItem ? (
            <section className="module-pending">
              <div className="module-pending__icon"><Gauge size={26} /></div>
              <p>This workspace is scheduled in a later implementation wave and has not been marked complete.</p>
              <Link className="button button--primary" to={recoveryHref}>Open an authorized workspace</Link>
            </section>
          ) : null}
        </main>
      </div>
    </div>
    </ActivePlantContext.Provider>
  );
}

export function AccessGate() {
  const navigate = useNavigate();
  const navigateRef = useRef(navigate);
  useLayoutEffect(() => { navigateRef.current = navigate; }, [navigate]);
  const [attempt, setAttempt] = useState(0);
  const [state, setState] = useState<
    | { status: "loading" }
    | { status: "error"; error: ApiError | Error }
    | { status: "ready"; me: Me; settings: BusinessRule[]; units: Unit[] }
  >({ status: "loading" });

  useEffect(() => {
    let active = true;
    setState({ status: "loading" });
    fetchMe()
      .then(async me => {
        if (!me.access_granted) {
          if (me.mfa_required && !me.two_factor_enabled) {
            if (active) navigateRef.current("/totp-setup", { replace: true });
            return;
          }
          throw new ApiError(403, "Your ERP profile is not active for workspace access.");
        }
        const [settings, units] = await Promise.all([fetchSettings(), fetchAllUnits()]);
        if (active) setState({ status: "ready", me, settings, units: units.filter(unit => unit.active) });
      })
      .catch(async error => {
        if (!active) return;
        if (error instanceof ApiError && error.status === 401) {
          await authClient.signOut();
          if (active) navigateRef.current("/", { replace: true, state: { sessionExpired: true } });
          return;
        }
        setState({ status: "error", error: error instanceof Error ? error : new Error("Workspace access could not be confirmed.") });
      });
    return () => { active = false; };
  }, [attempt]);

  if (state.status === "loading") {
    return <main className="access-loading" aria-live="polite"><BrandMark /><div className="skeleton skeleton--title" /><div className="skeleton skeleton--line" /><span>Checking your workspace access…</span></main>;
  }
  if (state.status === "error") {
    const retry = () => setAttempt(value => value + 1);
    if (state.error instanceof ApiError && state.error.status === 403) return <AccessBlockedState error={state.error} onRetry={retry} />;
    return <ErrorState error={state.error} onRetry={retry} />;
  }
  const hasAuthorizedActivePlant = state.me.all_plants || state.units.some(unit => state.me.unit_ids.includes(unit.unit_id));
  if (!hasAuthorizedActivePlant) return <NoPlantState onRetry={() => setAttempt(value => value + 1)} />;
  return <AppShell me={state.me} settings={state.settings} units={state.units} />;
}

export function NotFound() {
  const navigate = useNavigate();
  const headingRef = useStateHeadingFocus();
  return (
    <main className="system-state">
      <div className="system-state__icon"><HeartHandshake size={26} /></div>
      <h1 ref={headingRef} tabIndex={-1}>Page not found</h1>
      <p>This page could not be found. Check the address, go back, or return to sign in.</p>
      <div className="system-state__actions">
        <button className="button button--secondary" type="button" onClick={() => navigate(-1)}>Go back</button>
        <Link className="button button--primary" to="/">Go to sign in</Link>
      </div>
    </main>
  );
}

function UnavailableForHandover() { const location = useLocation(); return <section className="module-pending">{location.pathname.startsWith("/app/master-data/organization") && <OrganizationTabs active="plants" />}<h2>Not included in this handover</h2><p>This workflow is unfinished or awaits backend requirements. It remains preserved in the original workspace.</p></section>; }
