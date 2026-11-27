# Security

herdr-secret handles credentials, so reports are welcome and taken seriously.

## Reporting a vulnerability

Use **Report a vulnerability** on the
[Security tab](https://github.com/mazemaker/herdr-secret/security) of this repository. It opens
a private advisory that only the maintainer can see. Please don't open a public issue for a
security problem.

Include what you found, how to reproduce it, and what an attacker gains. You should hear back
within a week.

## What is in scope

- A typed value reaching the agent's conversation, a log, a command line, a temp file, or
  anywhere other than the OS credential store
- Input from the requesting agent (name, description, fields) changing how the form looks or
  behaves beyond what the README describes
- A value stored somewhere other than where the result line says, lost, or mixed up with
  another
- The installer, setup or uninstall changing files they don't announce
- Anything that makes the npm package, the herdr plugin or the Herdr.app mod run code it
  shouldn't

## Known limits, not vulnerabilities

These are by design and documented in the README:

- Any process running as your user, including an agent that can run commands, can read stored
  values with `herdr-secret get`, and can start the form with a tampered environment.
- The "From" line is reported by the requesting agent and is not verified.
- On Linux without a Secret Service, values are stored in unencrypted 0600 files. Setup asks
  before using them.
- In Herdr.app, a process running as your user can read the request file (with its one-time
  pad) and the hex arguments the dialog passes while you save, and so recover the value.

## Releases

Releases are published from GitHub Actions by the `release` workflow through npm trusted
publishing. Each npm version carries a provenance attestation that names the exact commit and
workflow run that built it. A version without one did not come from this repository's workflow.
