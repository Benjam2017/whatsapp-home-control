/**
 * security.js — HMAC signature verification, whitelist, deduplication
 */

const crypto = require('crypto');

// ─────────────────────────────────────────────────────────────────
// Verify Meta Webhook X-Hub-Signature-256 header
// ─────────────────────────────────────────────────────────────────
function verifySignature(headers, rawBody, appSecret) {
  const sigHeader = headers['x-hub-signature-256'] || '';
  if (!sigHeader.startsWith('sha256=')) {
    throw new Error('Missing X-Hub-Signature-256 header');
  }
  const received = sigHeader.slice(7); // strip 'sha256='
  const expected = crypto
    .createHmac('sha256', appSecret)
    .update(rawBody)
    .digest('hex');

  if (!crypto.timingSafeEqual(Buffer.from(expected), Buffer.from(received))) {
    throw new Error('Signature mismatch — possible forged request');
  }
}

// ─────────────────────────────────────────────────────────────────
// Authorized number whitelist
// ALLOWED_NUMBERS=33612345678,33698765432 (no + sign, comma-separated)
// ─────────────────────────────────────────────────────────────────
function checkWhitelist(fromNumber) {
  const allowed = (process.env.ALLOWED_NUMBERS || '')
    .split(',')
    .map(n => n.trim())
    .filter(Boolean);
  return allowed.includes(fromNumber);
}

// ─────────────────────────────────────────────────────────────────
// Message ID deduplication — prevents double execution on Meta retries
// Keeps last 200 message IDs in memory
// ─────────────────────────────────────────────────────────────────
const MAX_DEDUP = 200;
const seenIds   = [];

function checkDedup(messageId) {
  if (seenIds.includes(messageId)) return false; // already seen
  seenIds.push(messageId);
  if (seenIds.length > MAX_DEDUP) seenIds.shift(); // evict oldest
  return true;
}

module.exports = { verifySignature, checkWhitelist, checkDedup };
