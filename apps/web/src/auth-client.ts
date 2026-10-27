import { createAuthClient } from "better-auth/react";
import { organizationClient, twoFactorClient } from "better-auth/client/plugins";

export const authClient = createAuthClient({
  baseURL: import.meta.env.VITE_AUTH_URL ?? "http://localhost:3001",
  plugins: [organizationClient(), twoFactorClient({ twoFactorPage: "/two-factor" })],
});
