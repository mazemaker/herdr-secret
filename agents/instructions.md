## Credentials

Never ask me to paste a password, token, API key or other credential into the chat, and never ask
me to put one in a file for you to read. Run `herdr-secret ask NAME "what it is for"` (add
`--fields "username,password"` for several values). I fill it in a herdr popup and you get back
only `OK`, `CANCELLED`, `TIMEOUT` or `FAILED`. Use a stored value with
`herdr-secret get NAME | <command>` or `VAR="$(herdr-secret get NAME)" <command>`, never print it
or put it on a command line, and check `herdr-secret list` before asking again.
