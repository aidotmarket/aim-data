import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  AlertTriangle,
  CheckCircle,
  ChevronDown,
  ChevronUp,
  Code2,
  FileText,
  FileType,
  HardDrive,
  Loader2,
  Rows3,
  Store,
  Trash2,
} from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { ScrollArea, ScrollBar } from "@/components/ui/scroll-area";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import {
  filenameToTitle,
  ListingEditorForm,
  type ListingEditorValue,
} from "@/components/ListingEditorForm";
import { datasetsApi, type DatasetPreviewResponse } from "@/lib/api";
import { toast } from "@/hooks/use-toast";

const DataTypeColors: Record<string, string> = {
  string: "bg-blue-500/10 text-blue-700 border-blue-500/20",
  integer: "bg-green-500/10 text-green-700 border-green-500/20",
  int: "bg-green-500/10 text-green-700 border-green-500/20",
  float: "bg-yellow-500/10 text-yellow-700 border-yellow-500/20",
  double: "bg-yellow-500/10 text-yellow-700 border-yellow-500/20",
  number: "bg-yellow-500/10 text-yellow-700 border-yellow-500/20",
  date: "bg-purple-500/10 text-purple-700 border-purple-500/20",
  datetime: "bg-purple-500/10 text-purple-700 border-purple-500/20",
  boolean: "bg-pink-500/10 text-pink-700 border-pink-500/20",
  bool: "bg-pink-500/10 text-pink-700 border-pink-500/20",
};

function getTypeColor(type: string): string {
  return DataTypeColors[type.toLowerCase()] || "bg-secondary text-muted-foreground border-border";
}

