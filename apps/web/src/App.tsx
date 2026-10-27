import { FormEvent, useEffect, useState } from "react";
import { Link, Route, Routes, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { ArrowLeft, ArrowRight, Eye, EyeOff, Factory, LockKeyhole, Mail, ShieldCheck } from "lucide-react";
import { QRCodeSVG } from "qrcode.react";
import { authClient } from "./auth-client";
import { fetchMe, homeFor } from "./api";

type Mode = "signin" | "signup" | "forgot" | "reset" | "verify" | "totp-setup" | "two-factor";

function AuthCard({ mode }: { mode: Mode }) {
  const navigate = useNavigate();
  const params = useParams();
  const [query] = useSearchParams();
  const [email, setEmail] = useState("");
  const [name, setName] = useState("");
  const [password, setPassword] = useState("");
  const [code, setCode] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");
  const [totpUri, setTotpUri] = useState("");
  const [backupCodes, setBackupCodes] = useState<string[]>([]);

  const submit = async (event: FormEvent) => {
    event.preventDefault(); setBusy(true); setNotice(""); setError("");
    try {
      if (mode === "signin") {
        const result = await authClient.signIn.email({ email, password });
        if (result.error) throw new Error(result.error.message);
        if ((result.data as typeof result.data & { twoFactorRedirect?: boolean } | undefined)?.twoFactorRedirect) {
          navigate(params.id ? `/two-factor?invitationId=${encodeURIComponent(params.id)}` : "/two-factor"); return;
        }
        if (params.id) {
          const accepted = await authClient.organization.acceptInvitation({ invitationId: params.id });
          if (accepted.error) throw new Error(accepted.error.message);
        }
        navigate(homeFor(await fetchMe()));
      } else if (mode === "signup") {
        const invitationId = query.get("invitationId");
        const callbackURL = invitationId ? `${location.origin}/accept-invitation/${encodeURIComponent(invitationId)}` : `${location.origin}/`;
        const result = await authClient.signUp.email({ email, password, name, callbackURL });
        if (result.error) {
          const message = result.error.message ?? "Could not create account";
          if (/exist|already|unique|duplicate/i.test(message)) throw new Error("You have already created an ID with this email. Please sign in or reset your password.");
          throw new Error(message);
        }
        setNotice("If this email is eligible for registration, a verification link has been sent. Links expire in 10 minutes. Already registered? Sign in or reset your password.");
      } else if (mode === "forgot") {
        const result = await authClient.requestPasswordReset({ email, redirectTo: `${location.origin}/reset-password` });
        if (result.error) throw new Error(result.error.message);
        setNotice("If an account matches that email, a password reset link will arrive shortly. It expires in 10 minutes.");
      } else if (mode === "reset") {
        const token = query.get("token");
        if (!token) throw new Error("This reset link is invalid or expired. Request a new one.");
        const result = await authClient.resetPassword({ newPassword: password, token });
        if (result.error) throw new Error(result.error.message);
        setNotice("Password updated. You can now sign in.");
      } else if (mode === "verify") {
        const result = await authClient.sendVerificationEmail({ email, callbackURL: `${location.origin}/` });
        if (result.error) throw new Error(result.error.message);
        setNotice("If the account is awaiting verification, a fresh link is on its way. Links expire in 10 minutes.");
      } else if (mode === "totp-setup") {
        const result = await authClient.twoFactor.enable({ password, method: "totp", issuer: "heyyERP · V-Rub Suspensions" });
        if (result.error) throw new Error(result.error.message);
        if (result.data.method !== "totp") throw new Error("TOTP authenticator setup did not complete");
        setTotpUri(result.data.totpURI); setBackupCodes(result.data.backupCodes);
      } else if (mode === "two-factor") {
        const result = await authClient.twoFactor.verifyTotp({ code, trustDevice: false });
        if (result.error) throw new Error(result.error.message);
        const invitationId = query.get("invitationId");
        if (invitationId) {
          const accepted = await authClient.organization.acceptInvitation({ invitationId });
          if (accepted.error) throw new Error(accepted.error.message);
        }
        navigate("/app");
      }
    } catch (cause) { setError(cause instanceof Error ? cause.message : "Something went wrong. Please try again."); }
    finally { setBusy(false); }
  };

  const title: Record<Mode, string> = {
    signin: "Welcome back", signup: "Create your account", forgot: "Reset your password",
    reset: "Choose a new password", verify: "Verify your email", "totp-setup": "Secure your account",
    "two-factor": "Two-step verification",
  };
  const subtitle: Record<Mode, string> = {
    signin: "Sign in to your V-Rub workspace", signup: "Your email becomes your unique heyyERP identity",
    forgot: "We’ll email you a secure reset link", reset: "Choose a strong password for your account",
    verify: "We’ll send a fresh 10-minute verification link", "totp-setup": "Set up an authenticator app to continue",
    "two-factor": "Enter the 6-digit code from your authenticator app",
  };

  return <section className="auth-card" aria-labelledby="auth-title">
    <div className="card-icon"><LockKeyhole size={21} /></div>
    <h1 id="auth-title">{title[mode]}</h1><p className="subtitle">{subtitle[mode]}</p>
    {notice && <div role="status" className="notice"><ShieldCheck size={18} />{notice}</div>}
    {error && <div role="alert" className="error">{error}</div>}
    {mode === "totp-setup" && totpUri && <div className="totp-setup"><QRCodeSVG value={totpUri} size={176} /><p>Scan this QR code in your authenticator app, then confirm its 6-digit code.</p><div className="backup-codes"><b>Save these backup codes safely</b><pre>{backupCodes.join("\n")}</pre></div><form onSubmit={async (event) => { event.preventDefault(); const result = await authClient.twoFactor.verifyTotp({ code, trustDevice: false }); if (result.error) setError(result.error.message ?? "Invalid code"); else navigate("/app"); }}><label>Authenticator code<input inputMode="numeric" value={code} onChange={e => setCode(e.target.value)} required minLength={6} maxLength={6} /></label><button className="primary" type="submit">Confirm and continue <ArrowRight size={17} /></button></form></div>}
    {!(mode === "totp-setup" && totpUri) && <form onSubmit={submit}>
      {mode === "signup" && <label>Full name<input autoComplete="name" value={name} onChange={e => setName(e.target.value)} required placeholder="Your name" /></label>}
      {(mode === "signin" || mode === "signup" || mode === "forgot" || mode === "verify") && <label>Email address<div className="input-icon"><Mail size={17} /><input type="email" autoComplete="email" value={email} onChange={e => setEmail(e.target.value)} required placeholder="name@company.com" /></div></label>}
      {(mode === "signin" || mode === "signup" || mode === "reset" || mode === "totp-setup") && <label>{mode === "totp-setup" ? "Confirm your password" : mode === "reset" ? "New password" : "Password"}<div className="input-icon"><LockKeyhole size={17} /><input aria-label={mode === "totp-setup" ? "Confirm your password" : mode === "reset" ? "New password" : "Password"} type={showPassword ? "text" : "password"} autoComplete={mode === "signin" ? "current-password" : "new-password"} value={password} onChange={e => setPassword(e.target.value)} required minLength={mode === "reset" || mode === "signup" ? 12 : 1} placeholder={mode === "signup" || mode === "reset" ? "At least 12 characters" : "Enter your password"} /><button type="button" className="icon-button" aria-label={showPassword ? "Hide password" : "Show password"} onClick={() => setShowPassword(!showPassword)}>{showPassword ? <EyeOff size={17} /> : <Eye size={17} />}</button></div></label>}
      {mode === "two-factor" && <label>Authenticator code<input autoFocus inputMode="numeric" autoComplete="one-time-code" value={code} onChange={e => setCode(e.target.value)} required minLength={6} maxLength={6} placeholder="000000" /></label>}
      <button className="primary" disabled={busy} type="submit">{busy ? "Please wait…" : mode === "signin" ? "Sign in" : mode === "signup" ? "Create account" : mode === "forgot" ? "Send reset link" : mode === "verify" ? "Resend verification link" : mode === "two-factor" ? "Verify code" : mode === "reset" ? "Update password" : "Continue"}<ArrowRight size={17} /></button>
    </form>}
    {mode === "signin" && <div className="helper-links"><Link to="/forgot-password">Forgot password?</Link><Link to="/verify-email">Resend verification link</Link></div>}
    {mode === "signin" && <p className="footnote">New to heyyERP? <Link to={params.id ? `/register?invitationId=${encodeURIComponent(params.id)}` : "/register"}>Create account</Link></p>}
    {mode === "signup" && <p className="footnote">Already have an account? <Link to={query.get("invitationId") ? `/accept-invitation/${encodeURIComponent(query.get("invitationId")!)}` : "/"}>Sign in</Link></p>}
    {(mode === "forgot" || mode === "verify" || mode === "reset") && <p className="footnote"><Link to="/"><ArrowLeft size={15} /> Back to sign in</Link></p>}
  </section>;
}

function AppHome() {
  const navigate = useNavigate();
  const [ready, setReady] = useState(false);
  useEffect(() => {
    fetchMe().then((me) => (me.access_granted ? setReady(true) : navigate(homeFor(me))), () => navigate("/"));
  }, [navigate]);
  if (!ready) return <section className="auth-card"><p className="subtitle">Checking your access…</p></section>;
  return <section className="auth-card"><div className="card-icon"><ShieldCheck size={21} /></div><h1>Identity verified</h1><p className="subtitle">Your authentication is complete. ERP workspace integration is the next milestone.</p><button className="primary" type="button" onClick={async () => { await authClient.signOut(); navigate("/"); }}>Sign out <ArrowRight size={17} /></button></section>;
}

function App() {
  return <main className="page-shell"><header className="brand"><div className="brand-mark"><Factory size={21} /></div><div><strong>heyyERP</strong><span>V-Rub Suspensions</span></div></header><div className="auth-layout"><div className="welcome"><div className="eyebrow"><span className="status-dot" /> V-RUB WORKSPACE</div><h2>Operations,<br /><em>in sync.</em></h2><p>One secure sign-in for every part of your manufacturing operation.</p><div className="secure-note"><ShieldCheck size={18} /><span>Protected with verified email and two-factor authentication</span></div></div><Routes>
    <Route path="/" element={<AuthCard mode="signin" />} /><Route path="/register" element={<AuthCard mode="signup" />} />
    <Route path="/forgot-password" element={<AuthCard mode="forgot" />} /><Route path="/reset-password" element={<AuthCard mode="reset" />} />
    <Route path="/verify-email" element={<AuthCard mode="verify" />} /><Route path="/totp-setup" element={<AuthCard mode="totp-setup" />} />
    <Route path="/two-factor" element={<AuthCard mode="two-factor" />} /><Route path="/app" element={<AppHome />} />
    <Route path="/accept-invitation/:id" element={<AuthCard mode="signin" />} />
  </Routes></div><footer><span>© {new Date().getFullYear()} heyyERP</span><span>Secure access · V-Rub Suspensions</span></footer></main>;
}

export default App;
