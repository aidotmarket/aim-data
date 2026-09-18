import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { datasetsApi, marketplaceApi, previewBuildApi, piiApi, type ApiDataset, type DatasetListingMetadata, type PIIScanResponse } from "@/lib/api";
import { toast } from "@/hooks/use-toast";
import { AIM_CHANNEL_DISCLOSURE_CONFIRMATION_COPY } from "@/lib/disclosure";
import DatasetDetail, { DisclosureSnapshotFailurePanel, ListingPreparation, DirectoryMembers } from "./DatasetDetail";

import { MarketplaceProvider, useMarketplace } from "@/contexts/MarketplaceContext";

vi.mock("@/contexts/AuthContext", () => ({ useAuth: () => ({ onboarding_required: false }) }));
vi.mock("@/contexts/ModeContext", () => ({ useMode: () => ({ hasFeature: () => true, channel: "aim-data" }) }));
vi.mock("@/components/PublishModal", async (importOriginal) => ({
  ...await importOriginal<typeof import("@/components/PublishModal")>(), default: () => null,
}));
import { DirectoryPublishControl } from "@/components/PublishModal";
vi.mock("@/components/copilot/ChatPanel", () => ({ default: () => null }));

function MarketplaceProbe() {
  const { isPublished } = useMarketplace();
  return <div data-testid="context-published">{String(isPublished("ds-1"))}</div>;
}

vi.mock("@/hooks/use-toast", () => ({ toast: vi.fn() }));

vi.mock("@/contexts/CoPilotContext", () => ({
  useCoPilot: () => ({
    allieAvailable: false,
    listingDraftUpdates: {},
    sendMessage: vi.fn(),
    setEmbeddedSurfaceActive: vi.fn(),
  }),
}));

vi.mock("@/components/DataVerificationFlow", () => ({
  DataVerificationFlow: ({ datasetId }: { datasetId: string }) => <div data-testid="data-verification-flow">Verification for {datasetId}</div>,
}));

const listingMetadata: DatasetListingMetadata = {
  title: "Customer Spend",
  description: "Buyer-facing customer spend data.",
  tags: ["customers", "spend"],
  column_summary: [
    { name: "email", type: "string", null_percentage: 0, uniqueness_ratio: 1, sample_values: [] },
    { name: "spend", type: "float", null_percentage: 0, uniqueness_ratio: 0.8, sample_values: [] },
  ],
  row_count: 500,
  column_count: 2,
  file_format: "csv",
  size_bytes: 1024,
  freshness_score: 0.9,
  privacy_score: 7,
  data_categories: ["commerce"],
  generated_at: "2026-07-14T00:00:00Z",
};

const cleanScan: PIIScanResponse = {
  dataset_id: "ds-1",
  scan_status: "completed",
  overall_risk: "none",
  columns_scanned: 2,
  columns_with_pii: 0,
  column_results: [],
};

const flaggedScan: PIIScanResponse = {
  ...cleanScan,
  overall_risk: "high",
  columns_with_pii: 1,
  column_results: [
    { column: "email", pii_types: ["EMAIL_ADDRESS"], risk_level: "high" },
  ],
};

function dataset(metadata: DatasetListingMetadata | null = null): ApiDataset {
  return {
    id: "ds-1",
    original_filename: "customer-spend.csv",
    file_type: "csv",
    status: "preview_ready",
    listing_id: null,
    created_at: "2026-07-14T00:00:00Z",
    updated_at: "2026-07-14T00:00:00Z",
    metadata: {
      row_count: 500,
      column_count: 2,
      size_bytes: 1024,
      columns: [
        { name: "email", type: "string" },
        { name: "spend", type: "float" },
      ],
      ...(metadata ? { listing_metadata: metadata } : {}),
    },
  };
}

function renderPreparation(apiDataset: ApiDataset, onDatasetRefresh = vi.fn()) {
  return render(
    <MemoryRouter>
      <MarketplaceProvider>
      <MarketplaceProbe />
      <ListingPreparation
        dataset={apiDataset}
        onDatasetRefresh={onDatasetRefresh}
        draftListingId={apiDataset.listing_id ?? null}
        backPath="/datasets"
        onDelete={vi.fn()}
        isDeleting={false}
      />
      </MarketplaceProvider>
    </MemoryRouter>
  );
}

beforeEach(() => {
  vi.spyOn(previewBuildApi, "latest").mockResolvedValue(null);
  const storage = new Map<string, string>();
  vi.stubGlobal("localStorage", {
    getItem: (key: string) => storage.get(key) ?? null,
    setItem: (key: string, value: string) => storage.set(key, value),
  });
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  vi.clearAllMocks();
});

