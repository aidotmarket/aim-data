import { useCallback, useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { AlertTriangle, CheckCircle, ChevronLeft, ChevronRight, FileSearch, Loader2, Play, RefreshCw, UploadCloud } from "lucide-react";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { RadioGroup, RadioGroupItem } from "@/components/ui/radio-group";
import { ScrollArea, ScrollBar } from "@/components/ui/scroll-area";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { Textarea } from "@/components/ui/textarea";
import { toast } from "@/hooks/use-toast";
import { getApiUrl } from "@/lib/api";

interface S3ConnectionReviewConnection {
  id: string;
  name?: string;
  bucket?: string;
  prefix?: string | null;
  status: string;
}

interface S3ScanJob {
  id: string;
  connection_id: string;
  status: string;
  started_at: string;
  completed_at?: string | null;
  continuation_token?: string | null;
  error_message?: string | null;
  objects_enumerated: number;
  created_at: string;
  updated_at: string;
}

interface S3ObjectMetadata {
  id: string;
  connection_id: string;
  scan_job_id: string;
  object_key: string;
  size_bytes: number;
  content_type: string;
  last_modified: string;
  etag: string;
  dataset_id?: string | null;
  created_at: string;
  updated_at: string;
}

interface S3ObjectsResponse {
  items: S3ObjectMetadata[];
  limit: number;
  offset: number;
  total: number;
}

interface S3RegisterResponse {
  dataset: {
    id: string;
  };
  object: S3ObjectMetadata;
}

type BucketPublishScope = "prefix" | "bucket_root";

// Bucket-root delivery requires ai-market-backend STS policy support (build_s3_session_policy
// currently rejects empty prefixes). Until that backend build is live + verified, root listings
// are publishable-but-undeliverable, which violates "works like the customer sees it or not at
// all." Hide the root scope here and keep it 501-gated server-side until then (S711).
const BUCKET_ROOT_DELIVERY_ENABLED = false;

const PAGE_SIZE = 50;
const POLL_INTERVAL_MS = 2500;
const TERMINAL_SCAN_STATUSES = new Set(["completed", "failed", "error", "cancelled"]);

function apiHeaders(): Record<string, string> {
  const accessToken = localStorage.getItem("aim_data_access_token");
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  if (accessToken) headers["Authorization"] = `Bearer ${accessToken}`;
  return headers;
}

function formatBytes(value: number) {
  if (!Number.isFinite(value) || value <= 0) return "0 B";
  const units = ["B", "KB", "MB", "GB", "TB"];
  const index = Math.min(Math.floor(Math.log(value) / Math.log(1024)), units.length - 1);
  return `${(value / 1024 ** index).toFixed(index === 0 ? 0 : 1)} ${units[index]}`;
}

function scanFailed(status?: string) {
  return status === "failed" || status === "error" || status === "cancelled";
}

export function S3ConnectionReview({
  connection,
  onScanComplete,
}: {
  connection: S3ConnectionReviewConnection;
  onScanComplete?: () => void;
}) {
  const navigate = useNavigate();
  const [scanJob, setScanJob] = useState<S3ScanJob | null>(null);
  const [scanLoading, setScanLoading] = useState(false);
  const [scanError, setScanError] = useState("");
  const [objects, setObjects] = useState<S3ObjectMetadata[]>([]);
  const [objectsLoading, setObjectsLoading] = useState(false);
  const [objectsError, setObjectsError] = useState("");
  const [total, setTotal] = useState(0);
  const [offset, setOffset] = useState(0);
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());
  const [registeringIds, setRegisteringIds] = useState<Set<string>>(new Set());
  const [publishDialogOpen, setPublishDialogOpen] = useState(false);
  const [publishingBucket, setPublishingBucket] = useState(false);
  const [publishTitle, setPublishTitle] = useState(connection.name || "S3 bucket listing");
  const [publishDescription, setPublishDescription] = useState("");
  const [publishCategory, setPublishCategory] = useState("data");
  const [publishPrice, setPublishPrice] = useState("");
  const [publishScope, setPublishScope] = useState<BucketPublishScope>("prefix");
  const [rootAcknowledged, setRootAcknowledged] = useState(false);

  const scanInProgress = Boolean(scanJob && !TERMINAL_SCAN_STATUSES.has(scanJob.status));
  const selectableObjects = useMemo(() => objects, [objects]);
  const allPageSelectableSelected =
    selectableObjects.length > 0 && selectableObjects.every((object) => selectedIds.has(object.id));

  const updateObject = (updatedObject: S3ObjectMetadata) => {
    setObjects((currentObjects) =>
      currentObjects.map((object) => (object.id === updatedObject.id ? updatedObject : object)),
    );
    setSelectedIds((currentIds) => {
      const nextIds = new Set(currentIds);
      nextIds.delete(updatedObject.id);
      return nextIds;
    });
  };

  const fetchObjects = useCallback(
    async (nextOffset = 0) => {
      setObjectsLoading(true);
      setObjectsError("");
      try {
        const response = await fetch(
          `${getApiUrl()}/api/s3-connections/${connection.id}/objects?limit=${PAGE_SIZE}&offset=${nextOffset}`,
          { headers: apiHeaders() },
        );
        if (response.ok) {
          const data: S3ObjectsResponse = await response.json();
          setObjects(data.items);
          setOffset(data.offset);
          setTotal(data.total);
          setSelectedIds(new Set());
        } else {
          setObjectsError("Failed to load scanned objects.");
        }
      } catch {
        setObjectsError("Failed to load scanned objects.");
      } finally {
        setObjectsLoading(false);
      }
    },
    [connection.id],
  );

  useEffect(() => {
    fetchObjects(0);
  }, [fetchObjects]);

  useEffect(() => {
    if (!scanJob || TERMINAL_SCAN_STATUSES.has(scanJob.status)) return undefined;

    const timer = window.setInterval(async () => {
      try {
        const response = await fetch(`${getApiUrl()}/api/s3-connections/${connection.id}/scan/${scanJob.id}`, {
          headers: apiHeaders(),
        });
        if (!response.ok) {
          setScanError("Scan status could not be refreshed.");
          setScanJob((currentJob) => (currentJob ? { ...currentJob, status: "failed" } : currentJob));
          return;
        }

        const data: S3ScanJob = await response.json();
        setScanJob(data);
        if (data.status === "completed") {
          toast({ title: "Scan complete", description: `${data.objects_enumerated} object${data.objects_enumerated === 1 ? "" : "s"} found.` });
          onScanComplete?.();
          fetchObjects(0);
        } else if (scanFailed(data.status)) {
          setScanError(data.error_message || "Scan failed. Verify bucket access and retry.");
        }
      } catch {
        setScanError("Scan status could not be refreshed.");
        setScanJob((currentJob) => (currentJob ? { ...currentJob, status: "failed" } : currentJob));
      }
    }, POLL_INTERVAL_MS);

    return () => window.clearInterval(timer);
  }, [connection.id, fetchObjects, onScanComplete, scanJob]);

  const startScan = async () => {
    setScanLoading(true);
    setScanError("");
    try {
      const response = await fetch(`${getApiUrl()}/api/s3-connections/${connection.id}/scan`, {
        method: "POST",
        headers: apiHeaders(),
      });
      if (response.ok) {
        const data: S3ScanJob = await response.json();
        setScanJob(data);
        if (data.status === "completed") {
          toast({ title: "Scan complete", description: `${data.objects_enumerated} object${data.objects_enumerated === 1 ? "" : "s"} found.` });
          onScanComplete?.();
          fetchObjects(0);
        } else if (scanFailed(data.status)) {
          setScanError(data.error_message || "Scan failed. Verify bucket access and retry.");
        } else {
          toast({ title: "Scan started", description: "S3 object discovery is running." });
        }
      } else {
        setScanError("Scan could not be started.");
        toast({ title: "Error", description: "Scan could not be started.", variant: "destructive" });
      }
    } catch {
      setScanError("Scan could not be started.");
      toast({ title: "Error", description: "Scan could not be started.", variant: "destructive" });
    } finally {
      setScanLoading(false);
    }
  };

  const publishWholeBucket = async () => {
    const priceNumber = Number.parseFloat(publishPrice);
    if (!publishTitle.trim() || !publishDescription.trim() || !Number.isFinite(priceNumber) || priceNumber < 0) {
      toast({ title: "Publish details needed", description: "Add a title, description, and valid price.", variant: "destructive" });
      return;
    }
    if (publishScope === "bucket_root" && !rootAcknowledged) {
      toast({ title: "Root access confirmation required", description: "Confirm the bucket-root exposure before publishing.", variant: "destructive" });
      return;
    }

    setPublishingBucket(true);
    try {
      const response = await fetch(`${getApiUrl()}/api/s3-connections/${connection.id}/publish-bucket`, {
        method: "POST",
        headers: apiHeaders(),
        body: JSON.stringify({
          title: publishTitle.trim(),
          description: publishDescription.trim(),
          category: publishCategory.trim() || null,
          pricing_type: "one_time",
          price_cents: Math.round(priceNumber * 100),
          scope: publishScope,
        }),
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) {
        toast({
          title: "Publish failed",
          description: data.detail || "The bucket listing could not be published.",
          variant: "destructive",
        });
        return;
      }
      setPublishDialogOpen(false);
      toast({
        title: "Bucket listing published",
        description: data.marketplace_url ? "The marketplace listing is ready." : "The marketplace accepted the listing.",
      });
      if (data.marketplace_url) {
        window.open(data.marketplace_url, "_blank", "noopener,noreferrer");
      }
    } catch {
      toast({ title: "Publish failed", description: "The bucket listing could not be published.", variant: "destructive" });
    } finally {
      setPublishingBucket(false);
    }
  };

  const listObject = async (object: S3ObjectMetadata): Promise<string | null> => {
    if (object.dataset_id) {
      return object.dataset_id;
    }

    const objectId = object.id;
    setRegisteringIds((currentIds) => new Set(currentIds).add(objectId));
    try {
      const response = await fetch(`${getApiUrl()}/api/s3-connections/${connection.id}/objects/${objectId}/register`, {
        method: "POST",
        headers: apiHeaders(),
        body: JSON.stringify({}),
      });
      if (response.ok) {
        const data: S3RegisterResponse = await response.json();
        updateObject(data.object);
        toast({ title: "Listing created — set your price and publish." });
        return data.dataset.id;
      } else if (response.status === 403) {
        toast({
          title: "Registration blocked",
          description: "You can only register objects owned by this S3 connection.",
          variant: "destructive",
        });
      } else {
        toast({ title: "Error", description: "Failed to register object.", variant: "destructive" });
      }
    } catch {
      toast({ title: "Error", description: "Failed to register object.", variant: "destructive" });
    } finally {
      setRegisteringIds((currentIds) => {
        const nextIds = new Set(currentIds);
        nextIds.delete(objectId);
        return nextIds;
      });
    }
    return null;
  };

  const listSingleObject = async (object: S3ObjectMetadata) => {
    const datasetId = await listObject(object);
    if (datasetId) {
      navigate(`/datasets/${datasetId}`, { state: { from: "/list-data" } });
    }
  };

  const listSelected = async () => {
    const selectedObjects = objects.filter((object) => selectedIds.has(object.id));

    if (selectedObjects.length === 1) {
      await listSingleObject(selectedObjects[0]);
      return;
    }

    let listedCount = 0;
    for (const object of selectedObjects) {
      const datasetId = await listObject(object);
      if (datasetId) {
        listedCount += 1;
      }
    }

    if (listedCount > 0) {
      navigate("/datasets");
    }
  };

  const toggleObject = (objectId: string) => {
    setSelectedIds((currentIds) => {
      const nextIds = new Set(currentIds);
      if (nextIds.has(objectId)) {
        nextIds.delete(objectId);
      } else {
        nextIds.add(objectId);
      }
      return nextIds;
    });
  };

  const togglePageSelection = () => {
    setSelectedIds((currentIds) => {
      const nextIds = new Set(currentIds);
      if (allPageSelectableSelected) {
        selectableObjects.forEach((object) => nextIds.delete(object.id));
      } else {
        selectableObjects.forEach((object) => nextIds.add(object.id));
      }
      return nextIds;
    });
  };

  const currentPage = total === 0 ? 0 : Math.floor(offset / PAGE_SIZE) + 1;
  const pageCount = Math.ceil(total / PAGE_SIZE);
  const isRegisteringSelected = Array.from(selectedIds).some((id) => registeringIds.has(id));

  return (
    <div className="space-y-3 rounded-md border border-border bg-background/60 p-3">
      <div className="flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
        <div className="space-y-1">
          <div className="flex items-center gap-2 text-sm font-medium text-foreground">
            <FileSearch className="h-4 w-4 text-primary" />
            Scan and review bucket objects
          </div>
          <div className="text-xs text-muted-foreground">
            {scanJob ? (
              <span>
                Scan {scanJob.status} · {scanJob.objects_enumerated} object{scanJob.objects_enumerated === 1 ? "" : "s"} found
              </span>
            ) : (
              <span>{total} scanned object{total === 1 ? "" : "s"} available to list</span>
            )}
          </div>
        </div>
        <div className="flex flex-wrap items-center gap-1.5">
          <Button size="sm" className="gap-1.5" onClick={() => setPublishDialogOpen(true)} disabled={connection.status !== "verified"}>
            <UploadCloud className="h-3.5 w-3.5" />
            List the whole bucket
          </Button>
          <Button variant="outline" size="sm" className="gap-1.5" onClick={() => fetchObjects(offset)} disabled={objectsLoading}>
            {objectsLoading ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <RefreshCw className="h-3.5 w-3.5" />}
            Refresh
          </Button>
          <Button size="sm" className="gap-1.5" onClick={startScan} disabled={scanLoading || scanInProgress || connection.status !== "verified"}>
            {scanLoading || scanInProgress ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Play className="h-3.5 w-3.5" />}
            Scan bucket
          </Button>
        </div>
      </div>

      <Dialog open={publishDialogOpen} onOpenChange={setPublishDialogOpen}>
        <DialogContent className="max-w-lg">
          <DialogHeader>
            <DialogTitle>List the whole bucket</DialogTitle>
            <DialogDescription>
              Create one marketplace listing that gives the buyer scoped S3 credentials for the selected bucket scope.
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-4">
            <div className="grid gap-2">
              <Label htmlFor="s3-bucket-title">Title</Label>
              <Input id="s3-bucket-title" value={publishTitle} onChange={(event) => setPublishTitle(event.target.value)} />
            </div>
            <div className="grid gap-2">
              <Label htmlFor="s3-bucket-description">Description</Label>
              <Textarea
                id="s3-bucket-description"
                value={publishDescription}
                onChange={(event) => setPublishDescription(event.target.value)}
                rows={4}
              />
            </div>
            <div className="grid gap-3 sm:grid-cols-2">
              <div className="grid gap-2">
                <Label htmlFor="s3-bucket-category">Category</Label>
                <Input id="s3-bucket-category" value={publishCategory} onChange={(event) => setPublishCategory(event.target.value)} />
              </div>
              <div className="grid gap-2">
                <Label htmlFor="s3-bucket-price">Price</Label>
                <Input
                  id="s3-bucket-price"
                  type="number"
                  min="0"
                  step="0.01"
                  value={publishPrice}
                  onChange={(event) => setPublishPrice(event.target.value)}
                />
              </div>
            </div>
            <RadioGroup
              value={publishScope}
              onValueChange={(value) => {
                setPublishScope(value as BucketPublishScope);
                if (value !== "bucket_root") setRootAcknowledged(false);
              }}
              className="grid gap-2"
            >
              <Label className="flex items-start gap-2 rounded-md border border-border p-3">
                <RadioGroupItem value="prefix" className="mt-0.5" />
                <span className="grid gap-1 text-sm">
                  <span className="font-medium">Connection prefix</span>
                  <span className="text-muted-foreground">{connection.prefix ? `${connection.bucket || "Bucket"}/${connection.prefix}` : "Requires a non-root connection prefix."}</span>
                </span>
              </Label>
              {BUCKET_ROOT_DELIVERY_ENABLED ? (
              <Label className="flex items-start gap-2 rounded-md border border-border p-3">
                <RadioGroupItem value="bucket_root" className="mt-0.5" />
                <span className="grid gap-1 text-sm">
                  <span className="font-medium">Entire bucket root</span>
                  <span className="text-muted-foreground">{connection.bucket || "Bucket root"}</span>
                </span>
              </Label>
              ) : null}
            </RadioGroup>
            {publishScope === "bucket_root" ? (
              <Alert variant="destructive">
                <AlertTriangle className="h-4 w-4" />
                <AlertDescription>
                  Buyers will be able to access ALL current AND FUTURE files under the bucket root.
                </AlertDescription>
              </Alert>
            ) : null}
            {publishScope === "bucket_root" ? (
              <Label className="flex items-center gap-2 text-sm">
                <Checkbox checked={rootAcknowledged} onCheckedChange={(value) => setRootAcknowledged(value === true)} />
                I understand this exposes the entire bucket root to buyers.
              </Label>
            ) : null}
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setPublishDialogOpen(false)} disabled={publishingBucket}>
              Cancel
            </Button>
            <Button onClick={publishWholeBucket} disabled={publishingBucket || (publishScope === "bucket_root" && !rootAcknowledged)}>
              {publishingBucket ? <Loader2 className="mr-2 h-3.5 w-3.5 animate-spin" /> : null}
              Publish listing
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {scanError ? <p className="text-xs text-destructive">{scanError}</p> : null}
      {objectsError ? <p className="text-xs text-destructive">{objectsError}</p> : null}

      <div className="flex flex-wrap items-center justify-between gap-2">
        <Button
          variant="outline"
          size="sm"
          className="gap-1.5"
          disabled={selectedIds.size === 0 || isRegisteringSelected}
          onClick={listSelected}
        >
          {isRegisteringSelected ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <CheckCircle className="h-3.5 w-3.5" />}
          List data on the Market
        </Button>
        {selectedIds.size > 0 ? (
          <span className="text-xs text-muted-foreground">
            {selectedIds.size} object{selectedIds.size === 1 ? "" : "s"} selected
          </span>
        ) : null}
      </div>

      <ScrollArea className="w-full">
        <Table>
          <TableHeader>
            <TableRow className="hover:bg-transparent border-border">
              <TableHead className="w-10">
                <Checkbox
                  aria-label="Select page objects"
                  checked={allPageSelectableSelected}
                  disabled={selectableObjects.length === 0}
                  onCheckedChange={togglePageSelection}
                />
              </TableHead>
              <TableHead>Object</TableHead>
              <TableHead className="w-28">Size</TableHead>
              <TableHead className="w-48">Content type</TableHead>
              <TableHead className="w-28">Status</TableHead>
              <TableHead className="w-36 text-right">Action</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {objectsLoading ? (
              <TableRow>
                <TableCell colSpan={6} className="h-20 text-center">
                  <Loader2 className="mx-auto h-4 w-4 animate-spin text-muted-foreground" />
                </TableCell>
              </TableRow>
            ) : objects.length === 0 ? (
              <TableRow>
                <TableCell colSpan={6} className="h-20 text-center text-sm text-muted-foreground">
                  {scanInProgress ? "Waiting for scanned objects." : "No scanned objects found."}
                </TableCell>
              </TableRow>
            ) : (
              objects.map((object) => {
                const registering = registeringIds.has(object.id);
                return (
                  <TableRow key={object.id} className="border-border">
                    <TableCell>
                      <Checkbox
                        aria-label={`Select ${object.object_key}`}
                        checked={selectedIds.has(object.id)}
                        disabled={registering}
                        onCheckedChange={() => toggleObject(object.id)}
                      />
                    </TableCell>
                    <TableCell className="max-w-[24rem]">
                      <div className="truncate font-mono text-xs text-foreground" title={object.object_key}>
                        {object.object_key}
                      </div>
                    </TableCell>
                    <TableCell className="text-sm text-muted-foreground">{formatBytes(object.size_bytes)}</TableCell>
                    <TableCell className="text-sm text-muted-foreground">{object.content_type}</TableCell>
                    <TableCell className="text-sm text-muted-foreground">
                      {object.dataset_id ? "Listed" : ""}
                    </TableCell>
                    <TableCell className="text-right">
                      <Button
                        variant="outline"
                        size="sm"
                        disabled={registering}
                        onClick={() => listSingleObject(object)}
                      >
                        {registering ? <Loader2 className="mr-2 h-3.5 w-3.5 animate-spin" /> : null}
                        {object.dataset_id ? "Edit" : "List"}
                      </Button>
                    </TableCell>
                  </TableRow>
                );
              })
            )}
          </TableBody>
        </Table>
        <ScrollBar orientation="horizontal" />
      </ScrollArea>

      {total > PAGE_SIZE ? (
        <div className="flex items-center justify-end gap-2">
          <span className="text-xs text-muted-foreground">
            Page {currentPage} of {pageCount}
          </span>
          <Button variant="outline" size="sm" disabled={offset === 0 || objectsLoading} onClick={() => fetchObjects(Math.max(0, offset - PAGE_SIZE))}>
            <ChevronLeft className="h-3.5 w-3.5" />
          </Button>
          <Button
            variant="outline"
            size="sm"
            disabled={offset + PAGE_SIZE >= total || objectsLoading}
            onClick={() => fetchObjects(offset + PAGE_SIZE)}
          >
            <ChevronRight className="h-3.5 w-3.5" />
          </Button>
        </div>
      ) : null}
    </div>
  );
}
