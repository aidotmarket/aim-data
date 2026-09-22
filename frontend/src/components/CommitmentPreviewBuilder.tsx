import { useEffect, useRef, useState } from 'react';
import { Button } from '@/components/ui/button';
import { Textarea } from '@/components/ui/textarea';
import { previewBuildApi, type PreviewBuildStatus, type PreviewConsent, type LocalPreviewPage, type PreviewCreateOptions, type MarketplaceSummaryPreview } from '@/lib/api';
import { inertPreviewText, previewBudget, PREVIEW_ALL_FIELDS_WARNING, PREVIEW_MEMBERSHIP_DISCLAIMER, PREVIEW_PERMISSION } from '@/lib/disclosure';

interface Props {
  datasetId: string;
  /** Legacy caller input; live At a glance approval is now authoritative. */
  metadataApproved?: boolean;
  onStatus?: (status: string) => void;
  originReview?: (job: PreviewBuildStatus, onChange: (job: PreviewBuildStatus) => void) => React.ReactNode;
}

const CSV_DECLARATION_TEMPLATE = JSON.stringify({
  parsing: { format: 'csv', encoding: 'utf-8', delimiter: ',', quote: '"', escape: '', header: true, locale: 'C', null_token: '' },
  schema_descriptors: [['column_name', 'string', true, {}]],
}, null, 2);

