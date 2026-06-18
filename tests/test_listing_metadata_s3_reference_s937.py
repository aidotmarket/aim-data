from types import SimpleNamespace

import pytest

from app.services import allie_provider
from app.services import listing_metadata_service as metadata_module
from app.services.listing_metadata_service import MAX_README_BYTES, ListingMetadataService


class FakeProvider:
    def __init__(self, chunks=None, error=False):
        self.chunks = chunks or []
        self.error = error
        self.calls = []

    async def stream(self, prompt, context=None):
        self.calls.append({"prompt": prompt, "context": context})
        if self.error:
            raise RuntimeError("provider unavailable")
        for text in self.chunks:
            yield SimpleNamespace(text=text)


def _provider_json():
    return (
        '{"title":"Sales Orders","description":"Useful buyer description.",'
        '"category":"commerce","tags":["sales","orders","retail"]}'
    )


@pytest.mark.asyncio
async def test_tabular_author_listing_metadata_regression_parses_provider_json(monkeypatch, tmp_path):
    provider = FakeProvider([_provider_json()])
    monkeypatch.setattr(allie_provider, "get_allie_provider", lambda: provider)
    filepath = tmp_path / "orders.csv"
    filepath.write_text("id,total\n1,10\n")

    result = await ListingMetadataService()._author_listing_metadata(
        dataset_id="dataset-1",
        filepath=filepath,
        metadata={
            "file_type": "csv",
            "row_count": 1,
            "column_count": 2,
            "column_profiles": [],
            "sample_rows": [{"id": 1, "total": 10}],
        },
        categories=["commerce"],
        tags=["orders"],
        fallback_title="Orders (1 rows)",
        fallback_description="Fallback description.",
    )

    assert result == {
        "title": "Sales Orders",
        "description": "Useful buyer description.",
        "category": "commerce",
        "tags": ["sales", "orders", "retail"],
    }
    assert provider.calls[0]["context"] == (
        "You write honest buyer-facing marketplace listing metadata "
        "for datasets. Return only valid JSON."
    )
    assert "DuckDB" not in provider.calls[0]["prompt"]


@pytest.mark.asyncio
async def test_author_s3_reference_metadata_returns_parsed_fields(monkeypatch):
    provider = FakeProvider(["prefix ", _provider_json(), " suffix"])
    monkeypatch.setattr(allie_provider, "get_allie_provider", lambda: provider)

    result = await ListingMetadataService().author_s3_reference_metadata(
        sampled_stats={
            "object_count": 42,
            "total_size_bytes": 2048,
            "type_histogram": {"text/csv": 40, "application/json": 2},
            "approximate": False,
            "sample_coverage": "full",
            "sampled_object_count": 42,
        },
        readme_text="Seller README",
        bucket="seller-bucket",
        prefix="exports/",
        fallback_title="Fallback title",
        fallback_description="Fallback description",
    )

    assert result["title"] == "Sales Orders"
    assert result["description"] == "Useful buyer description."
    assert result["category"] == "commerce"
    assert result["tags"] == ["sales", "orders", "retail"]
    assert provider.calls[0]["context"] == (
        "You write honest buyer-facing marketplace listing metadata "
        "for cloud datasets. Return only valid JSON."
    )
    assert "S3 prefix reference" in provider.calls[0]["prompt"]
    assert "DuckDB" not in provider.calls[0]["prompt"]


@pytest.mark.asyncio
@pytest.mark.parametrize("chunks,error", [([], False), (["not json"], False), ([], True)])
async def test_author_s3_reference_metadata_returns_empty_on_bad_provider(monkeypatch, chunks, error):
    provider = FakeProvider(chunks, error=error)
    monkeypatch.setattr(allie_provider, "get_allie_provider", lambda: provider)

    result = await ListingMetadataService().author_s3_reference_metadata(
        sampled_stats={"object_count": 0, "total_size_bytes": 0, "type_histogram": {}},
        readme_text=None,
        bucket="seller-bucket",
        prefix=None,
        fallback_title="Fallback title",
        fallback_description="Fallback description",
    )

    assert result == {}


