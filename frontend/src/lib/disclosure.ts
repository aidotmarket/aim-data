import type {
  ApiDataset,
  DatasetListingMetadata,
  DisclosureApprovedFields,
} from "@/lib/api";
import type { ListingEditorValue } from "@/components/ListingEditorForm";

export const AIM_CHANNEL_DISCLOSURE_CONFIRMATION_COPY =
  "When I publish, my approved title, description, tags, category, schema, and sample-row choice become public on ai.market. They may be shared with search engines, AI assistants, HuggingFace, and other AI discovery systems. Preview rows require a separate local review and publication decision. I understand this public listing may be used by AI-training crawlers.";

export const AIM_CHANNEL_DISCLOSURE_LICENSE = "standard_marketplace";
export const AIM_CHANNEL_DISCLOSURE_APPROVAL_SOURCE = "aim_channel";

export type DisclosureSampleDecision = "none" | "approved_rows";

export type ApprovedMetadataDraft = Omit<DisclosureApprovedFields, "schema"> & {
  schema: DisclosureApprovedFields["schema"]["columns"];
};

export interface ApprovedSample {
  columns: string[];
  row_refs: string[];
  rows: Record<string, unknown>[];
}

export interface DisclosureSnapshotPayload {
  approved_fields: DisclosureApprovedFields;
  sample_decision: DisclosureSampleDecision;
  approved_sample: ApprovedSample | null;
  ai_training_notification_ack: boolean;
  ai_training_notification_text: string;
  license: string;
  approval_source: typeof AIM_CHANNEL_DISCLOSURE_APPROVAL_SOURCE;
  source_publish_operation_id: string;
}

export type DisclosureSnapshotFailureStatus =
  | "snapshot_pending"
  | "snapshot_rejected"
  | "disclosure_unknown";

export interface DisclosureSnapshotFailure {
  status: DisclosureSnapshotFailureStatus;
  title: string;
  description: string;
}

export function classifyDisclosureSnapshotFailure(error: unknown): DisclosureSnapshotFailure {
  const message = error instanceof Error ? error.message : "";
  const status = typeof error === "object" && error !== null && "status" in error
    ? (error as { status?: unknown }).status
    : undefined;

  if (status === 422) {
    return {
      status: "snapshot_rejected",
      title: "Disclosure snapshot rejected",
      description: message || "ai.market rejected the submitted disclosure data. Review the disclosure decision before trying again.",
    };
  }
  if (message.toLowerCase().includes("status unknown")) {
    return {
      status: "disclosure_unknown",
      title: "Disclosure status unknown",
      description: message,
    };
  }
  return {
    status: "snapshot_pending",
    title: "Listing published, disclosure snapshot pending",
    description: message || "The listing exists on ai.market, but the public discovery package is not complete.",
  };
}

export function buildApprovedMetadataDraft(
  form: ListingEditorValue,
  metadata: DatasetListingMetadata | null,
  dataset: ApiDataset
): ApprovedMetadataDraft {
  const schema = metadata?.column_summary?.length
    ? metadata.column_summary.map((column) => ({
        name: column.name,
        type: column.type,
        null_percentage: column.null_percentage,
        uniqueness_ratio: column.uniqueness_ratio,
      }))
    : (dataset.metadata?.columns || []).map((column) => ({
        name: column.name,
        type: column.type,
      }));

  return {
    title: form.title.trim(),
    description: form.description.trim(),
    category: form.category,
    tags: [...form.tags],
    schema,
    data_format: metadata?.file_format || dataset.file_type || null,
    source_row_count: metadata?.row_count ?? dataset.metadata?.row_count ?? null,
    source_column_count: metadata?.column_count ?? dataset.metadata?.column_count ?? schema.length ?? null,
    compliance_summary: {
      privacy_score: metadata?.privacy_score ?? null,
      freshness_score: metadata?.freshness_score ?? null,
      data_categories: metadata?.data_categories ?? [],
    },
    source_delivery_public_metadata: {
      file_format: metadata?.file_format || dataset.file_type || null,
      size_bytes: metadata?.size_bytes || dataset.metadata?.size_bytes || null,
    },
  };
}

export function buildDisclosureSnapshotPayload({
  approvedFields,
  sampleDecision,
  approvedSample,
  confirmed,
  sourcePublishOperationId,
}: {
  approvedFields: ApprovedMetadataDraft;
  sampleDecision: DisclosureSampleDecision;
  approvedSample: ApprovedSample | null;
  confirmed: boolean;
  sourcePublishOperationId: string;
}): DisclosureSnapshotPayload {
  if (!confirmed) {
    throw new Error("Final disclosure confirmation is required.");
  }
  if (sampleDecision === "none" && approvedSample !== null) {
    throw new Error("No sample rows must submit approved_sample=null.");
  }
  if (sampleDecision === "approved_rows") {
    throw new Error("legacy_sample_unavailable");
  }

  return {
    approved_fields: {
      ...approvedFields,
      schema: { columns: [...approvedFields.schema] },
    },
    sample_decision: "none",
    approved_sample: null,
    ai_training_notification_ack: true,
    ai_training_notification_text: AIM_CHANNEL_DISCLOSURE_CONFIRMATION_COPY,
    license: AIM_CHANNEL_DISCLOSURE_LICENSE,
    approval_source: AIM_CHANNEL_DISCLOSURE_APPROVAL_SOURCE,
    source_publish_operation_id: sourcePublishOperationId,
  };
}

export const PREVIEW_MEMBERSHIP_DISCLAIMER = 'Seller-selected preview. Membership does not establish quality, representativeness, legality, compliance, identity or external completeness.';
export const PREVIEW_ALL_FIELDS_WARNING = 'All fields of every selected record will be disclosed in the package, including fields hidden from display. Display columns do not redact records.';
export const PREVIEW_PERMISSION = 'I permit these exact complete selected records to be published as a public preview.';
export const PREVIEW_CAPS = { rows: 100, fields: 25, canonical_bytes: 250000 } as const;

/** React must render this return value as a text child, never HTML or Markdown. */
export function inertPreviewText(value: unknown): string {
  if (value === undefined) return '(missing)';
  if (value === null) return 'null';
  const neutralize = (text: string) => text.replace(/[\p{Cc}\p{Cf}\p{Cs}]/gu, '\uFFFD');
  const inertValue = (candidate: unknown): unknown => {
    if (typeof candidate === 'string') return neutralize(candidate);
    if (Array.isArray(candidate)) return candidate.map(inertValue);
    if (candidate !== null && typeof candidate === 'object') {
      return Object.fromEntries(
        Object.entries(candidate).map(([key, child]) => [neutralize(key), inertValue(child)]),
      );
    }
    return candidate;
  };
  return typeof value === 'string' ? neutralize(value) : JSON.stringify(inertValue(value));
}

export function previewBudget(indices: number[], fieldCount: number, sizes: Record<number, number>) {
  const bytes = indices.reduce((sum, index) => sum + (sizes[index] ?? 0), 0);
  const known = indices.every(index => sizes[index] !== undefined);
  const code = indices.length > PREVIEW_CAPS.rows ? 'rows_limit'
    : fieldCount > PREVIEW_CAPS.fields ? 'fields_limit'
    : bytes > PREVIEW_CAPS.canonical_bytes ? 'canonical_bytes_limit' : null;
  return { rows: indices.length, fields: fieldCount, canonical_bytes: bytes, known, code };
}
