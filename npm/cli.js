#!/usr/bin/env node
// herdr-secret from npm.
//   npx herdr-secret@latest            install (or update) herdr-secret, then run its setup
//   npx herdr-secret uninstall         undo setup and optionally remove the plugin
//   npx herdr-secret ask|get|list|rm   the herdr-secret command itself, once it is installed
// With the herdr TUI, the plugin comes from GitHub through `herdr plugin install`, so herdr shows
// what it will run before anything does. With only Herdr.app there is no plugin manager: setup
// runs from the engine/ folder in this package (the same files as the repository) and installs
// that and the Herdr.app mod. This file only finds herdr and Python and hands over.
'use strict';
const { spawnSync } = require('child_process');
const fs = require('fs');
const os = require('os');
const path = require('path');

const REPO = 'mazemaker/herdr-secret';
const ID = 'mazemaker.secret';
const ENGINE = path.join(__dirname, 'engine', 'bin', 'herdr_secret.py');
const isWin = process.platform === 'win32';

// The wizard and the form handle Ctrl-C themselves; don't let Node exit underneath them.
process.on('SIGINT', () => {});

function fail(msg) {
  console.error(`herdr-secret: ${msg}`);
  process.exit(1);
}

function capture(cmd, args) {
  const r = spawnSync(cmd, args, { encoding: 'utf8', stdio: ['ignore', 'pipe', 'ignore'] });
  return r.status === 0 ? r.stdout : null;
}

function run(cmd, args) {
  const r = spawnSync(cmd, args, { stdio: 'inherit' });
  if (r.error) fail(`could not run ${cmd}: ${r.error.message}`);
  return r.status === null ? 1 : r.status;
}

function onPath(name) {
  const exts = isWin ? ['', ...(process.env.PATHEXT || '.EXE;.CMD;.BAT').split(';')] : [''];
  return (process.env.PATH || '').split(path.delimiter)
    .some((dir) => dir && exts.some((x) => fs.existsSync(path.join(dir, name + x))));
}

// Herdr.app: its herdr-app command, or the home folder it writes when it first starts.
// Found without running herdr-app.
function hasHerdrApp() {
  const home = process.env.HERDR_APP_HOME || path.join(os.homedir(), '.config', 'herdr-app');
  return onPath('herdr-app') || fs.existsSync(home);
}

function findPython() {
  const candidates = isWin ? [['py', '-3'], ['python']] : [['python3']];
  for (const [cmd, ...pre] of candidates) {
    const ok = capture(cmd, [...pre, '-c', 'import sys; print(sys.version_info >= (3, 8))']);
    if (ok && ok.trim() === 'True') return [cmd, ...pre];
  }
  fail(isWin ? 'Python 3.8 or newer is required (python.org installer, which adds the py launcher)'
             : 'Python 3.8 or newer is required (python3 on PATH)');
}

function plugin() {
  const out = capture('herdr', ['plugin', 'list', '--json']);
  if (!out) return null;
  try {
    const plugins = (JSON.parse(out).result || {}).plugins || [];
    return plugins.find((p) => p.plugin_id === ID) || null;
  } catch {
    return null;
  }
}

const USAGE = `herdr-secret (npm)

  npx herdr-secret@latest            install or update herdr-secret, then run setup
  npx herdr-secret setup [--yes]     run setup again
  npx herdr-secret uninstall         undo setup, optionally remove the plugin
  npx herdr-secret ask|get|list|rm   the herdr-secret command (see herdr-secret help)

With the herdr TUI it installs the herdr plugin. With Herdr.app it installs the
herdr-secret command and the Herdr.app mod. With both, it does both.

Documentation: npm home herdr-secret`;

function main(argv) {
  if (['-h', '--help', 'help'].includes(argv[0])) {
    console.log(USAGE);
    return 0;
  }
  const tui = Boolean(capture('herdr', ['--version']));
  if (!tui && !hasHerdrApp()) fail('herdr (the TUI) or Herdr.app is required. Install one first, then run this again.');
  const py = findPython();
  const python = (script, args) => run(py[0], [...py.slice(1), '-I', script, ...args]);
  const packaged = fs.existsSync(ENGINE) ? ENGINE : null;
  const [cmd, ...rest] = argv;
  let p = tui ? plugin() : null;

  const flagsOnly = !cmd || cmd.startsWith('-');      // `npx herdr-secret --yes` means setup --yes
  if (flagsOnly || cmd === 'install' || cmd === 'setup') {
    const flags = flagsOnly ? argv : rest;
    if (!tui) {
      // Herdr.app only: setup copies this package's engine to a lasting place and adds the mod.
      if (!packaged) fail('this copy of the package has no engine folder. Run: npx herdr-secret@latest');
      return python(packaged, ['setup', ...flags]);
    }
    if (!p) {
      const code = run('herdr', ['plugin', 'install', REPO]);
      if (code !== 0) process.exit(code);
    } else if ((p.source || {}).kind !== 'local') {
      console.log('herdr-secret is already installed; updating it.');
      run('herdr', ['plugin', 'update', ID]);
    }
    p = plugin();
    if (!p || !p.plugin_root) fail(`herdr did not report the plugin folder; run: herdr plugin action invoke ${ID}.setup`);
    return python(path.join(p.plugin_root, 'bin', 'herdr_secret.py'), ['setup', ...flags]);
  }

  const script = p && p.plugin_root ? path.join(p.plugin_root, 'bin', 'herdr_secret.py') : packaged;
  if (!script) fail('herdr-secret is not installed yet. Run: npx herdr-secret@latest');
  return python(script, argv);
}

process.exit(main(process.argv.slice(2)));
