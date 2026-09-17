import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { PreviewOriginReview } from './PreviewOriginReview';
import { previewBuildApi } from '@/lib/api';
import { fixture } from '@/test/previewFixture';
vi.mock('@/lib/api',()=>({previewBuildApi:{package:vi.fn(),originCheck:vi.fn(),download:vi.fn()}}));
const packaged=()=>({...fixture(),state:'packaged',policy:{policy:'aim-preview-policy-v1',version:'1.0.0',passed:true,reason_codes:[]},publication:{destination:'local' as const,local_directory:'/app/preview/public',relative_path:'previews/version/hash.json',package_sha256:'a'.repeat(64),byte_count:100,sample_hash:'b'.repeat(64),disclosure_version:'version'}});
beforeEach(()=>vi.clearAllMocks());afterEach(cleanup);
it('shows S3/R2 non-claim and offers only app-managed directory or export',()=>{
  render(<PreviewOriginReview job={{...fixture(),policy:{policy:'aim-preview-policy-v1',version:'1.0.0',passed:true,reason_codes:[]}}} onChange={vi.fn()} />);
  expect(screen.getByText(/A connected S3\/R2 bucket is not automatically writable or public/)).toBeInTheDocument();
  expect(screen.getAllByRole('radio')).toHaveLength(2);
  expect(screen.queryByLabelText(/directory path/i)).not.toBeInTheDocument();
});
it('sends bounded destination choice and displays the exact path',async()=>{
  const changed=vi.fn();vi.mocked(previewBuildApi.package).mockResolvedValue(packaged());
  const {rerender}=render(<PreviewOriginReview job={{...fixture(),policy:packaged().policy}} onChange={changed} />);
  fireEvent.click(screen.getByRole('radio',{name:'Export package for my own hosting'}));
  fireEvent.click(screen.getByRole('button',{name:'Write preview package'}));
  await waitFor(()=>expect(previewBuildApi.package).toHaveBeenCalledWith('job','export'));
  rerender(<PreviewOriginReview job={packaged()} onChange={changed} />);
  expect(screen.getByText('/app/preview/public')).toBeInTheDocument();
  expect(screen.getByText('previews/version/hash.json')).toBeInTheDocument();
});
it('checks seller URL and displays GET and OPTIONS receipts',async()=>{
  const job=packaged(), changed=vi.fn();
  const checked={...job,origin:'https://seller.example/previews/version/hash.json',receipts:[{url:'https://seller.example/previews/version/hash.json',method:'GET' as const,status:200,captured_at:'2026-09-17',headers:{'cache-control':'no-store'},no_set_cookie:true},{url:'https://seller.example/previews/version/hash.json',method:'OPTIONS' as const,status:204,captured_at:'2026-09-17',headers:{'cache-control':'no-store'},no_set_cookie:true}]};
  vi.mocked(previewBuildApi.originCheck).mockResolvedValue(checked);
  const {rerender}=render(<PreviewOriginReview job={job} onChange={changed} />);
  fireEvent.change(screen.getByLabelText('Seller HTTPS package URL'),{target:{value:checked.origin}});
  fireEvent.click(screen.getByRole('button',{name:'Check GET and OPTIONS'}));
  await waitFor(()=>expect(changed).toHaveBeenCalledWith(checked));
  rerender(<PreviewOriginReview job={checked} onChange={changed} />);
  expect(screen.getByText(/GET: 200/)).toBeInTheDocument();expect(screen.getByText(/OPTIONS: 204/)).toBeInTheDocument();
  expect(screen.getByText(/Marketplace preview submission still awaits backend support/)).toBeInTheDocument();
});
it('announces origin failure and keeps retry available',async()=>{
  vi.mocked(previewBuildApi.originCheck).mockRejectedValue(new Error('cors_origin'));
  render(<PreviewOriginReview job={packaged()} onChange={vi.fn()} />);
  fireEvent.change(screen.getByLabelText('Seller HTTPS package URL'),{target:{value:'https://seller.example/p'}});
  fireEvent.click(screen.getByRole('button',{name:'Check GET and OPTIONS'}));
  expect(await screen.findByRole('alert')).toHaveTextContent('cors_origin');
  expect(screen.getByRole('alert')).toHaveFocus();expect(screen.getByRole('button',{name:'Check GET and OPTIONS'})).toBeEnabled();
});
it('explains external retirement for exported package',()=>{
  const job=packaged();render(<PreviewOriginReview job={{...job,publication:{...job.publication,destination:'export'}}} onChange={vi.fn()} />);
  expect(screen.getByText(/You must remove that external object when withdrawing/)).toBeInTheDocument();
  expect(screen.getByRole('button',{name:'Download package'})).toBeInTheDocument();
});
