import { describe, it, expect } from "vitest";
import { formatErrorDetail } from "./format-error";

describe("formatErrorDetail", () => {
  it("returns a string message for a FastAPI 422 detail array (the React #31 crash case)", () => {
    const detail = [
      { type: "extra_forbidden", loc: ["body", "s3_connection", "serial_id"], msg: "Extra inputs are not permitted", input: "x", url: "https://errors" },
    ];
    const out = formatErrorDetail(detail, "fallback");
    expect(typeof out).toBe("string");
    expect(out).toContain("Extra inputs are not permitted");
  });

  it("passes through a plain string detail", () => {
    expect(formatErrorDetail("install serial linkage required", "fallback")).toBe("install serial linkage required");
  });

  it("uses the fallback for null/empty", () => {
    expect(formatErrorDetail(null, "fallback")).toBe("fallback");
    expect(formatErrorDetail([], "fallback")).toBe("fallback");
  });

  it("extracts msg from a single error object", () => {
    expect(formatErrorDetail({ msg: "bad thing" }, "fallback")).toBe("bad thing");
  });
});
