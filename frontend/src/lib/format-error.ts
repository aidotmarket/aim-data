// Coerce an API error "detail" of ANY shape into a safe, human-readable string.
//
// FastAPI request-validation errors (HTTP 422) return `detail` as an ARRAY of
// objects ({type, loc, msg, input, url}). Rendering that array/object directly
// as a React child throws React error #31 ("Objects are not valid as a React
// child"), which the ErrorBoundary catches as a full "Something went wrong"
// white-screen. This helper guarantees a string so an error is shown as a
// message, never as a crash. BQ-AIM-DATA-PUBLISH-ERROR-RENDER-S1136.
export function formatErrorDetail(detail: unknown, fallback: string): string {
  if (detail == null) return fallback;
  if (typeof detail === "string") return detail.trim() || fallback;
  if (Array.isArray(detail)) {
    const parts = detail
      .map((item) => {
        if (item == null) return "";
        if (typeof item === "string") return item;
        if (typeof item === "object") {
          const msg = (item as Record<string, unknown>).msg;
          if (typeof msg === "string") return msg;
        }
        return "";
      })
      .filter(Boolean);
    return parts.length ? parts.join("; ") : fallback;
  }
  if (typeof detail === "object") {
    const rec = detail as Record<string, unknown>;
    const msg = rec.msg ?? rec.message ?? rec.title;
    if (typeof msg === "string" && msg.trim()) return msg;
  }
  return fallback;
}
