import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { CommitmentPreviewBuilder } from "./CommitmentPreviewBuilder";
import { PreviewOriginReview } from "./PreviewOriginReview";

describe("CommitmentPreviewBuilder", () => {
  it("shows the non-custodial boundary and enforces both sample caps", () => {
    const onBuild = vi.fn();
    render(
      <CommitmentPreviewBuilder
        datasetVersion="v2"
        leafCount={100}
        selectedRowCount={51}
        canonicalRowBytes={5_121}
        onBuild={onBuild}
      />
    );
    expect(screen.getByText(/ai.market receives only metadata, digests, proofs/i)).toBeTruthy();
    expect(screen.getByRole("alert").textContent).toContain("preview_cap_exceeded");
    expect(screen.getByRole("button", { name: /build full commitment/i })).toBeDisabled();
  });

  it("builds only when a dataset version and bounded selection exist", () => {
    const onBuild = vi.fn();
    render(
      <CommitmentPreviewBuilder
        datasetVersion="v2"
        leafCount={null}
        selectedRowCount={2}
        canonicalRowBytes={200}
        onBuild={onBuild}
      />
    );
    fireEvent.click(screen.getByRole("button", { name: /build full commitment/i }));
    expect(onBuild).toHaveBeenCalledOnce();
  });

  it("waits for rights, origin, and listing prerequisites", () => {
    render(
      <CommitmentPreviewBuilder
        datasetVersion="v2"
        leafCount={null}
        selectedRowCount={1}
        canonicalRowBytes={100}
        readyToBuild={false}
        onBuild={vi.fn()}
      />
    );
    expect(screen.getByRole("button", { name: /build full commitment/i })).toBeDisabled();
  });
});

describe("PreviewOriginReview", () => {
  it("requires HTTPS before customer-side validation", () => {
    const onValidate = vi.fn();
    const { rerender } = render(
      <PreviewOriginReview url="http://local.test/preview" onUrlChange={vi.fn()} onValidate={onValidate} />
    );
    expect(screen.getByRole("button", { name: /validate origin/i })).toBeDisabled();
    rerender(
      <PreviewOriginReview url="https://seller.example/preview" onUrlChange={vi.fn()} onValidate={onValidate} />
    );
    fireEvent.click(screen.getByRole("button", { name: /validate origin/i }));
    expect(onValidate).toHaveBeenCalledOnce();
  });
});
