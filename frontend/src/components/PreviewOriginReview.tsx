import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

export interface PreviewOriginReviewProps {
  url: string;
  onUrlChange: (url: string) => void;
  onValidate: () => void;
  validating?: boolean;
  validationState?: "idle" | "passed" | "failed";
  errorCode?: string | null;
}

export function PreviewOriginReview({
  url,
  onUrlChange,
  onValidate,
  validating = false,
  validationState = "idle",
  errorCode,
}: PreviewOriginReviewProps) {
  const isHttps = url.startsWith("https://");
  return (
    <section className="space-y-3 rounded-md border p-3" aria-labelledby="preview-origin-title">
      <div>
        <h3 id="preview-origin-title" className="text-sm font-medium">Seller-controlled preview origin</h3>
        <p className="text-xs text-muted-foreground">
          Validation runs from this customer installation. Upload and hosting remain under seller control.
        </p>
      </div>
      <div className="space-y-2">
        <Label htmlFor="preview-origin-url">Public HTTPS package URL</Label>
        <Input
          id="preview-origin-url"
          type="url"
          value={url}
          onChange={(event) => onUrlChange(event.target.value)}
          placeholder="https://data.example.com/preview.json"
          aria-invalid={Boolean(url) && !isHttps}
        />
      </div>
      <p className="text-xs text-muted-foreground">
        Required: application/vnd.aim.preview+json, aim-preview-package-v1, public host, CORS, no redirects,
        and a 128 KiB response ceiling.
      </p>
      {validationState === "passed" ? <p className="text-sm text-emerald-700">Origin contract passed.</p> : null}
      {validationState === "failed" || errorCode ? <p role="alert" className="text-sm text-destructive">{errorCode || "preview_origin_invalid"}</p> : null}
      <Button type="button" size="sm" variant="outline" onClick={onValidate} disabled={!isHttps || validating}>
        {validating ? "Validating origin…" : "Validate origin"}
      </Button>
    </section>
  );
}

