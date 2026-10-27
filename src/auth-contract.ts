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

export function mayAccessErp(principal: AuthenticatedPrincipal | null): boolean {
  return Boolean(
    principal && principal.emailVerified && principal.twoFactorEnabled &&
    principal.organizationId && principal.organizationRole,
  );
}
