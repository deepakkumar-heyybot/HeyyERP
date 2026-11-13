import { FormEvent, useEffect, useRef, useState } from "react";
import { ArrowLeft, ArrowRight, Building2, CheckCircle2, Eye, EyeOff, LockKeyhole, Mail, ShieldCheck } from "lucide-react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { QRCodeSVG } from "qrcode.react";
import { authClient } from "../auth-client";

export type AuthMode = "signin" | "signup" | "forgot" | "reset" | "verify" | "totp-setup" | "two-factor";
type ResetOutcome = "form" | "unavailable" | "success";
type VerificationOutcome = "form" | "sent" | "success" | "expired" | "invalid";

const genericRecoveryNotice = "If an account matches that email, a password reset link will arrive shortly. It expires in 10 minutes.";
const genericVerificationNotice = "If the account is awaiting verification, a fresh link is on its way. It expires in 10 minutes.";
const resetUnavailableNotice = "This reset link is invalid, expired, or has already been used. Request a new link to continue.";

const titles: Record<AuthMode, string> = {
  signin: "Welcome back",
  signup: "Create your account",
  forgot: "Reset your password",
  reset: "Choose a new password",
  verify: "Verify your email",
  "totp-setup": "Secure your account",
  "two-factor": "Two-step verification",
};

const subtitles: Record<AuthMode, string> = {
  signin: "Sign in to your V-Rub workspace",
  signup: "Your email becomes your unique heyyERP identity",
  forgot: "We’ll email you a secure reset link",
  reset: "Choose a strong password for your account",
  verify: "We’ll send a fresh 10-minute verification link",
  "totp-setup": "Set up an authenticator app to continue",
  "two-factor": "Enter the 6-digit code from your authenticator app",
};

function friendlyAuthError(cause: unknown) {
  const message = cause instanceof Error ? cause.message : "Something went wrong. Please try again.";
  if (/invalid.*(email|password)|(email|password).*invalid|credentials/i.test(message)) {
    return "Email or password is incorrect. Check both fields and try again.";
  }
  if (/failed to fetch|networkerror|network request|load failed|econn/i.test(message)) {
    return "We could not reach the authentication service. Check your connection and try again.";
  }
  return message;
}

function isNetworkError(message: string) {
  return /failed to fetch|networkerror|network request|load failed|econn/i.test(message);
}

function unavailableInvitationMessage() {
  return "This invitation is no longer available. It may have expired, been cancelled, or already been used. Ask your administrator for a new invitation.";
}

function isResetTokenError(message: string) {
  return /invalid.*token|token.*invalid|expired|already.*used|token.*used/i.test(message);
}

function isUnverifiedEmailError(message: string) {
  return /email.*not verified|EMAIL_NOT_VERIFIED/i.test(message);
}

function friendlyMfaError(cause: unknown, stage: "password" | "code") {
  const message = cause instanceof Error ? cause.message : "";
  if (stage === "password" && /invalid.*password|password.*invalid/i.test(message)) {
    return "Your password confirmation did not match. Try again.";
  }
  if (stage === "code" && /invalid.*code|code.*invalid|incorrect|expired/i.test(message)) {
    return "That authenticator code is incorrect or expired. Enter the latest six-digit code and try again.";
  }
  if (isNetworkError(message)) return friendlyAuthError(cause);
  return stage === "password"
    ? "We could not start authenticator setup. Confirm your password and try again."
    : "We could not verify that authenticator code. Enter the latest six-digit code and try again.";
}

function normalizeAuthenticatorCode(value: string) {
  return value.replace(/\D/g, "").slice(0, 6);
}

function manualTotpKey(uri: string) {
  try {
    return new URL(uri).searchParams.get("secret") ?? "";
  } catch {
    return "";
  }
}

