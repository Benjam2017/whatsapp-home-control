/**
 * tests/test_state.js — Unit tests for state.js
 * Run with: node tests/test_state.js
 */

const assert = require('assert');
const { updateState, getStatusReport, getDevice } = require('../state');

let passed = 0;
let failed = 0;

function test(name, fn) {
  try {
    fn();
    console.log(`  ✅ PASS  ${name}`);
    passed++;
  } catch (err) {
    console.error(`  ❌ FAIL  ${name}`);
    console.error(`         ${err.message}`);
    failed++;
  }
}

// ─────────────────────────────────────────────────────────────────
// updateState + getDevice
// ─────────────────────────────────────────────────────────────────
console.log('\n── updateState / getDevice ──────────────────────────────────');

test('update light state to ON via whatsapp', () => {
  updateState('relay1', 'ON', 'whatsapp');
  const d = getDevice('relay1');
  assert.strictEqual(d.status, 'ON');
  assert.strictEqual(d.source, 'whatsapp');
  assert.ok(d.updatedAt, 'updatedAt should be set');
});

test('update light state to OFF via physical', () => {
  updateState('relay1', 'OFF', 'physical');
  const d = getDevice('relay1');
  assert.strictEqual(d.status, 'OFF');
  assert.strictEqual(d.source, 'physical');
});

test('update via push device name (light)', () => {
  updateState('light', 'ON', 'physical');
  const d = getDevice('relay1');
  assert.strictEqual(d.status, 'ON');
});

test('update curtain_up via push name', () => {
  updateState('curtain_up', 'ON', 'physical');
  const d = getDevice('relay2');
  assert.strictEqual(d.status, 'ON');
});

test('update curtain_down via push name', () => {
  updateState('curtain_down', 'OFF', 'physical');
  const d = getDevice('relay3');
  assert.strictEqual(d.status, 'OFF');
});

test('unknown key does not throw', () => {
  assert.doesNotThrow(() => updateState('nonexistent', 'ON', 'test'));
});

test('updatedAt is ISO string', () => {
  updateState('relay1', 'ON', 'whatsapp');
  const d = getDevice('relay1');
  assert.ok(!isNaN(Date.parse(d.updatedAt)), 'updatedAt should be valid ISO date');
});

// ─────────────────────────────────────────────────────────────────
// getStatusReport
// ─────────────────────────────────────────────────────────────────
console.log('\n── getStatusReport ──────────────────────────────────────────');

test('report contains Light', () => {
  const report = getStatusReport();
  assert.ok(report.includes('Light'), 'Should mention Light');
});

test('report contains Curtain', () => {
  const report = getStatusReport();
  assert.ok(report.includes('Curtain'), 'Should mention Curtain');
});

test('report contains Home Status header', () => {
  const report = getStatusReport();
  assert.ok(report.includes('Home Status'), 'Should have header');
});

test('report contains source info', () => {
  updateState('relay1', 'ON', 'whatsapp');
  const report = getStatusReport();
  assert.ok(report.includes('whatsapp'), 'Should include source');
});

// ─────────────────────────────────────────────────────────────────
// Summary
// ─────────────────────────────────────────────────────────────────
console.log(`\n${'─'.repeat(55)}`);
console.log(`  Results: ${passed} passed, ${failed} failed`);
console.log(`${'─'.repeat(55)}\n`);

if (failed > 0) process.exit(1);
