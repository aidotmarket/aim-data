import { useState, useEffect } from "react";
import { useParams, useNavigate, Link } from "react-router-dom";
import {
  ArrowLeft,
  FileText,
  FileImage,
  FileAudio,
  File,
  Download,
  Trash2,
  Save,
  Loader2,
  CheckCircle2,
  XCircle,
  Store,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { rawFilesApi, type RawFile } from "@/lib/api";
import { toast } from "@/hooks/use-toast";
import { useMode } from "@/contexts/ModeContext";
import { useCoPilot } from "@/contexts/CoPilotContext";
import {
  filenameToTitle,
  ListingEditorForm,
  type ListingEditorValue,
} from "@/components/ListingEditorForm";

function getFileIcon(mimeType: string | null) {
  if (!mimeType) return File;
  if (mimeType.startsWith("image/")) return FileImage;
  if (mimeType.startsWith("audio/")) return FileAudio;
  if (mimeType === "application/pdf") return FileText;
  return File;
}

function formatBytes(bytes: number): string {
  if (bytes === 0) return "0 B";
  const k = 1024;
  const sizes = ["B", "KB", "MB", "GB"];
  const i = Math.floor(Math.log(bytes) / Math.log(k));
  return `${parseFloat((bytes / Math.pow(k, i)).toFixed(1))} ${sizes[i]}`;
}

function getListingStatusBadge(status: string | null) {
  if (!status) return <Badge variant="outline">No Listing</Badge>;
  if (status === "listed") return <Badge className="bg-green-500/20 text-green-400 border-green-500/30">Listed</Badge>;
  if (status === "draft") return <Badge className="bg-yellow-500/20 text-yellow-400 border-yellow-500/30">Draft</Badge>;
  return <Badge variant="outline">{status}</Badge>;
}

function ListingReadiness({ file, isConnected }: { file: RawFile; isConnected: boolean }) {
  const meta = file.metadata as Record<string, unknown> | null;
  const hasTitle = !!(meta?.title);
  const hasDescription = !!(meta?.description);
  const hasTags = Array.isArray(meta?.tags) ? (meta.tags as string[]).length > 0 : false;
  const metadataComplete = hasTitle && hasDescription && hasTags;
  const hasPriceOrFree = file.price_cents != null && file.price_cents > 0;

  const checks = [
    { label: "File registered", passed: true },
    { label: "Metadata complete (title, description, tags)", passed: metadataComplete },
    { label: "Price set (if paid listing)", passed: hasPriceOrFree },
    { label: "Connected to ai.market (trust channel active)", passed: isConnected },
  ];

  return (
    <Card>
      <CardHeader className="pb-3">
        <CardTitle className="text-base">Listing Readiness</CardTitle>
        <CardDescription>Complete these steps to publish your file</CardDescription>
      </CardHeader>
      <CardContent>
        <ul className="space-y-2">
          {checks.map((check) => (
            <li key={check.label} className="flex items-center gap-2 text-sm">
              {check.passed ? (
                <CheckCircle2 className="w-4 h-4 text-green-500 shrink-0" />
              ) : (
                <XCircle className="w-4 h-4 text-muted-foreground shrink-0" />
              )}
              <span className={check.passed ? "text-foreground" : "text-muted-foreground"}>
                {check.label}
              </span>
            </li>
          ))}
        </ul>
      </CardContent>
    </Card>
  );
}

function FilePreview({ file }: { file: RawFile }) {
  const mime = file.mime_type || "";
  const [objectUrl, setObjectUrl] = useState<string | null>(null);
  const [loadError, setLoadError] = useState(false);

  useEffect(() => {
    const needsPreview = mime.startsWith("image/") || mime.startsWith("audio/") || mime === "application/pdf";
    if (!needsPreview) return;

    let cancelled = false;
    let url: string | null = null;
    rawFilesApi.getFileObjectUrl(file.id).then((u) => {
      if (cancelled) { URL.revokeObjectURL(u); return; }
      url = u;
      setObjectUrl(u);
    }).catch(() => {
      if (!cancelled) setLoadError(true);
    });

    return () => {
      cancelled = true;
      if (url) URL.revokeObjectURL(url);
    };
  }, [file.id, mime]);

  if (mime.startsWith("image/")) {
    return (
      <div className="rounded-lg border bg-muted/50 p-4 flex items-center justify-center min-h-[200px]">
        {objectUrl && !loadError ? (
          <img
            src={objectUrl}
            alt={file.filename}
            className="max-h-[400px] max-w-full object-contain rounded"
            onError={() => setLoadError(true)}
          />
        ) : loadError ? (
          <>
            <FileImage className="w-16 h-16 text-muted-foreground" />
            <span className="ml-3 text-sm text-muted-foreground">Failed to load preview</span>
          </>
        ) : (
          <Loader2 className="w-8 h-8 animate-spin text-muted-foreground" />
        )}
      </div>
    );
  }

  if (mime.startsWith("audio/")) {
    return (
      <div className="rounded-lg border bg-muted/50 p-4">
        <div className="flex items-center gap-3 mb-3">
          <FileAudio className="w-8 h-8 text-muted-foreground" />
          <span className="text-sm font-medium">{file.filename}</span>
        </div>
        {objectUrl && !loadError ? (
          <audio controls className="w-full" src={objectUrl} />
        ) : loadError ? (
          <span className="text-xs text-muted-foreground">Failed to load audio</span>
        ) : (
          <div className="h-10 flex items-center justify-center">
            <Loader2 className="w-6 h-6 animate-spin text-muted-foreground" />
          </div>
        )}
      </div>
    );
  }

  if (mime === "application/pdf") {
    return (
      <div className="rounded-lg border bg-muted/50 p-4 min-h-[400px]">
        {objectUrl && !loadError ? (
          <object
            data={objectUrl}
            type="application/pdf"
            className="w-full h-[500px] rounded"
          >
            <p className="text-sm text-muted-foreground text-center py-8">
              PDF preview not supported in this browser.{" "}
              <button className="underline" onClick={() => rawFilesApi.downloadRawFile(file)}>Download</button> instead.
            </p>
          </object>
        ) : loadError ? (
          <div className="flex items-center justify-center min-h-[200px]">
            <FileText className="w-16 h-16 text-muted-foreground" />
            <span className="ml-3 text-sm text-muted-foreground">Failed to load PDF preview</span>
          </div>
        ) : (
          <div className="flex items-center justify-center min-h-[200px]">
            <Loader2 className="w-8 h-8 animate-spin text-muted-foreground" />
          </div>
        )}
      </div>
    );
  }

  const IconComponent = getFileIcon(file.mime_type);
  return (
    <div className="rounded-lg border bg-muted/50 p-4 flex items-center justify-center min-h-[200px]">
      <IconComponent className="w-16 h-16 text-muted-foreground" />
      <span className="ml-3 text-sm text-muted-foreground">{file.filename}</span>
    </div>
  );
}

export default function RawFileDetail() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const { isConnected } = useMode();
  const { listingDraftUpdates } = useCoPilot();
  const [file, setFile] = useState<RawFile | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [publishing, setPublishing] = useState(false);
  const [publishError, setPublishError] = useState<string | null>(null);
  const [deleting, setDeleting] = useState(false);
  const [tagInput, setTagInput] = useState("");
  const [form, setForm] = useState<ListingEditorValue>({
    title: "",
    description: "",
    priceUsd: "25",
    category: "other",
    tags: [],
  });

  useEffect(() => {
    if (!id) return;
    setLoading(true);
    rawFilesApi.getRawFile(id)
      .then((data) => {
        setFile(data);
        const meta = data.metadata as Record<string, unknown> | null;
        setForm({
          title: (meta?.title as string) || filenameToTitle(data.filename) || data.filename,
          description: (meta?.description as string) || "",
          priceUsd: data.price_cents ? String(data.price_cents / 100) : "25",
          category: (meta?.category as string) || "other",
          tags: Array.isArray(meta?.tags) ? (meta.tags as string[]) : [],
        });
      })
      .catch(() => {
        toast({ title: "Error", description: "Failed to load file details", variant: "destructive" });
      })
      .finally(() => setLoading(false));
  }, [id]);

  useEffect(() => {
    if (!file) return;
    const updatedListing = listingDraftUpdates[file.id];
    if (!updatedListing) return;

    setForm((current) => ({
      ...current,
      title: updatedListing.title ?? current.title,
      description: updatedListing.description ?? current.description,
      category: (updatedListing.auto_metadata?.category as string) || current.category,
      tags: Array.isArray(updatedListing.tags) ? updatedListing.tags : current.tags,
    }));
    setFile((current) => current ? {
      ...current,
      listing_status: updatedListing.status,
      price_cents: updatedListing.price_cents,
    } : current);
  }, [file?.id, listingDraftUpdates]);

  const handleSave = async () => {
    if (!id || !file) return;
    setSaving(true);
    try {
      const metadata = {
        ...(file.metadata || {}),
        title: form.title,
        description: form.description,
        category: form.category,
        tags: form.tags,
      };
      const updated = await rawFilesApi.updateRawFile(id, metadata);
      setFile(updated);
      toast({ title: "Saved", description: "Metadata updated successfully" });
    } catch {
      toast({ title: "Error", description: "Failed to save metadata", variant: "destructive" });
    } finally {
      setSaving(false);
    }
  };

  const handlePublish = async () => {
    if (!file) return;
    setPublishError(null);
    const price = Number.parseFloat(form.priceUsd);
    if (!form.title.trim()) {
      toast({ title: "Title required", description: "Add a title for this listing.", variant: "destructive" });
      return;
    }
    if (!form.description.trim()) {
      toast({ title: "Description required", description: "Add listing details for buyers.", variant: "destructive" });
      return;
    }
    if (!Number.isFinite(price) || price < 25) {
      toast({ title: "Price required", description: "Set a price of at least $25.", variant: "destructive" });
      return;
    }

    setPublishing(true);
    try {
      const listing = await rawFilesApi.createRawListing({
        raw_file_id: file.id,
        title: form.title.trim(),
        description: form.description.trim(),
        category: form.category,
        tags: form.tags,
        price_cents: Math.round(price * 100),
      });
      await rawFilesApi.publishRawListing(listing.id);
      setFile({ ...file, listing_status: "listed", price_cents: Math.round(price * 100) });
      toast({ title: "Live on ai.market", description: "Your listing has been published." });
    } catch (e) {
      const message = e instanceof Error ? e.message : "Failed to publish listing.";
      setPublishError(message);
      toast({
        title: "Publish failed",
        description: message,
        variant: "destructive",
      });
    } finally {
      setPublishing(false);
    }
  };

  const handleDelete = async () => {
    if (!id || !confirm("Are you sure you want to delete this file?")) return;
    setDeleting(true);
    try {
      await rawFilesApi.deleteRawFile(id);
      toast({ title: "Deleted", description: "File has been deleted" });
      navigate("/datasets");
    } catch {
      toast({ title: "Error", description: "Failed to delete file", variant: "destructive" });
      setDeleting(false);
    }
  };

  if (loading) {
    return (
      <div className="container mx-auto py-6 max-w-4xl space-y-6">
        <Skeleton className="h-8 w-48" />
        <Skeleton className="h-[200px] w-full" />
        <Skeleton className="h-[300px] w-full" />
      </div>
    );
  }

  if (!file) {
    return (
      <div className="container mx-auto py-6 max-w-4xl">
        <p className="text-muted-foreground">File not found.</p>
        <Button variant="ghost" asChild className="mt-4">
          <Link to="/datasets"><ArrowLeft className="w-4 h-4 mr-2" />Back to Datasets</Link>
        </Button>
      </div>
    );
  }

  return (
    <div className="container mx-auto py-6 max-w-4xl space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <Button variant="ghost" size="icon" asChild>
            <Link to="/datasets"><ArrowLeft className="w-4 h-4" /></Link>
          </Button>
          <div>
            <h1 className="text-xl font-semibold">{file.filename}</h1>
            <p className="text-sm text-muted-foreground">
              {formatBytes(file.file_size_bytes)} &middot; {file.mime_type || "Unknown type"} &middot; {getListingStatusBadge(file.listing_status)}
            </p>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <Button
            variant="outline"
            size="sm"
            onClick={async () => {
              try {
                await rawFilesApi.downloadRawFile(file);
              } catch {
                toast({ title: "Error", description: "Failed to download file", variant: "destructive" });
              }
            }}
          >
            <Download className="w-4 h-4 mr-1" />Download
          </Button>
          <Button variant="destructive" size="sm" onClick={handleDelete} disabled={deleting}>
            {deleting ? <Loader2 className="w-4 h-4 animate-spin" /> : <Trash2 className="w-4 h-4 mr-1" />}
            Delete
          </Button>
        </div>
      </div>

      {/* File Preview */}
      <FilePreview file={file} />

      {/* Listing Editor */}
      <Card>
        <CardHeader className="pb-3">
          <CardTitle className="flex items-center gap-2 text-base">
            <Store className="h-4 w-4 text-primary" />
            Listing Details
          </CardTitle>
          <CardDescription>Edit buyer-facing details and publish when ready</CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <ListingEditorForm
            value={form}
            onChange={setForm}
            tagInput={tagInput}
            onTagInputChange={setTagInput}
            disabled={saving || publishing}
          />

          {publishError && (
            <Alert variant="destructive">
              <AlertDescription>{publishError}</AlertDescription>
            </Alert>
          )}

          {/* Show extracted technical metadata if present */}
          {file.metadata && (file.metadata as Record<string, unknown>).technical_metadata && (
            <div className="pt-2">
              <Label className="text-muted-foreground text-xs">Technical Metadata (auto-extracted)</Label>
              <pre className="mt-1 text-xs bg-muted p-2 rounded overflow-auto max-h-32">
                {JSON.stringify((file.metadata as Record<string, unknown>).technical_metadata, null, 2)}
              </pre>
            </div>
          )}

          <div className="flex flex-wrap gap-2">
            <Button onClick={handlePublish} disabled={saving || publishing || file.listing_status === "listed"} size="sm" className="gap-2">
              {publishing ? <Loader2 className="h-4 w-4 animate-spin" /> : <Store className="h-4 w-4" />}
              {publishing ? "Publishing..." : file.listing_status === "listed" ? "Published" : "Publish to ai.market"}
            </Button>
            <Button onClick={handleSave} disabled={saving || publishing} size="sm" variant="outline">
              {saving ? <Loader2 className="w-4 h-4 mr-1 animate-spin" /> : <Save className="w-4 h-4 mr-1" />}
              Save draft
            </Button>
          </div>
        </CardContent>
      </Card>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
        {/* Listing Readiness */}
        <ListingReadiness file={file} isConnected={isConnected} />

      </div>
    </div>
  );
}
