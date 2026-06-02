import { Globe, MessageCircle } from "lucide-react";

interface EmptyStateProps {
  allieAvailable: boolean;
}

export default function EmptyState({ allieAvailable }: EmptyStateProps) {
  if (!allieAvailable) {
    return (
      <div className="flex-1 flex flex-col items-center justify-center px-6 text-center gap-4">
        <div className="w-12 h-12 rounded-full bg-muted flex items-center justify-center">
          <Globe className="h-6 w-6 text-muted-foreground" />
        </div>
        <div className="space-y-1.5">
          <h3 className="font-medium text-sm text-foreground">allAI is unavailable right now.</h3>
        </div>
      </div>
    );
  }

  return (
    <div className="flex-1 flex flex-col items-center justify-center px-6 text-center gap-3">
      <div className="w-12 h-12 rounded-full bg-primary/10 flex items-center justify-center">
        <MessageCircle className="h-6 w-6 text-primary" />
      </div>
      <div>
        <h3 className="font-medium text-sm text-foreground">Chat with Allie</h3>
        <p className="text-xs text-muted-foreground mt-1">
          Ask questions about your data, run queries, and get insights.
        </p>
      </div>
    </div>
  );
}
