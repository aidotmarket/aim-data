from pathlib import Path


DATASET_DETAIL = Path("frontend/src/pages/DatasetDetail.tsx")
DISCLOSURE_HELPER = Path("frontend/src/lib/disclosure.ts")


def test_dataset_detail_keeps_three_step_disclosure_flow():
    source = DATASET_DETAIL.read_text()

    assert "3. Listing Details and Disclosure" in source
    assert "Step 4" not in source
    assert "No sample rows" in source
    assert "CommitmentPreviewBuilder" in source
    assert "Publish these real sample rows" not in source
    assert "Synthetic" not in source
    assert "Listing published, disclosure snapshot pending" in source
    assert "Retry disclosure snapshot" in source
    assert "Review disclosure decision" in source


def test_dataset_detail_uses_unredacted_preview_and_retry_without_republish():
    source = DATASET_DETAIL.read_text()

    assert "datasetsApi.getDisclosureSample(dataset.id, 100)" not in source
    assert "metadataApproved={metadataApproved}" in source
    assert "submitDisclosureSnapshot(publishedListingId, retrySnapshotPayload)" in source
    retry_block = source[source.index("const handleRetryDisclosureSnapshot"):source.index("const handleReviewDisclosureDecision")]
    assert "marketplaceApi.publish" not in retry_block


def test_confirmation_copy_contract_is_exported_verbatim():
    source = DISCLOSURE_HELPER.read_text()

    assert "export const AIM_CHANNEL_DISCLOSURE_CONFIRMATION_COPY" in source
    assert "become public on ai.market" in source
    assert "search engines, AI assistants, HuggingFace" in source
    assert "Preview rows require a separate local review" in source
    assert "AI-training crawlers" in source