describe("seller listing preparation", () => {
  it("enables metadata acceptance without a draft listing id", async () => {
    vi.spyOn(piiApi, "getConfig").mockResolvedValue({
      dataset_id: "ds-1",
      column_actions: {},
      privacy_attested: false,
      updated_at: null,
    });
    vi.spyOn(piiApi, "getScan").mockResolvedValue(cleanScan);
    vi.spyOn(datasetsApi, "getListingMetadata").mockResolvedValue(listingMetadata);

    renderPreparation(dataset());

    expect(screen.getByTestId("data-verification-flow")).toHaveTextContent("ds-1");
    expect(screen.getByRole("heading", { name: "Optional: add a verified shape label" })).toBeInTheDocument();

    const continueButton = await screen.findByRole("button", { name: "Continue to metadata" });
    await waitFor(() => expect(continueButton).toBeEnabled());
    fireEvent.click(continueButton);

    const acceptButton = await screen.findByRole("button", { name: "Accept all & continue" });
    expect(screen.getByLabelText("Title")).toHaveValue("Customer Spend");
    expect(screen.getByLabelText("Description")).toHaveValue("Buyer-facing customer spend data.");
    expect(acceptButton).toBeEnabled();
    expect(screen.getByText("Conversational field review with allAI is not available yet. Edit the fields directly and continue.")).toBeInTheDocument();
  });

  it("rehydrates persisted metadata and privacy decisions at step 2 without regenerating metadata", async () => {
    vi.spyOn(piiApi, "getConfig").mockResolvedValue({
      dataset_id: "ds-1",
      column_actions: { email: "redact" },
      privacy_attested: false,
      updated_at: "2026-07-14T01:00:00Z",
    });
    vi.spyOn(piiApi, "getScan").mockResolvedValue(flaggedScan);
    const generateMetadata = vi.spyOn(datasetsApi, "getListingMetadata");

    renderPreparation(dataset(listingMetadata));

    expect(await screen.findByText("Step 2: Metadata Review")).toBeInTheDocument();
    expect(screen.getByLabelText("Title")).toHaveValue("Customer Spend");
    expect(screen.getByLabelText("Description")).toHaveValue("Buyer-facing customer spend data.");
    expect(screen.getByText("customers")).toBeInTheDocument();
    expect(screen.queryByText("Step 1: Privacy Review")).not.toBeInTheDocument();
    expect(generateMetadata).not.toHaveBeenCalled();
  });
});

describe("disclosure snapshot failure panel", () => {
  it("renders the detailed rejection beside review without offering Retry", () => {
    const description = "ai.market rejected the disclosure snapshot because its data is invalid: Input should be a valid dictionary";

    render(
      <DisclosureSnapshotFailurePanel
        failure={{ status: "snapshot_rejected", title: "Disclosure snapshot rejected", description }}
        publishing={false}
        canRetry={true}
        onRetry={vi.fn()}
        onReview={vi.fn()}
      />
    );

    expect(screen.getByText(description)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Review disclosure decision" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Retry disclosure snapshot" })).not.toBeInTheDocument();
  });

  it.each(["snapshot_pending", "disclosure_unknown"] as const)("offers Retry for %s", (status) => {
    render(
      <DisclosureSnapshotFailurePanel
        failure={{ status, title: "Disclosure incomplete", description: "Try again safely." }}
        publishing={false}
        canRetry={true}
        onRetry={vi.fn()}
        onReview={vi.fn()}
      />
    );

    expect(screen.getByRole("button", { name: "Retry disclosure snapshot" })).toBeEnabled();
  });
});


