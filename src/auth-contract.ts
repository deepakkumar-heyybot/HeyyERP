/** Shared identity/session boundary. API middleware must populate these fields only after
 * validating a Better Auth session and its active V-Rub organization membership. */
export type AuthenticatedPrincipal = {
  userId: string;
  email: string;
  organizationId: string;
  organizationRole: string;
  twoFactorEnabled: boolean;
  emailVerified: boolean;
};

export const REQUIRED_ORGANIZATION_SLUG = "v-rub-suspensions";

/** BRD section 9.4: MFA is mandatory only for these roles. Other roles (including shop-floor
 * users, who later get OTP or PIN sign-in) may access the ERP without TOTP. */
export const MFA_REQUIRED_ROLES: readonly string[] = ["ADMIN", "MD", "ACCOUNTS"];

export function mfaRequired(role: string): boolean {
  return MFA_REQUIRED_ROLES.includes(role);
}

export function mayAccessErp(principal: AuthenticatedPrincipal | null): boolean {
  return Boolean(
    principal && principal.emailVerified &&
    (principal.twoFactorEnabled || !mfaRequired(principal.organizationRole)) &&
    principal.organizationId && principal.organizationRole,
  );
}
