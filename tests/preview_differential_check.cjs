// Independent ECMAScript serialization/Ed25519 verification. No Python helper.
const fs = require('fs'), crypto = require('crypto');
const corpus = JSON.parse(fs.readFileSync('tests/fixtures/aim_preview_differential_v1.json'));
function codePointCompare(a, b) {
  const x = Array.from(a, c => c.codePointAt(0)), y = Array.from(b, c => c.codePointAt(0));
  for (let i = 0; i < Math.min(x.length, y.length); i++) if (x[i] !== y[i]) return x[i] - y[i];
  return x.length - y.length;
}
function canon(v) {
  if (typeof v === 'number') {
    if (!Number.isInteger(v)) throw Error('invalid_metadata');
    if (!Number.isSafeInteger(v)) throw Error('unsafe_integer');
  }
  if (v === null || typeof v !== 'object') return JSON.stringify(v);
  if (Array.isArray(v)) return '[' + v.map(canon).join(',') + ']';
  const keys = Object.keys(v).sort(); // JCS UTF-16 order
  if (JSON.stringify(keys) !== JSON.stringify([...keys].sort(codePointCompare)))
    throw Error('noncanonical_key_order'); // producer 2a refusal, not general JCS refusal
  return '{' + keys.map(k => JSON.stringify(k) + ':' + canon(v[k])).join(',') + '}';
}
function preimage(row) {
  let object = structuredClone(row.input), domain = '';
  if (row.kind === 'disclosure') domain = 'aim-preview-disclosure-signature-v1\0';
  else if (row.kind === 'platform-envelope') {
    domain = 'aim-preview-platform-envelope-v1\0';
    delete object.signature;
    for (const key of object.signer_keys) if (key.valid_until == null) delete key.valid_until;
  } else if (row.kind !== 'jcs') throw Error('unknown kind');
  return Buffer.from(domain + canon(object), 'utf8');
}
const digests = {};
for (const row of corpus.valid) {
  const bytes = preimage(row);
  if (bytes.toString('hex') !== row.signed_bytes_hex) throw Error('BLOCKER preimage mismatch: ' + row.name);
  const digest = crypto.createHash('sha256').update(bytes).digest('hex');
  if (digest !== row.signed_bytes_sha256) throw Error('BLOCKER digest mismatch: ' + row.name);
  const der = Buffer.concat([Buffer.from('302a300506032b6570032100', 'hex'), Buffer.from(row.public_key, 'base64url')]);
  const key = crypto.createPublicKey({key: der, format: 'der', type: 'spki'});
  if (!crypto.verify(null, bytes, key, Buffer.from(row.signature, 'base64url'))) throw Error('signature mismatch: ' + row.name);
  digests[row.name] = digest;
}
for (const row of corpus.must_reject) {
  let error;
  try { preimage(row); } catch (e) { error = e.message; }
  if (error !== row.error) throw Error('BLOCKER admission mismatch: ' + row.name + ': ' + error);
}
console.log(JSON.stringify({digests, rejected: corpus.must_reject.map(row => row.name)}));
