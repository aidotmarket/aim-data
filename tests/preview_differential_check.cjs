// Independent ECMAScript byte and signature check of the backend-owned corpus.
const fs = require('fs'), path = require('path'), crypto = require('crypto');
if (process.argv.length !== 3) throw Error('one contract corpus directory required');
const root = process.argv[2];
const manifest = JSON.parse(fs.readFileSync(path.join(root, 'manifest.json')));
const sha = bytes => crypto.createHash('sha256').update(bytes).digest('hex');
const codePoints = (a, b) => {
  const x = Array.from(a, c => c.codePointAt(0)), y = Array.from(b, c => c.codePointAt(0));
  for (let i = 0; i < Math.min(x.length, y.length); i++) if (x[i] !== y[i]) return x[i] - y[i];
  return x.length - y.length;
};
function canon(v) {
  if (typeof v === 'number' && (!Number.isInteger(v) || !Number.isSafeInteger(v))) throw Error('unsafe_integer');
  if (v === null || typeof v !== 'object') return JSON.stringify(v);
  if (Array.isArray(v)) return '[' + v.map(canon).join(',') + ']';
  const keys = Object.keys(v).sort();
  if (JSON.stringify(keys) !== JSON.stringify([...keys].sort(codePoints))) throw Error('noncanonical_key_order');
  return '{' + keys.map(k => JSON.stringify(k) + ':' + canon(v[k])).join(',') + '}';
}
const bytes = v => Buffer.from(canon(v));
const without = (v, key) => { const copy = structuredClone(v); delete copy[key]; return copy; };
const domain = (name, v) => Buffer.concat([Buffer.from(name + '\0'), bytes(v)]);
const disclosure = b => domain('aim-preview-disclosure-signature-v1', b);
const commitment = c => domain('aim-dataset-commitment-signature-v1', without(c, 'seller_signature'));
const proof = (c, p) => domain('aim-preview-proof-signature-v1', {
  commitment_id: c.commitment_id, listing_id: c.listing_id,
  seller_dataset_version: c.seller_dataset_version, schema_digest: c.schema_digest,
  dataset_merkle_root: c.dataset_merkle_root, proof: without(p, 'signature')
});
const envelope = e => {
  const copy = without(e, 'signature');
  for (const key of copy.signer_keys) if (key.valid_until == null) delete key.valid_until;
  return domain('aim-preview-platform-envelope-v1', copy);
};
function model(v, kind) {
  const copy = structuredClone(v);
  if (kind === 'proof') copy.signature_algorithm ??= 'ed25519';
  if (kind === 'envelope') for (const key of copy.signer_keys) key.valid_until ??= null;
  if (kind === 'commitment') {
    copy.canonicalization_profile ??= 'aim-dataset-merkle-v1';
    copy.hash_algorithm ??= 'sha-256';
    copy.signature_algorithm ??= 'ed25519';
    copy.previous_commitment_id ??= null;
    copy.proofs ??= [];
    copy.proofs = copy.proofs.map(p => model(p, 'proof'));
  }
  return copy;
}
const seed = label => crypto.createHash('sha256').update(label).digest();
const privateKey = bytes => crypto.createPrivateKey({key: Buffer.concat([Buffer.from('302e020100300506032b657004220420', 'hex'), bytes]), format: 'der', type: 'pkcs8'});
const publicRaw = key => crypto.createPublicKey(key).export({format: 'der', type: 'spki'}).subarray(-32);
const sellerKey = privateKey(seed('preview-contract-cross-repo-v1 synthetic signing seed'));
const platformKey = privateKey(seed('preview-contract-cross-repo-v1 synthetic platform signing seed'));
const sellerPublic = publicRaw(sellerKey), platformPublic = publicRaw(platformKey);
const publicKey = raw => crypto.createPublicKey({key: Buffer.concat([Buffer.from('302a300506032b6570032100', 'hex'), raw]), format: 'der', type: 'spki'});
let signatures = 0, requiredSignatures = 0;
function checkSignature(signature, message, raw, reference, id) {
  if (raw.length !== 32 || !reference.endsWith(':' + sha(raw))) throw Error('signer reference: ' + id);
  const decoded = Buffer.from(signature, 'base64url');
  if (decoded.length !== 64 || !crypto.verify(null, message, publicKey(raw), decoded)) throw Error('signature: ' + id);
  signatures++;
}
function signedCommitment(c, id) {
  const normalized = model(c, 'commitment');
  checkSignature(normalized.seller_signature, commitment(normalized), sellerPublic, normalized.aim_data_signer_reference, id);
  for (const p of normalized.proofs) checkSignature(p.signature, proof(normalized, p), sellerPublic, p.signer_reference, id);
  return normalized;
}
function signedRequest(r, id) {
  checkSignature(r.seller_signature, disclosure(r.binding), sellerPublic, r.binding.signer_reference, id);
  if (r.commitment) {
    const c = signedCommitment(r.commitment, id);
    if (canon(c.proofs) !== canon(r.proofs)) throw Error('request proof order: ' + id);
  }
}
function signedEnvelope(e, id) {
  checkSignature(e.seller_signature, disclosure(e.binding), sellerPublic, e.binding.signer_reference, id);
  const key = e.signer_keys.find(k => k.key_id === e.key_id);
  if (!key) throw Error('platform key absent: ' + id);
  const raw = Buffer.from(key.public_key, 'base64url');
  if (raw.length !== 32 || !raw.equals(platformPublic) || key.fingerprint !== sha(raw)) throw Error('platform key: ' + id);
  const signature = Buffer.from(e.signature, 'base64url');
  if (signature.length !== 64 || !crypto.verify(null, envelope(e), publicKey(raw), signature)) throw Error('platform signature: ' + id);
  signatures++;
}
const digests = {};
for (const row of manifest.vectors) {
  if (row.expected !== 'accept') continue;
  const input = JSON.parse(fs.readFileSync(path.join(root, 'inputs/', row.id + '.json')));
  if (row.operation === 'proof-model') requiredSignatures++;
  if (row.operation === 'commitment-model') requiredSignatures += 1 + (input.proofs || []).length;
  if (row.operation === 'request-bytes') {
    const nested = input.commitment ? 1 + (input.commitment.proofs || []).length : 0;
    requiredSignatures += 1 + nested + (row.id === 'request-approve-v2' ? nested : 0);
  }
  if (row.operation === 'platform-envelope-model' || row.operation === 'platform-envelope-preimage') requiredSignatures += 2;
  let raw;
  if (row.id === 'request-approve-v2') {
    const {candidate, commitment: c, proofs, approved_p1: approved} = input;
    const fields = ['summary_id', 'summary_approval_id', 'summary_hash', 'render_hash', 'aggregate_hash', 'content_revision', 'source_revision', 'listing_id', 'listing_version_id'];
    if (Object.keys(approved).length !== fields.length || fields.some(k => canon(approved[k]) !== canon(candidate[k]))) throw Error('approved P1: ' + row.id);
    const normalized = signedCommitment(c, row.id);
    if (canon(normalized.proofs) !== canon(proofs)) throw Error('constructed proof order');
    const signature = crypto.sign(null, disclosure(candidate), sellerKey).toString('base64url');
    const request = {profile: candidate.profile, summary_id: candidate.summary_id, binding: candidate,
      seller_signature: signature, commitment: normalized, proofs: normalized.proofs};
    signedRequest(request, row.id);
    raw = bytes(request);
  } else if (row.operation === 'disclosure-preimage') raw = disclosure(input);
  else if (row.operation === 'platform-envelope-preimage') { signedEnvelope(input, row.id); raw = envelope(input); }
  else {
    let value = input;
    if (row.operation === 'proof-model') {
      value = model(input, 'proof');
      const contextId = row.id === 'proof-v1-policy-v1' ? 'commitment-v1-previous-absent' : 'commitment-v2-previous-present';
      const context = JSON.parse(fs.readFileSync(path.join(root, 'inputs/', contextId + '.json')));
      checkSignature(value.signature, proof(model(context, 'commitment'), value), sellerPublic, value.signer_reference, row.id);
    } else if (row.operation === 'commitment-model') value = signedCommitment(input, row.id);
    else if (row.operation === 'request-bytes') signedRequest(input, row.id);
    else if (row.operation === 'platform-envelope-model') { signedEnvelope(input, row.id); value = model(input, 'envelope'); }
    raw = bytes(value);
  }
  const expected = fs.readFileSync(path.join(root, 'bytes/', row.id + '.bin'));
  const digest = sha(raw);
  if (!raw.equals(expected) || raw.length !== row.byte_length || digest !== row.sha256) throw Error('byte mismatch: ' + row.id);
  digests[row.id] = digest;
}
if (signatures !== requiredSignatures) throw Error('missing signature checks');
console.log(JSON.stringify({digests, signatures}));
