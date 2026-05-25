/**
 * app.js — WhatsApp Home Control: Node.js Webhook Server
 *
 * Responsibilities:
 *   - Receive WhatsApp messages from Meta Cloud API (POST /webhook)
 *   - Verify Meta webhook handshake (GET /webhook)
 *   - Receive IPX800 push notifications (GET /ipx-notify)
 *   - Forward commands to Python FastAPI for relay control
 *   - Send WhatsApp replies and physical-change alerts
 */

require('dotenv').config();

const https   = require('https');
const http    = require('http');
const fs      = require('fs');
const express = require('express');

const { verifySignature, checkWhitelist, checkDedup } = require('./security');
const { sendWhatsAppMessage }                          = require('./whatsapp');
const { updateState, getStatusReport, getDevice }      = require('./state');
const logger                                           = require('./logger');

const app         = express();
const PORT        = parseInt(process.env.NODE_PORT || '443');
const USE_SSL     = process.env.USE_SSL !== 'false'; // default true

app.use(express.json({
  verify: (req, res, buf) => { req.rawBody = buf; } // keep raw body for HMAC
}));

// ─────────────────────────────────────────────────────────────────
// GET /webhook — Meta handshake verification
// ─────────────────────────────────────────────────────────────────
app.get('/webhook', (req, res) => {
  const mode      = req.query['hub.mode'];
  const token     = req.query['hub.verify_token'];
  const challenge = req.query['hub.challenge'];

  if (mode === 'subscribe' && token === process.env.WA_VERIFY_TOKEN) {
    logger.info('Webhook handshake verified by Meta');
    return res.status(200).send(challenge);
  }
  logger.warn('Webhook handshake failed — invalid verify_token');
  return res.sendStatus(403);
});

// ─────────────────────────────────────────────────────────────────
// POST /webhook — receive WhatsApp messages
// ─────────────────────────────────────────────────────────────────
app.post('/webhook', async (req, res) => {
  // Step 1: verify HMAC signature
  try {
    verifySignature(req.headers, req.rawBody, process.env.WA_APP_SECRET);
  } catch (err) {
    logger.warn(`Signature verification failed: ${err.message}`);
    return res.sendStatus(403);
  }

  // Step 2: return 200 immediately — Meta requires response within 20s
  res.sendStatus(200);

  // Step 3: process asynchronously
  try {
    const body     = req.body;
    const messages = body?.entry?.[0]?.changes?.[0]?.value?.messages;
    if (!messages || messages.length === 0) return; // status notification, ignore

    const msg = messages[0];
    if (msg.type !== 'text') return; // non-text message, ignore

    const from = msg.from;       // sender number without +
    const text = msg.text.body.trim().toLowerCase();
    const msgId = msg.id;

    logger.info(`Incoming message from ${from}: "${text}"`);

    // Step 4: whitelist check
    if (!checkWhitelist(from)) {
      logger.warn(`Unauthorized number: ${from} — ignored`);
      return;
    }

    // Step 5: deduplication
    if (!checkDedup(msgId)) {
      logger.warn(`Duplicate message ${msgId} — ignored`);
      return;
    }

    // Step 6: forward to FastAPI for command processing
    const result = await callFastAPI('/control', { command: text, from });

    // Step 7: send WhatsApp reply
    await sendWhatsAppMessage(from, result.reply);

  } catch (err) {
    logger.error(`Error processing webhook: ${err.message}`);
  }
});