describe("publish completion", () => {
  async function preparePublish(onDatasetRefresh = vi.fn()) {
    vi.spyOn(piiApi, "getConfig").mockResolvedValue({
      dataset_id: "ds-1", column_actions: {}, privacy_attested: false, updated_at: null,
    });
    vi.spyOn(piiApi, "getScan").mockResolvedValue(cleanScan);
    vi.spyOn(datasetsApi, "getDisclosureSample").mockResolvedValue({
      dataset_id: "ds-1", sample: [], count: 0,
    });
    const publish = vi.spyOn(marketplaceApi, "publish").mockResolvedValue({
      status: "published", listing_id: "listing-1", marketplace_url: "https://ai.market/listing/listing-1",
    });
    renderPreparation(dataset(listingMetadata), onDatasetRefresh);
    fireEvent.click(await screen.findByRole("button", { name: "Accept all & continue" }));
    fireEvent.click(screen.getByRole("checkbox", { name: AIM_CHANNEL_DISCLOSURE_CONFIRMATION_COPY }));
    fireEvent.click(screen.getByRole("button", { name: "Publish to ai.market" }));
    return publish;
  }

  function expectSuccessToast() {
    expect(toast).toHaveBeenCalledWith(expect.objectContaining({ title: "Dataset published to ai.market" }));
    const confirmation = vi.mocked(toast).mock.calls.find(([message]) => message.title === "Dataset published to ai.market")![0];
    render(<>{confirmation.description}</>);
    expect(screen.getByRole("link", { name: "View listing on ai.market" })).toHaveAttribute("href", "https://ai.market/listing/listing-1");
  }

  it("confirms completion, disables Publish and refetches the dataset for the published view", async () => {
    vi.spyOn(marketplaceApi, "createDisclosureSnapshot").mockResolvedValue({
      status: "complete", listing_id: "listing-1", disclosure_version: "v1",
    });
    const refreshed = { ...dataset(listingMetadata), listing_id: "listing-1" };
    const refetch = vi.spyOn(datasetsApi, "get").mockResolvedValue(refreshed);
    const onDatasetRefresh = vi.fn();
    const publish = await preparePublish(onDatasetRefresh);

    await waitFor(() => expect(onDatasetRefresh).toHaveBeenCalledWith(refreshed));
    expectSuccessToast();
    expect(screen.getByTestId("context-published")).toHaveTextContent("true");
    expect(refetch).toHaveBeenCalledWith("ds-1");
    expect(datasetsApi.getDisclosureSample).not.toHaveBeenCalled();
    expect(marketplaceApi.createDisclosureSnapshot).toHaveBeenCalledWith("listing-1", expect.objectContaining({sample_decision:"none", approved_sample:null}));
    expect(screen.getByRole("region", {name:"Public sample"})).toBeInTheDocument();
    expect(screen.getByText("Complete")).toBeInTheDocument();
    const published = screen.getByRole("button", { name: "Published" });
    expect(published).toBeDisabled();
    fireEvent.click(published);
    expect(publish).toHaveBeenCalledTimes(1);
    expect(screen.queryByRole("button", { name: "Retry disclosure snapshot" })).not.toBeInTheDocument();
  });

  it("keeps Publish disabled after snapshot failure and retries only the snapshot", async () => {
    const snapshot = vi.spyOn(marketplaceApi, "createDisclosureSnapshot")
      .mockRejectedValueOnce(new Error("Service unavailable"))
      .mockResolvedValueOnce({ status: "complete", listing_id: "listing-1", disclosure_version: "v1" });
    const refetch = vi.spyOn(datasetsApi, "get").mockResolvedValue({ ...dataset(), listing_id: "listing-1" });
    const publish = await preparePublish();

    const retry = await screen.findByRole("button", { name: "Retry disclosure snapshot" });
    expect(retry).toBeEnabled();
    expect(screen.getAllByText("Listing published, disclosure snapshot pending").length).toBeGreaterThan(0);
    const published = screen.getByRole("button", { name: "Published" });
    expect(published).toBeDisabled();
    fireEvent.click(published);
    expect(publish).toHaveBeenCalledTimes(1);
    expect(refetch).not.toHaveBeenCalled();
    expect(screen.queryByText("Complete")).not.toBeInTheDocument();

    fireEvent.click(retry);
    await waitFor(() => expect(refetch).toHaveBeenCalledWith("ds-1"));
    expectSuccessToast();
    expect(snapshot).toHaveBeenCalledTimes(2);
    expect(snapshot.mock.calls[1]).toEqual(snapshot.mock.calls[0]);
    expect(publish).toHaveBeenCalledTimes(1);
    expect(screen.getByRole("button", { name: "Published" })).toBeDisabled();
  });

  it("preserves successful publication if the dataset refresh fails", async () => {
    vi.spyOn(marketplaceApi, "createDisclosureSnapshot").mockResolvedValue({
      status: "complete", listing_id: "listing-1", disclosure_version: "v1",
    });
    vi.spyOn(datasetsApi, "get").mockRejectedValue(new Error("Network unavailable"));
    vi.spyOn(console, "warn").mockImplementation(() => {});
    await preparePublish();

    await waitFor(() => expect(console.warn).toHaveBeenCalled());
    expectSuccessToast();
    expect(screen.getByText("Complete")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Published" })).toBeDisabled();
    expect(screen.queryByRole("button", { name: "Retry disclosure snapshot" })).not.toBeInTheDocument();
  });
});


describe("dataset detail publication state", () => {
  async function renderDetail(listingId: string | null) {
    vi.spyOn(datasetsApi, "get").mockResolvedValue({ ...dataset(listingMetadata), listing_id: listingId });
    vi.spyOn(datasetsApi, "getSample").mockResolvedValue({ dataset_id: "ds-1", sample: [], count: 0 });
    vi.spyOn(datasetsApi, "getStatistics").mockRejectedValue(new Error("Unavailable"));
    vi.spyOn(datasetsApi, "getReadiness").mockRejectedValue(new Error("Unavailable"));
    vi.spyOn(piiApi, "getConfig").mockResolvedValue({
      dataset_id: "ds-1", column_actions: {}, privacy_attested: false, updated_at: null,
    });
    vi.spyOn(piiApi, "getScan").mockResolvedValue(cleanScan);
    vi.spyOn(datasetsApi, "getDisclosureSample").mockResolvedValue({ dataset_id: "ds-1", sample: [], count: 0 });
    render(
      <MemoryRouter initialEntries={["/datasets/ds-1"]}>
        <MarketplaceProvider>
          <MarketplaceProbe />
          <Routes><Route path="/datasets/:id" element={<DatasetDetail />} /></Routes>
        </MarketplaceProvider>
      </MemoryRouter>
    );
  }

  it("shows a server-published listing with an empty marketplace registry", async () => {
    await renderDetail("listing-1");
    expect(await screen.findByText("Published")).toBeInTheDocument();
    expect(screen.getByTestId("context-published")).toHaveTextContent("false");
    expect(screen.queryByRole("button", { name: "Publish to ai.market" })).not.toBeInTheDocument();
    fireEvent.mouseDown(screen.getByRole("tab", { name: "Marketplace" }), { button: 0, ctrlKey: false });
    expect(await screen.findByText("Listing ID: listing-1")).toBeInTheDocument();
    expect(screen.getByText("Published to ai.market")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "View Listing" })).toHaveAttribute("href", "https://ai.market/listing/listing-1");
    expect(screen.queryByText("Views")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Unpublish" })).not.toBeInTheDocument();
  });

  it("keeps publication available when listing_id is null", async () => {
    await renderDetail(null);
    fireEvent.click(await screen.findByRole("button", { name: "Accept all & continue" }));
    expect(screen.getByRole("button", { name: "Publish to ai.market" })).toBeInTheDocument();
    expect(screen.queryByText("Published")).not.toBeInTheDocument();
    expect(screen.queryByRole("tab", { name: "Marketplace" })).not.toBeInTheDocument();
  });
});


