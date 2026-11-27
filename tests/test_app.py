"""The Herdr.app path: `ask` in a Herdr.app terminal writes a request, the mod answers through
`app-store` or `app-cancel`. Runs in-process in a throwaway home, with the credential store
swapped for a file store inside it and a fake herdr-app on PATH, so the real home, credential
store and Herdr.app are never touched and no window opens. Only dummy values are used."""
import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
MOD = os.path.join(ROOT, "mods", "herdr-secret")
IS_WIN = os.name == "nt"
NODE = shutil.which("node")          # before PATH is narrowed below

home = tempfile.mkdtemp()
fakebin = os.path.join(home, "fakebin")
os.makedirs(fakebin)
app_log = os.path.join(home, "herdr-app.log")
herdr_log = os.path.join(home, "herdr.log")

# A fake herdr-app. FAKE_APP picks what the "mod" does when the dialog is opened: nothing
# (silent), cancel, or the mod's own deadline (timeout); fail makes herdr-app itself fail.
with open(os.path.join(fakebin, "fake_herdr_app.py"), "w") as f:
    f.write(f'''import json, os, subprocess, sys
args = sys.argv[1:]
open({app_log!r}, "a").write(" ".join(args) + "\\n")
mode = os.environ.get("FAKE_APP", "silent")
if mode == "fail":
    print("no Herdr window", file=sys.stderr)
    sys.exit(3)
if args[:2] == ["do", "mod.open"] and mode in ("cancel", "timeout"):
    path = os.path.join(os.path.expanduser("~"), ".local", "state", "herdr-secret", "app-request.json")
    r = json.load(open(path))
    subprocess.run([r["python"], "-I", r["script"], "app-cancel", r["req"]] + (["TIMEOUT"] if mode == "timeout" else []))
''')
with open(os.path.join(fakebin, "fake_herdr.py"), "w") as f:
    f.write(f'''import sys
open({herdr_log!r}, "a").write(" ".join(sys.argv[1:]) + "\\n")
print("fake herdr", file=sys.stderr)
sys.exit(1)
''')
for name, script in (("herdr-app", "fake_herdr_app.py"), ("fake-herdr", "fake_herdr.py")):
    if IS_WIN:
        with open(os.path.join(fakebin, name + ".cmd"), "w") as f:
            f.write(f'@"{sys.executable}" "%~dp0{script}" %*\r\n')
    else:
        p = os.path.join(fakebin, name)
        with open(p, "w") as f:
            f.write(f'#!/bin/sh\nexec "{sys.executable}" "$(dirname "$0")/{script}" "$@"\n')
        os.chmod(p, 0o755)
FAKE_HERDR = os.path.join(fakebin, "fake-herdr.cmd" if IS_WIN else "fake-herdr")


def has_herdr(d):
    return any(os.path.exists(os.path.join(d, n + x)) for n in ("herdr", "herdr-app", "hc")
               for x in ("", ".exe", ".cmd"))


PATH = os.pathsep.join([fakebin, os.path.dirname(sys.executable)] +
                       [d for d in os.environ.get("PATH", "").split(os.pathsep) if d and not has_herdr(d)])
os.environ.update(HOME=home, USERPROFILE=home, PATH=PATH, HERDR_APP_HOME=os.path.join(home, "app"),
                  LOCALAPPDATA=os.path.join(home, "AppData", "Local"),
                  HC_TERMINAL_SOCKET=os.path.join(home, "core.sock"), HC_TERMINAL_ID="t9")
for k in ("XDG_STATE_HOME", "XDG_DATA_HOME", "HERDR_PANE_ID", "HERDR_BIN_PATH", "HC_SCOPE",
          "HERDR_PLUGIN_ENTRYPOINT_ID", "FAKE_APP"):
    os.environ.pop(k, None)
found = shutil.which("herdr-app")
if not found or os.path.normcase(os.path.dirname(found)) != os.path.normcase(fakebin):
    sys.exit(f"refusing to run: herdr-app resolves to {found}, not the fake")

sys.path.insert(0, os.path.join(ROOT, "bin"))
import herdr_secret as hs  # noqa: E402

hs.backends = lambda: [hs.FileStore()]
raised = []
hs.app_raise = lambda: raised.append(True)      # never bring a real window forward

TRICKY = "pä\"ss\\wörd 日本 🔑 'q' $(x) `y` %PATH%"
failures = []


def check(cond, msg):
    if not cond:
        failures.append(msg)


def run(*args):
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code = hs.main(list(args))
    return code, out.getvalue().strip()


