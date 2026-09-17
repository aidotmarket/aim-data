import type { PreviewBuildStatus } from '@/lib/api';
export const fixture = (): PreviewBuildStatus => ({
  job_id:'job',dataset_id:'ds',source_version:'a'.repeat(64),state:'ready',code:null,review_ready:true,
  progress:{phase:'ready',records:3,canonical_bytes:120,elapsed_seconds:1},columns:['value'],
  selection:{leaf_indices:[],display_columns:[],rows:0,fields:1,canonical_bytes:0},caps:{rows:100,fields:25,canonical_bytes:250000},
  commitment:{schema_digest:'s'.repeat(43),dataset_merkle_root:'r'.repeat(43),leaf_count:3},policy:null,publication:null,origin:null,receipts:[],candidate:null,outcome:null,
});
