// Regent on Cloudflare: this Worker is the front door. Every request goes to the one Regent
// container. The container keeps its data in R2 through /__regent/backup on this Worker,
// which only answers with the container's own secret.
import { Container, getContainer } from "@cloudflare/containers";

export { ContainerProxy } from "@cloudflare/containers";   // required by the containers library

interface Env {
  REGENT: DurableObjectNamespace<Regent>;
  BACKUPS: R2Bucket;
  REGENT_ACCESS_KEY: string;
  CLAUDE_CODE_OAUTH_TOKEN?: string;
  ANTHROPIC_API_KEY?: string;
  SLEEP_AFTER?: string;
  BACKUP_SECRET: string;
  PUBLIC_URL: string;
  APPS_ORIGIN?: string;
}

const HOME = "regent-home.tar.gz";
const PREVIOUS = "regent-home.previous.tar.gz";

async function backup(req: Request, env: Env): Promise<Response> {
  try {
    const res = await backupInner(req, env);
    console.log("backup", req.method, res.status);
    return res;
  } catch (e) {
    console.log("backup failed", req.method, String(e));
    return new Response(String(e), { status: 500 });
  }
}

async function backupInner(req: Request, env: Env): Promise<Response> {
  if (req.method === "GET") {
    const obj = await env.BACKUPS.get(HOME);
    return obj ? new Response(obj.body) : new Response("none", { status: 404 });
  }
  if (req.method === "PUT") {
    const current = await env.BACKUPS.get(HOME);
    if (current) await env.BACKUPS.put(PREVIOUS, current.body);   // one step back, in case a save is bad
    await env.BACKUPS.put(HOME, await req.arrayBuffer());
    return new Response(null, { status: 204 });
  }
  return new Response("method not allowed", { status: 405 });
}

export class Regent extends Container<Env> {
  defaultPort = 8080;
  sleepAfter = "720h";          // Regent keeps working while you are away; the cron below wakes it after restarts
  enableInternet = true;

  async restartIfChanged(fingerprint: string): Promise<void> {
    const started = await this.ctx.storage.get<string>("settings");
    if (started === fingerprint) return;
    await this.ctx.storage.put("settings", fingerprint);
    try {
      await this.stop();          // Regent saves its data on the way down; the next request starts it fresh
    } catch {
      // not running: nothing to restart
    }
  }

  constructor(ctx: DurableObjectState<{}>, env: Env) {
    super(ctx, env);
    if (env.SLEEP_AFTER) this.sleepAfter = env.SLEEP_AFTER;
    const vars: Record<string, string> = {
      REGENT_ACCESS_KEY: env.REGENT_ACCESS_KEY,
      REGENT_BACKUP_URL: `${(env.PUBLIC_URL || "").replace(/\/$/, "")}/__regent/backup`,
      REGENT_BACKUP_SECRET: env.BACKUP_SECRET,
    };
    if (env.CLAUDE_CODE_OAUTH_TOKEN) vars.CLAUDE_CODE_OAUTH_TOKEN = env.CLAUDE_CODE_OAUTH_TOKEN;
    if (env.ANTHROPIC_API_KEY) vars.ANTHROPIC_API_KEY = env.ANTHROPIC_API_KEY;
    this.envVars = vars;
  }
}

function regent(env: Env) {
  return getContainer(env.REGENT, "regent");
}

// A container reads its settings only when it starts: when a key changes (e.g. a Claude key is
// added), restart it once so the change takes effect without anyone having to do anything.
async function settingsFingerprint(env: Env): Promise<string> {
  const text = [env.REGENT_ACCESS_KEY, env.CLAUDE_CODE_OAUTH_TOKEN, env.ANTHROPIC_API_KEY, env.BACKUP_SECRET,
                env.PUBLIC_URL].map((v) => v || "").join("\n");
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(text));
  return [...new Uint8Array(digest)].map((b) => b.toString(16).padStart(2, "0")).join("");
}

async function fresh(env: Env) {
  const stub = regent(env);
  await stub.restartIfChanged(await settingsFingerprint(env));
  return stub;
}

export default {
  async fetch(req: Request, env: Env): Promise<Response> {
    if (!env.REGENT_ACCESS_KEY) return new Response("Regent is not set up yet: its access key is missing.", { status: 503 });
    if (new URL(req.url).pathname === "/__regent/backup") {
      const given = req.headers.get("x-regent-backup") || "";
      if (!env.BACKUP_SECRET || given !== env.BACKUP_SECRET) return new Response("forbidden", { status: 403 });
      return backup(req, env);
    }
    // Apps Regent built are served on their own address (the regent-apps Worker), never on this one.
    const url = new URL(req.url);
    const fwd = new Request(req);
    fwd.headers.delete("x-regent-surface");
    fwd.headers.set("x-regent-apps-origin", env.APPS_ORIGIN || `https://${url.hostname.replace(/^[^.]+/, "$&-apps")}`);
    fwd.headers.set("x-forwarded-proto", url.protocol.replace(":", ""));
    try {
      return await (await fresh(env)).fetch(fwd);
    } catch (e) {
      return new Response(
        "<!doctype html><meta name=viewport content='width=device-width'><meta http-equiv=refresh content=5>" +
          "<body style='font-family:system-ui;padding:2em'>Regent is starting… this page will refresh by itself.",
        { status: 503, headers: { "content-type": "text/html; charset=utf-8" } },
      );
    }
  },
  // Keep Regent running (reminders, things it watches), and bring it back if the host restarted it.
  async scheduled(_ev: ScheduledController, env: Env, ctx: ExecutionContext): Promise<void> {
    ctx.waitUntil(fresh(env).then((r) => r.fetch(new Request("http://regent/healthz"))).then(() => undefined, () => undefined));
  },
};
