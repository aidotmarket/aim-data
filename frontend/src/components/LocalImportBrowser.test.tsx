import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { importApi } from "@/lib/api";
import { UploadProvider, useUpload } from "@/contexts/UploadContext";
import { toast } from "sonner";
import { LocalImportBrowser } from "./LocalImportBrowser";

vi.mock("@/contexts/BrandContext", async () => {
  const { AIM_DATA_BRAND } = await import("@/lib/brandConfig");
  return { useBrand: () => AIM_DATA_BRAND };
});
vi.mock("sonner", () => ({ toast: { success: vi.fn(), warning: vi.fn(), error: vi.fn() } }));

const browseRequest = importApi.browse.bind(importApi);

beforeEach(() => {
  const storage = new Map<string, string>();
  vi.stubGlobal("localStorage", {
    getItem: (key: string) => storage.get(key) ?? null,
    setItem: (key: string, value: string) => storage.set(key, value),
    removeItem: (key: string) => storage.delete(key),
    clear: () => storage.clear(),
  });
  vi.spyOn(importApi, "browse").mockResolvedValue({ path: "/import/", entries: [
    { name: "a.csv", type: "file", size_bytes: 4 },
    { name: "b.csv", type: "file", size_bytes: 4 },
  ], total: 2, limit: 500, offset: 0 });
  vi.spyOn(importApi, "getStatus");
});
afterEach(() => { cleanup(); vi.useRealTimers(); vi.unstubAllGlobals(); vi.restoreAllMocks(); vi.clearAllMocks(); });

function UploadProbe() {
  const { hasImportFiles, openModal } = useUpload();
  return <><button onClick={openModal}>Open upload</button><span>{String(hasImportFiles)}</span></>;
}

async function startImport(beforeStart = () => {}) {
  const onSuccess = vi.fn();
  const onImportingChange = vi.fn();
  const onClose = vi.fn();
  render(<LocalImportBrowser onSuccess={onSuccess} onImportingChange={onImportingChange} onClose={onClose} />);
  expect(await screen.findByText("If folder datasets are enabled on this install, the whole current folder becomes one dataset regardless of the selection.")).toBeInTheDocument();
  fireEvent.click(await screen.findByRole("button", { name: "Select All" }));
  beforeStart();
  await act(async () => { fireEvent.click(screen.getByRole("button", { name: "Import selected (2)" })); });
  return { onSuccess, onImportingChange, onClose };
}

