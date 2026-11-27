// Tests for the npm bootstrap. Run with: cd npm && npm test
// A copy of cli.js, a fake herdr, a fake plugin and a fake engine keep everything inside a temp
// directory, with a throwaway home so the real herdr, Herdr.app and home are never involved.
'use strict';
const { test } = require('node:test');
const assert = require('node:assert');
const { spawnSync } = require('node:child_process');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { FILES, REPO, stage } = require('../stage.js');

const NPM = path.join(__dirname, '..');
const CLI = path.join(NPM, 'cli.js');
const isWin = process.platform === 'win32';
// Code that ships in the package. Markdown is left out: the agent skill shows an example URL.
const CODE = /\.(js|py|json|toml|cmd)$|\/herdr-secret$/;

function run(cli, args, env, input) {
  return spawnSync(process.execPath, [cli, ...args], {
    encoding: 'utf8', env, input: input === undefined ? '' : input,
  });
}

function script(dir, name, js) {
  // A program on PATH that runs `js` with node and logs how it was called.
  const file = path.join(dir, `${name}.js`);
  fs.writeFileSync(file, js);
  if (isWin) {
    fs.writeFileSync(path.join(dir, `${name}.cmd`), `@"${process.execPath}" "${file}" %*\r\n`);
  } else {
    fs.writeFileSync(path.join(dir, name), `#!/bin/sh\nexec "${process.execPath}" "${file}" "$@"\n`);
    fs.chmodSync(path.join(dir, name), 0o755);
  }
}

function sandbox({ withHerdr = true, withPlugin = true, withEngine = false, appHome = false, appOnPath = false } = {}) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'herdr-secret-cli-'));
  const home = path.join(dir, 'home');
  const bin = path.join(dir, 'bin');
  const pkg = path.join(dir, 'pkg');
  fs.mkdirSync(home);
  fs.mkdirSync(bin);
  fs.mkdirSync(pkg);
  fs.copyFileSync(CLI, path.join(pkg, 'cli.js'));
  const echo = (who) => `import sys\nprint("${who}", " ".join(sys.argv[1:]))\nsys.exit(7 if sys.argv[1:2] == ["fail"] else 0)\n`;
  if (withEngine) {
    fs.mkdirSync(path.join(pkg, 'engine', 'bin'), { recursive: true });
    fs.writeFileSync(path.join(pkg, 'engine', 'bin', 'herdr_secret.py'), echo('ENGINE'));
  }
  const root = path.join(dir, 'plugin');
  fs.mkdirSync(path.join(root, 'bin'), { recursive: true });
  fs.writeFileSync(path.join(root, 'bin', 'herdr_secret.py'), echo('PLUGIN'));
  const log = path.join(dir, 'calls.log');
  const plugins = withPlugin ? [{ plugin_id: 'mazemaker.secret', plugin_root: root, source: { kind: 'github' } }] : [];
  if (withHerdr) {
    script(bin, 'herdr', `
const fs = require('fs'); const a = process.argv.slice(2);
fs.appendFileSync(${JSON.stringify(log)}, 'herdr ' + a.join(' ') + '\\n');
if (a[0] === '--version') console.log('herdr 0.9.5');
else if (a.join(' ') === 'plugin list --json') console.log(JSON.stringify({ result: { plugins: ${JSON.stringify(plugins)} } }));
`);
  }
  if (appOnPath) {
    script(bin, 'herdr-app', `require('fs').appendFileSync(${JSON.stringify(log)}, 'herdr-app ' + process.argv.slice(2).join(' ') + '\\n');`);
  }
  if (appHome) fs.mkdirSync(path.join(home, '.config', 'herdr-app'), { recursive: true });
  // Keep the real Python reachable, but nothing else from the real PATH (no real herdr).
  const py = spawnSync(isWin ? 'where' : 'which', [isWin ? 'python' : 'python3'], { encoding: 'utf8' }).stdout.split(/\r?\n/)[0];
  let pyDir = path.dirname(py || '.');
  if (!isWin) {
    pyDir = path.join(dir, 'py');
    fs.mkdirSync(pyDir);
    if (py) fs.symlinkSync(py, path.join(pyDir, path.basename(py)));
  }
  const env = { ...process.env, HOME: home, USERPROFILE: home,
    PATH: [bin, pyDir, isWin ? process.env.SystemRoot + '\\System32' : '/usr/bin:/bin'].join(path.delimiter) };
  delete env.HERDR_APP_HOME;
  return { cli: path.join(pkg, 'cli.js'), env, readLog: () => (fs.existsSync(log) ? fs.readFileSync(log, 'utf8') : '') };
}

