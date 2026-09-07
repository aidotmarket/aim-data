import type { AuthLoginResponse } from "./api";

const PATH = "/api/auth/aim-market";
export interface Bootstrap {
  enabled: boolean;
  reason: string | null;
  csrf_nonce: string;
  loopback_origin: string;
}
export const fallbackGuidance = "Password and two-factor sign-in remain available if your account has a password. Provider-only accounts must retry later or contact support; do not disable two-factor authentication.";
export function authGuidance(reason: string): string {
  const messages: Record<string, string> = {
    local_disabled: "ai.market sign-in is not enabled on this installation.",
    client_disabled: "ai.market sign-in is disabled.",
    backend_unsupported: "This marketplace version does not support ai.market sign-in.",
    backend_unavailable: "The marketplace is temporarily unavailable. Please retry.",
    loopback_origin_required: "Open AIM Data on this computer using the displayed 127.0.0.1 address. Remote-host sign-in is unsupported.",
    access_denied: "Sign-in was not approved. Please start again.",
  };
  return `${messages[reason] ?? "Sign-in could not finish. Please start again or contact support."} ${fallbackGuidance}`;
}
async function request<T>(path: string, nonce?: string): Promise<T> {
  const response = await fetch(PATH + path, {
    method: nonce === undefined ? "GET" : "POST",
    credentials: "same-origin", cache: "no-store", redirect: "error",
    headers: nonce === undefined ? {} : { "Content-Type": "application/json" },
    ...(nonce === undefined ? {} : { body: JSON.stringify({ csrf_nonce: nonce }) }),
  });
  const body = await response.json();
  if (!response.ok) throw new Error(authGuidance(body.error_code));
  return body;
}
export const bootstrapAuth = () => request<Bootstrap>("/bootstrap");
export async function startAuth(bootstrap: Bootstrap): Promise<string> {
  if (!bootstrap.enabled) throw new Error(authGuidance(bootstrap.reason ?? "backend_unavailable"));
  if (location.origin !== bootstrap.loopback_origin) throw new Error(authGuidance("loopback_origin_required"));
  const result = await request<{ authorization_url: string }>("/start", bootstrap.csrf_nonce);
  // The server owns the issuer, PKCE and continuation. Never append browser credentials.
  return result.authorization_url;
}
export async function completeAuth(): Promise<AuthLoginResponse> {
  const bootstrap = await bootstrapAuth();
  const data = await request<AuthLoginResponse>("/complete", bootstrap.csrf_nonce);
  if (data.auth_mode !== "oauth" || !["registered", "not_ready"].includes(data.registration_status)) {
    throw new Error(authGuidance("upstream_invalid_response"));
  }
  return data;
}
