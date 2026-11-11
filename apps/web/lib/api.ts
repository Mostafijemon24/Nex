export type ApiError = { error?: { message?: string } };

const base = process.env.NEXT_PUBLIC_API_URL || "";

/** Absolute URL for a file endpoint (images, downloads, video) served by the API. */
export function apiUrl(path: string) {
  return `${base}${path}`;
}

/** Fetch a text file (e.g. a Markdown document version) from the API with the session cookie. */
export async function apiText(path: string): Promise<string> {
  let response: Response;
  try {
    response = await fetch(`${base}${path}`, { credentials: "include" });
  } catch {
    throw new Error("Could not reach the API.");
  }
  if (!response.ok) throw new Error(`Could not load the file (${response.status}).`);
  return response.text();
}

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