describe("directory members", () => {
  it("pages, filters and saves explicit role and sample choices", async () => {
    const row = { dataset_id: "ds-1", index: 0, relative_path: "data.csv", size_bytes: 4,
      sha256: "0".repeat(64), detected_type: "csv", role: "data" as const,
      is_sample: false, status: "current" as const, reason: null };
    const members = vi.spyOn(datasetsApi, "members").mockResolvedValue({ members: [row], total: 101, page: 1, page_size: 100, editable: true });
    const patch = vi.spyOn(datasetsApi, "patchMember").mockResolvedValue(row);
    render(<DirectoryMembers dataset={{ ...dataset(), file_type: "directory" }} />);
    await screen.findByText("data.csv");
    fireEvent.click(screen.getByLabelText("Sample data.csv"));
    await waitFor(() => expect(patch).toHaveBeenCalledWith("ds-1", 0, { is_sample: true }));
    await screen.findByText("data.csv");
    fireEvent.change(screen.getByLabelText("Role for data.csv"), { target: { value: "documentation" } });
    await waitFor(() => expect(patch).toHaveBeenCalledWith("ds-1", 0, { role: "documentation" }));
    await screen.findByText("data.csv");
    fireEvent.click(screen.getByRole("button", { name: "Next" }));
    await waitFor(() => expect(members).toHaveBeenCalledWith("ds-1", 2, "", ""));
    fireEvent.change(screen.getByLabelText("Filter by role"), { target: { value: "documentation" } });
    await waitFor(() => expect(members).toHaveBeenCalledWith("ds-1", 1, "documentation", ""));
    fireEvent.change(screen.getByLabelText("Filter by status"), { target: { value: "removed" } });
    await waitFor(() => expect(members).toHaveBeenCalledWith("ds-1", 1, "documentation", "removed"));
  });

  it("refuses sample selection on non-data and freezes published choices", async () => {
    const row = { dataset_id: "ds-1", index: 0, relative_path: "README.md", size_bytes: 0,
      sha256: "0".repeat(64), detected_type: "md", role: "documentation" as const,
      is_sample: false, status: "unsupported" as const, reason: "unsupported_type" };
    vi.spyOn(datasetsApi, "members").mockResolvedValue({ members: [row], total: 1, page: 1, page_size: 100, editable: true });
    const patch = vi.spyOn(datasetsApi, "patchMember").mockResolvedValue(row);
    const view = render(<DirectoryMembers dataset={dataset()} />);
    await screen.findByText("README.md");
    expect(screen.getByLabelText("Sample README.md")).toBeDisabled();
    expect(patch).not.toHaveBeenCalled();
    view.unmount();
    vi.mocked(datasetsApi.members).mockResolvedValue({ members: [row], total: 1, page: 1, page_size: 100, editable: false });
    render(<DirectoryMembers dataset={dataset()} />);
    await screen.findByText("Published member choices are frozen.");
    expect(screen.getByLabelText("Role for README.md")).toBeDisabled();
  });
});


