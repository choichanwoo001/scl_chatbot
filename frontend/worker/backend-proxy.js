// Connected deployments use Python as the sole owner of API business rules.
// Standalone preview keeps its public snapshot adapter when no backend is configured.
export async function relayBackend(request, env) {
  const origin = new URL(env.BACKEND_API_URL);
  if (origin.protocol !== "https:" || origin.username || origin.password || origin.pathname !== "/" || origin.search || origin.hash) {
    throw new Error("BACKEND_API_URL must be an HTTPS origin");
  }
  if (origin.origin === new URL(request.url).origin) throw new Error("Backend cannot target this Worker");
  const incoming = new URL(request.url);
  const target = new URL(incoming.pathname + incoming.search, origin);
  const headers = new Headers();
  for (const name of ["content-type", "accept"]) {
    if (request.headers.has(name)) headers.set(name, request.headers.get(name));
  }
  try {
    const response = await fetch(target, {
      method: request.method, headers,
      body: ["GET", "HEAD"].includes(request.method) ? undefined : await request.arrayBuffer(),
      redirect: "manual", signal: AbortSignal.timeout(60000),
    });
    // Never redirect a request containing user data to another origin.
    if (response.status >= 300 && response.status < 400) throw new Error("Unexpected backend redirect");
    const outgoing = new Headers({ "cache-control": "no-store" });
    for (const name of ["content-type", "retry-after"]) {
      if (response.headers.has(name)) outgoing.set(name, response.headers.get(name));
    }
    return new Response(response.body, { status: response.status, headers: outgoing });
  } catch {
    return new Response(JSON.stringify({ detail: "API 서버에 연결하지 못했습니다. 잠시 후 다시 시도해 주세요." }), {
      status: 503, headers: { "content-type": "application/json; charset=utf-8", "cache-control": "no-store" },
    });
  }
}
