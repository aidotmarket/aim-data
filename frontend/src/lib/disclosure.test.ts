import { describe, expect, it } from "vitest";
import {
  AIM_CHANNEL_DISCLOSURE_CONFIRMATION_COPY,
  buildApprovedMetadataDraft,
  buildDisclosureSnapshotPayload,
  prepareDisclosureSample,
} from "./disclosure";
import type { ApiDataset, DatasetListingMetadata } from "./api";
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

describe("disclosure payload builder", () => {
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

  it("maps sample decision none to approved_sample null", () => {
    const payload = buildDisclosureSnapshotPayload({
      approvedFields: buildApprovedMetadataDraft(form, metadata, dataset),
      sampleDecision: "none",
      approvedSample: null,
      confirmed: true,
      sourcePublishOperationId: "op-1",
    });

    expect(payload.sample_decision).toBe("none");
    expect(payload.approved_sample).toBeNull();
    expect(payload.approval_source).toBe("aim_channel");
    expect(payload.ai_training_notification_ack).toBe(true);
    expect(payload.ai_training_notification_text).toBe(AIM_CHANNEL_DISCLOSURE_CONFIRMATION_COPY);
  });

  it("approved_rows includes only displayed columns and rows with deterministic refs", () => {
    const prepared = prepareDisclosureSample([
      { a: 1, b: "x", hidden: "not-hidden-until-column-truncation" },
      { a: 2, b: "y", hidden: "not-hidden-until-column-truncation" },
    ]);

    expect(prepared.sample).toEqual({
      columns: ["a", "b", "hidden"],
      row_refs: ["preview:0", "preview:1"],
      rows: [
        { a: 1, b: "x", hidden: "not-hidden-until-column-truncation" },
        { a: 2, b: "y", hidden: "not-hidden-until-column-truncation" },
      ],
    });
  });

  it("truncates over 100 rows and over 25 columns before submit", () => {
    const wideRow = Object.fromEntries(Array.from({ length: 30 }, (_, index) => [`c${index}`, index]));
    const rows = Array.from({ length: 110 }, () => wideRow);
    const prepared = prepareDisclosureSample(rows);

    expect(prepared.sample?.rows).toHaveLength(100);
    expect(prepared.sample?.columns).toHaveLength(25);
    expect(prepared.truncatedRows).toBe(true);
    expect(prepared.truncatedColumns).toBe(true);
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
