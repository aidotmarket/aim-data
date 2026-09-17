import { cleanup, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import Datasets from "./Datasets";

vi.mock("@/contexts/BrandContext", () => ({ useBrand: () => ({ name: "AIM Data" }) }));
vi.mock("@/contexts/UploadContext", () => ({ useUpload: () => ({ openModal: vi.fn(), setOnSuccess: vi.fn(), queue: [] }) }));
vi.mock("@/contexts/MarketplaceContext", () => ({ useMarketplace: () => ({ isPublished: () => false, getPublishedData: () => null }) }));
vi.mock("@/contexts/ModeContext", () => ({ useMode: () => ({ hasFeature: () => false }) }));
vi.mock("@/hooks/use-toast", () => ({ useToast: () => ({ toast: vi.fn() }) }));
vi.mock("@/hooks/useApi", () => ({ useDatasets: () => ({ loading: false, error: null, refetch: vi.fn(), data: {
  count: 1, datasets: [{ id: "directory-1", original_filename: "Acid Trap Dataset", file_type: "directory", status: "uploaded",
    created_at: "2026-09-17T00:00:00Z", updated_at: "2026-09-17T00:00:00Z",
    metadata: { directory: { member_count: 21000, total_data_bytes: 84000 } } }],
} }) }));

afterEach(cleanup);
describe("directory cards", () => {
  it("renders one card with the directory name and member count", () => {
    render(<MemoryRouter><Datasets /></MemoryRouter>);
    expect(screen.getAllByText("Acid Trap Dataset")).toHaveLength(1);
    expect(screen.getByText(/21,000 files/)).toBeInTheDocument();
    expect(screen.getByText("Registered")).toBeInTheDocument();
    expect(screen.queryByText("Processing")).not.toBeInTheDocument();
  });
});
