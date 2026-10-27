import "dotenv/config";
import { betterAuth } from "better-auth";
import { organization, twoFactor } from "better-auth/plugins";
import { PostgresDialect } from "kysely";
import { Pool } from "pg";
import { sendAuthEmail } from "./mailer.js";

const required = (key: string, fallback?: string) => {
  const value = process.env[key] ?? fallback;
  if (!value) throw new Error(`${key} is required`);
  return value;
};

const authSecret = process.env.BETTER_AUTH_SECRET ??
  (process.env.NODE_ENV === "production" ? undefined : "local-only-secret-change-this-before-production-123456");

export const auth = betterAuth({
  appName: "heyyERP · V-Rub Suspensions",
  baseURL: required("AUTH_BASE_URL", "http://localhost:3001"),
  secret: authSecret ?? required("BETTER_AUTH_SECRET"),
  trustedOrigins: [required("WEB_BASE_URL", "http://localhost:5173")],
  database: {
    dialect: new PostgresDialect({ pool: new Pool({ connectionString: required("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/heyyerp") }) }),
    type: "postgres",
    schemaName: "auth",
  },
  emailAndPassword: {
    enabled: true,
    requireEmailVerification: true,
    minPasswordLength: 12,
    maxPasswordLength: 128,
    resetPasswordTokenExpiresIn: 600,
    revokeSessionsOnPasswordReset: true,
    sendResetPassword: async ({ user, url }) => sendAuthEmail({
      to: user.email,
      subject: "Reset your heyyERP password",
      text: `Reset your password within 10 minutes: ${url}`,
      html: `<p>Use the link below within 10 minutes to reset your heyyERP password.</p><p><a href="${url}">Reset password</a></p>`,
    }),
  },
  emailVerification: {
    sendOnSignUp: true,
    sendOnSignIn: true,
    expiresIn: 600,
    autoSignInAfterVerification: false,
    sendVerificationEmail: async ({ user, url }) => sendAuthEmail({
      to: user.email,
      subject: "Verify your heyyERP email",
      text: `Verify your email within 10 minutes: ${url}`,
      html: `<p>Verify your email address within 10 minutes.</p><p><a href="${url}">Verify email</a></p>`,
    }),
  },
  plugins: [
    organization({
      allowUserToCreateOrganization: false,
      requireEmailVerificationOnInvitation: true,
      invitationExpiresIn: 604800, // 7 days; verification and reset links stay at 10 minutes
      async sendInvitationEmail(data) {
        const url = `${required("WEB_BASE_URL", "http://localhost:5173")}/accept-invitation/${encodeURIComponent(data.id)}`;
        await sendAuthEmail({
          to: data.email,
          subject: `Invitation to join ${data.organization.name} on heyyERP`,
          text: `You have been invited to join ${data.organization.name}. Accept the invitation: ${url}`,
          html: `<p>You have been invited to join ${data.organization.name}.</p><p><a href="${url}">Accept invitation</a></p>`,
        });
      },
    }),
    twoFactor({ issuer: "heyyERP · V-Rub Suspensions", backupCodeOptions: { amount: 10, length: 10 } }),
  ],
  rateLimit: {
    enabled: true,
    window: 60,
    max: 10,
    // The ERP API checks the session on every request from one server address, so a shared
    // 10/minute limit would sign everyone out. Throttle the API itself instead (Phase 12).
    customRules: { "/get-session": false },
  },
  advanced: {
    cookiePrefix: "heyyerp",
    useSecureCookies: process.env.NODE_ENV === "production",
    crossSubDomainCookies: { enabled: false },
  },
});

export type AuthInstance = typeof auth;
