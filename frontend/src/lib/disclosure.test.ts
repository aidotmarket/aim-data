import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  AIM_CHANNEL_DISCLOSURE_CONFIRMATION_COPY,
  buildApprovedMetadataDraft,
  buildDisclosureSnapshotPayload,
  classifyDisclosureSnapshotFailure,
  previewBudget,
} from "./disclosure";
import {
  DisclosureSnapshotRequestError,
  marketplaceApi,
  previewBuildApi,
  storeAuthTokens,
  type ApiDataset,
  type DatasetListingMetadata,
} from "./api";
import type { ListingEditorValue } from "@/components/ListingEditorForm";

const form: ListingEditorValue = {
  title: " Customer Spend ",
  description: " Buyer-facing spend data. ",
  category: "retail",
  tags: ["customers", "spend"],
  priceUsd: "25",
};

const metadata: DatasetListingMetadata = {
  title: "Customer Spend",
  description: "Buyer-facing spend data.",
  tags: ["customers"],
  column_summary: [
    { name: "segment", type: "string", null_percentage: 0, uniqueness_ratio: 0.5, sample_values: [] },
    { name: "spend", type: "float", null_percentage: 0, uniqueness_ratio: 0.9, sample_values: [] },
  ],
  row_count: 500,
  column_count: 2,
  file_format: "csv",
  size_bytes: 1024,
  freshness_score: 0.8,
  privacy_score: 9,
  data_categories: ["retail"],
  generated_at: "2026-07-08T00:00:00Z",
};

const dataset = {
  id: "ds-1",
  original_filename: "customers.csv",
  file_type: "csv",
  status: "preview_ready",
  created_at: "2026-07-08T00:00:00Z",
  updated_at: "2026-07-08T00:00:00Z",
  metadata: { row_count: 500, column_count: 2, size_bytes: 1024, columns: [] },
} as ApiDataset;

const expectedApprovedFields = {
  title: "Customer Spend",
  description: "Buyer-facing spend data.",
  category: "retail",
  tags: ["customers", "spend"],
  schema: {
    columns: [
      { name: "segment", type: "string", null_percentage: 0, uniqueness_ratio: 0.5 },
      { name: "spend", type: "float", null_percentage: 0, uniqueness_ratio: 0.9 },
    ],
  },
  data_format: "csv",
  source_row_count: 500,
  source_column_count: 2,
  compliance_summary: {
    privacy_score: 9,
    freshness_score: 0.8,
    data_categories: ["retail"],
  },
  source_delivery_public_metadata: {
    file_format: "csv",
    size_bytes: 1024,
  },
};

