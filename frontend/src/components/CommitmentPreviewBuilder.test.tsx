import { fireEvent, render, screen, waitFor, cleanup } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { CommitmentPreviewBuilder } from './CommitmentPreviewBuilder';
import { previewBuildApi, type PreviewBuildStatus } from '@/lib/api';
import { PREVIEW_PERMISSION, PREVIEW_MEMBERSHIP_DISCLAIMER, PREVIEW_ALL_FIELDS_WARNING } from '@/lib/disclosure';
vi.mock('@/lib/api', () => ({ previewBuildApi: { latest: vi.fn(),create: vi.fn(),status: vi.fn(),rows: vi.fn(),selection:vi.fn(),cancel:vi.fn(),policy:vi.fn(),candidate:vi.fn(),submit:vi.fn(),withdraw:vi.fn(),refresh:vi.fn(),marketplaceSummary:vi.fn() } }));
import { fixture } from '@/test/previewFixture';
let job: PreviewBuildStatus;
beforeEach(() => {
  vi.clearAllMocks();job=fixture();
  vi.mocked(previewBuildApi.latest).mockResolvedValue(null);
  vi.mocked(previewBuildApi.create).mockResolvedValue(job);
  vi.mocked(previewBuildApi.status).mockImplementation(async () => job);
  vi.mocked(previewBuildApi.rows).mockResolvedValue({items:[
    {leaf_index:0,canonical_bytes:40,code:null,cells:{value:'<script>alert(1)</script>'}},
    {leaf_index:1,canonical_bytes:40,code:null,cells:{value:'=SUM(1,2)'}},
    {leaf_index:2,canonical_bytes:40,code:null,cells:{value:'https://hostile.example'}},
  ],total:3,next:null});
  vi.mocked(previewBuildApi.selection).mockImplementation(async (_,indices,columns) => {
    job={...job,state:'selected',selection:{leaf_indices:indices,display_columns:columns,rows:indices.length,fields:1,canonical_bytes:indices.length*40}};return job;
  });
  vi.mocked(previewBuildApi.cancel).mockImplementation(async () => ({...job,state:'cancelled',review_ready:false}));
  vi.mocked(previewBuildApi.marketplaceSummary).mockResolvedValue({summary_id:'summary',state:'approved',status:'approved',at_a_glance:{profile:'aim-listing-enrichment-profile-v2'},approval_text:'Approve current At a glance.'});
});
afterEach(cleanup);
async function begin() {
  render(<CommitmentPreviewBuilder datasetId="ds" metadataApproved />);
  await waitFor(() => expect(screen.getByRole('button',{name:'Prepare verified preview'})).toBeEnabled());
  fireEvent.click(screen.getByRole('button',{name:'Prepare verified preview'}));
  await screen.findByLabelText('Select leaf 0');
}
it('defaults to no sample and never creates a job on mount',async () => {
  render(<CommitmentPreviewBuilder datasetId="ds" metadataApproved />);
  await screen.findByRole('button',{name:'No sample'});
  expect(previewBuildApi.create).not.toHaveBeenCalled();
  expect(screen.getByText(PREVIEW_MEMBERSHIP_DISCLAIMER)).toBeInTheDocument();
});
it('neutralises scalar and nested controls at render time without changing selection or hashes',async () => {
  const scalar='<script>alert(1)</script> https://hostile.example \u0000\u007f\u202e\u200b\ud800';
  const nested={
    ['key\u0000\u202e\ud800']:'</style><style>bad</style> https://nested.example \u007f\u200b\ud800',
    child:'<script>nested</script>\u0000',
  };
  const rows={items:[
    {leaf_index:0,canonical_bytes:40,code:null,cells:{value:scalar}},
    {leaf_index:1,canonical_bytes:40,code:null,cells:{value:nested}},
  ],total:2,next:null};
  vi.mocked(previewBuildApi.rows).mockResolvedValue(rows);
  const sourceRows=structuredClone(rows);
  const commitment=structuredClone(job.commitment);
  await begin();
  expect(screen.getByText('<script>alert(1)</script> https://hostile.example �����')).toBeInTheDocument();
  expect(screen.getByText('{"key���":"</style><style>bad</style> https://nested.example ���","child":"<script>nested</script>�"}')).toBeInTheDocument();
  expect(document.querySelectorAll('[data-preview-cell] script, [data-preview-cell] a, [data-preview-cell] iframe, [data-preview-cell] style, [data-preview-cell] [style]')).toHaveLength(0);
  fireEvent.click(screen.getByLabelText('Select leaf 1'));
  fireEvent.click(screen.getByRole('button',{name:'Save selection'}));
  await waitFor(() => expect(previewBuildApi.selection).toHaveBeenCalledWith('job',[1],['value']));
  expect(rows).toEqual(sourceRows);
  expect(job.commitment).toEqual(commitment);
  expect(screen.getByText(PREVIEW_ALL_FIELDS_WARNING)).toBeInTheDocument();
});
it('supports keyboard selection and updates whole-record budgets without trimming',async () => {
  await begin();
  const row=screen.getByLabelText('Select leaf 0');row.focus();
  expect(row).toHaveFocus();fireEvent.keyDown(row,{key:' '});
  expect(row).toBeChecked();expect(screen.getByText(/Budget: 1\/100 rows.*40\/250000/)).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button',{name:'Save selection'}));
  await waitFor(() => expect(previewBuildApi.selection).toHaveBeenCalledWith('job',[0],['value']));
});
it('requires exact seller confirmations before recording policy v2',async () => {
  await begin();fireEvent.click(screen.getByLabelText('Select leaf 0'));fireEvent.click(screen.getByRole('button',{name:'Save selection'}));
  await waitFor(() => expect(previewBuildApi.selection).toHaveBeenCalled());
  expect(screen.getByRole('button',{name:'Confirm selected rows'})).toBeDisabled();
  fireEvent.change(screen.getByLabelText('Rights basis'),{target:{value:'owner'}});
  fireEvent.click(screen.getByLabelText(PREVIEW_PERMISSION));
  fireEvent.click(screen.getByLabelText(/I confirm I reviewed these exact rows/));
  vi.mocked(previewBuildApi.policy).mockImplementation(async () => {
    const policy={policy:'aim-preview-policy-v2',version:'2.0.0',passed:true,reason_codes:[]};job={...job,state:'scanned',policy};return policy;
  });
  fireEvent.click(screen.getByRole('button',{name:'Confirm selected rows'}));
  expect(await screen.findByText(/Seller confirmations recorded/)).toBeInTheDocument();
});
it('recovers an in-progress job after reload and allows cancellation',async () => {
  job={...job,state:'building',review_ready:false,progress:{...job.progress,phase:'reading'}};
  vi.mocked(previewBuildApi.latest).mockResolvedValue(job);
  render(<CommitmentPreviewBuilder datasetId="ds" metadataApproved />);
  await screen.findByText(/Building or recovering local index/);
  expect(previewBuildApi.create).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button',{name:'Cancel preview build'}));
  await waitFor(() => expect(previewBuildApi.cancel).toHaveBeenCalledWith('job'));
});
it('announces errors, focuses the message and offers retry',async () => {
  vi.mocked(previewBuildApi.create).mockRejectedValueOnce(new Error('parsing_declaration_required'));
  render(<CommitmentPreviewBuilder datasetId="ds" metadataApproved />);
  await waitFor(() => expect(screen.getByRole('button',{name:'Prepare verified preview'})).toBeEnabled());
  fireEvent.click(screen.getByRole('button',{name:'Prepare verified preview'}));
  expect(await screen.findByRole('alert')).toHaveTextContent('parsing_declaration_required');
  expect(screen.getByRole('alert')).toHaveFocus();
  expect(screen.getByLabelText('Missing parsing declarations')).toBeInTheDocument();
});
it('shows a submitted marketplace state and key fingerprint on recovery',async () => {
  job={...job,state:'submitted',candidate:{kind:'marketplace_candidate',request_digest:'d'.repeat(64),key_fingerprint:'f'.repeat(64),sample_hash:'s'.repeat(64),disclosure_version:'v'},outcome:'Verified preview is visible on ai.market.',marketplace:{state:'visible',listing_url:'https://ai.market/listings/synthetic'}};
  vi.mocked(previewBuildApi.latest).mockResolvedValue(job);
  render(<CommitmentPreviewBuilder datasetId="ds" metadataApproved />);
  expect(await screen.findByText(job.outcome!)).toBeInTheDocument();
  expect(screen.getByText('f'.repeat(64))).toBeInTheDocument();
  expect(previewBuildApi.submit).not.toHaveBeenCalled();
});