describe("folder import response wiring", () => {
  it("shows the compose host directory and environment variable when empty", async () => {
    vi.mocked(importApi.browse).mockResolvedValue({ path: "/data/import/", entries: [], total: 0, limit: 500, offset: 0 });
    render(<LocalImportBrowser />);

    expect(await screen.findByText("No files found in import directory.")).toBeInTheDocument();
    expect(screen.getByText(/Add files to/)).toHaveTextContent(
      "Add files to ./import (next to your compose file) on your machine,or set HOST_IMPORT_DIR in your .env file."
    );
  });

  it("loads the initial folder without serialising an empty path", async () => {
    vi.mocked(importApi.browse).mockImplementation(browseRequest);
    const fetcher = vi.fn().mockResolvedValue({ ok: true, json: async () => ({
      path: "/import/", entries: [], total: 0, limit: 500, offset: 0,
    }) });
    vi.stubGlobal("fetch", fetcher);
    render(<LocalImportBrowser />);

    await screen.findByText("No files found in import directory.");
    const requestUrl = new URL(String(fetcher.mock.calls[0][0]), "http://localhost");
    expect(requestUrl.pathname).toBe("/api/datasets/import/browse");
    expect(requestUrl.searchParams.has("path")).toBe(false);
  });

  it("marks local import files available after the upload-modal probe succeeds", async () => {
    render(<UploadProvider><UploadProbe /></UploadProvider>);
    fireEvent.click(screen.getByRole("button", { name: "Open upload" }));
    expect(await screen.findByText("true")).toBeInTheDocument();
    expect(importApi.browse).toHaveBeenCalledWith("", 1, 0);
  });

  it("requires an explicit selection before starting an import", async () => {
    const start = vi.spyOn(importApi, "start");
    render(<LocalImportBrowser />);

    const button = await screen.findByRole("button", { name: "Import selected (0)" });
    expect(button).toBeDisabled();
    fireEvent.click(button);
    expect(start).not.toHaveBeenCalled();
  });

  it("completes a direct directory response without polling and triggers card refresh", async () => {
    vi.spyOn(importApi, "start").mockResolvedValue({ dataset_id: "directory-1", status: "complete", total_files: 2, total_bytes: 8 });
    const interval = vi.spyOn(globalThis, "setInterval");
    const callbacks = await startImport(() => interval.mockClear());
    expect(importApi.getStatus).not.toHaveBeenCalled();
    expect(interval).not.toHaveBeenCalled();
    expect(screen.getByText("Successfully imported 1 dataset")).toBeInTheDocument();
    expect(toast.success).toHaveBeenCalledWith("Imported folder import as one dataset (2 files)");
    expect(callbacks.onSuccess).toHaveBeenCalledTimes(1);
    expect(callbacks.onImportingChange.mock.calls).toEqual([[true], [false]]);

    fireEvent.click(screen.getByRole("button", { name: "Done" }));
    expect(callbacks.onClose).toHaveBeenCalledTimes(1);
  });

  it.each(["directory", "legacy"])("imports the browsed folder with relative files in %s mode", async (mode) => {
    vi.mocked(importApi.browse).mockResolvedValueOnce({ path: "/import/", entries: [
      { name: "subset", type: "directory" },
    ], total: 1, limit: 500, offset: 0 }).mockResolvedValueOnce({ path: "/import/subset/", entries: [
      { name: "a.csv", type: "file", size_bytes: 4 },
    ], total: 1, limit: 500, offset: 0 });
    vi.spyOn(importApi, "start").mockResolvedValue(mode === "directory"
      ? { dataset_id: "directory-1", status: "complete", total_files: 1, total_bytes: 4 }
      : { job_id: "legacy-1", status: "running", total_files: 1, total_bytes: 4 });
    render(<LocalImportBrowser />);
    fireEvent.click(await screen.findByRole("button", { name: "subset" }));
    fireEvent.click(await screen.findByRole("button", { name: "Select All" }));
    await act(async () => { fireEvent.click(screen.getByRole("button", { name: "Import selected (1)" })); });
    expect(importApi.start).toHaveBeenCalledWith("/import/subset/", ["a.csv"]);
    if (mode === "directory") {
      expect(toast.success).toHaveBeenCalledWith("Imported folder subset as one dataset (1 file)");
    }
  });

  it("keeps legacy job polling and completion callbacks", async () => {
    vi.spyOn(importApi, "start").mockImplementation(async () => {
      vi.useFakeTimers();
      return { job_id: "legacy-1", status: "running", total_files: 2, total_bytes: 8 };
    });
    vi.mocked(importApi.getStatus).mockResolvedValue({ job_id: "legacy-1", status: "complete",
      progress: { files_total: 2, files_complete: 2, files_copying: 0, files_pending: 0, bytes_total: 8, bytes_copied: 8 },
      results: [{ file: "a.csv", dataset_id: "ds-a", status: "complete" }, { file: "b.csv", dataset_id: "ds-b", status: "done" }],
    });
    const callbacks = await startImport();
    expect(screen.getByText("Importing files…")).toBeInTheDocument();
    expect(callbacks.onSuccess).not.toHaveBeenCalled();
    await act(async () => { await vi.advanceTimersByTimeAsync(1000); });
    expect(importApi.getStatus).toHaveBeenCalledWith("legacy-1");
    expect(screen.getByText("Successfully imported 2 datasets")).toBeInTheDocument();
    expect(toast.success).toHaveBeenCalledWith("Imported 2 datasets");
    expect(callbacks.onSuccess).toHaveBeenCalledTimes(1);
    expect(callbacks.onImportingChange.mock.calls).toEqual([[true], [false]]);
    await act(async () => { await vi.advanceTimersByTimeAsync(3000); });
    expect(importApi.getStatus).toHaveBeenCalledTimes(1);
  });

  it("shows a named registration refusal as an import error without polling", async () => {
    const message = "oversized.csv exceeds MAX_MEMBER_BYTES=100";
    vi.spyOn(importApi, "start").mockRejectedValue(new Error(message));
    const callbacks = await startImport();
    expect(screen.getByText(message)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Import selected (2)" })).toBeEnabled();
    expect(importApi.getStatus).not.toHaveBeenCalled();
    expect(callbacks.onSuccess).not.toHaveBeenCalled();
    expect(callbacks.onImportingChange.mock.calls).toEqual([[true], [false]]);
  });
});