function staged() {
  const dest = fs.mkdtempSync(path.join(os.tmpdir(), 'herdr-secret-stage-'));
  return stage(path.join(dest, 'engine'));
}

function walk(dir, base = dir) {
  return fs.readdirSync(dir, { withFileTypes: true }).flatMap((e) => {
    const p = path.join(dir, e.name);
    return e.isDirectory() ? walk(p, base) : [path.relative(base, p).split(path.sep).join('/')];
  });
}

test('--help prints usage and needs nothing installed', () => {
  const r = run(CLI, ['--help'], { ...process.env, PATH: '' });
  assert.strictEqual(r.status, 0);
  assert.match(r.stdout, /npx herdr-secret@latest/);
});

test('the package ships cli.js and the staged engine, and nothing runs at install', () => {
  const pkg = JSON.parse(fs.readFileSync(path.join(NPM, 'package.json'), 'utf8'));
  assert.deepStrictEqual(pkg.files, ['cli.js', 'engine/']);
  assert.deepStrictEqual(Object.keys(pkg.dependencies || {}), []);
  for (const hook of ['preinstall', 'install', 'postinstall', 'prepare', 'prepack', 'prepublish']) {
    assert.ok(!(pkg.scripts || {})[hook], hook);
  }
});

test('stage.js copies exactly the engine files setup installs, unchanged', () => {
  const py = fs.readFileSync(path.join(REPO, 'bin', 'herdr_secret.py'), 'utf8');
  const tuple = py.match(/^ENGINE_FILES = \(([^)]*)\)/m);
  assert.ok(tuple, 'ENGINE_FILES not found in herdr_secret.py');
  assert.deepStrictEqual([...tuple[1].matchAll(/"([^"]+)"/g)].map((m) => m[1]), FILES);
  const dest = staged();
  assert.deepStrictEqual(walk(dest).sort(), [...FILES].sort());
  for (const f of FILES) {
    assert.ok(fs.readFileSync(path.join(dest, f)).equals(fs.readFileSync(path.join(REPO, f))), f);
  }
  if (!isWin) assert.ok(fs.statSync(path.join(dest, 'bin', 'herdr-secret')).mode & 0o111, 'launcher not executable');
});

test('a staged npm/engine matches the repository (nothing stale gets published)', { skip: !fs.existsSync(path.join(NPM, 'engine')) && 'not staged' }, () => {
  const dest = path.join(NPM, 'engine');
  assert.deepStrictEqual(walk(dest).sort(), [...FILES].sort());
  for (const f of FILES) {
    assert.ok(fs.readFileSync(path.join(dest, f)).equals(fs.readFileSync(path.join(REPO, f))), `${f} is stale: run node npm/stage.js`);
  }
});

test('shipped code contains no URL strings', () => {
  // Package scanners flag URLs in code as possible network access; none of this makes any.
  const dest = staged();
  const files = [[CLI, 'cli.js'], ...FILES.filter((f) => CODE.test(f)).map((f) => [path.join(dest, f), f])];
  assert.ok(files.length >= 7, files.map((f) => f[1]).join(' '));
  for (const [file, name] of files) {
    assert.doesNotMatch(fs.readFileSync(file, 'utf8'), /https?:\/\/|\b\d{1,3}(\.\d{1,3}){3}\b/, name);
  }
});