describe("directory publish control", () => {
  it("publishes metadata.directory samples through preparation and leaves verification untouched", async () => {
    vi.spyOn(piiApi, "getConfig").mockResolvedValue({
      dataset_id: "ds-1", column_actions: {}, privacy_attested: false, updated_at: null,
    });
    vi.spyOn(piiApi, "getScan").mockResolvedValue(cleanScan);
    vi.spyOn(datasetsApi, "getDisclosureSample").mockResolvedValue({ dataset_id: "ds-1", sample: [], count: 0 });
    const directory = dataset(listingMetadata);
    directory.file_type = "directory";
    directory.metadata = { ...directory.metadata, directory: { sample_member_count: 2 } };
    vi.spyOn(datasetsApi, "get").mockResolvedValue(directory);
    const fetcher = vi.fn()
      .mockResolvedValueOnce({ ok: true, json: async () => ({ status: "published", listing_id: "listing-1" }) })
      .mockResolvedValueOnce({ ok: true, json: async () => ({ status: "complete" }) });
    vi.stubGlobal("fetch", fetcher);
    renderPreparation(directory);
    const panel = screen.getByTestId("data-verification-flow");
    const panelBefore = panel.outerHTML;
    fireEvent.click(await screen.findByRole("button", { name: "Accept all & continue" }));
    expect(screen.getByText("2 seller-selected sample files. Sample files are also part of the purchased set.")).toBeInTheDocument();
    expect(screen.getByRole("checkbox", { name: "Publish selected sample files for free download" })).toBeChecked();
    fireEvent.click(screen.getByRole("checkbox", { name: AIM_CHANNEL_DISCLOSURE_CONFIRMATION_COPY }));
    fireEvent.click(screen.getByRole("button", { name: "Publish to ai.market" }));
    await screen.findByRole("button", { name: "Published" });
    expect(fetcher).toHaveBeenCalledTimes(2);
    expect(JSON.parse(fetcher.mock.calls[1][1].body).sample_decision).toBe("member_files");
    expect(screen.getByTestId("data-verification-flow")).toBe(panel);
    expect(panel.outerHTML).toBe(panelBefore);
    expect(screen.getByRole("heading", { name: "Optional: add a verified shape label" })).toBeInTheDocument();
  });

  const props = { datasetId: "directory-1", publishPayload: { title: "Set", description: "Set", price_cents: 2500 },
    disclosurePayload: { approved_fields: { title: "Set" }, source_publish_operation_id: "op-1" },
    disabled: false, sampleCount: 2, onPublished: vi.fn() };

  it("accepts member_files without opening verification", async () => {
    const fetcher = vi.fn().mockResolvedValueOnce({ ok: true, json: async () => ({ status: "published", listing_id: "listing-1" }) })
      .mockResolvedValueOnce({ ok: true, json: async () => ({ status: "complete" }) });
    vi.stubGlobal("fetch", fetcher);
    render(<DirectoryPublishControl {...props} />);
    fireEvent.click(screen.getByRole("button", { name: "Publish to ai.market" }));
    await screen.findByRole("button", { name: "Published" });
    expect(fetcher).toHaveBeenCalledTimes(2);
    const wire = JSON.parse(fetcher.mock.calls[0][1].body);
    expect(wire).not.toHaveProperty("verification");
    expect(wire.vz_dataset_id).toBe("directory-1");
    const disclosure = JSON.parse(fetcher.mock.calls[1][1].body);
    expect(disclosure.sample_decision).toBe("member_files");
    expect(disclosure.approved_sample).toBeNull();
  });

  it("shows pending_members and permits a resume without claiming publication", async () => {
    const fetcher = vi.fn().mockResolvedValue({ ok: true, json: async () => ({ status: "pending_members", listing_id: "listing-1" }) });
    vi.stubGlobal("fetch", fetcher);
    render(<DirectoryPublishControl {...props} />);
    fireEvent.click(screen.getByRole("button", { name: "Publish to ai.market" }));
    expect(await screen.findByRole("status")).toHaveTextContent("Pending members");
    expect(screen.getByRole("button", { name: "Publish to ai.market" })).toBeEnabled();
    expect(fetcher).toHaveBeenCalledTimes(1);
  });

  it("renders the receiver's paid-set refusal", async () => {
    const detail = "at least one non-sample data member is required";
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: false, json: async () => ({ detail }) }));
    render(<DirectoryPublishControl {...props} />);
    fireEvent.click(screen.getByRole("button", { name: "Publish to ai.market" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(detail);
  });

  it("renders the receiver's named upgrade refusal", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: false, json: async () => ({ detail: { code: "agent_upgrade_required", minimum_version: "1.24.0", detail: "upgrade AIM Data to 1.24.0" } }) }));
    render(<DirectoryPublishControl {...props} />);
    fireEvent.click(screen.getByRole("button", { name: "Publish to ai.market" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("agent_upgrade_required: upgrade AIM Data to 1.24.0");
  });
});

