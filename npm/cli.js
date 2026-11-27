#!/usr/bin/env node
// herdr-secret from npm.
//   npx herdr-secret@latest            install (or update) the herdr plugin, then run its setup
//   npx herdr-secret uninstall         undo setup and optionally remove the plugin
//   npx herdr-secret ask|get|list|rm   the plugin's own command, once it is installed
// The plugin itself comes from GitHub through `herdr plugin install`, so herdr shows what it
// will run before anything does. This file only finds herdr and Python and hands over.
'use strict';
const { spawnSync } = require('child_process');
const path = require('path');

const REPO = 'mazemaker/herdr-secret';
const ID = 'mazemaker.secret';
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

  npx herdr-secret@latest            install or update the herdr plugin, then run setup
  npx herdr-secret setup [--yes]     run setup again
  npx herdr-secret uninstall         undo setup, optionally remove the plugin
  npx herdr-secret ask|get|list|rm   the plugin's own command (see herdr-secret help)

https://github.com/mazemaker/herdr-secret`;

function main(argv) {
  if (['-h', '--help', 'help'].includes(argv[0])) {
    console.log(USAGE);
    return 0;
  }
  if (!capture('herdr', ['--version'])) fail('herdr is required: https://herdr.dev');
  const py = findPython();
  const [cmd, ...rest] = argv;
  let p = plugin();

  if (!cmd || cmd === 'install' || cmd === 'setup') {
    const flags = cmd ? rest : argv;
    if (!p) {
      const code = run('herdr', ['plugin', 'install', REPO]);
      if (code !== 0) process.exit(code);
    } else if ((p.source || {}).kind !== 'local') {
      console.log('herdr-secret is already installed; updating it.');
      run('herdr', ['plugin', 'update', ID]);
    }
    p = plugin();
    if (!p || !p.plugin_root) fail(`herdr did not report the plugin folder; run: herdr plugin action invoke ${ID}.setup`);
    return run(py[0], [...py.slice(1), '-I', path.join(p.plugin_root, 'bin', 'herdr_secret.py'), 'setup', ...flags]);
  }

  if (!p || !p.plugin_root) fail('the herdr plugin is not installed yet. Run: npx herdr-secret@latest');
  return run(py[0], [...py.slice(1), '-I', path.join(p.plugin_root, 'bin', 'herdr_secret.py'), ...argv]);
}

process.exit(main(process.argv.slice(2)));
