import { AlertCircle } from "lucide-react";
import { useState } from "react";
import { ApiError } from "../api-client";

function RequestIdentifier({ requestId }: { requestId: string }) {
  const [copyStatus, setCopyStatus] = useState<"idle" | "copied" | "failed">("idle");
  async function copy() {
    try { await navigator.clipboard.writeText(requestId); setCopyStatus("copied"); }
    catch { setCopyStatus("failed"); }
  }
  return <>
    <span>Request ID: {requestId}</span>
    <button type="button" className="button button--secondary" aria-label="Copy request ID" onClick={copy}>{copyStatus === "copied" ? "Copied" : "Copy request ID"}</button>
    {copyStatus === "failed" && <span role="status">Copy failed. Select the request ID and copy it manually.</span>}
    {copyStatus === "copied" && <span className="sr-only" role="status">Request ID copied.</span>}
  </>;
}

// Older pages render client-error IDs themselves. Opt in for new pages so the
// shared helper can own those IDs without duplicating existing page feedback.
export function RequestError({ error, includeClientRequestId = false }: { error: Error; includeClientRequestId?: boolean }) {
  const requestId = error instanceof ApiError && (error.status >= 500 || includeClientRequestId) ? error.requestId : undefined;
  return <div className="decision-error" role="alert"><AlertCircle size={17} /><div><span>{error.message}</span>
    {error instanceof ApiError && error.fieldErrors.map((field, index) => <span key={`${field.field}-${index}`}>{field.field ? `${field.field}: ` : ""}{field.message}</span>)}
    {requestId && <RequestIdentifier key={requestId} requestId={requestId} />}</div></div>;
}
