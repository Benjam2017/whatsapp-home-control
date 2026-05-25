/**
 * state.js — In-memory device state cache
 *
 * Tracks the last known status of each device:
 *   - status:     ON | OFF | MOVING_UP | MOVING_DOWN | STOPPED | UNKNOWN
 *   - source:     whatsapp | physical | poll
 *   - updatedAt:  ISO timestamp
 */

const deviceState = {
  relay1: {
    name:      'Light',
    type:      'light',
    relay:     1,
    status:    'UNKNOWN',
    source:    null,
    updatedAt: null,
  },
  relay2: {
    name:      'Curtain UP',
    type:      'curtain',
    relay:     2,
    status:    'UNKNOWN',
    source:    null,
    updatedAt: null,
  },
  relay3: {
    name:      'Curtain DOWN',
    type:      'curtain',
    relay:     3,
    status:    'UNKNOWN',
    source:    null,
    updatedAt: null,
  },
};

// Device key lookup by push notification device name
const deviceKeyMap = {
  light:        'relay1',
  curtain_up:   'relay2',
  curtain_down: 'relay3',
};

/**
 * Update state for a device.
 * @param {string} deviceKey - 'relay1' | 'relay2' | 'relay3' | push name like 'light'
 * @param {string} status    - 'ON' | 'OFF' | 'MOVING_UP' | 'MOVING_DOWN' | 'STOPPED'
 * @param {string} source    - 'whatsapp' | 'physical' | 'poll'
 */
function updateState(deviceKey, status, source) {
  const key = deviceKeyMap[deviceKey] || deviceKey;
  if (!deviceState[key]) return;
  deviceState[key].status    = status;
  deviceState[key].source    = source;
  deviceState[key].updatedAt = new Date().toISOString();
}

/**
 * Get a formatted status report for all devices.
 */
function getStatusReport() {
  const lines = Object.values(deviceState).map(d => {
    const icon    = d.type === 'light' ? '💡' : '🪟';
    const src     = d.source    ? ` (via ${d.source})`                   : '';
    const time    = d.updatedAt ? ` at ${d.updatedAt.slice(11, 19)}`     : '';
    return `${icon} ${d.name}: ${d.status}${src}${time}`;
  });
  const timestamp = new Date().toTimeString().slice(0, 8);
  return `Home Status — ${timestamp}\n\n${lines.join('\n')}`;
}

/**
 * Get a single device record by key.
 */
function getDevice(key) {
  return deviceState[deviceKeyMap[key] || key] || null;
}

module.exports = { updateState, getStatusReport, getDevice };
