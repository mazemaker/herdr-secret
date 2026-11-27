---
name: herdr-secret
description: Use whenever you need a password, API key, token, login, connection string or any other credential from the user. Ask for it through a herdr popup form instead of having the user paste it into the chat, then use it without ever printing it.
---

# Getting credentials with herdr-secret

Never ask the user to paste a credential into the conversation, and never ask them to put one
in a file for you to read. Ask through `herdr-secret`: the user types the value into a popup
form in herdr, it goes straight into the OS credential store, and you only get a status line.

## Before asking

Check whether it is already stored:

```sh
herdr-secret list
```

Reuse an existing name instead of asking again.

## Ask

```sh
herdr-secret ask NAME "what it is for" [--fields "username,password"] [--timeout 300]
```

- `NAME`: letters, digits, `_ . -`, starting with a letter or digit. Make it say what the value
  is: `STAGING_DB`, `CLOUDFLARE_DEPLOY_TOKEN`.
- The description is shown to the user. Say exactly what the value is for and where it will be
  used, so they can judge whether to give it.
- `--fields` asks for several values in one form. Each is stored as `NAME.<field>`. Without it,
  one hidden value is stored as `NAME`. Fields named `user`, `username`, `email`, `login`,
  `host` or `url` are visible while typing; others are masked.
- You get one line back: `OK NAME ... [store]`, `CANCELLED NAME`, `TIMEOUT NAME` or
  `FAILED NAME`. On CANCELLED or TIMEOUT, tell the user and stop. Do not ask again in a loop.
- The command waits until the user answers. Run it in the foreground.

## Use

The value must never appear in your output, in a log, or on a command line (other processes
can read command lines).

```sh
herdr-secret get NAME | some-command-that-reads-stdin
VAR="$(herdr-secret get NAME)" some-command            # environment of one command only
herdr-secret get NAME | ssh host 'sudo tee /etc/app/token >/dev/null'
herdr-secret get API_TOKEN | sed 's/^/Authorization: Bearer /' | curl -sS -H @- https://api.example.com/
```

- `herdr-secret get` refuses to print to a terminal. Do not work around that with `cat`,
  `echo`, `tee` to the screen, or by writing the value to a file you then read.
- Do not pass a value as a command-line argument (`--password=...`).
- Check results by exit status or by the target's response, not by printing the value.

## Other commands

```sh
herdr-secret rm NAME      # deletes NAME and every NAME.<field>
```

## When it is not available

If `herdr-secret` is not on PATH, or you are not running inside herdr (`HERDR_PANE_ID` is not
set), say so and ask the user how they want to provide the credential. Do not quietly fall back
to asking them to paste it.
