// Tests for the npm bootstrap. Run with: cd npm && npm test
// A fake herdr and a fake plugin keep everything inside a temp directory.
'use strict';
const { test } = require('node:test');
const assert = require('node:assert');
const { spawnSync } = require('node:child_process');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');

const CLI = path.join(__dirname, '..', 'cli.js');
const isWin = process.platform === 'win32';

function run(args, env, input) {
  return spawnSync(process.execPath, [CLI, ...args], {
    encoding: 'utf8', env, input: input === undefined ? '' : input,
  });
}

function sandbox({ withHerdr = true, withPlugin = true } = {}) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'herdr-secret-cli-'));
  const bin = path.join(dir, 'bin');
  fs.mkdirSync(bin);
  const root = path.join(dir, 'plugin');
  fs.mkdirSync(path.join(root, 'bin'), { recursive: true });
  // The fake plugin echoes the arguments it was given.
  fs.writeFileSync(path.join(root, 'bin', 'herdr_secret.py'),
    'import sys\nprint("PLUGIN", " ".join(sys.argv[1:]))\nsys.exit(7 if sys.argv[1:2] == ["fail"] else 0)\n');
  const log = path.join(dir, 'herdr.log');
  const plugins = withPlugin ? [{ plugin_id: 'mazemaker.secret', plugin_root: root, source: { kind: 'github' } }] : [];
  if (withHerdr) {
    const js = path.join(bin, 'fake-herdr.js');
    fs.writeFileSync(js, `
const fs = require('fs'); const a = process.argv.slice(2);
fs.appendFileSync(${JSON.stringify(log)}, a.join(' ') + '\\n');
if (a[0] === '--version') console.log('herdr 0.9.5');
else if (a.join(' ') === 'plugin list --json') console.log(JSON.stringify({ result: { plugins: ${JSON.stringify(plugins)} } }));
`);
    const sh = path.join(bin, 'herdr');
    fs.writeFileSync(sh, `#!/bin/sh\nexec "${process.execPath}" "${js}" "$@"\n`);
    fs.chmodSync(sh, 0o755);
  }
  // Keep the real Python reachable, but nothing else from the real PATH (no real herdr).
  const py = spawnSync(isWin ? 'where' : 'which', [isWin ? 'python' : 'python3'], { encoding: 'utf8' }).stdout.split(/\r?\n/)[0];
  const pyDir = path.join(dir, 'py');
  fs.mkdirSync(pyDir);
  if (py) {
    const link = path.join(pyDir, path.basename(py));
    try { fs.symlinkSync(py, link); } catch { fs.copyFileSync(py, link); }
  }
  const env = { ...process.env, PATH: [bin, pyDir, isWin ? process.env.SystemRoot + '\\System32' : '/usr/bin:/bin'].join(path.delimiter) };
  return { dir, env, log, readLog: () => (fs.existsSync(log) ? fs.readFileSync(log, 'utf8') : '') };
}

test('--help prints usage and needs nothing installed', () => {
  const r = run(['--help'], { ...process.env, PATH: '' });
  assert.strictEqual(r.status, 0);
  assert.match(r.stdout, /npx herdr-secret@latest/);
});

test('cli.js contains no URL strings', () => {
  // Package scanners flag URLs in code as possible network access; this file makes none.
  const src = fs.readFileSync(CLI, 'utf8');
  assert.doesNotMatch(src, /https?:\/\/|\b\d{1,3}(\.\d{1,3}){3}\b/);
});

test('cli.js uses no network, eval or install hooks', () => {
  const src = fs.readFileSync(CLI, 'utf8');
  assert.doesNotMatch(src, /require\(['"](https?|net|dns|tls|http2)['"]\)|\beval\(|new Function\(/);
  const pkg = JSON.parse(fs.readFileSync(path.join(__dirname, '..', 'package.json'), 'utf8'));
  assert.deepStrictEqual(Object.keys(pkg.dependencies || {}), []);
  for (const hook of ['preinstall', 'install', 'postinstall', 'prepare']) assert.ok(!(pkg.scripts || {})[hook], hook);
});

test('fails clearly when herdr is missing', () => {
  const { env } = sandbox({ withHerdr: false });
  const r = run(['list'], env);
  assert.strictEqual(r.status, 1);
  assert.match(r.stderr, /herdr is required/);
});

test('passes other commands to the installed plugin and returns its exit code', { skip: isWin && 'fake herdr is a shell script' }, () => {
  const { env } = sandbox();
  let r = run(['list'], env);
  assert.strictEqual(r.status, 0, r.stderr);
  assert.match(r.stdout, /^PLUGIN list$/m);
  r = run(['fail'], env);
  assert.strictEqual(r.status, 7);
});

test('says how to install when the plugin is missing', { skip: isWin && 'fake herdr is a shell script' }, () => {
  const { env } = sandbox({ withPlugin: false });
  const r = run(['get', 'X'], env);
  assert.strictEqual(r.status, 1);
  assert.match(r.stderr, /not installed yet/);
});

test('no arguments updates an installed plugin, then runs its setup with the flags', { skip: isWin && 'fake herdr is a shell script' }, () => {
  const { env, readLog } = sandbox();
  const r = run(['--yes'], env);
  assert.strictEqual(r.status, 0, r.stderr);
  assert.match(readLog(), /^plugin update mazemaker\.secret$/m);
  assert.match(r.stdout, /^PLUGIN setup --yes$/m);
});