def request():
    try:
        with open(hs.app_request_path(), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def new_request(name, fields="", deadline=None):
    req = hs.app_request(name, "a test", fields, deadline or time.time() + 60)
    return req, request()


def encode(value, pad_hex, field, raw=None):
    """What the mod passes: the value's UTF-8 XORed with its slice of the pad, as hex."""
    data = value.encode("utf-8") if raw is None else raw
    key = bytes.fromhex(pad_hex)[field * hs.PAD_PER_FIELD:field * hs.PAD_PER_FIELD + len(data)]
    return bytes(a ^ b for a, b in zip(data, key)).hex()


def stored(acct):
    return hs.FileStore().fetch(acct)


def status(req):
    try:
        with open(hs.app_status_path(req), encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        return None


# ------------------------------------------------ app-store and app-cancel

req, r = new_request("ZZ_APP")
check(r["fields"] == [{"name": "value", "label": "Value", "hidden": True}], f"fields: {r['fields']}")
check(len(r["pad"]) == 2 * hs.PAD_PER_FIELD, "pad is not one slice per field")
check(r["from"] == "terminal t9" and r["purpose"] == "a test", "request header")
check(r["script"] == os.path.realpath(hs.__file__) and r["python"] == sys.executable, "request names the engine")
if not IS_WIN:
    check(os.stat(hs.app_request_path()).st_mode & 0o777 == 0o600, "request is not 0600")
    check(os.stat(hs.app_state_dir()).st_mode & 0o777 == 0o700, "state folder is not 0700")
hexed = encode(TRICKY, r["pad"], 0)
check(TRICKY.encode("utf-8").hex() != hexed, "the pad did not change the value")
code, out = run("app-store", req, hexed)
check(code == 0 and out == "OK file 0600", f"app-store: {code} {out!r}")
check(stored("ZZ_APP") == TRICKY, "value not stored exactly")
check(request() is None, "request (with its pad) left behind after app-store")
check(status(req) == "OK file 0600", f"status: {status(req)!r}")

code, out = run("app-store", req, encode("replayed", r["pad"], 0))
check(code == 1 and out.startswith("FAILED"), f"replay accepted: {out!r}")
check(stored("ZZ_APP") == TRICKY, "replay changed the stored value")

hs.FileStore().store("ZZ_LOGIN.username", "old-user")
req, r = new_request("ZZ_LOGIN", "username,password")
check(r["replaces"] == ["ZZ_LOGIN.username"], f"replaces: {r['replaces']}")
check([f["hidden"] for f in r["fields"]] == [False, True], "username shown, password masked")
check(len(r["pad"]) == 4 * hs.PAD_PER_FIELD, "pad for two fields")
code, out = run("app-store", req, encode("alicé", r["pad"], 0), encode(TRICKY, r["pad"], 1))
check(code == 0 and stored("ZZ_LOGIN.username") == "alicé" and stored("ZZ_LOGIN.password") == TRICKY,
      f"multi-field store: {out!r}")

# all or nothing: the second field fails, the first gets its old value back (or none)
real_store = hs.FileStore.store
hs.FileStore.store = lambda self, acct, value: False if acct.endswith(".password") else real_store(self, acct, value)
try:
    req, r = new_request("ZZ_LOGIN", "username,password")
    code, out = run("app-store", req, encode("new-user", r["pad"], 0), encode("new-pass", r["pad"], 1))
    check(code == 1 and out == "FAILED" and status(req) == "FAILED", f"failed store reported as {out!r}")
    check(stored("ZZ_LOGIN.username") == "alicé" and stored("ZZ_LOGIN.password") == TRICKY,
          "a failed multi-field save left a field changed")
    check(request() is None, "request left behind after a failed store")
    req, r = new_request("ZZ_FRESH", "username,password")
    run("app-store", req, encode("new-user", r["pad"], 0), encode("new-pass", r["pad"], 1))
    check(stored("ZZ_FRESH.username") is None, "a failed save left a new field stored")
finally:
    hs.FileStore.store = real_store

req, r = new_request("ZZ_CANCEL")
check(run("app-cancel", req)[0] == 0 and status(req) == "CANCELLED", "app-cancel")
check(request() is None and stored("ZZ_CANCEL") is None, "app-cancel left the request or stored")
check(run("app-cancel", req)[0] == 1, "a second app-cancel was accepted")
req, r = new_request("ZZ_CANCEL")
run("app-cancel", req, "TIMEOUT")
check(status(req) == "TIMEOUT", f"app-cancel TIMEOUT: {status(req)!r}")

# a request id that is not the open one is refused and leaves the open one alone
req, r = new_request("ZZ_FOREIGN")
for other in ("1-2-deadbeef", "../app-request", "", "x" * 65):
    code, out = run("app-store", other, encode("v", r["pad"], 0))
    check(code == 1 and out == "FAILED request expired", f"foreign id {other!r} accepted: {out!r}")
    check(run("app-cancel", other)[0] == 1, f"foreign id {other!r} cancelled")
check((request() or {}).get("req") == req and stored("ZZ_FOREIGN") is None, "a foreign id touched the request")
code, out = run("app-store", req, encode("v", r["pad"], 0))
check(code == 0 and stored("ZZ_FOREIGN") == "v", "the open request no longer works")
check(run("app-store")[0] == 2, "app-store without arguments")

# refused values: nothing is stored, and the request ends so its pad is never used again
for value, raw, reason in (("x" * (hs.MAX_VALUE + 1), None, "value too long"),
                           ("日" * (hs.MAX_VALUE + 1), None, "value too long"),
                           ("a\tb", None, "control characters are not allowed"),
                           ("a\nb", None, "control characters are not allowed"),
                           ("a\x85b", None, "control characters are not allowed"),
                           ("", None, "empty value"),
                           ("", b"\xff\xfe", "bad value encoding")):
    req, r = new_request("ZZ_BAD")
    code, out = run("app-store", req, encode(value, r["pad"], 0, raw))
    check(code == 1 and out == "FAILED " + reason, f"{value[:12]!r}: {out!r}")
    check(stored("ZZ_BAD") is None, f"{value[:12]!r} was stored")
    check(request() is None and status(req) == "FAILED " + reason, f"{value[:12]!r}: request not ended")
for args, reason in ((["00" * (hs.PAD_PER_FIELD + 1)], "value too long"), (["zz"], "bad value encoding"),
                     (["00", "00"], "wrong number of values"), ([], "wrong number of values")):
    req, r = new_request("ZZ_BAD")
    code, out = run("app-store", req, *args)
    check(code == 1 and out == "FAILED " + reason and request() is None, f"{args[:1]}: {out!r}")
req, r = new_request("ZZ_OLD", deadline=time.time() - 10)
code, out = run("app-store", req, encode("v", r["pad"], 0))
check(code == 1 and out == "FAILED request expired" and stored("ZZ_OLD") is None, f"expired: {out!r}")
os.unlink(hs.app_request_path())

# the mod's own encoding (utf8() and padded() from main.js) decodes to the same value
if NODE:
    req, r = new_request("ZZ_JS", "username,password")
    js = r'''
const fs = require("fs");
const src = fs.readFileSync(process.argv[1], "utf8");
const pad = src.match(/^const PAD_PER_FIELD = \d+;$/m)[0];
const fns = src.slice(src.indexOf("function utf8("), src.indexOf("export default class"));
const padded = new Function(pad + "\n" + fns + "\nreturn padded;")();
const [padHex, ...values] = JSON.parse(fs.readFileSync(0, "utf8"));
console.log(JSON.stringify(values.map((v, i) => padded(v, padHex, i))));
'''
    p = subprocess.run([NODE, "-e", js, os.path.join(MOD, "main.js")], input=json.dumps([r["pad"], "ü-user", TRICKY]),
                       capture_output=True, text=True, encoding="utf-8")
    hexes = json.loads(p.stdout or "[]")
    code, out = run("app-store", req, *hexes)
    check(code == 0 and stored("ZZ_JS.username") == "ü-user" and stored("ZZ_JS.password") == TRICKY,
          f"the mod's encoding does not round-trip: {out!r} {p.stderr[-300:]}")
else:
    print("node not found: skipped the check of the mod's encoding")


# ------------------------------------------------ ask in a Herdr.app terminal

def ask(*args, mode="silent"):
    os.environ["FAKE_APP"] = mode
    for p in (app_log, herdr_log):
        open(p, "w").close()
    return run("ask", *args)


def logged(p):
    with open(p, encoding="utf-8") as f:
        return f.read()


mod_dir = hs.app_mod_dir()
check(mod_dir.startswith(os.path.join(home, "app")), f"mod folder outside the test home: {mod_dir}")
code, out = ask("ZZ_ASK", "a test", "--fields", "username,password")
check(code == 1 and out.startswith("FAILED ZZ_ASK (username,password) [this terminal belongs to Herdr.app"),
      f"no mod: {out!r}")
check(logged(app_log) == "" and request() is None, "no mod, yet herdr-app ran or a request was written")

os.makedirs(mod_dir)
for name in hs.APP_MOD_FILES:
    shutil.copyfile(os.path.join(MOD, name), os.path.join(mod_dir, name))

code, out = ask("ZZ_ASK", "a test", "--fields", "username,password", mode="cancel")
check(code == 1 and out == "CANCELLED ZZ_ASK (username,password)", f"cancel through the mod: {out!r}")
check(logged(app_log).strip() == "do mod.open --name herdr-secret", f"herdr-app got: {logged(app_log)!r}")
check(raised == [True] and request() is None, "window not raised or request left behind")
check(stored("ZZ_ASK.username") is None, "a cancelled ask stored something")

code, out = ask("ZZ_ASK", "a test", mode="timeout")
check(code == 1 and out == "TIMEOUT ZZ_ASK", f"the mod's deadline: {out!r}")

code, out = ask("ZZ_ASK", "a test", mode="fail")
check(code == 1 and out == "FAILED ZZ_ASK [herdr-app: no Herdr window]" and request() is None,
      f"herdr-app failing: {out!r}")


def mod_saves(values):
    """Plays the mod: waits for the request, then answers with app-store."""
    end = time.time() + 20
    while time.time() < end:
        r = request()
        if r:
            hs.cmd_app_store([r["req"]] + [encode(v, r["pad"], i) for i, v in enumerate(values)])
            return
        time.sleep(0.05)


t = threading.Thread(target=mod_saves, args=(["bob", TRICKY],))
t.start()
code, out = ask("ZZ_ASK", "a test", "--fields", "username,password")
t.join()
check(code == 0 and "OK ZZ_ASK (username,password) [file 0600]" in out.splitlines(), f"save through the mod: {out!r}")
check(stored("ZZ_ASK.username") == "bob" and stored("ZZ_ASK.password") == TRICKY, "ask did not store exactly")
check(TRICKY not in out, "the value reached the agent's output")

# one dialog at a time: wait for the open request, then leave the closing dialog a moment
other = hs.app_request("ZZ_OTHER", "", "", time.time() + 60)
threading.Thread(target=lambda: (time.sleep(1.0), hs.cmd_app_cancel([other]))).start()
t0 = time.time()
code, out = ask("ZZ_ASK", "a test", mode="cancel")
check(out == "CANCELLED ZZ_ASK" and time.time() - t0 >= 1.5 and status(other) == "CANCELLED",
      f"queued ask: {out!r} after {time.time() - t0:.1f}s")

hs.app_request("ZZ_GONE", "", "", time.time() - 60)       # left by an asker that was killed
code, out = ask("ZZ_ASK", "a test", mode="cancel")
check(out == "CANCELLED ZZ_ASK", f"abandoned request not cleared: {out!r}")

other = hs.app_request("ZZ_OTHER", "", "", time.time() + 60)
code, out = ask("ZZ_ASK", "a test", "--timeout", "5")
check(out == "TIMEOUT ZZ_ASK [another credential request was still open]" and logged(app_log) == "",
      f"busy dialog: {out!r}")
check((request() or {}).get("req") == other, "a waiting ask touched the open request")
os.unlink(hs.app_request_path())

code, out = ask("ZZ_ASK", "a test", "--timeout", "5")
check(code == 1 and out == "TIMEOUT ZZ_ASK" and request() is None, f"nobody answers: {out!r}")

# a herdr TUI pane (HERDR_PANE_ID) still gets the TUI popup, even inside herdr-core
os.environ.update(HERDR_PANE_ID="w1:p1", HERDR_BIN_PATH=FAKE_HERDR)
code, out = ask("ZZ_ASK", "a test")
check(out.startswith("FAILED ZZ_ASK [herdr: fake herdr") and "plugin pane open" in logged(herdr_log),
      f"TUI routing: {out!r}")
check(logged(app_log) == "", "a herdr TUI pane opened the Herdr.app dialog")
os.environ.pop("HERDR_PANE_ID")
os.environ.pop("HC_TERMINAL_SOCKET")
code, out = ask("ZZ_ASK", "a test")
check("plugin pane open" in logged(herdr_log) and logged(app_log) == "", "outside Herdr.app, ask used the mod")

shutil.rmtree(home, ignore_errors=True)
print("\n".join(failures) if failures else "Herdr.app requests, answers and routing behave")
sys.exit(1 if failures else 0)
