import { useEffect, useState } from "react";
import { getApiUrl } from "@/lib/api";

interface VersionInfo {
  current?: string;
}

const VersionBadge = () => {
  const [version, setVersion] = useState<string | null>(null);

  useEffect(() => {
    fetch(`${getApiUrl()}/api/version`)
      .then((r) => (r.ok ? r.json() : null))
      .then((data: VersionInfo | null) => {
        if (data?.current) setVersion(data.current);
      })
      .catch(() => {});
  }, []);

  if (!version) return null;

  const normalized = version.replace(/^v/, "");

  return (
    <span
      className="fixed bottom-3 right-4 z-40 text-muted-foreground/70 select-none pointer-events-none"
      style={{ fontSize: 12 }}
    >
      {normalized}
    </span>
  );
};

export default VersionBadge;
