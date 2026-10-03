// Regent on Cloudflare: this Worker is the front door. Every request goes to the one Regent
// container; the container's data is kept in R2 through a host only the container can reach.
import { Container, getContainer } from "@cloudflare/containers";

export { ContainerProxy } from "@cloudflare/containers";

interface Env {
  REGENT: DurableObjectNamespace<Regent>;
  BACKUPS: R2Bucket;
  REGENT_ACCESS_KEY: string;
  CLAUDE_CODE_OAUTH_TOKEN?: string;
  ANTHROPIC_API_KEY?: string;
  SLEEP_AFTER?: string;
  APPS_ORIGIN?: string;
}

const HOME = "regent-home.tar.gz";
const PREVIOUS = "regent-home.previous.tar.gz";

// The container saves and restores its home at http://backup.regent.internal/home.
async function backup(req: Request, env: Env): Promise<Response> {
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

  static outboundByHost = {
    "backup.regent.internal": (req: Request, env: Env) => backup(req, env),
  };

  constructor(ctx: DurableObjectState<{}>, env: Env) {
    super(ctx, env);
    if (env.SLEEP_AFTER) this.sleepAfter = env.SLEEP_AFTER;
    const vars: Record<string, string> = {
      REGENT_ACCESS_KEY: env.REGENT_ACCESS_KEY,
      REGENT_BACKUP_URL: "http://backup.regent.internal/home",
    };
    if (env.CLAUDE_CODE_OAUTH_TOKEN) vars.CLAUDE_CODE_OAUTH_TOKEN = env.CLAUDE_CODE_OAUTH_TOKEN;
    if (env.ANTHROPIC_API_KEY) vars.ANTHROPIC_API_KEY = env.ANTHROPIC_API_KEY;
    this.envVars = vars;
  }
}

function regent(env: Env) {
  return getContainer(env.REGENT, "regent");
}

export default {
  async fetch(req: Request, env: Env): Promise<Response> {
    if (!env.REGENT_ACCESS_KEY) return new Response("Regent is not set up yet: its access key is missing.", { status: 503 });
    // Apps Regent built are served on their own address (the regent-apps Worker), never on this one.
    const url = new URL(req.url);
    const fwd = new Request(req);
    fwd.headers.delete("x-regent-surface");
    fwd.headers.set("x-regent-apps-origin", env.APPS_ORIGIN || `https://${url.hostname.replace(/^[^.]+/, "$&-apps")}`);
    fwd.headers.set("x-forwarded-proto", url.protocol.replace(":", ""));
    try {
      return await regent(env).fetch(fwd);
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
    ctx.waitUntil(regent(env).fetch(new Request("http://regent/healthz")).then(() => undefined, () => undefined));
  },
};
