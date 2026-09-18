import { useEffect, useRef, useState } from 'react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { previewBuildApi, type PreviewBuildStatus } from '@/lib/api';

export function PreviewOriginReview({ job, onChange }: { job: PreviewBuildStatus; onChange: (job: PreviewBuildStatus) => void }) {
  const [destination, setDestination] = useState<'local' | 'export'>(job.publication?.destination || 'local');
  const [url, setUrl] = useState(job.origin || '');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const errorRef = useRef<HTMLParagraphElement>(null);
  useEffect(() => { if (error) errorRef.current?.focus(); }, [error]);
  const run = async (fn: () => Promise<void>) => {
    setBusy(true); setError(null);
    try { await fn(); } catch (e) { setError(e instanceof Error ? e.message : 'origin_operation_failed'); }
    finally { setBusy(false); }
  };
  const exportPackage = () => run(async () => {
    const blob = await previewBuildApi.download(job.job_id);
    const objectUrl = URL.createObjectURL(blob);
    try {
      const anchor = document.createElement('a'); anchor.href = objectUrl;
      // Preserve the immutable object filename and explicitly show its containing path.
      anchor.download = `${job.publication!.sample_hash}.json`;
      document.body.appendChild(anchor); anchor.click(); anchor.remove();
    } finally { URL.revokeObjectURL(objectUrl); }
  });
  return <section aria-label="Preview origin review" className="space-y-3 rounded-md border p-3">
    <h4 className="font-medium">Publication location</h4>
    <p>A connected S3/R2 bucket is not automatically writable or public. AIM Data does not upload to it. You control HTTPS hosting, public access, CORS and retirement.</p>
    {error && <p role="alert" ref={errorRef} tabIndex={-1}>{error}</p>}
    <fieldset disabled={busy || !!job.publication} className="space-y-2">
      <legend>Choose publication method</legend>
      <label className="flex gap-2"><input type="radio" name={`publication-${job.job_id}`} checked={destination === 'local'} onChange={() => setDestination('local')} />App-managed local publication directory</label>
      <label className="flex gap-2"><input type="radio" name={`publication-${job.job_id}`} checked={destination === 'export'} onChange={() => setDestination('export')} />Export package for my own hosting</label>
    </fieldset>
    {!job.publication && <Button type="button" disabled={busy || !job.policy?.passed} onClick={() => run(async () => { onChange(await previewBuildApi.package(job.job_id,destination)); })}>Write preview package</Button>}
    {job.publication && <>
      <p>Exact local directory: <code>{job.publication.local_directory}</code></p>
      <p>Exact object path: <code>{job.publication.relative_path}</code></p>
      <p>Package: {job.publication.byte_count} bytes · SHA-256 <code>{job.publication.package_sha256.slice(0,12)}</code></p>
      <p>{job.publication.destination === 'local' ? 'Controlled hosting: expose this directory only through the isolated preview origin behind your HTTPS proxy. Local withdrawal creates a retirement tombstone.' : 'Exported hosting: publish these exact bytes at the displayed object path using your own tools. You must remove that external object when withdrawing.'}</p>
      {job.publication.destination === 'export' && <Button type="button" variant="outline" disabled={busy} onClick={exportPackage}>Download package</Button>}
      <label className="block" htmlFor={`origin-url-${job.job_id}`}>Seller HTTPS package URL</label>
      <Input id={`origin-url-${job.job_id}`} type="url" maxLength={2048} value={url} disabled={busy || !!job.candidate} onChange={e => setUrl(e.target.value)} placeholder={`https://your-host.example/${job.publication.relative_path}`} />
      <p>Exact destination: <span>{url || 'Enter your seller HTTPS URL'}</span></p>
      <p>The browser must be able to fetch a JSON package without credentials. CORS must allow https://ai.market or *. Other response headers and OPTIONS behaviour are recorded as observations.</p>
      <Button type="button" disabled={busy || !url || !!job.candidate} onClick={() => run(async () => { onChange(await previewBuildApi.originCheck(job.job_id,url)); })}>Check browser access</Button>
    </>}
    {busy && <p role="status">Checking local publication…</p>}
    {job.receipts.map(receipt => <div key={receipt.method} className="text-sm">
      <p>{receipt.method}: {receipt.status} · {receipt.captured_at}</p>
      <dl>{Object.entries(receipt.headers).map(([key,value]) => <div key={key}><dt className="inline font-medium">{key}: </dt><dd className="inline">{value ?? '(absent)'}</dd></div>)}</dl>
      <p>No Set-Cookie: {receipt.no_set_cookie ? 'confirmed' : 'not confirmed'}</p>
    </div>)}
    {job.receipts.length === 2 && <p role="status">GET and OPTIONS receipts recorded for {job.origin}. Marketplace preview submission still awaits backend support.</p>}
  </section>;
}