// ─────────────────────────────────────────────────────────────────
// GET /ipx-notify — receive IPX800 push notifications
// Called by IPX800 Scenario when a physical switch changes state
// Example: GET /ipx-notify?device=light&state=0
// ─────────────────────────────────────────────────────────────────
app.get('/ipx-notify', async (req, res) => {
  const device = req.query.device;
  const state  = req.query.state; // "0" or "1"

  if (!device || state === undefined) {
    return res.status(400).json({ error: 'Missing device or state parameter' });
  }

  res.sendStatus(200); // respond immediately

  const status    = state === '1' ? 'ON' : 'OFF';
  const stateIcon = status === 'ON' ? '✅' : '⭕';
  const deviceMap = {
    light:       '💡 Light',
    curtain_up:  '🪟 Curtain UP relay',
    curtain_down:'🪟 Curtain DOWN relay',
  };
  const deviceName = deviceMap[device] || device;

  // Update state cache
  updateState(device, status, 'physical');
  logger.info(`Physical state change: ${device} -> ${status}`);

  // Send WhatsApp alert to all authorized numbers
  const authorizedNumbers = (process.env.ALLOWED_NUMBERS || '').split(',').map(n => n.trim());
  const now = new Date().toTimeString().slice(0, 8);
  const alertText = `${stateIcon} ${deviceName} was physically switched ${status} at ${now}`;

  for (const number of authorizedNumbers) {
    if (number) {
      await sendWhatsAppMessage(number, alertText).catch(err =>
        logger.error(`Failed to send alert to ${number}: ${err.message}`)
      );
    }
  }
});

// ─────────────────────────────────────────────────────────────────
// GET /health — service health check
// ─────────────────────────────────────────────────────────────────
app.get('/health', async (req, res) => {
  try {
    const fastapiHealth = await callFastAPI('/health', null, 'GET');
    res.json({
      status:   'ok',
      service:  'whatsapp-webhook',
      uptime:   process.uptime(),
      fastapi:  fastapiHealth,
      time:     new Date().toISOString(),
    });
  } catch (err) {
    res.json({
      status:   'degraded',
      service:  'whatsapp-webhook',
      uptime:   process.uptime(),
      fastapi:  'unreachable',
      error:    err.message,
      time:     new Date().toISOString(),
    });
  }
});

// ─────────────────────────────────────────────────────────────────
// Helper: call Python FastAPI service (internal)
// ─────────────────────────────────────────────────────────────────
async function callFastAPI(path, body = null, method = 'POST') {
  const FASTAPI_PORT = process.env.FASTAPI_PORT || '8000';
  const FASTAPI_HOST = process.env.FASTAPI_HOST || '127.0.0.1';

  return new Promise((resolve, reject) => {
    const payload = body ? JSON.stringify(body) : null;
    const options = {
      hostname: FASTAPI_HOST,
      port:     parseInt(FASTAPI_PORT),
      path,
      method,
      headers:  {
        'Content-Type': 'application/json',
        ...(payload ? { 'Content-Length': Buffer.byteLength(payload) } : {}),
      },
    };

    const req = http.request(options, (res) => {
      let data = '';
      res.on('data', chunk => data += chunk);
      res.on('end', () => {
        try {
          resolve(JSON.parse(data));
        } catch {
          resolve({ reply: data });
        }
      });
    });

    req.on('error', reject);
    req.setTimeout(15000, () => {
      req.destroy(new Error('FastAPI request timeout'));
    });

    if (payload) req.write(payload);
    req.end();
  });
}

// ─────────────────────────────────────────────────────────────────
// Start server
// ─────────────────────────────────────────────────────────────────
if (USE_SSL) {
  const sslOptions = {
    key:  fs.readFileSync(process.env.SSL_KEY_PATH  || '/etc/letsencrypt/live/yourdomain.com/privkey.pem'),
    cert: fs.readFileSync(process.env.SSL_CERT_PATH || '/etc/letsencrypt/live/yourdomain.com/fullchain.pem'),
  };
  https.createServer(sslOptions, app).listen(PORT, () => {
    logger.info(`Webhook server (HTTPS) listening on port ${PORT}`);
  });
} else {
  // HTTP mode for local testing only
  app.listen(PORT, () => {
    logger.info(`Webhook server (HTTP) listening on port ${PORT}`);
  });
}
