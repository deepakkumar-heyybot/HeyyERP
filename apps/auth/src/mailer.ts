import nodemailer from "nodemailer";

const host = process.env.RESEND_SMTP_HOST ?? "smtp.resend.com";
const port = Number(process.env.RESEND_SMTP_PORT ?? 465);
const user = process.env.RESEND_SMTP_USER ?? "resend";
const password = process.env.RESEND_SMTP_PASSWORD;
const from = process.env.AUTH_EMAIL_FROM ?? "HeyyERP <deepak.kumar@heyybot.com>";

export async function sendAuthEmail(input: {
  to: string;
  subject: string;
  text: string;
  html?: string;
}): Promise<void> {
  if (process.env.NODE_ENV === "test") {
    const hook = globalThis.__heyyerpTestMailSink;
    if (!hook) throw new Error("Test mail sink is not configured");
    hook(input);
    return;
  }
  if (!password) {
    if (process.env.NODE_ENV === "production") {
      throw new Error("RESEND_SMTP_PASSWORD is required to send auth email");
    }
    // Local development without a mail service: show the email here so its link can be opened.
    console.warn(
      `\n[dev mail] RESEND_SMTP_PASSWORD is not set, so this email was NOT sent.\n` +
      `  To:      ${input.to}\n  Subject: ${input.subject}\n  ${input.text}\n`,
    );
    return;
  }
  const transport = nodemailer.createTransport({ host, port, secure: port === 465, auth: { user, pass: password } });
  try {
    await transport.sendMail({ from, ...input });
  } finally {
    transport.close();
  }
}
