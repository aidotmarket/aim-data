/**
 * BQ-VZ-SHARED-SEARCH: Portal Datasets Page
 *
 * Lists portal-visible datasets.
 */

import { useState, useEffect } from "react";
import { Card, CardContent } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Database, AlertCircle } from "lucide-react";
import { portalApi, type PortalDataset } from "@/api/portalApi";

const PortalDatasets = () => {
  const [datasets, setDatasets] = useState<PortalDataset[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    (async () => {
      try {
        const res = await portalApi.getDatasets();
        setDatasets(res.datasets);
      } catch (e) {
        setError(e instanceof Error ? e.message : "Failed to load datasets");
      } finally {
        setLoading(false);
      }
    })();
  }, []);

  if (loading) {
    return (
      <div className="space-y-3">
        {[1, 2, 3].map((i) => (
          <Skeleton key={i} className="h-20 w-full" />
        ))}
      </div>
    );
  }

  if (error) {
    return (
      <Alert variant="destructive">
        <AlertCircle className="h-4 w-4" />
        <AlertDescription>{error}</AlertDescription>
      </Alert>
    );
  }

  if (datasets.length === 0) {
    return (
      <Card className="bg-card border-border">
        <CardContent className="py-12 text-center">
          <Database className="w-12 h-12 text-muted-foreground mx-auto mb-4" />
          <h3 className="text-lg font-semibold text-foreground mb-2">No datasets available</h3>
          <p className="text-muted-foreground">
            No datasets have been shared on this portal yet.
          </p>
        </CardContent>
      </Card>
    );
  }

  return (
    <div className="space-y-4">
      <h2 className="text-xl font-semibold text-foreground">Available Datasets</h2>
      <div className="space-y-3">
        {datasets.map((ds) => (
          <Card
            key={ds.dataset_id}
            className="bg-card border-border"
          >
            <CardContent className="p-4 flex items-center justify-between">
              <div className="flex items-center gap-3">
                <div className="w-10 h-10 rounded-lg bg-secondary flex items-center justify-center">
                  <Database className="w-5 h-5 text-muted-foreground" />
                </div>
                <div>
                  <h3 className="font-medium text-foreground">{ds.name}</h3>
                  <p className="text-sm text-muted-foreground">
                    {ds.row_count.toLocaleString()} rows
                  </p>
                </div>
              </div>
            </CardContent>
          </Card>
        ))}
      </div>
    </div>
  );
};

export default PortalDatasets;