function formatBytes(bytes: number): string {
  if (!Number.isFinite(bytes) || bytes <= 0) return "0 B";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(2)} MB`;
}

interface DatasetPreviewProps {
  datasetId: string;
}

export default function DatasetPreview({ datasetId }: DatasetPreviewProps) {
  const navigate = useNavigate();
  const [preview, setPreview] = useState<DatasetPreviewResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [metadataLoading, setMetadataLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [publishing, setPublishing] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [published, setPublished] = useState(false);
  const [textExpanded, setTextExpanded] = useState(false);
  const [tagInput, setTagInput] = useState("");
  const [form, setForm] = useState<ListingEditorValue>({
    title: "",
    description: "",
    priceUsd: "25",
    category: "tabular",
    tags: [],
  });

  useEffect(() => {
    const fetchPreview = async () => {
      setLoading(true);
      setError(null);
      try {
        const data = await datasetsApi.getPreview(datasetId);
        setPreview(data);
        const filename = data.file?.original_filename || "Dataset";
        setForm((current) => ({
          ...current,
          title: current.title || filenameToTitle(filename) || filename,
          description: current.description || `Dataset file: ${filename}`,
          category: data.file?.file_type === "pdf" ? "documents" : current.category,
        }));
      } catch (e) {
        setError(e instanceof Error ? e.message : "Failed to load listing draft");
      } finally {
        setLoading(false);
      }
    };
    fetchPreview();
  }, [datasetId]);

  useEffect(() => {
    if (!preview?.file) return;
    let cancelled = false;
    setMetadataLoading(true);
    datasetsApi.getListingMetadata(datasetId)
      .then((metadata) => {
        if (cancelled) return;
        setForm((current) => ({
          ...current,
          title: current.title || metadata.title,
          description: metadata.description || current.description,
          category: metadata.data_categories[0] || current.category,
          tags: metadata.tags || current.tags,
        }));
      })
      .catch(() => {
        // S3 drafts may not have generated profile artifacts yet; filename defaults still apply.
      })
      .finally(() => {
        if (!cancelled) setMetadataLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [datasetId, preview?.file]);

  const handlePublish = async () => {
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
      await datasetsApi.publish(datasetId, {
        title: form.title.trim(),
        description: form.description.trim(),
        tags: form.tags,
        price,
        category: form.category,
      });
      setPublished(true);
      toast({ title: "Live on ai.market", description: "Your listing has been published." });
      navigate("/datasets");
    } catch (e) {
      toast({
        title: "Publish failed",
        description: e instanceof Error ? e.message : "Failed to publish listing.",
        variant: "destructive",
      });
    } finally {
      setPublishing(false);
    }
  };

  const handleDelete = async () => {
    setDeleting(true);
    try {
      await datasetsApi.delete(datasetId);
      toast({ title: "Draft deleted", description: "The listing draft has been removed." });
      navigate("/datasets");
    } catch (e) {
      toast({
        title: "Delete failed",
        description: e instanceof Error ? e.message : "Failed to delete draft.",
        variant: "destructive",
      });
    } finally {
      setDeleting(false);
    }
  };

  if (loading) {
    return (
      <div className="space-y-4">
        <Skeleton className="h-6 w-48" />
        <div className="grid grid-cols-2 gap-4 md:grid-cols-4">
          {Array.from({ length: 4 }).map((_, i) => (
            <Skeleton key={i} className="h-20 rounded-lg" />
          ))}
        </div>
        <Skeleton className="h-72 rounded-lg" />
      </div>
    );
  }

  if (error || !preview) {
    return (
      <Card className="bg-card border-destructive/50">
        <CardContent className="py-8">
          <div className="flex flex-col items-center gap-3 text-center">
            <AlertTriangle className="h-10 w-10 text-destructive" />
            <h3 className="text-lg font-semibold text-foreground">Failed to load listing draft</h3>
            <p className="max-w-md text-sm text-muted-foreground">
              {error || "An unexpected error occurred while loading this listing draft."}
            </p>
            <Button variant="outline" size="sm" onClick={() => navigate("/datasets")}>
              Back to Datasets
            </Button>
          </div>
        </CardContent>
      </Card>
    );
  }

  const { file, preview: previewData, warnings } = preview;
  const hasText = Boolean(previewData?.text);
  const hasSchema = Boolean(previewData?.schema?.length);
  const hasSampleRows = Boolean(previewData?.sample_rows?.length);
  const isTabular = previewData?.kind === "tabular";
  const truncatedText =
    previewData?.text && previewData.text.length > 500 && !textExpanded
      ? previewData.text.slice(0, 500)
      : previewData?.text || "";

  return (
    <div className="space-y-6">
      <div className="flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
        <div>
          <h2 className="text-lg font-semibold text-foreground">Listing Draft</h2>
          <p className="text-sm text-muted-foreground">Set buyer-facing details and publish when ready.</p>
        </div>
        <Badge
          variant="secondary"
          className={published ? "bg-haven-success/20 text-haven-success border-haven-success/30" : ""}
        >
          {published ? "Live" : "Draft"}
        </Badge>
      </div>

      {warnings && warnings.length > 0 ? (
        <Card className="border-haven-warning/50 bg-haven-warning/5">
          <CardContent className="px-4 py-3">
            <div className="flex items-start gap-3">
              <AlertTriangle className="mt-0.5 h-5 w-5 shrink-0 text-haven-warning" />
              <div className="space-y-1">
                {warnings.map((warning, i) => (
                  <p key={i} className="text-sm text-haven-warning">{warning}</p>
                ))}
              </div>
            </div>
          </CardContent>
        </Card>
      ) : null}

      {file ? (
        <div className="grid grid-cols-2 gap-4 md:grid-cols-4">
          <Card className="bg-card border-border">
            <CardContent className="p-4">
              <div className="flex items-center gap-3">
                <div className="flex h-9 w-9 items-center justify-center rounded-lg bg-secondary">
                  <FileText className="h-4 w-4 text-primary" />
                </div>
                <div className="min-w-0">
                  <p className="text-xs text-muted-foreground">Filename</p>
                  <p className="truncate text-sm font-medium text-foreground">{file.original_filename}</p>
                </div>
              </div>
            </CardContent>
          </Card>

          <Card className="bg-card border-border">
            <CardContent className="p-4">
              <div className="flex items-center gap-3">
                <div className="flex h-9 w-9 items-center justify-center rounded-lg bg-secondary">
                  <HardDrive className="h-4 w-4 text-primary" />
                </div>
                <div>
                  <p className="text-xs text-muted-foreground">Size</p>
                  <p className="text-sm font-medium text-foreground">{formatBytes(file.size_bytes)}</p>
                </div>
              </div>
            </CardContent>
          </Card>

          <Card className="bg-card border-border">
            <CardContent className="p-4">
              <div className="flex items-center gap-3">
                <div className="flex h-9 w-9 items-center justify-center rounded-lg bg-secondary">
                  <FileType className="h-4 w-4 text-primary" />
                </div>
                <div>
                  <p className="text-xs text-muted-foreground">Format</p>
                  <p className="text-sm font-medium uppercase text-foreground">{file.file_type}</p>
                </div>
              </div>
            </CardContent>
          </Card>

          <Card className="bg-card border-border">
            <CardContent className="p-4">
              <div className="flex items-center gap-3">
                <div className="flex h-9 w-9 items-center justify-center rounded-lg bg-secondary">
                  {isTabular ? <Rows3 className="h-4 w-4 text-primary" /> : <Code2 className="h-4 w-4 text-primary" />}
                </div>
                <div>
                  <p className="text-xs text-muted-foreground">{isTabular ? "Rows / Cols" : "Encoding"}</p>
                  <p className="text-sm font-medium text-foreground">
                    {isTabular
                      ? `${(previewData?.row_count_estimate || 0).toLocaleString()} / ${previewData?.column_count || 0}`
                      : file.encoding || "UTF-8"}
                  </p>
                </div>
              </div>
            </CardContent>
          </Card>
        </div>
      ) : null}

      <Card className="bg-card border-border">
        <CardHeader className="pb-3">
          <CardTitle className="flex items-center gap-2 text-base">
            <Store className="h-4 w-4 text-primary" />
            Listing Details
            {metadataLoading ? <Loader2 className="h-3.5 w-3.5 animate-spin text-muted-foreground" /> : null}
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-5">
          <ListingEditorForm
            value={form}
            onChange={setForm}
            tagInput={tagInput}
            onTagInputChange={setTagInput}
            disabled={publishing || deleting}
          />

          <div className="flex flex-col gap-2 sm:flex-row sm:items-center">
            <Button onClick={handlePublish} disabled={publishing || deleting} className="gap-2">
              {publishing ? <Loader2 className="h-4 w-4 animate-spin" /> : <CheckCircle className="h-4 w-4" />}
              {publishing ? "Publishing..." : "Publish to ai.market"}
            </Button>
            <Button variant="outline" onClick={() => navigate("/datasets")} disabled={publishing || deleting}>
              Cancel
            </Button>
            <Button
              variant="outline"
              onClick={handleDelete}
              disabled={publishing || deleting}
              className="gap-2 text-destructive hover:text-destructive"
            >
              {deleting ? <Loader2 className="h-4 w-4 animate-spin" /> : <Trash2 className="h-4 w-4" />}
              {deleting ? "Deleting..." : "Delete draft"}
            </Button>
          </div>
        </CardContent>
      </Card>

      {hasText ? (
        <Card className="bg-card border-border">
          <CardHeader className="pb-3">
            <CardTitle className="text-base">File Text</CardTitle>
          </CardHeader>
          <CardContent>
            <div className="rounded-lg bg-secondary/50 p-4">
              <pre className="whitespace-pre-wrap font-mono text-sm leading-relaxed text-foreground">{truncatedText}</pre>
              {previewData!.text!.length > 500 ? (
                <Button
                  variant="ghost"
                  size="sm"
                  className="mt-2 text-primary hover:text-primary"
                  onClick={() => setTextExpanded(!textExpanded)}
                >
                  {textExpanded ? <ChevronUp className="mr-1 h-4 w-4" /> : <ChevronDown className="mr-1 h-4 w-4" />}
                  {textExpanded ? "Show less" : `Show more (${previewData!.text!.length.toLocaleString()} chars total)`}
                </Button>
              ) : null}
            </div>
          </CardContent>
        </Card>
      ) : null}

      {hasSchema ? (
        <Card className="overflow-hidden bg-card border-border">
          <CardHeader className="pb-3">
            <div className="flex items-center gap-2">
              <CardTitle className="text-base">Schema</CardTitle>
              <Badge variant="secondary" className="text-xs">{previewData!.schema.length} columns</Badge>
            </div>
          </CardHeader>
          <Table>
            <TableHeader>
              <TableRow className="hover:bg-transparent border-border">
                <TableHead>Column Name</TableHead>
                <TableHead>Data Type</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {previewData!.schema.map((col) => (
                <TableRow key={col.name} className="border-border hover:bg-secondary/50">
                  <TableCell className="font-mono text-sm">{col.name}</TableCell>
                  <TableCell>
                    <Badge variant="outline" className={getTypeColor(col.type)}>{col.type}</Badge>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </Card>
      ) : null}

      {hasSampleRows ? (
        <Card className="overflow-hidden bg-card border-border">
          <CardHeader className="pb-3">
            <div className="flex items-center gap-2">
              <CardTitle className="text-base">Sample Data</CardTitle>
              <Badge variant="secondary" className="text-xs">{previewData!.sample_rows.length} rows</Badge>
            </div>
          </CardHeader>
          <ScrollArea className="w-full">
            <Table>
              <TableHeader>
                <TableRow className="hover:bg-transparent border-border">
                  {Object.keys(previewData!.sample_rows[0]).map((key) => (
                    <TableHead key={key} className="whitespace-nowrap">{key}</TableHead>
                  ))}
                </TableRow>
              </TableHeader>
              <TableBody>
                {previewData!.sample_rows.map((row, index) => (
                  <TableRow key={index} className="border-border hover:bg-secondary/50">
                    {Object.values(row).map((value, cellIndex) => (
                      <TableCell key={cellIndex} className="max-w-[300px] truncate whitespace-nowrap">
                        {value === null ? <span className="text-muted-foreground italic">null</span> : String(value)}
                      </TableCell>
                    ))}
                  </TableRow>
                ))}
              </TableBody>
            </Table>
            <ScrollBar orientation="horizontal" />
          </ScrollArea>
        </Card>
      ) : null}
    </div>
  );
}
