const METHODS = ["GET", "POST", "PUT", "PATCH", "DELETE"] as const;

async function proxy(request: Request, context: { params: Promise<{ path: string[] }> }) {
  const { path } = await context.params;
  const current = new URL(request.url);
  const target = `${process.env.API_INTERNAL_URL || "http://127.0.0.1:8000"}/api/${path.join("/")}${current.search}`;
  const headers = new Headers(request.headers);
  headers.delete("host");
  const body = request.method === "GET" || request.method === "HEAD" ? undefined : await request.arrayBuffer();
  const response = await fetch(target, { method: request.method, headers, body, redirect: "manual" });
  const outbound = new Headers();
  response.headers.forEach((value, key) => {
    if (key.toLowerCase() !== "set-cookie") outbound.append(key, value);
  });
  for (const cookie of response.headers.getSetCookie()) outbound.append("set-cookie", cookie);
  return new Response(response.body, { status: response.status, headers: outbound });
}

export const GET = proxy;
export const POST = proxy;
export const PUT = proxy;
export const PATCH = proxy;
export const DELETE = proxy;

export const dynamic = "force-dynamic";

void METHODS;
