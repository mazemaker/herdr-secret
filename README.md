# herdr-secret

A [herdr](https://herdr.dev) plugin that lets a coding agent ask you for a credential
without the value ever entering the agent's conversation.

The agent runs one command. A form pops up in your herdr session, you type the values there,
and they go straight into your operating system's credential store. The agent only gets back
`OK`, `CANCELLED`, `TIMEOUT` or `FAILED`.

The form is a herdr popup, not a desktop window, so it shows up wherever you are attached:
at the machine, over SSH, or from a phone.

```
  Credential requested: DB_LOGIN
  From  : w3:p1  (as reported by the requester)
  For   : Staging database, read-only user

  ┌ Username ─────────────────────────────────────┐
  │ readonly                                      │
  └───────────────────────────────────────────────┘
  ┌ Password (hidden) ────────────────────────────┐
  │ ••••••••••••                                  │
  └───────────────────────────────────────────────┘

  [ Save ]  [ Cancel ]

  Tab/↑↓ move · Enter next · Ctrl-R reveal · Esc cancel
```

## Install

```sh
herdr plugin install mazemaker/herdr-secret
herdr plugin action invoke mazemaker.secret.setup     # Windows: mazemaker.secret.setup-windows
```

`setup` links the `herdr-secret` command into `~/.local/bin`. On Windows it prints the folder
to add to your PATH instead.

Requirements:

- herdr 0.9.5 or newer
- Python 3.8 or newer. Only the standard library is used.
  - macOS and Linux: `python3` on the PATH herdr runs with.
  - Windows: the `py` launcher from the python.org installer, or `python` on PATH.
- Linux, recommended: `secret-tool`, from `libsecret-tools` (Debian, Ubuntu) or `libsecret` (Fedora).

## Use

From an agent, or any shell inside herdr:

```sh
herdr-secret ask DB_LOGIN "Staging database, read-only user" --fields "username,password"
# OK DB_LOGIN (username,password) [macOS Keychain]

herdr-secret ask API_TOKEN "Deploy token"            # one hidden field, stored as API_TOKEN
herdr-secret get DB_LOGIN.password | psql ...        # refuses to print to a terminal
herdr-secret list                                     # names only
herdr-secret rm DB_LOGIN                              # removes DB_LOGIN and DB_LOGIN.<field>
```

- With `--fields`, each value is stored as `NAME.<field>`. Without it, one hidden value is
  stored as `NAME`.
- Fields named `user`, `username`, `email`, `login`, `host` or `url` are shown as you type.
  Every other field is masked. Override with `name:plain` or `name:hidden`. Up to 6 fields,
  1000 characters each.
- `--timeout` sets how long to wait, in seconds. The default is 300. When it runs out the form
  closes itself and nothing is saved.
- If a name already holds a value, the form says it will be replaced before you save.
- A multi-field save is all or nothing: if one field fails, the others are put back.

In the form: Tab or the arrow keys move between fields. Enter moves to the next field and
saves from the last one. Ctrl-R reveals the focused field, Ctrl-U clears it, and Esc or
Ctrl-C cancels.

### Telling your agent about it

Add something like this to your agent instructions (`CLAUDE.md`, `AGENTS.md`):

> When you need a credential, run `herdr-secret ask NAME "what it is for" [--fields ...]`
> instead of asking me to paste it. Use it with `herdr-secret get NAME | <command>` and never
> print it.

## Where values are stored

- **macOS:** the login Keychain, service `herdr-secret`.
- **Windows:** Credential Manager, as generic credentials named `herdr-secret:<NAME>`.
- **Linux:** the Secret Service (GNOME Keyring, KWallet) through `secret-tool`.
  - On a machine without one, such as a headless server, values go to files under
    `~/.local/share/herdr-secret/store/` with mode 0600. These files are **not encrypted**.
    The result line names the store used, so this never happens silently.

Values reach the store over stdin or the OS API, never on a command line. Nothing is written
to logs. The status file the agent reads holds only the outcome and the store's name.

The popup is not a herdr pane: it does not show up in `herdr pane list`, and `herdr pane read`
cannot reach it, so an agent cannot watch what you type.

## Language

The form is in English, or in Indonesian when your locale starts with `id`. To choose
explicitly, set `HERDR_SECRET_LANG=id` (or `en`) in the agent's environment, or put
`lang=id` in a file named `config` in the plugin's config directory
(`herdr plugin config-dir mazemaker.secret`).

## What this does and doesn't protect

It keeps credentials out of the agent's chat, transcript and logs, and puts them in an
encrypted store instead of a plaintext `.env`.

It does not hide them from programs running as you. An agent that runs `herdr-secret get`
can read what is stored, and so can anything else under your account. An agent that can run
commands could also start the form itself with a tampered environment. Treat all of this the
same way you treat letting an agent run commands at all.

The form shows which herdr pane asked and the agent's description of what the value is for.
Both come from the agent, so the "From" line is marked as reported, not verified. Read both
before typing: an agent can be talked into asking for something it shouldn't have.

## Development

```sh
herdr plugin link .                 # use a local checkout
python3 -I tests/test_store.py      # round-trips awkward values through this OS's store
python3 -I tests/test_form_pty.py   # drives the form in a pseudo-terminal (macOS, Linux)
```

CI runs these on macOS, on Linux with and without a Secret Service, and on Windows.

## License

MIT