beforeEach(() => {
  vi.stubGlobal("localStorage", {
    getItem: vi.fn(() => null),
    setItem: vi.fn(),
    removeItem: vi.fn(),
  });
});

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("disclosure payload builder", () => {
  it("keeps AIM Data auth tokens when marketplace auth returns the reserved local status", async () => {
    const values = new Map<string, string>();
    const storage = {
      getItem: vi.fn((key: string) => values.get(key) ?? null),
      setItem: vi.fn((key: string, value: string) => values.set(key, value)),
      removeItem: vi.fn((key: string) => values.delete(key)),
    };
    vi.stubGlobal("localStorage", storage);
    storeAuthTokens({
      access_token: "local-access",
      refresh_token: "local-refresh",
      token_type: "bearer",
      auth_mode: "password",
      user: { id: "seller" },
    } as never);
    storage.removeItem.mockClear();
    vi.spyOn(globalThis, "fetch").mockResolvedValue({
      ok: false,
      status: 409,
      json: vi.fn().mockResolvedValue({
        detail: {
          code: "seller_session_required",
          message: "Sign in to ai.market in AIM Data, then try again.",
        },
      }),
    } as unknown as Response);

    await expect(previewBuildApi.marketplaceSummary("job")).rejects.toThrow(
      "Sign in to ai.market in AIM Data, then try again.",
    );
    expect(storage.removeItem).not.toHaveBeenCalled();
    expect(values.get("aim_data_access_token")).toBe("local-access");
    expect(values.get("aim_data_refresh_token")).toBe("local-refresh");
  });

  it("maps approved metadata to approved_fields", () => {
    const approved = buildApprovedMetadataDraft(form, metadata, dataset);

    expect(approved).toMatchObject({
      title: "Customer Spend",
      description: "Buyer-facing spend data.",
      category: "retail",
      tags: ["customers", "spend"],
      data_format: "csv",
      source_row_count: 500,
      source_column_count: 2,
    });
    expect(approved.schema).toEqual([
      { name: "segment", type: "string", null_percentage: 0, uniqueness_ratio: 0.5 },
      { name: "spend", type: "float", null_percentage: 0, uniqueness_ratio: 0.9 },
    ]);
  });

  it("serializes the exact no-sample request with schema.columns", async () => {
    const payload = buildDisclosureSnapshotPayload({
      approvedFields: buildApprovedMetadataDraft(form, metadata, dataset),
      sampleDecision: "none",
      approvedSample: null,
      confirmed: true,
      sourcePublishOperationId: "op-1",
    });
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue({
      ok: true,
      status: 201,
      json: vi.fn().mockResolvedValue({ status: "complete", listing_id: "listing-1" }),
    } as unknown as Response);

    await marketplaceApi.createDisclosureSnapshot("listing-1", { dataset_id: dataset.id, ...payload });

    const request = fetchMock.mock.calls[0]?.[1];
    expect(JSON.parse(String(request?.body))).toEqual({
      dataset_id: "ds-1",
      approved_fields: expectedApprovedFields,
      sample_decision: "none",
      approved_sample: null,
      ai_training_notification_ack: true,
      ai_training_notification_text: AIM_CHANNEL_DISCLOSURE_CONFIRMATION_COPY,
      license: "standard_marketplace",
      approval_source: "aim_channel",
      source_publish_operation_id: "op-1",
    });
  });

  it("rejects legacy row approval before any outbound request", () => {
    expect(() => buildDisclosureSnapshotPayload({approvedFields:buildApprovedMetadataDraft(form, metadata, dataset),sampleDecision:"approved_rows",approvedSample:{columns:["x"],row_refs:["preview:0"],rows:[{x:"private"}]},confirmed:true,sourcePublishOperationId:"op"})).toThrow("legacy_sample_unavailable");
  });

  it("surfaces a deterministic 422 as a plain-English rejection", async () => {
    const payload = buildDisclosureSnapshotPayload({
      approvedFields: buildApprovedMetadataDraft(form, metadata, dataset),
      sampleDecision: "none",
      approvedSample: null,
      confirmed: true,
      sourcePublishOperationId: "op-1",
    });
    vi.spyOn(globalThis, "fetch").mockResolvedValue({
      ok: false,
      status: 422,
      json: vi.fn().mockResolvedValue({
        detail: [{ msg: "Input should be a valid dictionary" }],
      }),
    } as unknown as Response);

    const request = marketplaceApi.createDisclosureSnapshot("listing-1", { dataset_id: dataset.id, ...payload });

    await expect(request).rejects.toEqual(expect.objectContaining<Partial<DisclosureSnapshotRequestError>>({
      name: "DisclosureSnapshotRequestError",
      status: 422,
      message: "ai.market rejected the disclosure snapshot because its data is invalid: Input should be a valid dictionary",
    }));
  });

  it("classifies a 422 validation response as a non-retryable rejection", () => {
    const message = "ai.market rejected the disclosure snapshot because its data is invalid: Input should be a valid dictionary";

    expect(classifyDisclosureSnapshotFailure(new DisclosureSnapshotRequestError(message, 422))).toEqual({
      status: "snapshot_rejected",
      title: "Disclosure snapshot rejected",
      description: message,
    });
  });

  it.each([502, 504])("classifies HTTP %s as a pending snapshot", (status) => {
    expect(classifyDisclosureSnapshotFailure(
      new DisclosureSnapshotRequestError(`Disclosure snapshot failed: ${status}`, status)
    )).toEqual({
      status: "snapshot_pending",
      title: "Listing published, disclosure snapshot pending",
      description: `Disclosure snapshot failed: ${status}`,
    });
  });

  it("classifies an indeterminate audit result as disclosure unknown", () => {
    const message = "Disclosure snapshot status unknown after local audit persistence failed";

    expect(classifyDisclosureSnapshotFailure(new Error(message))).toEqual({
      status: "disclosure_unknown",
      title: "Disclosure status unknown",
      description: message,
    });
  });

  it("fails caps without projecting or trimming selected records", () => {
    expect(previewBudget(Array.from({length:101},(_,i)=>i),1,{}).code).toBe("rows_limit");
    expect(previewBudget([0],26,{0:20}).code).toBe("fields_limit");
    expect(previewBudget([0],25,{0:250001}).code).toBe("canonical_bytes_limit");
    expect(previewBudget([0],25,{0:250000}).code).toBeNull();
  });

  it("requires final confirmation before building acked payload", () => {
    expect(() =>
      buildDisclosureSnapshotPayload({
        approvedFields: buildApprovedMetadataDraft(form, metadata, dataset),
        sampleDecision: "none",
        approvedSample: null,
        confirmed: false,
        sourcePublishOperationId: "op-1",
      })
    ).toThrow("Final disclosure confirmation is required.");
  });
});
