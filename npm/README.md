# herdr-secret

[![npm](https://img.shields.io/npm/v/herdr-secret)](https://www.npmjs.com/package/herdr-secret)
[![test](https://github.com/mazemaker/herdr-secret/actions/workflows/test.yml/badge.svg?branch=main)](https://github.com/mazemaker/herdr-secret/actions/workflows/test.yml)
[![license](https://img.shields.io/npm/l/herdr-secret)](https://github.com/mazemaker/herdr-secret/blob/main/LICENSE)

Let your coding agents ask you for credentials without the value ever entering their chat.

For [herdr](https://herdr.dev): the terminal app (the TUI) and Herdr.app, the desktop app. An
agent runs `herdr-secret ask`, a form pops up (a herdr popup, or a dialog in the Herdr.app
window), you type the value there, and it goes straight into your operating system's credential
store. The agent only gets back whether you answered.

![An agent asks for a database login; the human fills it in a herdr popup](https://raw.githubusercontent.com/mazemaker/herdr-secret/main/docs/form.png)

## Install

```sh
npx herdr-secret@latest
```

Works on macOS, Linux and Windows. What it installs depends on what you have:

- **herdr (the TUI):** the herdr plugin, from
  [GitHub](https://github.com/mazemaker/herdr-secret). herdr shows what it will run and asks first.
- **Herdr.app:** the `herdr-secret` command (into `~/.local/share/herdr-secret/engine`) and the
  herdr-secret mod for Herdr.app (into `~/.config/herdr-app/mods/herdr-secret`), which Herdr.app
  then checks.
- **Both:** the plugin and the mod.

Then setup walks you through the rest:

- checks that your credential store works,
- puts the `herdr-secret` command on your PATH,
- offers to teach the agents it finds (Claude Code, Codex, OpenCode, Gemini CLI) to ask
  through it instead of asking you to paste credentials,
- lets you try the form once.

`npx herdr-secret uninstall` removes everything setup added.

## Where values go

- **macOS:** login Keychain
- **Windows:** Credential Manager
- **Linux:** Secret Service (GNOME Keyring, KWallet). Without one, setup asks before falling
  back to 0600 files.

Values never pass through a command line or a log. In herdr the popup is not a herdr pane, so an
agent cannot read it; in Herdr.app the dialog is drawn by the app, not in a terminal.

## Trust

- No dependencies and no install scripts. The package is a small script (`cli.js`) that finds
  herdr and Python and hands over, plus `engine/`: the same Python program, agent instructions
  and Herdr.app mod as the GitHub repository, used only when the herdr TUI is not installed.
- `cli.js` runs exactly two programs, `herdr` and `python3` (`py` on Windows), without a shell,
  and makes no network requests. With the herdr TUI, the plugin download is done by
  `herdr plugin install`, which shows you what it will run first.
- Published from GitHub Actions with npm provenance: the attestation on each version names the
  commit and workflow run that built it.
- [What it protects and what it doesn't](https://github.com/mazemaker/herdr-secret#what-this-does-and-doesnt-protect),
  and [how to report a security problem](https://github.com/mazemaker/herdr-secret/blob/main/SECURITY.md).

Requires the herdr TUI 0.9.5+ or Herdr.app, Python 3.8+ (herdr-secret runs on it), and Node 18+
for this installer. MIT licensed.