export function AuthCard({ mode, initialNotice = "" }: { mode: AuthMode; initialNotice?: string }) {
  const navigate = useNavigate();
  const params = useParams();
  const [query] = useSearchParams();
  const [email, setEmail] = useState("");
  const [name, setName] = useState("");
  const [password, setPassword] = useState("");
  const [code, setCode] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState(initialNotice);
  const [error, setError] = useState("");
  const [invitationIssue, setInvitationIssue] = useState("");
  const [recoverySent, setRecoverySent] = useState(false);
  const [resetOutcome, setResetOutcome] = useState<ResetOutcome>("form");
  const [resetCallback] = useState(() => {
    const token = mode === "reset" ? query.get("token") : null;
    return {
      token,
      unavailable: mode === "reset" && (!token || query.get("error") === "INVALID_TOKEN"),
    };
  });
  const [verificationOutcome, setVerificationOutcome] = useState<VerificationOutcome>(() => {
    if (mode !== "verify") return "form";
    const callbackError = query.get("error");
    if (callbackError === "TOKEN_EXPIRED") return "expired";
    if (callbackError) return "invalid";
    return query.get("status") === "success" ? "success" : "form";
  });
  const [totpUri, setTotpUri] = useState("");
  const [backupCodes, setBackupCodes] = useState<string[]>([]);
  const totpSetupHeadingRef = useRef<HTMLHeadingElement>(null);

  useEffect(() => {
    if (mode !== "reset" && mode !== "verify") return;
    const callbackUrl = new URL(window.location.href);
    const callbackParameters = mode === "reset" ? ["token", "error"] : ["token", "error", "status"];
    if (!callbackParameters.some(parameter => callbackUrl.searchParams.has(parameter))) return;
    callbackParameters.forEach(parameter => callbackUrl.searchParams.delete(parameter));
    const cleanSearch = callbackUrl.searchParams.toString();
    window.history.replaceState(
      window.history.state,
      "",
      `${callbackUrl.pathname}${cleanSearch ? `?${cleanSearch}` : ""}${callbackUrl.hash}`,
    );
  }, [mode]);

  useEffect(() => {
    if (mode === "totp-setup" && totpUri) totpSetupHeadingRef.current?.focus();
  }, [mode, totpUri]);

  const invitationId = params.id ?? query.get("invitationId");
  const isInvitation = Boolean(invitationId);
  const resetToken = resetCallback.token;
  const resetCallbackUnavailable = resetCallback.unavailable;
  const resetUnavailable = mode === "reset" && (resetCallbackUnavailable || resetOutcome === "unavailable");
  const resetSucceeded = mode === "reset" && resetOutcome === "success";
  const verificationFinished = mode === "verify" && verificationOutcome !== "form";
  const totpManualKey = manualTotpKey(totpUri);
  const title = invitationIssue
    ? "Invitation unavailable"
    : recoverySent
      ? "Check your email"
      : resetUnavailable
        ? "Reset link unavailable"
        : resetSucceeded
          ? "Password updated"
          : verificationOutcome === "sent"
            ? "Check your email"
            : verificationOutcome === "success"
              ? "Email verified"
              : verificationOutcome === "expired"
                ? "Verification link expired"
                : verificationOutcome === "invalid"
                  ? "Verification link unavailable"
    : mode === "signin" && isInvitation
      ? "Accept your invitation"
      : mode === "signup" && isInvitation
        ? "Create an account for your invitation"
        : titles[mode];
  const subtitle = invitationIssue
    ? "Your account is signed in, but this invitation could not be accepted"
    : recoverySent
      ? "Use the secure link in the email to choose a new password"
      : resetUnavailable
        ? "The link cannot be used to update your password"
        : resetSucceeded
          ? "Your password has been changed securely"
          : verificationOutcome === "sent"
            ? "Use the secure link in the email to verify your address"
            : verificationOutcome === "success"
              ? "Your verified identity is ready for sign in"
              : verificationOutcome === "expired"
                ? "This verification link can no longer be used"
                : verificationOutcome === "invalid"
                  ? "This verification link cannot be used"
    : mode === "signin" && isInvitation
      ? "Sign in with the email address that received the invitation"
      : mode === "signup" && isInvitation
        ? "Create your heyyERP identity, then continue to the invited workspace"
        : subtitles[mode];

  const acceptInvitation = async (id: string) => {
    const accepted = await authClient.organization.acceptInvitation({ invitationId: id });
    if (accepted.error) {
      setInvitationIssue(unavailableInvitationMessage());
      return false;
    }
    return true;
  };

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (busy) return;
    if (mode === "two-factor" && !/^\d{6}$/.test(code)) {
      setError("Enter the six-digit code from your authenticator app.");
      return;
    }
    setBusy(true);
    setNotice("");
    setError("");

    try {
      if (mode === "signin") {
        const result = await authClient.signIn.email({ email, password, callbackURL: `${location.origin}/verify-email?status=success` });
        if (result.error) {
          if (isUnverifiedEmailError(result.error.message ?? "")) {
            setError("We could not sign you in. Check your email and password, or request a verification link.");
            return;
          }
          throw new Error(result.error.message);
        }
        if ((result.data as typeof result.data & { twoFactorRedirect?: boolean } | undefined)?.twoFactorRedirect) {
          navigate(invitationId ? `/two-factor?invitationId=${encodeURIComponent(invitationId)}` : "/two-factor");
          return;
        }
        if (invitationId) {
          if (!await acceptInvitation(invitationId)) return;
        }
        navigate("/app");
        return;
      }

      if (mode === "signup") {
        const genericRegistrationNotice = "If this email is eligible for registration, a verification link has been sent. Links expire in 10 minutes. Already registered? Sign in or reset your password.";
        const callbackURL = invitationId
          ? `${location.origin}/accept-invitation/${encodeURIComponent(invitationId)}`
          : `${location.origin}/verify-email?status=success`;
        const result = await authClient.signUp.email({ email, password, name, callbackURL });
        if (result.error) {
          const message = result.error.message ?? "Could not create account";
          if (/exist|already|unique|duplicate/i.test(message)) {
            setNotice(genericRegistrationNotice);
            return;
          }
          if (invitationId) {
            setError("We could not create your account for this invitation. Try again, or ask your administrator to send a new invitation.");
            return;
          }
          throw new Error(message);
        }
        setNotice(genericRegistrationNotice);
        return;
      }

      if (mode === "forgot") {
        await authClient.requestPasswordReset({ email, redirectTo: `${location.origin}/reset-password` });
        setRecoverySent(true);
        setNotice(genericRecoveryNotice);
        return;
      }

      if (mode === "reset") {
        if (password.length < 12 || password.length > 128) {
          setError("Password must contain 12 to 128 characters.");
          return;
        }
        if (!resetToken) {
          setResetOutcome("unavailable");
          return;
        }
        const result = await authClient.resetPassword({ newPassword: password, token: resetToken });
        if (result.error) {
          const message = result.error.message ?? "Could not reset password";
          if (isResetTokenError(message)) {
            setResetOutcome("unavailable");
            return;
          }
          if (/failed to fetch|networkerror|network request|load failed|econn/i.test(message)) throw new Error(message);
          setError("We could not update your password. Try again, or request a new reset link.");
          return;
        }
        setPassword("");
        setResetOutcome("success");
        setNotice("Your password has been updated. You can now sign in with your new password.");
        return;
      }

      if (mode === "verify") {
        const result = await authClient.sendVerificationEmail({ email, callbackURL: `${location.origin}/verify-email?status=success` });
        if (result.error?.statusText === "Fetch Error") throw new Error(result.error.message);
        setVerificationOutcome("sent");
        setNotice(genericVerificationNotice);
        return;
      }

      if (mode === "totp-setup") {
        const result = await authClient.twoFactor.enable({
          password,
          method: "totp",
          issuer: "heyyERP · V-Rub Suspensions",
        });
        if (result.error) {
          setError(friendlyMfaError(new Error(result.error.message), "password"));
          return;
        }
        if (result.data.method !== "totp") throw new Error("Authenticator setup did not complete.");
        setTotpUri(result.data.totpURI);
        setBackupCodes(result.data.backupCodes);
        return;
      }

      const result = await authClient.twoFactor.verifyTotp({ code, trustDevice: false });
      if (result.error) {
        setError(friendlyMfaError(new Error(result.error.message), "code"));
        return;
      }
      if (invitationId) {
        if (!await acceptInvitation(invitationId)) return;
      }
      navigate("/app");
    } catch (cause) {
      setError(friendlyAuthError(cause));
    } finally {
      setBusy(false);
    }
  };

  const useAnotherAccount = async () => {
    if (busy) return;
    setBusy(true);
    setError("");
    try {
      const result = await authClient.signOut();
      if (result.error) throw new Error(result.error.message);
      setInvitationIssue("");
      setEmail("");
      setPassword("");
    } catch (cause) {
      setError(friendlyAuthError(cause));
    } finally {
      setBusy(false);
    }
  };

  const restartRecovery = () => {
    setRecoverySent(false);
    setNotice("");
    setError("");
    setEmail("");
  };

  const restartVerification = () => {
    setVerificationOutcome("form");
    setNotice("");
    setError("");
    setEmail("");
  };

  const verifyTotpSetup = async (event: FormEvent) => {
    event.preventDefault();
    if (busy) return;
    if (!/^\d{6}$/.test(code)) {
      setError("Enter the six-digit code from your authenticator app.");
      return;
    }
    setBusy(true);
    setError("");
    try {
      const result = await authClient.twoFactor.verifyTotp({ code, trustDevice: false });
      if (result.error) {
        setError(friendlyMfaError(new Error(result.error.message), "code"));
        return;
      }
      navigate("/app");
    } catch (cause) {
      setError(friendlyAuthError(cause));
    } finally {
      setBusy(false);
    }
  };

  const submitLabel = busy
    ? mode === "verify" ? "Sending verification link…" : mode === "two-factor" ? "Verifying code…" : "Please wait…"
    : mode === "signin" ? (isInvitation ? "Sign in and accept invitation" : "Sign in")
      : mode === "signup" ? (isInvitation ? "Create account and continue" : "Create account")
        : mode === "forgot" ? "Send reset link"
          : mode === "verify" ? "Resend verification link"
            : mode === "two-factor" ? "Verify code"
              : mode === "reset" ? "Update password"
                : "Continue";

  return (
    <section className="auth-card" aria-labelledby="auth-title">
      <div className="auth-card__icon" aria-hidden="true"><LockKeyhole size={22} /></div>
      <h1 id="auth-title">{title}</h1>
      <p className="auth-card__subtitle">{subtitle}</p>

      {isInvitation && !invitationIssue && (
        <div className="invitation-context">
          <Building2 size={19} aria-hidden="true" />
          <div>
            <strong>Invitation to V-Rub Suspensions</strong>
            <span>Use the invited email address. The invitation is accepted only after your identity is verified.</span>
          </div>
        </div>
      )}

      {notice && <div role="status" className="alert alert--success"><CheckCircle2 size={18} />{notice}</div>}
      {error && <div role="alert" className="alert alert--danger">{error}</div>}

      {mode === "forgot" && recoverySent && (
        <div className="auth-outcome__actions">
          <Link className="button button--primary" to="/">Back to sign in</Link>
          <button className="button button--secondary" type="button" onClick={restartRecovery}>Send another link</button>
        </div>
      )}

      {resetUnavailable && (
        <div className="auth-outcome">
          <div role="alert" className="alert alert--danger">{resetUnavailableNotice}</div>
          <div className="auth-outcome__actions">
            <Link className="button button--primary" to="/forgot-password">Request a new reset link</Link>
            <Link className="button button--secondary" to="/">Back to sign in</Link>
          </div>
        </div>
      )}

      {resetSucceeded && (
        <div className="auth-outcome__actions auth-outcome__actions--single">
          <Link className="button button--primary" to="/">Sign in</Link>
        </div>
      )}

      {verificationOutcome === "sent" && (
        <div className="auth-outcome__actions">
          <Link className="button button--primary" to="/">Back to sign in</Link>
          <button className="button button--secondary" type="button" onClick={restartVerification}>Send another link</button>
        </div>
      )}

      {verificationOutcome === "success" && (
        <div className="auth-outcome">
          <div role="status" className="alert alert--success"><CheckCircle2 size={18} />Your email address is verified. If it was already verified, no further action was required.</div>
          <div className="auth-outcome__actions auth-outcome__actions--single">
            <Link className="button button--primary" to="/">Sign in</Link>
          </div>
        </div>
      )}

      {(verificationOutcome === "expired" || verificationOutcome === "invalid") && (
        <div className="auth-outcome">
          <div role="alert" className="alert alert--danger">
            {verificationOutcome === "expired"
              ? "This verification link has expired. Request a new link to continue."
              : "This verification link is invalid or cannot be used. Request a new link to continue."}
          </div>
          <div className="auth-outcome__actions">
            <button className="button button--primary" type="button" onClick={restartVerification}>Request a new verification link</button>
            <Link className="button button--secondary" to="/">Sign in</Link>
          </div>
        </div>
      )}

      {invitationIssue && (
        <div className="invitation-recovery">
          <div role="alert" className="alert alert--danger">{invitationIssue}</div>
          <div className="invitation-recovery__actions">
            <Link className="button button--primary" to="/app">Continue to heyyERP</Link>
            <button className="button button--secondary" type="button" disabled={busy} onClick={useAnotherAccount}>
              {busy ? "Signing out…" : "Use another account"}
            </button>
          </div>
        </div>
      )}

      {!invitationIssue && !recoverySent && !resetUnavailable && !resetSucceeded && !verificationFinished && mode === "totp-setup" && totpUri ? (
        <div className="totp-setup" role="region" aria-labelledby="totp-setup-stage-title">
          <h2 id="totp-setup-stage-title" ref={totpSetupHeadingRef} tabIndex={-1}>Authenticator app ready</h2>
          <div className="totp-setup__qr" role="img" aria-label="Authenticator QR code"><QRCodeSVG value={totpUri} size={168} aria-hidden="true" /></div>
          <p>Scan this QR code in your authenticator app, then enter its six-digit code.</p>
          {totpManualKey && (
            <div className="totp-manual-key">
              <strong>Manual setup key</strong>
              <span>If you cannot scan the QR code, enter this key in your authenticator app.</span>
              <code data-testid="totp-manual-key">{totpManualKey}</code>
            </div>
          )}
          <div className="backup-codes">
            <strong>Save these backup codes in a secure place</strong>
            <span>Each backup code can be used once if your authenticator is unavailable.</span>
            <ul aria-label="Backup codes">{backupCodes.map(backupCode => <li key={backupCode}><code>{backupCode}</code></li>)}</ul>
          </div>
          <form onSubmit={verifyTotpSetup}>
            <label htmlFor="setup-code">Authenticator code</label>
            <input
              id="setup-code"
              inputMode="numeric"
              autoComplete="one-time-code"
              value={code}
              onChange={event => { setCode(normalizeAuthenticatorCode(event.target.value)); setError(""); }}
              aria-required="true"
              aria-invalid={Boolean(error)}
            />
            <button className="button button--primary" disabled={busy} type="submit">{busy ? "Verifying…" : "Confirm and continue"}<ArrowRight size={17} /></button>
          </form>
        </div>
      ) : !invitationIssue && !recoverySent && !resetUnavailable && !resetSucceeded && !verificationFinished ? (
        <form className="auth-form" onSubmit={submit}>
          {mode === "signup" && (
            <div className="field">
              <label htmlFor="full-name">Full name</label>
              <input id="full-name" autoComplete="name" value={name} onChange={event => setName(event.target.value)} required placeholder="Your name" />
            </div>
          )}

          {(mode === "signin" || mode === "signup" || mode === "forgot" || mode === "verify") && (
            <div className="field">
              <label htmlFor="email">Email address</label>
              <div className="input-with-icon">
                <Mail size={17} aria-hidden="true" />
                <input id="email" type="email" autoComplete="email" value={email} onChange={event => setEmail(event.target.value)} required placeholder="name@company.com" />
              </div>
            </div>
          )}

          {(mode === "signin" || mode === "signup" || mode === "reset" || mode === "totp-setup") && (
            <div className="field">
              <label htmlFor="password">{mode === "totp-setup" ? "Confirm your password" : mode === "reset" ? "New password" : "Password"}</label>
              <div className="input-with-icon">
                <LockKeyhole size={17} aria-hidden="true" />
                <input
                  id="password"
                  aria-label={mode === "totp-setup" ? "Confirm your password" : mode === "reset" ? "New password" : "Password"}
                  type={showPassword ? "text" : "password"}
                  autoComplete={mode === "signin" || mode === "totp-setup" ? "current-password" : "new-password"}
                  value={password}
                  onChange={event => setPassword(event.target.value)}
                  required
                  minLength={mode === "signup" ? 12 : mode === "reset" ? undefined : 1}
                  aria-describedby={mode === "reset" ? "reset-password-hint" : undefined}
                  placeholder={mode === "signup" ? "At least 12 characters" : mode === "reset" ? "12 to 128 characters" : "Enter your password"}
                />
                <button type="button" className="input-icon-button" aria-label={showPassword ? "Hide password" : "Show password"} onClick={() => setShowPassword(value => !value)}>
                  {showPassword ? <EyeOff size={17} /> : <Eye size={17} />}
                </button>
              </div>
              {mode === "reset" && <span id="reset-password-hint" className="field-hint">Use 12 to 128 characters.</span>}
            </div>
          )}

          {mode === "two-factor" && (
            <div className="field">
              <label htmlFor="challenge-code">Authenticator code</label>
              <input
                id="challenge-code"
                autoFocus
                inputMode="numeric"
                autoComplete="one-time-code"
                value={code}
                onChange={event => { setCode(normalizeAuthenticatorCode(event.target.value)); setError(""); }}
                aria-required="true"
                aria-invalid={Boolean(error)}
                placeholder="000000"
              />
            </div>
          )}

          <button className="button button--primary" disabled={busy} type="submit">{submitLabel}<ArrowRight size={17} /></button>
        </form>
      ) : null}

      {!invitationIssue && mode === "signin" && (
        <div className="auth-card__links">
          <Link to="/forgot-password">Forgot password?</Link>
          <Link to="/verify-email">Resend verification link</Link>
        </div>
      )}
      {!invitationIssue && mode === "signin" && <p className="auth-card__footnote">New to heyyERP? <Link to={params.id ? `/register?invitationId=${encodeURIComponent(params.id)}` : "/register"}>{isInvitation ? "Create an account" : "Create account"}</Link></p>}
      {!invitationIssue && mode === "signup" && <p className="auth-card__footnote">Already have an account? <Link to={invitationId ? `/accept-invitation/${encodeURIComponent(invitationId)}` : "/"}>Sign in</Link></p>}
      {((mode === "forgot" && !recoverySent) || (mode === "verify" && !verificationFinished) || (mode === "reset" && !resetUnavailable && !resetSucceeded)) && <p className="auth-card__footnote"><Link to="/"><ArrowLeft size={15} />Back to sign in</Link></p>}

      <div className="auth-card__security"><ShieldCheck size={15} />Secure access for V-Rub Suspensions</div>
    </section>
  );
}