export function CommitmentPreviewBuilder({ datasetId, onStatus, originReview }: Props) {
  const [job, setJob] = useState<PreviewBuildStatus | null>(null);
  const [page, setPage] = useState<LocalPreviewPage | null>(null);
  const [start, setStart] = useState(0);
  const [selected, setSelected] = useState<number[]>([]);
  const [columns, setColumns] = useState<string[]>([]);
  const [sizes, setSizes] = useState<Record<number, number>>({});
  const [busy, setBusy] = useState(false);
  const [recovering, setRecovering] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [declarationsNeeded, setDeclarationsNeeded] = useState(false);
  const [declarations, setDeclarations] = useState('');
  const [marketplaceSummary, setMarketplaceSummary] = useState<MarketplaceSummaryPreview | null>(null);
  const [retirePreviousPending, setRetirePreviousPending] = useState(false);
  const [rights, setRights] = useState<PreviewConsent['rights_basis'] | ''>('');
  const [permission, setPermission] = useState(false);
  const [restricted, setRestricted] = useState(false);
  const [accuracy, setAccuracy] = useState(false);
  const errorRef = useRef<HTMLParagraphElement>(null);
  const headingRef = useRef<HTMLHeadingElement>(null);
  const consent = { rights_basis: rights as PreviewConsent['rights_basis'], public_preview_permission: permission, restricted_content_confirmed: restricted };

  useEffect(() => { if (error) errorRef.current?.focus(); }, [error]);
  useEffect(() => { onStatus?.(job?.outcome || job?.state || 'No sample'); }, [job?.outcome, job?.state, onStatus]);
  useEffect(() => {
    let active = true;
    setRecovering(true);
    previewBuildApi.latest(datasetId).then(value => {
      if (!active) return;
      setJob(value);
      setSelected(value?.selection.leaf_indices || []);
      setColumns(value?.selection.display_columns || []);
      setSizes(value?.selection.row_sizes || {});
    }).catch(e => { if (active) setError(e instanceof Error ? e.message : 'recovery_failed'); })
      .finally(() => { if (active) setRecovering(false); });
    return () => { active = false; };
  }, [datasetId]);

  useEffect(() => {
    if (!job || job.state !== 'building') return;
    let active = true;
    const timer = window.setInterval(() => {
      previewBuildApi.status(job.job_id).then(value => { if (active) setJob(value); })
        .catch(e => { if (active) setError(e instanceof Error ? e.message : 'status_failed'); });
    }, 750);
    return () => { active = false; window.clearInterval(timer); };
  }, [job]);

  useEffect(() => {
    if (!job?.review_ready || ['cancelled', 'retired', 'withdrawn'].includes(job.state)) { setPage(null); return; }
    let active = true;
    previewBuildApi.rows(job.job_id, start).then(value => {
      if (!active) return;
      setPage(value);
      setSizes(current => ({ ...current, ...Object.fromEntries(value.items.map(row => [row.leaf_index, row.canonical_bytes])) }));
    }).catch(e => { if (active) setError(e instanceof Error ? e.message : 'rows_failed'); });
    return () => { active = false; };
  }, [job?.job_id, job?.review_ready, job?.state, start]);

  useEffect(() => {
    if (!job?.job_id || job.receipts.length !== 2 || job.candidate) return;
    let active = true;
    previewBuildApi.marketplaceSummary(job.job_id)
      .then(value => { if (active) setMarketplaceSummary(value); })
      .catch(e => { if (active) setError(e instanceof Error ? e.message : 'marketplace_summary_failed'); });
    return () => { active = false; };
  }, [job?.job_id, job?.receipts.length, job?.candidate]);

  const run = async (action: () => Promise<void>) => {
    setBusy(true); setError(null);
    try { await action(); } catch (e) {
      const message = e instanceof Error ? e.message : 'preview_operation_failed';
      setError(message);
      if (message === 'parsing_declaration_required') {
        setDeclarationsNeeded(true);
        setDeclarations(current => current || CSV_DECLARATION_TEMPLATE);
      }
      if (message === 'review_expired' || message === 'job_already_running') {
        try { setJob(await previewBuildApi.latest(datasetId)); } catch { /* Keep the original error visible. */ }
      }
    } finally { setBusy(false); }
  };
  const prepare = () => run(async () => {
    let options: PreviewCreateOptions = { dataset_id: datasetId };
    if (declarationsNeeded) {
      try { options = { ...JSON.parse(declarations), dataset_id: datasetId }; }
      catch { throw new Error('Enter valid parsing declarations.'); }
    }
    const value = await previewBuildApi.create(options);
    setJob(value); setSelected([]); setColumns(value.columns); setSizes({}); setStart(0);
    setPermission(false); setRestricted(false); setAccuracy(false); setRights('');
    headingRef.current?.focus();
  });
  const toggle = (index: number) => {
    setSelected(current => current.includes(index) ? current.filter(i => i !== index) : [...current, index].sort((a,b) => a-b));
    setPermission(false); setRestricted(false); setAccuracy(false);
  };
  const budget = previewBudget(selected, job?.columns.length || 0, sizes);
  const fixed = !!job?.publication || !!job?.candidate;
  const active = !!job && !['cancelled','failed','expired','retired','withdrawn'].includes(job.state);
  const selectionChanged = JSON.stringify(selected) !== JSON.stringify(job?.selection.leaf_indices) || JSON.stringify(columns) !== JSON.stringify(job?.selection.display_columns);
  const readyConsent = rights !== '' && permission && restricted;

  return <section aria-label="Public sample" className="space-y-4 rounded-md border p-4">
    <h3 ref={headingRef} tabIndex={-1} className="font-medium">Public sample</h3>
    <p>{PREVIEW_MEMBERSHIP_DISCLAIMER}</p>
    <p className="text-sm text-muted-foreground">Rows are reviewed on this machine. No sample is the default. A prepared preview becomes public only at your chosen publication location.</p>
    {recovering && <p role="status">Recovering local preview job…</p>}
    {error && <p role="alert" tabIndex={-1} ref={errorRef}>{error}</p>}
    <div className="flex flex-wrap gap-2">
      <Button type="button" variant="outline" disabled={busy || !!job?.publication} onClick={() => run(async () => {
        if (job && active) await previewBuildApi.cancel(job.job_id);
        setJob(null); setSelected([]); setPermission(false); setRestricted(false); setAccuracy(false);
      })}>No sample</Button>
      {!active && job?.state !== 'withdrawn' && <Button type="button" disabled={busy || recovering} onClick={prepare}>Prepare verified preview</Button>}
      {active && !job?.publication && <Button type="button" variant="outline" disabled={busy} onClick={() => run(async () => { setJob(await previewBuildApi.cancel(job!.job_id)); })}>Cancel preview build</Button>}
    </div>
    {declarationsNeeded && !active && <div className="space-y-2">
      <label htmlFor="preview-parsing">Missing parsing declarations</label>
      <p className="text-sm">Declare the original file format and complete logical schema. Ordinary CSV uses an empty escape and doubled quotes inside quoted fields. A distinct escape character is also supported. Schema entries are [name, type, nullable, parameters].</p>
      <Textarea id="preview-parsing" value={declarations} onChange={e => setDeclarations(e.target.value)} rows={4} maxLength={262144}
        placeholder={CSV_DECLARATION_TEMPLATE} />
    </div>}
    {job && <>
      <p>Source version: <code>{job.source_version.slice(0,12)}</code></p>
      <p role="status" aria-live="polite">{job.progress.phase}: {job.progress.records} records · {job.progress.canonical_bytes} canonical bytes{job.state === 'building' ? ' · Building or recovering local index' : ''}</p>
      {job.code && <p role="alert">{job.message || job.code}</p>}
      {job.review_ready && active && <>
        <h4 className="font-medium">Select complete records</h4>
        <p>{PREVIEW_ALL_FIELDS_WARNING}</p>
        <p role="status" aria-live="polite">Budget: {budget.rows}/100 rows · {budget.fields}/25 complete-row fields · {budget.known ? budget.canonical_bytes : job.selection.canonical_bytes}/250000 canonical bytes</p>
        {budget.code && <p role="alert">{budget.code}</p>}
        <fieldset disabled={busy || fixed} className="space-y-2">
          <legend>Display columns</legend>
          {job.columns.map(column => <label key={column} className="mr-4 inline-flex items-center gap-2">
            <input type="checkbox" checked={columns.includes(column)} onChange={() => { setColumns(current => current.includes(column) ? current.filter(c => c !== column) : [...current,column]); setPermission(false); setAccuracy(false); }} />{column}
          </label>)}
        </fieldset>
        <div className="overflow-x-auto">
          <table className="w-full text-sm"><caption className="sr-only">Local complete rows in immutable leaf order</caption>
            <thead><tr><th scope="col">Select leaf</th>{job.columns.map(column => <th scope="col" key={column}>{column}</th>)}<th scope="col">Eligibility</th></tr></thead>
            <tbody>{page?.items.map(row => <tr key={row.leaf_index}>
              <td><label><input type="checkbox" aria-label={`Select leaf ${row.leaf_index}`} checked={selected.includes(row.leaf_index)} disabled={busy || fixed || !!row.code}
                onChange={() => toggle(row.leaf_index)} onKeyDown={e => { if (e.key === ' ') { e.preventDefault(); toggle(row.leaf_index); } }} /> {row.leaf_index}</label></td>
              {job.columns.map(column => <td key={column} className="max-w-xs break-words whitespace-pre-wrap" data-preview-cell><span>{inertPreviewText(row.cells?.[column])}</span></td>)}
              <td>{row.code || 'Within technical limits'}</td>
            </tr>)}</tbody>
          </table>
        </div>
        <div className="flex gap-2">
          <Button type="button" variant="outline" disabled={busy || start === 0} onClick={() => setStart(Math.max(0,start-25))}>Previous rows</Button>
          <Button type="button" variant="outline" disabled={busy || page?.next == null} onClick={() => setStart(page!.next!)}>Next rows</Button>
          <Button type="button" disabled={busy || fixed || !selected.length || !columns.length || !!budget.code} onClick={() => run(async () => {
            const value = await previewBuildApi.selection(job.job_id,selected,columns); setJob(value); setPermission(false); setRestricted(false); setAccuracy(false);
          })}>Save selection</Button>
        </div>
      </>}
      {active && (job.review_ready || !!job.publication) && <>
        <fieldset disabled={busy} className="space-y-3">
          <legend className="font-medium">Seller publication confirmation</legend>
          <p>You are responsible for what you publish. Confirm that you have the right to show these exact selected rows publicly.</p>
          <label className="block">Rights basis <select aria-label="Rights basis" value={rights} onChange={e => { setRights(e.target.value as typeof rights); setPermission(false); setAccuracy(false); }}>
            <option value="">Choose rights basis</option><option value="owner">I own the rights</option><option value="licensed">Licensed for public preview</option><option value="public_domain">Public domain</option><option value="other_authorized">Other authorization</option>
          </select></label>
          <label className="flex items-start gap-2"><input type="checkbox" checked={permission} onChange={e => setPermission(e.target.checked)} />{PREVIEW_PERMISSION}</label>
          <label className="flex items-start gap-2"><input type="checkbox" checked={restricted} onChange={e => setRestricted(e.target.checked)} />I confirm I reviewed these exact rows for restricted or third-party content.</label>
          <Button type="button" disabled={busy || fixed || selectionChanged || !job.selection.rows || !readyConsent} onClick={() => run(async () => {
            await previewBuildApi.policy(job.job_id,consent); setJob(await previewBuildApi.status(job.job_id));
          })}>Confirm selected rows</Button>
        </fieldset>
        {job.policy?.passed && <p role="status">Seller confirmations recorded for these exact selected rows.</p>}
        {job.policy?.passed && originReview?.(job,setJob)}
        {job.receipts.length === 2 && job.state !== 'retired' && <div className="space-y-3">
          <h4 className="font-medium">Confirm preview</h4>
          {marketplaceSummary && <div className="rounded-md border p-3">
            <p className="font-medium">Current ai.market At a glance ({marketplaceSummary.state})</p>
            <pre className="mt-2 max-h-64 overflow-auto whitespace-pre-wrap text-xs">{JSON.stringify(marketplaceSummary.at_a_glance, null, 2)}</pre>
            <p className="mt-2 text-sm">{marketplaceSummary.approval_text}</p>
          </div>}
          <p>Root: <code>{job.commitment?.dataset_merkle_root.slice(0,12)}</code> · Sample: <code>{job.publication?.sample_hash.slice(0,12)}</code></p>
          <p>{job.selection.rows} selected records · Display fields: {job.selection.display_columns.join(', ')}</p>
          <p>Origin: {job.origin}</p>
          <p>Registered key fingerprint: {job.signing?.fingerprint || job.signing?.code || "Install signing key unavailable"}</p>
          <label className="flex gap-2"><input type="checkbox" checked={accuracy} onChange={e => setAccuracy(e.target.checked)} />I confirm the approved metadata is accurate for this source and selection.</label>
          <Button type="button" disabled={busy || !!job.candidate || !marketplaceSummary || !readyConsent || !accuracy} onClick={() => run(async () => {
            setJob(await previewBuildApi.candidate(job.job_id,{...consent,metadata_accuracy_confirmed:accuracy}));
          })}>Approve At a glance and prepare signed preview</Button>
        </div>}
      </>}
      {job.candidate && <div className="space-y-2">
        <p>Registered key fingerprint: <code>{job.candidate.key_fingerprint}</code></p>
        <p>ai.market candidate digest: <code>{job.candidate.request_digest.slice(0,12)}</code></p>
        {!job.outcome && active && <Button type="button" disabled={busy} onClick={() => run(async () => { setJob(await previewBuildApi.submit(job.job_id)); })}>Submit verified preview to ai.market</Button>}
      </div>}
      {job.outcome && <p role="status">{job.outcome}</p>}
      {job.marketplace?.listing_url && <p>Live listing: <a className="underline" href={job.marketplace.listing_url} target="_blank" rel="noreferrer">{job.marketplace.listing_url}</a> · state: {job.marketplace.state}</p>}
      {job.state === 'submitted' && <Button type="button" variant="outline" disabled={busy || !readyConsent || !accuracy} onClick={() => run(async () => {
        const value = await previewBuildApi.refresh(job.job_id,{...consent,metadata_accuracy_confirmed:accuracy});
        setJob(value); setSelected(value.selection.leaf_indices); setColumns(value.selection.display_columns); setSizes(value.selection.row_sizes || {}); setStart(0);
        setPermission(false); setRestricted(false); setAccuracy(false);
      })}>Refresh attestation</Button>}
      {job.prior_job_id && !retirePreviousPending && <p>Prior hosting retirement remains separate. <Button type="button" variant="outline" disabled={busy} onClick={() => setRetirePreviousPending(true)}>Retire previous package</Button></p>}
      {job.prior_job_id && retirePreviousPending && <div role="group" aria-label="Confirm previous package retirement" className="space-y-2 rounded-md border border-destructive p-3">
        <p role="alert">Retiring the previous package will make the public verified preview disappear until a replacement is accepted.</p>
        <div className="flex gap-2">
          <Button type="button" variant="outline" disabled={busy} onClick={() => run(async () => {
            await previewBuildApi.withdraw(job.prior_job_id!);
            setRetirePreviousPending(false);
          })}>Confirm retire previous package</Button>
          <Button type="button" variant="outline" disabled={busy} onClick={() => setRetirePreviousPending(false)}>Keep previous package</Button>
        </div>
      </div>}
      {job.state === 'withdrawn' && <p role="status">Local package retired. Remove the external object at {job.origin || job.publication?.relative_path}, then retry withdrawal to verify GET/OPTIONS retirement receipts.</p>}
      {job.publication && <Button type="button" variant="outline" disabled={busy || job.state === 'retired'} onClick={() => run(async () => { try { await previewBuildApi.withdraw(job.job_id); } finally { setJob(await previewBuildApi.status(job.job_id)); } })}>Withdraw preview</Button>}
      {job.state === 'retired' && <Button type="button" disabled={busy} onClick={prepare}>Replace preview</Button>}
      {job.state === 'retired' && <p role="status">The marketplace preview and hosting are retired. Prepare a replacement with fresh consent.</p>}
    </>}
  </section>;
}