it('reviews source, origin and fingerprint before signing, then records pending submission',async () => {
  job={...job,state:'hosted',review_ready:false,selection:{leaf_indices:[0],display_columns:['value'],rows:1,fields:1,canonical_bytes:40},
    policy:{policy:'aim-preview-policy-v2',version:'2.0.0',passed:true,reason_codes:[]},
    publication:{destination:'export',local_directory:'/app/exports',relative_path:'previews/v/h.json',package_sha256:'a'.repeat(64),byte_count:100,sample_hash:'b'.repeat(64),disclosure_version:'v'},
    origin:'https://seller.example/previews/v/h.json',signing:{fingerprint:'f'.repeat(64),code:null},
    receipts:[{url:'https://seller.example/previews/v/h.json',method:'GET',status:200,captured_at:'2026-09-17',headers:{},no_set_cookie:true},{url:'https://seller.example/previews/v/h.json',method:'OPTIONS',status:204,captured_at:'2026-09-17',headers:{},no_set_cookie:true}]};
  vi.mocked(previewBuildApi.latest).mockResolvedValue(job);
  vi.mocked(previewBuildApi.candidate).mockImplementation(async()=>{
    job={...job,state:'signed_candidate',candidate:{kind:'marketplace_candidate',key_fingerprint:'f'.repeat(64),request_digest:'d'.repeat(64),sample_hash:'b'.repeat(64),disclosure_version:'v'}};return job;
  });
  vi.mocked(previewBuildApi.submit).mockImplementation(async()=>({...job,state:'submitted',outcome:'Verified preview is visible on ai.market.',marketplace:{state:'visible',listing_url:'https://ai.market/listings/synthetic'}}));
  render(<CommitmentPreviewBuilder datasetId="ds" metadataApproved />);
  expect(await screen.findByRole('button',{name:'Approve At a glance and prepare signed preview'})).toBeDisabled();
  expect(await screen.findByText(/Current ai.market At a glance/)).toBeInTheDocument();
  expect(screen.getByText(/Registered key fingerprint:/)).toHaveTextContent('f'.repeat(64));
  expect(screen.getByText('Origin: https://seller.example/previews/v/h.json')).toBeInTheDocument();
  fireEvent.change(screen.getByLabelText('Rights basis'),{target:{value:'owner'}});
  fireEvent.click(screen.getByLabelText(PREVIEW_PERMISSION));
  fireEvent.click(screen.getByLabelText(/I confirm I reviewed these exact rows/));
  fireEvent.click(screen.getByLabelText(/I confirm the approved metadata is accurate/));
  fireEvent.click(screen.getByRole('button',{name:'Approve At a glance and prepare signed preview'}));
  fireEvent.click(await screen.findByRole('button',{name:'Submit verified preview to ai.market'}));
  expect(await screen.findByText('Verified preview is visible on ai.market.')).toBeInTheDocument();
  expect(screen.getByRole('link',{name:'https://ai.market/listings/synthetic'})).toBeInTheDocument();
  expect(previewBuildApi.candidate).toHaveBeenCalledWith('job',{rights_basis:'owner',public_preview_permission:true,restricted_content_confirmed:true,metadata_accuracy_confirmed:true});
});

