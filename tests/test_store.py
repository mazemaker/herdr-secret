"""Round-trips awkward values through whichever credential store this OS uses.
Non-interactive; run with `python -I tests/test_store.py`. Cleans up after itself."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "bin"))
import herdr_secret as hs  # noqa: E402

VALUES = ["plain", "deadbeef", "0x41", "with space", 'quote"inside', "back\\slash",
          "trailing ", "alicé", "pässwörd\"\\x", "日本語パス", "a" * 200]
want_backend = os.environ.get("EXPECT_BACKEND")
failures = []

print("backends:", [b.label for b in hs.backends()])
for i, v in enumerate(VALUES):
    acct = f"ZZ_TEST.v{i}"
    where = hs.store_any(acct, v)
    got = hs.fetch_any(acct)
    if where is None or got != v:
        failures.append(f"{acct}: stored={where!r} round-trip={'ok' if got == v else 'MISMATCH'}")
    elif want_backend and where != want_backend:
        failures.append(f"{acct}: stored in {where!r}, expected {want_backend!r}")
    v2 = v + "-updated"
    if hs.store_any(acct, v2) is None or hs.fetch_any(acct) != v2:
        failures.append(f"{acct}: update failed")

listed = [n for n in hs.names_any() if n.startswith("ZZ_TEST.")]
if len(listed) != len(VALUES):
    failures.append(f"list shows {len(listed)} of {len(VALUES)} test entries")
for i in range(len(VALUES)):
    if not hs.remove_any(f"ZZ_TEST.v{i}"):
        failures.append(f"ZZ_TEST.v{i}: remove failed")
if any(n.startswith("ZZ_TEST.") for n in hs.names_any()):
    failures.append("entries left after remove")
for bad in ("..", "../x", ".hidden", "a/b", ""):
    if hs.NAME_RE.fullmatch(bad):
        failures.append(f"NAME_RE accepts {bad!r}")
if hs.clean("x\x1b[2Jy\r\nz") != "x[2Jyz":
    failures.append("clean() leaves control characters")
if hs.clean("a\u202eb\u200bc\u2028d") != "abcd":
    failures.append("clean() leaves bidi / zero-width / separator characters")

# layout: the title and "From" line stay on screen whatever the agent asks for
fields, _ = hs.parse_fields("a,b,c,d,e,f")
form = hs.Form(fields, "N", "w1:p1", "long purpose " * 40, "Replaces the stored value of: N.a, N.b")
for cols, rows in ((76, 24), (76, 14), (40, 12), (120, 40)):
    lines, cursor = form.render(cols, rows)
    if len(lines) > rows or "Credential requested: N" not in lines[0] or "w1:p1" not in lines[1]:
        failures.append(f"layout {cols}x{rows}: {len(lines)} lines, header lost")
    if cursor and cursor[0] > rows:
        failures.append(f"layout {cols}x{rows}: cursor off screen")
form.values[0] = list("日本語")
form.reveal[0] = True
lines, cursor = form.render(76, 40)
if cursor[1] != 2 + 2 + 6 + 1:
    failures.append(f"wide characters: cursor column {cursor[1]}")

# the form says where the value goes, and only claims encryption where it is true
for label, want in (("macOS Keychain", "(encrypted)"), ("Secret Service", "(encrypted)"),
                    ("Windows Credential Manager", "(encrypted)"), ("file 0600", "(not encrypted)")):
    text, enc = hs.STORE_NOTE[label]
    if want not in text or enc != (want == "(encrypted)"):
        failures.append(f"store note for {label}: {text!r}")
note = hs.store_note()[0]
lines, _ = hs.Form(fields[:2], "N", "w1:p1", "short", "").render(76, 30)
import re  # noqa: E402
joined = " ".join(re.sub(r"\x1b\[[0-9;?]*[A-Za-z]", "", ln).strip() for ln in lines)
if "Saved : " not in joined or "never sent to the agent's chat" not in joined:
    failures.append("form does not say where the value goes")

# rm NAME also removes NAME.<field>
import subprocess  # noqa: E402
for a in ("ZZ_RM", "ZZ_RM.username", "ZZ_RM.password", "ZZ_RMX"):
    hs.store_any(a, "x")
script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "bin", "herdr_secret.py")
subprocess.run([sys.executable, "-I", script, "rm", "ZZ_RM"], stdout=subprocess.DEVNULL)
left = [n for n in hs.names_any() if n.startswith("ZZ_RM")]
if left != ["ZZ_RMX"]:
    failures.append(f"rm prefix: left {left}")
hs.remove_any("ZZ_RMX")

print("\n".join(failures) if failures else f"all {len(VALUES)} values round-tripped")
sys.exit(1 if failures else 0)
