const crypto = require('crypto');
const assert = require('assert');
delete require.cache[require.resolve('../security')];
const { verifySignature, checkWhitelist, checkDedup } = require('../security');

let passed = 0, failed = 0;
function test(name, fn) {
  try { fn(); console.log(`  ✅ PASS  ${name}`); passed++; }
  catch(e) { console.error(`  ❌ FAIL  ${name}: ${e.message}`); failed++; }
}

console.log('\n── verifySignature ──────────────────────────────────────────');
test('valid signature passes', () => {
  const secret = 'test-secret', body = Buffer.from('{"test":"data"}');
  const sig = 'sha256=' + crypto.createHmac('sha256', secret).update(body).digest('hex');
  assert.doesNotThrow(() => verifySignature({ 'x-hub-signature-256': sig }, body, secret));
});
test('invalid signature throws', () => {
  assert.throws(
    () => verifySignature({ 'x-hub-signature-256': 'sha256=' + 'a'.repeat(64) }, Buffer.from('{}'), 'secret'),
    /mismatch/
  );
});
test('missing header throws', () => {
  assert.throws(() => verifySignature({}, Buffer.from('{}'), 'secret'), /Missing/);
});
test('wrong prefix throws', () => {
  assert.throws(
    () => verifySignature({ 'x-hub-signature-256': 'md5=abc' }, Buffer.from('{}'), 'secret'),
    /Missing/
  );
});

console.log('\n── checkWhitelist ───────────────────────────────────────────');
test('authorized number true', () => {
  process.env.ALLOWED_NUMBERS = '33612345678,33698765432';
  assert.strictEqual(checkWhitelist('33612345678'), true);
});
test('second number true', () => { assert.strictEqual(checkWhitelist('33698765432'), true); });
test('unauthorized number false', () => { assert.strictEqual(checkWhitelist('999'), false); });
test('empty whitelist rejects all', () => {
  process.env.ALLOWED_NUMBERS = '';
  assert.strictEqual(checkWhitelist('33612345678'), false);
});
test('padded numbers handled', () => {
  process.env.ALLOWED_NUMBERS = ' 33612345678 , 33698765432 ';
  assert.strictEqual(checkWhitelist('33612345678'), true);
});

console.log('\n── checkDedup ───────────────────────────────────────────────');
test('new ID returns true', () => { assert.strictEqual(checkDedup('msg-unique-001'), true); });
test('duplicate returns false', () => {
  const id = 'dup-' + Date.now();
  assert.strictEqual(checkDedup(id), true);
  assert.strictEqual(checkDedup(id), false);
});
test('different IDs accepted', () => {
  const ts = Date.now();
  assert.ok(checkDedup(`a-${ts}`) && checkDedup(`b-${ts}`) && checkDedup(`c-${ts}`));
});

console.log(`\n${'─'.repeat(55)}`);
console.log(`  Results: ${passed} passed, ${failed} failed`);
console.log(`${'─'.repeat(55)}\n`);
if (failed > 0) process.exit(1);