class FakeBroker:
    def __init__(self, objects):
        self.objects = objects
        self.presigned_keys = []

    def list_objects(self, **kwargs):
        self.list_kwargs = kwargs
        return {"status": "listed", "objects": self.objects}

    def presign_object(self, **kwargs):
        self.presigned_keys.append(kwargs["object_key"])
        return {"url": "https://presigned.example/readme"}


class FakeStreamResponse:
    def __init__(self, chunks):
        self.chunks = chunks
        self.status_code = 200
        self.yielded_bytes = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return None

    def raise_for_status(self):
        return None

    async def aiter_bytes(self):
        for chunk in self.chunks:
            self.yielded_bytes += len(chunk)
            yield chunk


class FakeAsyncClient:
    calls = []
    response = None

    def __init__(self, *args, **kwargs):
        self.args = args
        self.kwargs = kwargs

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return None

    def stream(self, method, url, headers=None):
        self.calls.append({"method": method, "url": url, "headers": headers})
        return self.response


@pytest.mark.asyncio
async def test_read_s3_readme_finds_prefix_root_case_variant(monkeypatch):
    FakeAsyncClient.calls = []
    FakeAsyncClient.response = FakeStreamResponse([b"# Dataset\n", b"Details"])
    monkeypatch.setattr(metadata_module.httpx, "AsyncClient", FakeAsyncClient)
    broker = FakeBroker(
        [
            {"key": "exports/nested/README.md", "size": 100},
            {"key": "exports/readme.md", "size": 20},
        ]
    )

    text = await ListingMetadataService().read_s3_readme(
        broker=broker,
        role_arn="arn:aws:iam::123:role/seller",
        region="us-east-1",
        bucket="seller-bucket",
        prefix="exports/",
    )

    assert text == "# Dataset\nDetails"
    assert broker.presigned_keys == ["exports/readme.md"]
    assert FakeAsyncClient.calls == [
        {
            "method": "GET",
            "url": "https://presigned.example/readme",
            "headers": {"Range": f"bytes=0-{MAX_README_BYTES - 1}"},
        }
    ]


@pytest.mark.asyncio
async def test_read_s3_readme_returns_none_when_absent(monkeypatch):
    FakeAsyncClient.calls = []
    FakeAsyncClient.response = FakeStreamResponse([b"unused"])
    monkeypatch.setattr(metadata_module.httpx, "AsyncClient", FakeAsyncClient)
    broker = FakeBroker([{"key": "exports/nested/README.md", "size": 100}])

    text = await ListingMetadataService().read_s3_readme(
        broker=broker,
        role_arn="arn:aws:iam::123:role/seller",
        region="us-east-1",
        bucket="seller-bucket",
        prefix="exports/",
    )

    assert text is None
    assert broker.presigned_keys == []
    assert FakeAsyncClient.calls == []


@pytest.mark.asyncio
async def test_read_s3_readme_respects_byte_cap(monkeypatch):
    FakeAsyncClient.calls = []
    chunks = [b"a" * (MAX_README_BYTES - 3), b"bcdef"]
    FakeAsyncClient.response = FakeStreamResponse(chunks)
    monkeypatch.setattr(metadata_module.httpx, "AsyncClient", FakeAsyncClient)
    broker = FakeBroker([{"key": "README.txt", "size": MAX_README_BYTES + 100}])

    text = await ListingMetadataService().read_s3_readme(
        broker=broker,
        role_arn="arn:aws:iam::123:role/seller",
        region="us-east-1",
        bucket="seller-bucket",
        prefix=None,
    )

    assert len(text.encode("utf-8")) == MAX_README_BYTES
    assert text.endswith("bcd")
    assert FakeAsyncClient.response.yielded_bytes == MAX_README_BYTES + 2
    assert FakeAsyncClient.calls[0]["headers"] == {"Range": f"bytes=0-{MAX_README_BYTES - 1}"}
