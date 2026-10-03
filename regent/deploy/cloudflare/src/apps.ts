// The address the apps Regent built are opened at. It reaches the same Regent container as the
// main Worker, marked so that Regent passes the request to the app you opened, not to itself.
interface Env {
  REGENT: DurableObjectNamespace;
}

export default {
  async fetch(req: Request, env: Env): Promise<Response> {
    const url = new URL(req.url);
    const fwd = new Request(req);
    fwd.headers.set("x-regent-surface", "apps");
    fwd.headers.delete("x-regent-apps-origin");
    fwd.headers.set("x-forwarded-proto", url.protocol.replace(":", ""));
    try {
      return await env.REGENT.get(env.REGENT.idFromName("regent")).fetch(fwd);
    } catch {
      return new Response("Regent is starting… try again in a moment.", { status: 503 });
    }
  },
};
