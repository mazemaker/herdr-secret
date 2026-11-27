# herdr-secret

Installer and command for [herdr-secret](https://github.com/mazemaker/herdr-secret), a
[herdr](https://herdr.dev) plugin that lets coding agents ask you for credentials in a popup
form instead of having you paste them into the chat. Values go straight to the OS credential
store (macOS Keychain, Windows Credential Manager, Linux Secret Service); the agent only learns
whether you answered.

```sh
npx herdr-secret@latest
```

This installs the plugin from GitHub through `herdr plugin install` (herdr shows what it will
run and asks first), then walks you through setup: it checks the credential store, puts the
`herdr-secret` command on your PATH, and offers to teach the agents it finds (Claude Code,
Codex, OpenCode, Gemini CLI) to use it.

```sh
npx herdr-secret uninstall     # undo setup, optionally remove the plugin
```

Requires herdr 0.9.5 or newer, Python 3.8 or newer, and Node 18 or newer for this installer.
See the [full README](https://github.com/mazemaker/herdr-secret#readme) for how it works and
what it does and doesn't protect.
