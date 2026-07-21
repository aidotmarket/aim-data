import { AlertTriangle, CheckCircle2, ShieldCheck } from "lucide-react";
import { Button } from "@/components/ui/button";

export const COMMITMENT_PREVIEW_MAX_ROWS = 50;
export const COMMITMENT_PREVIEW_MAX_CANONICAL_BYTES = 5_120;

export interface CommitmentPreviewBuilderProps {
  datasetVersion: string;
  leafCount: number | null;
  selectedRowCount: number;
  canonicalRowBytes: number;
  merkleRoot?: string | null;
  validationErrorCode?: string | null;
  building?: boolean;
  readyToBuild?: boolean;
  onBuild: () => void;
}

export function CommitmentPreviewBuilder({
  datasetVersion,
  leafCount,
  selectedRowCount,
  canonicalRowBytes,
  merkleRoot,
  validationErrorCode,
  building = false,
  readyToBuild = true,
  onBuild,
}: CommitmentPreviewBuilderProps) {
  const withinCaps = selectedRowCount <= COMMITMENT_PREVIEW_MAX_ROWS
    && canonicalRowBytes <= COMMITMENT_PREVIEW_MAX_CANONICAL_BYTES;
  const canBuild = Boolean(datasetVersion.trim()) && withinCaps && readyToBuild && !building;

  return (
    <section className="space-y-3 rounded-md border p-3" aria-labelledby="commitment-preview-title">
      <div>
        <h3 id="commitment-preview-title" className="flex items-center gap-2 text-sm font-medium">
          <ShieldCheck className="h-4 w-4" /> Full-dataset commitment
        </h3>
        <p className="text-xs text-muted-foreground">
          AIM Data reads the full dataset locally. ai.market receives only metadata, digests, proofs,
          scan evidence, attestations, and the seller-controlled origin reference.
        </p>
      </div>

      <dl className="grid gap-2 text-sm sm:grid-cols-2">
        <div><dt className="text-muted-foreground">Dataset version</dt><dd>{datasetVersion || "Required"}</dd></div>
        <div><dt className="text-muted-foreground">Committed leaves</dt><dd>{leafCount ?? "Not built"}</dd></div>
        <div><dt className="text-muted-foreground">Seller-selected rows</dt><dd>{selectedRowCount} / 50</dd></div>
        <div><dt className="text-muted-foreground">Canonical row bytes</dt><dd>{canonicalRowBytes.toLocaleString()} / 5,120</dd></div>
      </dl>

      {!withinCaps ? (
        <p role="alert" className="flex items-center gap-2 text-sm text-destructive">
          <AlertTriangle className="h-4 w-4" /> preview_cap_exceeded
        </p>
      ) : null}
      {validationErrorCode ? (
        <p role="alert" className="flex items-center gap-2 text-sm text-destructive">
          <AlertTriangle className="h-4 w-4" /> {validationErrorCode}
        </p>
      ) : null}
      {merkleRoot ? (
        <p className="flex items-center gap-2 text-sm text-emerald-700">
          <CheckCircle2 className="h-4 w-4" /> Commitment built locally
        </p>
      ) : null}

      <Button type="button" size="sm" onClick={onBuild} disabled={!canBuild}>
        {building ? "Building full commitment…" : "Build full commitment"}
      </Button>
    </section>
  );
}