describe("directory profiling outcomes", () => {
  it.each(["unsupported_type", "too_large", "parse_failed", "timeout"])("renders %s and a terminal state without a spinner", async (reason) => {
    const row = { dataset_id: "ds-1", index: 0, relative_path: "data.bin", size_bytes: 4,
      sha256: "0".repeat(64), detected_type: "unsupported", role: "data" as const,
      is_sample: false, status: "current" as const, reason: null };
    vi.spyOn(datasetsApi, "members").mockResolvedValue({ members: [row], total: 2, page: 1, page_size: 100, editable: true });
    const fixture = { ...dataset(), file_type: "directory", metadata: {
      ...dataset().metadata,
      directory_profile: { status: "completed", summary: "profiled on 1 of 2 files", profiled_bytes: 4,
        members: { "0": { status: reason, reason: `${reason}: member could not be profiled` } } },
    } };
    const { container } = render(<DirectoryMembers dataset={fixture} />);
    expect(await screen.findByText(`${reason}: member could not be profiled`)).toBeInTheDocument();
    expect(screen.getByText("Ready to list")).toBeInTheDocument();
    expect(screen.getByText("profiled on 1 of 2 files")).toBeInTheDocument();
    expect(screen.getByText("4 bytes profiled")).toBeInTheDocument();
    expect(screen.queryByText("Processing")).not.toBeInTheDocument();
    expect(container.querySelector(".animate-spin")).toBeNull();
  });

  it("renders not profiled and the skip reason", async () => {
    vi.spyOn(datasetsApi, "members").mockResolvedValue({ members: [], total: 2, page: 1, page_size: 100, editable: true });
    const fixture = { ...dataset(), file_type: "directory", metadata: {
      ...dataset().metadata,
      directory_profile: { status: "profiling_skipped", summary: "not profiled (0 of 2 files)",
        reason: "No member fits PROFILE_MAX_MEMBER_BYTES=268435456", members: {} },
    } };
    const { container } = render(<DirectoryMembers dataset={fixture} />);
    expect(await screen.findByText("Profiling skipped")).toBeInTheDocument();
    expect(screen.getByText("not profiled (0 of 2 files)")).toBeInTheDocument();
    expect(screen.getByText("No member fits PROFILE_MAX_MEMBER_BYTES=268435456")).toBeInTheDocument();
    expect(screen.queryByText("Processing")).not.toBeInTheDocument();
    expect(container.querySelector(".animate-spin")).toBeNull();
  });
});


describe("directory profile read states", () => {
  it.each(["not_started", "absent"])("renders neutral copy for %s", async (state) => {
    vi.spyOn(datasetsApi, "members").mockResolvedValue({ members: [], total: 0, page: 1, page_size: 100, editable: true });
    const fixture = { ...dataset(), file_type: "directory", metadata: {
      ...dataset().metadata,
      ...(state === "absent" ? {} : { directory_profile: { status: state, members: {} } }),
    } };
    render(<DirectoryMembers dataset={fixture} />);
    expect(await screen.findByText("Not profiled yet")).toBeInTheDocument();
    expect(screen.queryByText("Ready to list")).not.toBeInTheDocument();
    expect(screen.queryByText("Processing")).not.toBeInTheDocument();
  });

  it("renders a stale timeout without Processing", async () => {
    vi.spyOn(datasetsApi, "members").mockResolvedValue({ members: [], total: 0, page: 1, page_size: 100, editable: true });
    const fixture = { ...dataset(), file_type: "directory", metadata: {
      ...dataset().metadata,
      directory_profile: { status: "timeout", reason: "profiling run did not complete (stale)", members: {} },
    } };
    render(<DirectoryMembers dataset={fixture} />);
    expect(await screen.findByText("Profiling timed out")).toBeInTheDocument();
    expect(screen.getByText("profiling run did not complete (stale)")).toBeInTheDocument();
    expect(screen.queryByText("Processing")).not.toBeInTheDocument();
    expect(screen.queryByText("Ready to list")).not.toBeInTheDocument();
  });
});


