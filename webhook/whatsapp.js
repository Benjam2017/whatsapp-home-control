/**
 * whatsapp.js — Meta WhatsApp Cloud API: send message
 */

const https = require('https');

const WA_API_VERSION     = process.env.WA_API_VERSION     || 'v19.0';
const WA_PHONE_NUMBER_ID = process.env.WA_PHONE_NUMBER_ID || '';
const WA_ACCESS_TOKEN    = process.env.WA_ACCESS_TOKEN    || '';

/**
 * Send a plain text message to a WhatsApp number.
 * @param {string} to      - recipient number without + (e.g. 33612345678)
 * @param {string} message - message body text
 */
async function sendWhatsAppMessage(to, message) {
  return new Promise((resolve, reject) => {
    const body = JSON.stringify({
      messaging_product: 'whatsapp',
      recipient_type:    'individual',
      to,
      type:              'text',
      text:              { body: message },
    });

    const options = {
      hostname: 'graph.facebook.com',
      path:     `/${WA_API_VERSION}/${WA_PHONE_NUMBER_ID}/messages`,
      method:   'POST',
      headers:  {
        'Authorization':  `Bearer ${WA_ACCESS_TOKEN}`,
        'Content-Type':   'application/json',
        'Content-Length': Buffer.byteLength(body),
      },
    };

    const req = https.request(options, (res) => {
      let data = '';
      res.on('data', chunk => data += chunk);
      res.on('end', () => {
        if (res.statusCode === 200) {
          resolve(JSON.parse(data));
        } else {
          reject(new Error(`WhatsApp API error ${res.statusCode}: ${data}`));
        }
      });
    });

    req.on('error', reject);
    req.setTimeout(10000, () => req.destroy(new Error('WhatsApp API timeout')));
    req.write(body);
    req.end();
  });
}

module.exports = { sendWhatsAppMessage };
