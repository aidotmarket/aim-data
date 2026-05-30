import { getApiUrl } from "@/lib/api";

export interface CoPilotSessionMessage {
  id: string;
  role: "user" | "assistant" | "system";
  content: string;
  created_at: string;
  kind?: "chat" | "nudge" | "system";
}

export interface S3ConnectionSnapshot {
  id: string;
  name: string;
  bucket: string;
  region: string;
  prefix?: string | null;
  role_arn?: string | null;
  external_id?: string | null;
  status: string;
  error_message?: string | null;
  last_scanned_at?: string | null;
  trust_policy?: Record<string, unknown> | null;
  permission_policy?: Record<string, unknown> | null;
}

function authHeaders(token: string): Record<string, string> {
  return {
    "Content-Type": "application/json",
    Authorization: `Bearer ${token}`,
  };
}

async function copilotFetch<T>(endpoint: string, token: string): Promise<T> {
  const response = await fetch(`${getApiUrl()}${endpoint}`, {
    headers: authHeaders(token),
  });

  if (!response.ok) {
    const body = await response.json().catch(() => ({ detail: response.statusText }));
    throw new Error(body.detail || `CoPilot API error: ${response.status}`);
  }

  return response.json();
}

export const copilotApi = {
  currentMessages: (token: string) =>
    copilotFetch<CoPilotSessionMessage[]>("/api/copilot/sessions/current/messages", token),

  listS3Connections: (token: string) =>
    copilotFetch<S3ConnectionSnapshot[]>("/api/s3-connections/", token),

  getS3Connection: (token: string, connectionId: string) =>
    copilotFetch<S3ConnectionSnapshot>(`/api/s3-connections/${connectionId}`, token),

  websocketUrl: (token: string) => {
    const apiUrl = getApiUrl() || window.location.origin;
    const wsProtocol = apiUrl.startsWith("https") ? "wss" : "ws";
    const wsHost = apiUrl.replace(/^https?:\/\//, "");
    return `${wsProtocol}://${wsHost}/ws/copilot?token=${encodeURIComponent(token)}`;
  },
};
