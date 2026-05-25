/**
 * logger.js — Simple file + console logger for Node.js webhook service
 */

const fs   = require('fs');
const path = require('path');

const LOG_FILE  = process.env.LOG_FILE  || path.join(__dirname, 'logs', 'webhook.log');
const LOG_LEVEL = process.env.LOG_LEVEL || 'INFO';

const LEVELS = { DEBUG: 0, INFO: 1, WARN: 2, ERROR: 3 };
const currentLevel = LEVELS[LOG_LEVEL.toUpperCase()] ?? 1;

// Ensure log directory exists
const logDir = path.dirname(LOG_FILE);
if (!fs.existsSync(logDir)) fs.mkdirSync(logDir, { recursive: true });

function write(level, message) {
  if (LEVELS[level] < currentLevel) return;
  const timestamp = new Date().toISOString().replace('T', ' ').slice(0, 19);
  const line = `[${timestamp}] [${level}] ${message}`;
  console.log(line);
  fs.appendFileSync(LOG_FILE, line + '\n');
}

const logger = {
  debug: (msg) => write('DEBUG', msg),
  info:  (msg) => write('INFO',  msg),
  warn:  (msg) => write('WARN',  msg),
  error: (msg) => write('ERROR', msg),
};

module.exports = logger;
