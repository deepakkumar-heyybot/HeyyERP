import { Route, Routes, useLocation } from "react-router-dom";
import { AuthCard, AuthMode } from "./components/AuthCard";
import { AccessGate, NotFound } from "./components/AppShell";
import { BrandMark } from "./components/Brand";
import { PublicApproval } from "./components/PublicApproval";

function PublicAuthPage({ mode }: { mode: AuthMode }) {
  const location = useLocation();
  const sessionExpired = mode === "signin" && Boolean((location.state as { sessionExpired?: boolean } | null)?.sessionExpired);
  return (
    <main className="public-shell">
      <header className="public-header"><BrandMark /></header>
      <div className="public-layout">
        <section className="public-intro" aria-labelledby="public-intro-title">
          <p id="public-intro-title" className="public-intro__statement">Your operational work,<br />in one dependable place.</p>
          <p>Access production, inventory, quality, finance, and approvals through your authorized heyyERP account.</p>
          <div className="public-intro__note">
            <strong>Built for daily operations</strong>
            <span>Permission-aware access, plant scope, and verified workflow controls.</span>
          </div>
        </section>
        <AuthCard mode={mode} initialNotice={sessionExpired ? "Your session expired. Sign in again to continue." : ""} />
      </div>
      <footer className="public-footer"><span>© {new Date().getFullYear()} heyyERP</span><span>V-Rub Suspensions</span></footer>
    </main>
  );
}

function App() {
  return (
    <Routes>
      <Route path="/" element={<PublicAuthPage mode="signin" />} />
      <Route path="/register" element={<PublicAuthPage mode="signup" />} />
      <Route path="/forgot-password" element={<PublicAuthPage mode="forgot" />} />
      <Route path="/reset-password" element={<PublicAuthPage mode="reset" />} />
      <Route path="/verify-email" element={<PublicAuthPage mode="verify" />} />
      <Route path="/totp-setup" element={<PublicAuthPage mode="totp-setup" />} />
      <Route path="/two-factor" element={<PublicAuthPage mode="two-factor" />} />
      <Route path="/accept-invitation/:id" element={<PublicAuthPage mode="signin" />} />
      <Route path="/approve/:token" element={<PublicApproval />} />
      <Route path="/app/*" element={<AccessGate />} />
      <Route path="*" element={<NotFound />} />
    </Routes>
  );
}

export default App;
