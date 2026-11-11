export type ApiError = { error?: { message?: string } };

const base = process.env.NEXT_PUBLIC_API_URL || "";

export async function api<T>(path: string, options: RequestInit = {}): Promise<T> {
  const headers = new Headers(options.headers);
  if (options.body && !(options.body instanceof FormData) && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }
  let response: Response;
  try {
    response = await fetch(`${base}${path}`, { ...options, headers, credentials: "include" });
  } catch {
    throw new Error("Could not reach the API. The frontend has no backend URL configured, or the API host is down.");
  }
  const text = await response.text();
  let data: unknown = {};
  if (text) {
    try {
      data = JSON.parse(text);
    } catch {
      throw new Error(`API returned a non-JSON response (${response.status}). Check NEXT_PUBLIC_API_URL / API_INTERNAL_URL.`);
    }
  }
  if (!response.ok) {
    throw new Error((data as ApiError).error?.message || `Request failed (${response.status}).`);
  }
  return data as T;
}
