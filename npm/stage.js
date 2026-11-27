// Copies the engine (the Python program and its launchers, the agent instructions and the
// Herdr.app mod) from the repository into npm/engine, so the package can set herdr-secret up
// where there is no herdr TUI to install the plugin from GitHub. Run before packing or
// publishing; the release workflow does:
//   node npm/stage.js
// This script is not part of the package.
'use strict';
const fs = require('fs');
const path = require('path');

const REPO = path.join(__dirname, '..');
// The same list as ENGINE_FILES in bin/herdr_secret.py; npm/test checks that they agree.
const FILES = [
  'herdr-plugin.toml',
  'bin/herdr_secret.py', 'bin/herdr-secret', 'bin/herdr-secret.cmd',
  'agents/instructions.md', 'agents/SKILL.md',
  'mods/herdr-secret/main.js', 'mods/herdr-secret/mod.json',
];
const EXECUTABLE = ['bin/herdr_secret.py', 'bin/herdr-secret'];

function stage(dest = path.join(__dirname, 'engine')) {
  fs.rmSync(dest, { recursive: true, force: true });
  for (const f of FILES) {
    const to = path.join(dest, ...f.split('/'));
    fs.mkdirSync(path.dirname(to), { recursive: true });
    fs.copyFileSync(path.join(REPO, ...f.split('/')), to);
    fs.chmodSync(to, EXECUTABLE.includes(f) ? 0o755 : 0o644);
  }
  return dest;
}

module.exports = { FILES, REPO, stage };

if (require.main === module) {
  const dest = stage();
  console.log(`staged ${FILES.length} files in ${path.relative(process.cwd(), dest) || '.'}`);
}
