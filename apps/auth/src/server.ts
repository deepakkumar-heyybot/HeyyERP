import "dotenv/config";
import { serve } from "@hono/node-server";
import { auth } from "./auth.js";
import { Hono } from "hono";
import { cors } from "hono/cors";

const app = new Hono();
app.use("/api/auth/*", cors({
  origin: process.env.WEB_BASE_URL ?? "http://localhost:5173",
  allowHeaders: ["Content-Type", "Authorization"],
  allowMethods: ["GET", "POST", "OPTIONS"],
  exposeHeaders: ["Content-Length", "Set-Cookie"],
  credentials: true,
}));
app.on(["GET", "POST"], "/api/auth/*", (context) => auth.handler(context.req.raw));
app.get("/health/live", (context) => context.json({ status: "ok" }));

const port = Number(process.env.PORT ?? 3001);
serve({ fetch: app.fetch, port }, () => console.info(`heyyERP auth listening on ${port}`));