it('offers a fresh build after an expired review without polling or reading rows',async () => {
  job={...job,state:'expired',code:'review_expired',review_ready:false};
  vi.mocked(previewBuildApi.latest).mockResolvedValue(job);
  render(<CommitmentPreviewBuilder datasetId="ds" metadataApproved />);
  expect(await screen.findByRole('alert')).toHaveTextContent('review_expired');
  expect(screen.getByRole('button',{name:'Prepare verified preview'})).toBeEnabled();
  expect(previewBuildApi.rows).not.toHaveBeenCalled();
  await new Promise(resolve => setTimeout(resolve, 850));
  expect(previewBuildApi.status).not.toHaveBeenCalled();
});

it('keeps withdrawal retry available and hides preparation until retirement',async () => {
  job={...job,state:'withdrawn',code:'external_retirement_pending',review_ready:false,
    publication:{destination:'export',local_directory:'/app/exports',relative_path:'previews/v/h.json',package_sha256:'a'.repeat(64),byte_count:100,sample_hash:'b'.repeat(64),disclosure_version:'v'}};
  vi.mocked(previewBuildApi.latest).mockResolvedValue(job);
  vi.mocked(previewBuildApi.withdraw).mockImplementation(async () => {
    job={...job,state:'retired',code:null}; return job;
  });
  render(<CommitmentPreviewBuilder datasetId="ds" metadataApproved />);
  const retry = await screen.findByRole('button',{name:'Withdraw preview'});
  expect(retry).toBeEnabled();
  expect(screen.queryByRole('button',{name:'Prepare verified preview'})).not.toBeInTheDocument();
  expect(screen.queryByRole('button',{name:'Replace preview'})).not.toBeInTheDocument();
  fireEvent.click(retry);
  expect(await screen.findByRole('button',{name:'Replace preview'})).toBeEnabled();
});
