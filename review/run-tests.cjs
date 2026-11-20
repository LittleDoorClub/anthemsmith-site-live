/* Runs the order-client suite with jsdom resolved from the caller's env, the repo, or the
 * disposable temp install used for review. Passes the suite's exit code through unchanged. */
const { spawnSync } = require('node:child_process');
const fs = require('node:fs');
const path = require('node:path');

const candidates = [
  process.env.JSDOM_PATH,
  path.join(__dirname, '..', 'node_modules', 'jsdom'),
  'C:/Users/Owner/AppData/Local/Temp/as_jsdom_env/node_modules/jsdom',
];
const jsdom = candidates.find(p => p && fs.existsSync(p));
if (!jsdom) {
  console.error('jsdom not found — install it outside the repo, e.g.\n'
    + '  npm install --prefix C:/Users/Owner/AppData/Local/Temp/as_jsdom_env jsdom');
  process.exit(2);
}
const suite = spawnSync(process.execPath, ['--test', path.join(__dirname, 'order-client.test.cjs')],
  { stdio: 'inherit', env: { ...process.env, JSDOM_PATH: jsdom } });
process.exit(suite.status ?? 1);
