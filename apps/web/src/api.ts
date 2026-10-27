export type Me = {
  user_id: string;
  email: string;
  roles: string[];
  mfa_required: boolean;
  two_factor_enabled: boolean;
  access_granted: boolean;
};

const API_URL = import.meta.env.VITE_API_URL ?? "http://localhost:8000";

/** Who the signed-in caller is, per the ERP API (verified email, V-Rub membership, MFA rule). */
export async function fetchMe(): Promise<Me> {
  const response = await fetch(`${API_URL}/api/v1/me`, { credentials: "include" });
  if (response.ok) return response.json();
  const detail = await response.json().then((body) => body?.detail as string | undefined, () => undefined);
  throw new Error(response.status === 403 && detail ? detail : "Could not confirm your workspace access. Please sign in again.");
}

/** BRD 9.4: only roles that must use MFA and have not enrolled are sent to authenticator setup. */
export const homeFor = (me: Me) => (me.access_granted ? "/app" : "/totp-setup");
