import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  AlertCircle,
  ArrowUpRight,
  Bell,
  Check,
  CheckCheck,
  Inbox,
  RefreshCw,
} from "lucide-react";
import { useNavigate, useSearchParams } from "react-router-dom";
import {
  ApiError,
  InAppNotification,
  fetchNotificationCount,
  fetchNotifications,
  markAllNotificationsRead,
  markNotificationRead,
} from "../api-client";
import { authClient } from "../auth-client";

type NotificationFilter = "unread" | "all";
type NotificationState =
  | { status: "loading" }
  | { status: "error"; error: ApiError | Error }
  | { status: "ready"; items: InAppNotification[] };

function normalize(items: InAppNotification[]) {
  return Array.from(new Map(items.map(item => [item.notification_id, item])).values());
}

function formatDate(value: string) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "Time unavailable";
  return new Intl.DateTimeFormat("en-IN", {
    day: "2-digit",
    month: "short",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  }).format(date);
}

function referenceHref(notification: InAppNotification) {
  if (!notification.reference_id) return null;
  if (notification.reference_type?.toLowerCase() === "approval") return `/app/approvals/${notification.reference_id}`;
  return null;
}

function notificationTitle(notification: InAppNotification) {
  return notification.subject?.trim() || "ERP notification";
}

export function NotificationInbox({ onUnreadCountChange }: { onUnreadCountChange: (unread: number) => void }) {
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const filter: NotificationFilter = searchParams.get("filter") === "all" ? "all" : "unread";
  const canonicalParams = useMemo(() => new URLSearchParams({ filter }), [filter]);
  const [limit, setLimit] = useState(30);
  const [state, setState] = useState<NotificationState>({ status: "loading" });
  const [busyId, setBusyId] = useState<string | null>(null);
  const [markingAll, setMarkingAll] = useState(false);
  const [actionError, setActionError] = useState<ApiError | Error | null>(null);
  const [refreshToken, setRefreshToken] = useState(0);
  const lastRefreshAt = useRef(0);
  const headingRef = useRef<HTMLHeadingElement>(null);

  useEffect(() => {
    if (searchParams.toString() !== canonicalParams.toString()) setSearchParams(canonicalParams, { replace: true });
  }, [canonicalParams, searchParams, setSearchParams]);

  useEffect(() => { setLimit(30); }, [filter]);

  const load = useCallback(async (signal?: AbortSignal, quiet = false) => {
    if (!quiet) setState({ status: "loading" });
    try {
      const [items, count] = await Promise.all([
        fetchNotifications(filter === "unread", limit, signal),
        fetchNotificationCount(),
      ]);
      if (signal?.aborted) return;
      setState({ status: "ready", items: normalize(items) });
      onUnreadCountChange(count.unread);
    } catch (error) {
      if (signal?.aborted) return;
      const safeError = error instanceof Error ? error : new Error("Notifications could not be loaded.");
      if (!quiet) setState({ status: "error", error: safeError });
    }
  }, [filter, limit, onUnreadCountChange]);

  useEffect(() => {
    const controller = new AbortController();
    void load(controller.signal);
    return () => controller.abort();
  }, [load, refreshToken]);

  useEffect(() => {
    const refresh = () => {
      if (document.visibilityState !== "visible") return;
      const now = Date.now();
      if (now - lastRefreshAt.current < 250) return;
      lastRefreshAt.current = now;
      void load(undefined, true);
    };
    const interval = window.setInterval(refresh, 60_000);
    window.addEventListener("focus", refresh);
    document.addEventListener("visibilitychange", refresh);
    return () => {
      window.clearInterval(interval);
      window.removeEventListener("focus", refresh);
      document.removeEventListener("visibilitychange", refresh);
    };
  }, [load]);

  useEffect(() => {
    if (state.status !== "error" || !(state.error instanceof ApiError) || state.error.status !== 401) return;
    void authClient.signOut().finally(() => navigate("/", { replace: true, state: { sessionExpired: true } }));
  }, [navigate, state]);

  useEffect(() => { headingRef.current?.focus(); }, []);

  const updateFilter = (next: NotificationFilter) => setSearchParams(new URLSearchParams({ filter: next }));

  const reconcileCount = async () => {
    const count = await fetchNotificationCount();
    onUnreadCountChange(count.unread);
  };

  const readOne = async (notification: InAppNotification, openAfter = false) => {
    if (busyId) return;
    setBusyId(notification.notification_id);
    setActionError(null);
    try {
      const updated = notification.read_at ? notification : await markNotificationRead(notification.notification_id);
      setState(current => current.status !== "ready" ? current : {
        status: "ready",
        items: filter === "unread"
          ? current.items.filter(item => item.notification_id !== notification.notification_id)
          : current.items.map(item => item.notification_id === updated.notification_id ? updated : item),
      });
      await reconcileCount();
      const destination = referenceHref(notification);
      if (openAfter && destination) navigate(destination);
    } catch (error) {
      setActionError(error instanceof Error ? error : new Error("The notification could not be updated."));
    } finally {
      setBusyId(null);
    }
  };

  const readAll = async () => {
    if (markingAll) return;
    setMarkingAll(true);
    setActionError(null);
    try {
      const count = await markAllNotificationsRead();
      onUnreadCountChange(count.unread);
      const readAt = new Date().toISOString();
      setState(current => current.status !== "ready" ? current : {
        status: "ready",
        items: filter === "unread" ? [] : current.items.map(item => ({ ...item, read_at: item.read_at || readAt })),
      });
    } catch (error) {
      setActionError(error instanceof Error ? error : new Error("Notifications could not be marked read."));
    } finally {
      setMarkingAll(false);
    }
  };

  const items = state.status === "ready" ? state.items : [];
  const unreadOnPage = items.filter(item => !item.read_at).length;
  const canShowMore = state.status === "ready" && items.length >= limit && limit < 100;

  return (
    <section className="notification-inbox" aria-labelledby="notification-inbox-title">
      <div className="page-heading-row">
        <div><p className="eyebrow">Inbox</p><h2 id="notification-inbox-title" ref={headingRef} tabIndex={-1}>Notifications</h2><p>Review operational alerts and open supported records from one place.</p></div>
        <div className="notification-heading-actions">
          {unreadOnPage > 0 && <button className="button button--secondary" type="button" onClick={readAll} disabled={markingAll}>{markingAll ? "Marking…" : <><CheckCheck size={16} aria-hidden="true" />Mark all as read</>}</button>}
          <button className="button button--secondary" type="button" onClick={() => setRefreshToken(value => value + 1)} disabled={state.status === "loading"}><RefreshCw size={16} aria-hidden="true" />Refresh</button>
        </div>
      </div>

      <div className="notification-controls">
        <div className="segmented-control" aria-label="Notification filter"><button type="button" aria-pressed={filter === "unread"} onClick={() => updateFilter("unread")}>Unread</button><button type="button" aria-pressed={filter === "all"} onClick={() => updateFilter("all")}>All notifications</button></div>
        <span>{state.status === "ready" ? `${items.length} ${filter === "unread" ? "unread" : "loaded"}` : "Loading current inbox"}</span>
      </div>

      {actionError && <div className="decision-error" role="alert"><AlertCircle size={17} aria-hidden="true" /><span>{actionError.message}</span></div>}

      <div className="notification-results" aria-live="polite" aria-busy={state.status === "loading"}>
        {state.status === "loading" ? (
          <div className="results-loading" role="status"><span className="skeleton skeleton--line" /><span className="skeleton skeleton--line" /><span>Loading notifications…</span></div>
        ) : state.status === "error" ? (
          <div className="inline-error" role="alert"><AlertCircle size={22} aria-hidden="true" /><div><h3>Notifications unavailable</h3><p>{state.error.message}</p>{state.error instanceof ApiError && state.error.status >= 500 && state.error.requestId && <p className="inline-error__request">Request ID: <code>{state.error.requestId}</code></p>}</div><button className="button button--secondary" type="button" onClick={() => setRefreshToken(value => value + 1)}>Try again</button></div>
        ) : items.length === 0 ? (
          <div className="filtered-empty"><span><Inbox size={26} aria-hidden="true" /></span><h3>No {filter === "unread" ? "unread" : "available"} notifications</h3><p>{filter === "unread" ? "You are up to date. Open all notifications to review earlier alerts." : "New operational alerts will appear here when they are available."}</p></div>
        ) : (
          <ul className="notification-list" aria-label="Notification results">
            {items.map(notification => {
              const title = notificationTitle(notification);
              const destination = referenceHref(notification);
              const busy = busyId === notification.notification_id;
              return <li key={notification.notification_id} className={notification.read_at ? "notification-item notification-item--read" : "notification-item notification-item--unread"}>
                <span className="notification-item__icon"><Bell size={18} aria-hidden="true" /></span>
                <div className="notification-item__content"><div><strong>{title}</strong>{!notification.read_at && <span className="notification-unread"><span aria-hidden="true" />Unread</span>}</div><p>{notification.body?.trim() || "No additional message was supplied."}</p><time dateTime={notification.created_at}>{formatDate(notification.created_at)}</time></div>
                <div className="notification-item__actions">
                  {!notification.read_at && <button type="button" aria-label={`Mark ${title} as read`} onClick={() => void readOne(notification)} disabled={busy}><Check size={16} aria-hidden="true" />{busy ? "Updating…" : "Mark read"}</button>}
                  {destination && <button type="button" aria-label={`Open ${title}`} onClick={() => void readOne(notification, true)} disabled={busy}>Open<ArrowUpRight size={16} aria-hidden="true" /></button>}
                </div>
              </li>;
            })}
          </ul>
        )}
      </div>

      {canShowMore && <button className="button button--secondary notification-show-more" type="button" onClick={() => setLimit(value => Math.min(100, value + 30))}>Show more notifications</button>}
      <p className="approval-footnote"><RefreshCw size={14} aria-hidden="true" />The inbox refreshes once per minute while this page is visible. Unsupported references remain text-only.</p>
    </section>
  );
}