describe("directory detail preparation wiring", () => {
  async function openDirectory(scanStatus = "completed") {
    const storedPii = scanStatus === "completed" ? {
      scanned_at: "2026-09-18T00:00:00Z", total_rows: 1, rows_sampled: 1, total_columns: 1,
      columns_with_pii: 0, columns_clean: 1, overall_risk: "none", privacy_score: 10,
      column_results: [], clean_columns: ["document_content"], duration_seconds: 0.1,
      entities_checked: ["EMAIL_ADDRESS"], scan_type: "text_content", total_blocks: 1, blocks_sampled: 1,
      scope: "Bounded member previews only; not a whole-set clearance",
    } : { status: scanStatus, reason: "PROFILE_TIMEOUT_S=30" };
    const directory: ApiDataset = { ...dataset(), file_type: "directory", metadata: {
      ...dataset().metadata, directory: { member_count: 2, sample_member_count: 1, data_member_count: 2, total_data_bytes: 8, manifest_hash: "0".repeat(64) },
      ...{ directory_profile: { status: "completed", summary: "profiled on 1 of 2 files", members: {},
        profiled_members: 1, total_data_members: 2, row_count: 1234, column_count: 7, schema_count: 2, pii: storedPii } },
    } };
    vi.spyOn(datasetsApi, "get").mockResolvedValue(directory);
    vi.spyOn(datasetsApi, "members").mockResolvedValue({ members: [{
      dataset_id: "ds-1", index: 0, relative_path: "data.csv", size_bytes: 4,
      sha256: "0".repeat(64), detected_type: "csv", role: "data", is_sample: true,
      status: "current", reason: null,
    }], total: 2, page: 1, page_size: 100, editable: true });
    vi.spyOn(piiApi, "getConfig").mockResolvedValue({
      dataset_id: "ds-1", column_actions: {}, privacy_attested: false, updated_at: null,
    });
    // Exercise the real piiApi transport with the backend's directory response shape.
    vi.stubGlobal("fetch", vi.fn(async (url) => {
      expect(String(url)).toContain("/api/pii/scan/ds-1");
      return { ok: true, json: async () => ({
        ...storedPii, dataset_id: "ds-1", filename: "folder", scan_status: scanStatus,
        columns_scanned: scanStatus === "completed" ? 1 : 0, columns_with_pii: 0,
        column_results: [], overall_risk: scanStatus === "completed" ? "none" : "unknown",
        privacy_score: scanStatus === "completed" ? 10 : null,
        scope: "Bounded member previews only; not a whole-set clearance",
      }) };
    }));
    vi.spyOn(datasetsApi, "getListingMetadata").mockResolvedValue(listingMetadata);
    render(<MemoryRouter initialEntries={["/datasets/ds-1"]}><MarketplaceProvider>
      <MarketplaceProbe />
      <Routes><Route path="/datasets/:id" element={<DatasetDetail />} /></Routes>
    </MarketplaceProvider></MemoryRouter>);
    expect(await screen.findByText("data.csv")).toBeInTheDocument();
    const members = screen.getByRole("region", { name: "Directory members" });
    expect(members.querySelector("table")).toBeInTheDocument();
    expect(members.compareDocumentPosition(screen.getByText("Listing Flow")) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(screen.getByText("profiled on 1 of 2 files")).toBeInTheDocument();
    expect(screen.getByText("Step 1: Privacy Review")).toBeInTheDocument();
    expect(await screen.findByText("Bounded member previews only; not a whole-set clearance")).toBeInTheDocument();
    if (scanStatus !== "completed") return directory;
    const next = await screen.findByRole("button", { name: "Continue to metadata" });
    await waitFor(() => expect(next).toBeEnabled());
    fireEvent.click(next);
    expect(await screen.findByText("Step 2: Metadata Review")).toBeInTheDocument();
    fireEvent.click(await screen.findByRole("button", { name: "Accept all & continue" }));
    expect(screen.getByText("Step 3: Listing Details and Disclosure")).toBeInTheDocument();
    expect(screen.getByText("Disclosure summary")).toBeInTheDocument();
    expect(screen.getByRole("checkbox", { name: "Publish selected sample files for free download" })).toBeChecked();
    const heading = screen.getByRole("heading", { name: "Optional: add a verified shape label" });
    expect(heading).toHaveClass("border-t", "pt-6");
    expect(screen.getByRole("button", { name: "Publish to ai.market" })).toBeDisabled();
    fireEvent.click(screen.getByRole("checkbox", { name: AIM_CHANNEL_DISCLOSURE_CONFIRMATION_COPY }));
    expect(screen.getByRole("button", { name: "Publish to ai.market" })).toBeEnabled();
    return directory;
  }

  it("publishes from the page with directory payload and refreshes into the published view", async () => {
    const directory = await openDirectory();
    const fetcher = vi.fn()
      .mockResolvedValueOnce({ ok: true, json: async () => ({ status: "published", listing_id: "listing-directory" }) })
      .mockResolvedValueOnce({ ok: true, json: async () => ({ status: "complete" }) });
    vi.stubGlobal("fetch", fetcher);
    vi.mocked(datasetsApi.get).mockResolvedValue({ ...directory, listing_id: "listing-directory" });
    fireEvent.click(screen.getByRole("button", { name: "Publish to ai.market" }));
    await screen.findByRole("tab", { name: "Marketplace" });
    expect(screen.getByText("Published")).toBeInTheDocument();
    expect(screen.getByText("1,234")).toBeInTheDocument();
    expect(screen.getByText("7")).toBeInTheDocument();
    expect(screen.getByText("Profiled 1 of 2 files; 2 schemas.")).toBeInTheDocument();
    expect(screen.queryByRole("tab", { name: "Sample Data" })).not.toBeInTheDocument();
    fireEvent.mouseDown(screen.getByRole("tab", { name: "Readiness" }), { button: 0, ctrlKey: false });
    expect(await screen.findByText("Readiness is assessed per member; see the member table")).toBeInTheDocument();
    expect(screen.queryByText("Loading readiness report...")).not.toBeInTheDocument();
    expect(screen.getByTestId("context-published")).toHaveTextContent("true");
    expect(JSON.parse(fetcher.mock.calls[0][1].body)).toEqual(expect.objectContaining({
      vz_dataset_id: "ds-1", file_format: "directory", title: "Customer Spend",
    }));
    expect(JSON.parse(fetcher.mock.calls[1][1].body)).toEqual(expect.objectContaining({
      dataset_id: "ds-1", sample_decision: "member_files", approved_sample: null,
    }));
    expect(screen.getByRole("region", { name: "Directory members" })).toBeInTheDocument();
    fireEvent.mouseDown(screen.getByRole("tab", { name: "Marketplace" }), { button: 0, ctrlKey: false });
    expect(await screen.findByText("Listing ID: listing-directory")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "View Listing" })).toHaveAttribute("href", "https://ai.market/listing/listing-directory");
  });

  it.each(["timeout", "failed"])("shows the directory %s reason and blocks privacy continuation", async (status) => {
    await openDirectory(status);
    expect(screen.getByRole("alert")).toHaveTextContent("PROFILE_TIMEOUT_S=30");
    expect(screen.getByRole("button", { name: "Continue to metadata" })).toBeDisabled();
    expect(screen.queryByText("No personal data detected")).not.toBeInTheDocument();
    expect(screen.queryByText("Step 2: Metadata Review")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Run scan again" }));
    await waitFor(() => expect(fetch).toHaveBeenCalledWith(expect.stringContaining("/api/pii/scan/ds-1"), expect.objectContaining({ method: "POST" })));
    expect(screen.getByRole("button", { name: "Continue to metadata" })).toBeDisabled();
    expect(screen.getByRole("alert")).toHaveTextContent("PROFILE_TIMEOUT_S=30");
  });

  it("locks shared fields during directory publication and uses the receiver URL", async () => {
    await openDirectory();
    let finishPublish!: (value: unknown) => void;
    const fetcher = vi.fn()
      .mockImplementationOnce(() => new Promise(resolve => { finishPublish = resolve; }))
      .mockResolvedValueOnce({ ok: true, json: async () => ({ status: "complete" }) });
    vi.stubGlobal("fetch", fetcher);
    fireEvent.click(screen.getByRole("button", { name: "Publish to ai.market" }));
    expect(screen.getByLabelText("Title")).toBeDisabled();
    expect(screen.getByLabelText("Description")).toBeDisabled();
    expect(screen.getByRole("checkbox", { name: AIM_CHANNEL_DISCLOSURE_CONFIRMATION_COPY })).toBeDisabled();
    await act(async () => { finishPublish({ ok: true, json: async () => ({ status: "published",
      listing_id: "listing-directory", marketplace_url: "https://receiver.example/datasets/directory" }) }); });
    const completion = vi.mocked(toast).mock.calls.find(([value]) => value.title === "Dataset published to ai.market")?.[0];
    expect(completion).toBeDefined();
    render(<>{completion?.description}</>);
    expect(screen.getByRole("link", { name: "View listing on ai.market" })).toHaveAttribute("href", "https://receiver.example/datasets/directory");
    expect(screen.getByLabelText("Title")).toBeEnabled();
  });

  it("retains disclosure retry and does not republish when the snapshot fails", async () => {
    await openDirectory();
    const fetcher = vi.fn()
      .mockResolvedValueOnce({ ok: true, json: async () => ({ status: "published", listing_id: "listing-directory", marketplace_url: "https://receiver.example/retry" }) })
      .mockResolvedValueOnce({ ok: false, json: async () => ({ detail: "Snapshot unavailable" }) })
      .mockResolvedValueOnce({ ok: true, json: async () => ({ status: "complete" }) });
    vi.stubGlobal("fetch", fetcher);
    fireEvent.click(screen.getByRole("button", { name: "Publish to ai.market" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Snapshot unavailable");
    expect(screen.getByText("Listing published, disclosure snapshot pending")).toBeInTheDocument();
    expect(datasetsApi.get).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByRole("button", { name: "Retry disclosure" }));
    expect(await screen.findByRole("button", { name: "Published" })).toBeDisabled();
    expect(fetcher).toHaveBeenCalledTimes(3);
    expect(fetcher.mock.calls[2]).toEqual(fetcher.mock.calls[1]);
    expect(screen.getByText("Complete")).toBeInTheDocument();
    const completion = vi.mocked(toast).mock.calls.find(([value]) => value.title === "Dataset published to ai.market")?.[0];
    render(<>{completion?.description}</>);
    expect(screen.getByRole("link", { name: "View listing on ai.market" })).toHaveAttribute("href", "https://receiver.example/retry");
  });
});