test('shipped code uses no network or eval', () => {
  const dest = staged();
  const read = (f) => fs.readFileSync(f, 'utf8');
  for (const [name, src] of [['cli.js', read(CLI)], ['main.js', read(path.join(dest, 'mods/herdr-secret/main.js'))]]) {
    assert.doesNotMatch(src, /require\(['"](https?|net|dns|tls|http2)['"]\)|\bfetch\(|\beval\(|new Function\(/, name);
  }
  assert.doesNotMatch(read(path.join(dest, 'bin/herdr_secret.py')),
    /^\s*(import|from)\s+(socket|ssl|urllib|http|ftplib|smtplib|telnetlib|xmlrpc)\b|\beval\(|\bexec\(/m, 'herdr_secret.py');
});

test('fails clearly when neither herdr nor Herdr.app is there', () => {
  const { cli, env } = sandbox({ withHerdr: false, withEngine: true });
  const r = run(cli, ['list'], env);
  assert.strictEqual(r.status, 1);
  assert.match(r.stderr, /herdr \(the TUI\) or Herdr\.app is required/);
});

test('passes other commands to the installed plugin and returns its exit code', { skip: isWin && 'fake herdr is a shell script' }, () => {
  const { cli, env } = sandbox({ withEngine: true });
  let r = run(cli, ['list'], env);
  assert.strictEqual(r.status, 0, r.stderr);
  assert.match(r.stdout, /^PLUGIN list$/m);
  r = run(cli, ['fail'], env);
  assert.strictEqual(r.status, 7);
});

test('says how to install when nothing is installed', { skip: isWin && 'fake herdr is a shell script' }, () => {
  const { cli, env } = sandbox({ withPlugin: false });
  const r = run(cli, ['get', 'X'], env);
  assert.strictEqual(r.status, 1);
  assert.match(r.stderr, /not installed yet/);
});

test('no arguments updates an installed plugin, then runs its setup with the flags', { skip: isWin && 'fake herdr is a shell script' }, () => {
  const { cli, env, readLog } = sandbox({ withEngine: true });
  const r = run(cli, ['--yes'], env);
  assert.strictEqual(r.status, 0, r.stderr);
  assert.match(readLog(), /^herdr plugin update mazemaker\.secret$/m);
  assert.match(r.stdout, /^PLUGIN setup --yes$/m);
});

test('with the herdr TUI and Herdr.app, setup goes through the herdr plugin', { skip: isWin && 'fake herdr is a shell script' }, () => {
  const { cli, env } = sandbox({ withEngine: true, appHome: true, appOnPath: true });
  const r = run(cli, ['setup'], env);
  assert.strictEqual(r.status, 0, r.stderr);
  assert.match(r.stdout, /^PLUGIN setup$/m);
});

test('Herdr.app only (its home folder): setup runs from the packaged engine, herdr never', () => {
  const { cli, env, readLog } = sandbox({ withHerdr: false, withEngine: true, appHome: true });
  const r = run(cli, ['--yes'], env);
  assert.strictEqual(r.status, 0, r.stderr);
  assert.match(r.stdout, /^ENGINE setup --yes$/m);
  assert.strictEqual(readLog(), '');
});

test('Herdr.app only (herdr-app on PATH): found without running herdr-app; commands use the packaged engine', () => {
  const { cli, env, readLog } = sandbox({ withHerdr: false, withEngine: true, appOnPath: true });
  let r = run(cli, ['setup'], env);
  assert.strictEqual(r.status, 0, r.stderr);
  assert.match(r.stdout, /^ENGINE setup$/m);
  r = run(cli, ['fail'], env);
  assert.strictEqual(r.status, 7);
  assert.strictEqual(readLog(), '', 'herdr-app was run');
});

test('Herdr.app only without a packaged engine says how to get one', () => {
  const { cli, env } = sandbox({ withHerdr: false, appHome: true });
  const r = run(cli, [], env);
  assert.strictEqual(r.status, 1);
  assert.match(r.stderr, /no engine folder/);
});
