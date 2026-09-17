import { useEffect, useRef, useState } from 'react';
import { Button } from '@/components/ui/button';
import { Textarea } from '@/components/ui/textarea';
import { previewBuildApi, type PreviewBuildStatus, type PreviewConsent, type LocalPreviewPage, type PreviewCreateOptions } from '@/lib/api';
import { inertPreviewText, previewBudget, PREVIEW_ALL_FIELDS_WARNING, PREVIEW_MEMBERSHIP_DISCLAIMER, PREVIEW_PERMISSION } from '@/lib/disclosure';

interface Props {
  datasetId: string;
  metadataApproved: boolean;
  approvedMetadataDigest?: string;
  onStatus?: (status: string) => void;
  originReview?: (job: PreviewBuildStatus, onChange: (job: PreviewBuildStatus) => void) => React.ReactNode;
}

export function CommitmentPreviewBuilder({ datasetId, metadataApproved, approvedMetadataDigest, onStatus, originReview }: Props) {
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
    if (!job || job.review_ready || ['cancelled', 'failed', 'retired', 'withdrawn'].includes(job.state)) return;
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

  const run = async (action: () => Promise<void>) => {
    setBusy(true); setError(null);
    try { await action(); } catch (e) {
      const message = e instanceof Error ? e.message : 'preview_operation_failed';
      setError(message);
      if (message === 'parsing_declaration_required') setDeclarationsNeeded(true);
    } finally { setBusy(false); }
  };
  const prepare = () => run(async () => {
    let options: PreviewCreateOptions = { dataset_id: datasetId };
    if (declarationsNeeded) {
      try { options = { ...JSON.parse(declarations), dataset_id: datasetId }; }
      catch { throw new Error('Enter valid parsing declarations.'); }
    }
    if (approvedMetadataDigest) await previewBuildApi.approveMetadata(datasetId, approvedMetadataDigest);
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
  const metadataChanged = !!job?.candidate && !!approvedMetadataDigest && job.approved_metadata_digest !== approvedMetadataDigest;
  const fixed = !!job?.publication || !!job?.candidate;
  const active = !!job && !['cancelled','failed','retired','withdrawn'].includes(job.state);
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
      {!active && <Button type="button" disabled={busy || recovering || !metadataApproved} onClick={prepare}>Prepare verified preview</Button>}
      {active && !job?.publication && <Button type="button" variant="outline" disabled={busy} onClick={() => run(async () => { setJob(await previewBuildApi.cancel(job!.job_id)); })}>Cancel preview build</Button>}
    </div>
    {declarationsNeeded && !active && <div className="space-y-2">
      <label htmlFor="preview-parsing">Missing parsing declarations</label>
      <p className="text-sm">Declare the original file format and complete logical schema. CSV also needs UTF-8, delimiter, quote, escape, header, C locale and a null token. Schema entries are [name, type, nullable, parameters].</p>
      <Textarea id="preview-parsing" value={declarations} onChange={e => setDeclarations(e.target.value)} rows={4} maxLength={262144}
        placeholder={'{"parsing":{"format":"ndjson","encoding":"utf-8"},"schema_descriptors":[["name","string",true,{}]]}'} />
    </div>}
    {job && <>
      <p>Source version: <code>{job.source_version.slice(0,12)}</code></p>
      <p role="status" aria-live="polite">{job.progress.phase}: {job.progress.records} records · {job.progress.canonical_bytes} canonical bytes{!job.review_ready && active ? ' · Building or recovering local index' : ''}</p>
      {job.code && <p role="alert">{job.code}</p>}
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
              <td>{row.code || 'Eligible for local scan'}</td>
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
        <fieldset disabled={busy || !!job.candidate} className="space-y-3">
          <legend className="font-medium">Rights and local policy review</legend>
          <label className="block">Rights basis <select aria-label="Rights basis" value={rights} onChange={e => { setRights(e.target.value as typeof rights); setPermission(false); setAccuracy(false); }}>
            <option value="">Choose rights basis</option><option value="owner">I own the rights</option><option value="licensed">Licensed for public preview</option><option value="public_domain">Public domain</option><option value="other_authorized">Other authorization</option>
          </select></label>
          <label className="flex items-start gap-2"><input type="checkbox" checked={permission} onChange={e => setPermission(e.target.checked)} />{PREVIEW_PERMISSION}</label>
          <label className="flex items-start gap-2"><input type="checkbox" checked={restricted} onChange={e => setRestricted(e.target.checked)} />I confirm these selected records contain no prohibited or third-party restricted content.</label>
          <Button type="button" disabled={busy || fixed || selectionChanged || !job.selection.rows || !readyConsent} onClick={() => run(async () => {
            await previewBuildApi.policy(job.job_id,consent); setJob(await previewBuildApi.status(job.job_id));
          })}>Run local policy scan</Button>
        </fieldset>
        {job.policy && <p role="status">{job.policy.policy} / {job.policy.version}: {job.policy.passed ? 'Passed local scan; this is not clearance.' : job.policy.reason_codes.join(', ')}</p>}
        {job.policy?.passed && originReview?.(job,setJob)}
        {job.receipts.length === 2 && job.state !== 'retired' && <div className="space-y-3">
          <h4 className="font-medium">Confirm preview</h4>
          <p>Root: <code>{job.commitment?.dataset_merkle_root.slice(0,12)}</code> · Sample: <code>{job.publication?.sample_hash.slice(0,12)}</code></p>
          <p>{job.selection.rows} selected records · Display fields: {job.selection.display_columns.join(', ')}</p>
          <p>Origin: {job.origin}</p>
          <p>Registered key fingerprint: {job.signing?.fingerprint || job.signing?.code || "Registration evidence required"}</p>
          <label className="flex gap-2"><input type="checkbox" checked={accuracy} onChange={e => setAccuracy(e.target.checked)} disabled={!!job.candidate} />I confirm the approved metadata is accurate for this source and selection.</label>
          <Button type="button" disabled={busy || !!job.candidate || !metadataApproved || !readyConsent || !accuracy} onClick={() => run(async () => {
            if (approvedMetadataDigest) await previewBuildApi.approveMetadata(datasetId, approvedMetadataDigest);
            setJob(await previewBuildApi.candidate(job.job_id,{...consent,metadata_accuracy_confirmed:accuracy}));
          })}>Prepare signed preview</Button>
        </div>}
      </>}
      {metadataChanged && <p role="alert">Metadata changed. Withdraw and replace the preview with fresh consent.</p>}
      {job.candidate && <div className="space-y-2">
        <p>Registered key fingerprint: <code>{job.candidate.key_fingerprint}</code></p>
        <p>Local candidate digest: <code>{job.candidate.request_digest.slice(0,12)}</code></p>
        <p>Local candidate only; no public marketplace sample is active.</p>
        {!job.outcome && active && <Button type="button" disabled={busy || metadataChanged || !metadataApproved} onClick={() => run(async () => { setJob(await previewBuildApi.submit(job.job_id)); })}>Finish local preparation</Button>}
      </div>}
      {job.outcome && !metadataChanged && <p role="status">{job.outcome}</p>}
      {job.candidate && active && <Button type="button" variant="outline" disabled={busy || metadataChanged || !readyConsent || !accuracy} onClick={() => run(async () => {
        const value = await previewBuildApi.refresh(job.job_id,{...consent,metadata_accuracy_confirmed:accuracy});
        setJob(value); setSelected(value.selection.leaf_indices); setColumns(value.selection.display_columns); setSizes(value.selection.row_sizes || {}); setStart(0);
        setPermission(false); setRestricted(false); setAccuracy(false);
      })}>Refresh attestation</Button>}
      {job.prior_job_id && <p>Prior hosting retirement remains separate. <Button type="button" variant="outline" disabled={busy} onClick={() => run(async () => {
        await previewBuildApi.withdraw(job.prior_job_id!);
      })}>Retire previous package</Button></p>}
      {job.state === 'withdrawn' && <p role="status">Local package retired. Remove the external object at {job.origin || job.publication?.relative_path}, then retry withdrawal to verify GET/OPTIONS retirement receipts.</p>}
      {job.publication && <Button type="button" variant="outline" disabled={busy || job.state === 'retired'} onClick={() => run(async () => { try { await previewBuildApi.withdraw(job.job_id); } finally { setJob(await previewBuildApi.status(job.job_id)); } })}>Withdraw preview</Button>}
      {job.state === 'retired' && <Button type="button" disabled={busy || !metadataApproved} onClick={prepare}>Replace preview</Button>}
      {job.state === 'retired' && <p role="status">Hosting retired; marketplace submission was never made. Prepare a replacement preview with fresh consent.</p>}
    </>}
  </section>;
}
