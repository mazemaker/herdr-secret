#!/usr/bin/env python3
"""herdr-secret: an agent asks the human for a credential through a herdr popup form.

  herdr-secret ask NAME "what it is for" [--fields "username,password"] [--plain] [--timeout S]
      Opens the form in herdr and waits. Prints one line only:
      OK|CANCELLED|TIMEOUT|FAILED NAME [(fields)] [[where it was stored]]
      With --fields each value is stored as NAME.<field>; without, one hidden value as NAME.
  herdr-secret get NAME     value to stdout; refuses a terminal, so pipe it into the target
  herdr-secret list         stored names, never values
  herdr-secret rm NAME      delete NAME and any NAME.<field> entries
  herdr-secret setup        interactive setup: store check, PATH, agent instructions, test form
  herdr-secret uninstall    undo everything setup added (asks before deleting stored values)

Storage: macOS Keychain, Windows Credential Manager, Linux Secret Service (secret-tool),
or a 0600 file on Linux machines without a Secret Service. Values never pass through argv.
Python 3.8+, standard library only.
"""
import os
import re
import secrets
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unicodedata

IS_WIN = os.name == "nt"
IS_MAC = sys.platform == "darwin"
SERVICE = "herdr-secret"
PLUGIN_ID = "mazemaker.secret"
NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}")
REQ_RE = re.compile(r"[A-Za-z0-9-]{1,64}")
FIELD_RE = re.compile(r"[a-z0-9_]{1,32}")
MAX_FIELDS = 6
MAX_VALUE = 1000   # characters; Windows Credential Manager holds at most 2560 bytes of UTF-16
PLAIN_BY_DEFAULT = {"user", "username", "email", "login", "host", "url"}
DEVNULL = subprocess.DEVNULL
CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f]")

T = {"title": "Credential requested: ", "from": "From  : ", "for": "For   : ", "hidden": " (hidden)",
     "save": "[ Save ]", "cancel": "[ Cancel ]", "value": "Value", "unknown": "unknown",
     "help": "Tab/↑↓ move · Enter next · Ctrl-R reveal · Esc cancel",
     "required": "All fields are required.", "saved": "Saved to {}.", "failed": "Could not save.",
     "invalid": "Invalid request.", "reported": "  (as reported by the requester)",
     "replaces": "Replaces the stored value of: {}", "timeout": "Timed out. Nothing was saved.",
     "toolong": "Too long (max {} characters).", "saved_to": "Saved : ",
     "promise": "{} · never sent to the agent's chat"}

# What the form tells the human about where a value goes. Only the stores that really encrypt
# say so; the Linux file fallback says plainly that it does not.
STORE_NOTE = {"macOS Keychain": ("macOS Keychain (encrypted)", True),
              "Windows Credential Manager": ("Windows Credential Manager (encrypted)", True),
              "Secret Service": ("your keyring (encrypted)", True),
              "file 0600": ("a file only you can read (not encrypted)", False)}


def store_note():
    label = backends()[0].label
    text, encrypted = STORE_NOTE.get(label, (label, False))
    return T["promise"].format(text), encrypted


def clean(text, limit=300):
    """Text from the asking agent is shown in the form: strip control characters so it cannot
    move the cursor, recolour, overwrite the "From" line, or reorder text with bidi marks."""
    return "".join(ch for ch in text
                   if not CONTROL.match(ch) and unicodedata.category(ch) not in ("Cf", "Zl", "Zp", "Co", "Cs"))[:limit]


def cwidth(ch):
    if unicodedata.combining(ch):
        return 0
    return 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1


def dwidth(text):
    return sum(cwidth(c) for c in text)


def fit_head(text, width):
    out, w = [], 0
    for ch in text:
        if w + cwidth(ch) > width:
            break
        out.append(ch)
        w += cwidth(ch)
    return "".join(out), w


def fit_tail(text, width):
    out, w = [], 0
    for ch in reversed(text):
        if w + cwidth(ch) > width:
            break
        out.append(ch)
        w += cwidth(ch)
    return "".join(reversed(out)), w


def write_atomic(path, text):
    """Readers never see a half-written file: write a sibling temp file, then rename over."""
    d = os.path.dirname(path)
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".tmp-")   # created 0600
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def state_dir():
    if IS_WIN:
        base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~\\AppData\\Local")
    else:
        base = os.environ.get("XDG_STATE_HOME") or os.path.expanduser("~/.local/state")
    d = os.path.join(base, "herdr-secret")
    os.makedirs(d, mode=0o700, exist_ok=True)
    if not IS_WIN:
        os.chmod(d, 0o700)
    return d


# ---------------------------------------------------------------- storage backends

class MacKeychain:
    label = "macOS Keychain"

    def store(self, acct, value):
        if CONTROL.search(value):
            return False
        esc = value.replace("\\", "\\\\").replace('"', '\\"')
        cmd = f'add-generic-password -U -s {SERVICE} -a "{acct}" -w "{esc}"\n'
        subprocess.run(["security", "-i"], input=cmd, text=True, stdout=DEVNULL, stderr=DEVNULL)
        return self.fetch(acct) == value

    def fetch(self, acct):
        # `-w` alone prints non-ASCII data as bare hex, indistinguishable from a hex-looking
        # password. `-g` marks it: `password: 0x<HEX>  "..."` versus `password: "<text>"`.
        r = subprocess.run(["security", "find-generic-password", "-s", SERVICE, "-a", acct, "-g"],
                           capture_output=True, text=True, errors="replace")
        if r.returncode != 0:
            return None
        for line in r.stderr.splitlines():
            if line.startswith("password: 0x"):
                return bytes.fromhex(line[len("password: 0x"):].split()[0]).decode("utf-8", "replace")
            if line.startswith('password: "') and line.endswith('"'):
                return line[len('password: "'):-1]
            if line == "password: ":
                return ""
        return None

    def remove(self, acct):
        return subprocess.run(["security", "delete-generic-password", "-s", SERVICE, "-a", acct],
                              stdout=DEVNULL, stderr=DEVNULL).returncode == 0

    def names(self):
        r = subprocess.run(["security", "dump-keychain"], capture_output=True, text=True)
        out, acct = [], None
        for line in r.stdout.splitlines():
            if line.startswith("keychain:"):
                acct = None
            m = re.search(r'"acct"<blob>="(.*)"$', line)
            if m:
                acct = m.group(1)
            if f'"svce"<blob>="{SERVICE}"' in line and acct:
                out.append(acct)
        return out


class SecretService:
    label = "Secret Service"

    def _run(self, args, value=None):
        return subprocess.run(["secret-tool"] + args, input=value, text=True,
                              capture_output=True, timeout=30)

    def store(self, acct, value):
        r = self._run(["store", "--label", f"{SERVICE} {acct}", "service", SERVICE, "account", acct], value)
        return r.returncode == 0 and self.fetch(acct) == value

    def fetch(self, acct):
        r = self._run(["lookup", "service", SERVICE, "account", acct])
        return r.stdout.rstrip("\n") if r.returncode == 0 and r.stdout else None

    def remove(self, acct):
        return self._run(["clear", "service", SERVICE, "account", acct]).returncode == 0 and self.fetch(acct) is None

    def names(self):
        # `search` also prints each secret; only the account attribute is kept, nothing is shown.
        r = self._run(["search", "--all", "service", SERVICE])
        return [m.group(1) for m in re.finditer(r"^attribute\.account = (.+)$", r.stdout + r.stderr, re.M)]


class FileStore:
    label = "file 0600"

    def __init__(self):
        base = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
        self.dir = os.path.join(base, "herdr-secret", "store")

    def _path(self, acct):
        return os.path.join(self.dir, acct)

    def store(self, acct, value):
        os.makedirs(self.dir, mode=0o700, exist_ok=True)
        os.chmod(self.dir, 0o700)
        write_atomic(self._path(acct), value)
        return self.fetch(acct) == value

    def fetch(self, acct):
        try:
            with open(self._path(acct), encoding="utf-8") as f:
                return f.read()
        except OSError:
            return None

    def remove(self, acct):
        try:
            os.unlink(self._path(acct))
            return True
        except OSError:
            return False

    def names(self):
        try:
            return sorted(n for n in os.listdir(self.dir) if not n.startswith("."))
        except OSError:
            return []


class WinCred:
    label = "Windows Credential Manager"

    def __init__(self):
        import ctypes
        from ctypes import wintypes
        self.ct = ctypes

        class CREDENTIAL(ctypes.Structure):
            _fields_ = [("Flags", wintypes.DWORD), ("Type", wintypes.DWORD),
                        ("TargetName", wintypes.LPWSTR), ("Comment", wintypes.LPWSTR),
                        ("LastWritten", wintypes.FILETIME), ("CredentialBlobSize", wintypes.DWORD),
                        ("CredentialBlob", ctypes.POINTER(ctypes.c_ubyte)), ("Persist", wintypes.DWORD),
                        ("AttributeCount", wintypes.DWORD), ("Attributes", ctypes.c_void_p),
                        ("TargetAlias", wintypes.LPWSTR), ("UserName", wintypes.LPWSTR)]

        self.CRED = CREDENTIAL
        P = ctypes.POINTER(CREDENTIAL)
        a = ctypes.WinDLL("advapi32", use_last_error=True)
        a.CredWriteW.argtypes = [P, wintypes.DWORD]
        a.CredWriteW.restype = wintypes.BOOL
        a.CredReadW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.POINTER(P)]
        a.CredReadW.restype = wintypes.BOOL
        a.CredDeleteW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD]
        a.CredDeleteW.restype = wintypes.BOOL
        a.CredEnumerateW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD),
                                     ctypes.POINTER(ctypes.POINTER(P))]
        a.CredEnumerateW.restype = wintypes.BOOL
        a.CredFree.argtypes = [ctypes.c_void_p]
        a.CredFree.restype = None
        self.a = a

    @staticmethod
    def _target(acct):
        return f"{SERVICE}:{acct}"

    def store(self, acct, value):
        ct = self.ct
        blob = value.encode("utf-16-le")
        buf = ct.create_string_buffer(blob, len(blob))
        c = self.CRED()
        c.Type = 1                      # CRED_TYPE_GENERIC
        c.TargetName = self._target(acct)
        c.UserName = acct
        c.CredentialBlobSize = len(blob)
        c.CredentialBlob = ct.cast(buf, ct.POINTER(ct.c_ubyte))
        c.Persist = 2                   # CRED_PERSIST_LOCAL_MACHINE
        ok = self.a.CredWriteW(ct.byref(c), 0)
        ct.memset(buf, 0, len(blob))
        return bool(ok) and self.fetch(acct) == value

    def fetch(self, acct):
        ct = self.ct
        p = ct.POINTER(self.CRED)()
        if not self.a.CredReadW(self._target(acct), 1, 0, ct.byref(p)):
            return None
        try:
            c = p.contents
            return ct.string_at(c.CredentialBlob, c.CredentialBlobSize).decode("utf-16-le")
        finally:
            self.a.CredFree(p)

    def remove(self, acct):
        return bool(self.a.CredDeleteW(self._target(acct), 1, 0))

    def names(self):
        ct = self.ct
        from ctypes import wintypes
        n = wintypes.DWORD()
        arr = ct.POINTER(ct.POINTER(self.CRED))()
        if not self.a.CredEnumerateW(f"{SERVICE}:*", 0, ct.byref(n), ct.byref(arr)):
            return []
        try:
            return sorted(arr[i].contents.TargetName[len(SERVICE) + 1:] for i in range(n.value))
        finally:
            self.a.CredFree(arr)


def backends():
    if IS_MAC:
        return [MacKeychain()]
    if IS_WIN:
        return [WinCred()]
    chain = [SecretService()] if shutil.which("secret-tool") else []
    return chain + [FileStore()]


def store_any(acct, value):
    chain = backends()
    for i, b in enumerate(chain):
        try:
            stored = b.store(acct, value)
        except Exception:
            stored = False
        if not stored:
            continue
        for other in chain[:i] + chain[i + 1:]:
            try:
                other.remove(acct)
            except Exception:
                pass
        # A store that refused this write (say a locked keyring) may still hold an older copy,
        # and `get` asks it first. Report failure rather than leave the old value in front.
        return b.label if fetch_any(acct) == value else None
    return None


def fetch_any(acct):
    for b in backends():
        try:
            v = b.fetch(acct)
        except Exception:
            v = None
        if v is not None:
            return v
    return None


def remove_any(acct):
    hit = False
    for b in backends():
        try:
            hit = b.remove(acct) or hit
        except Exception:
            pass
    return hit


def names_any():
    out = set()
    for b in backends():
        try:
            out.update(b.names())
        except Exception:
            pass
    return sorted(out)


# ---------------------------------------------------------------- terminal form

BOLD, DIM, CYAN, RED, YELLOW, REV, RESET = "\x1b[1m", "\x1b[2m", "\x1b[36m", "\x1b[31m", "\x1b[33m", "\x1b[7m", "\x1b[0m"
GREEN = "\x1b[32m"


class Term:
    def __enter__(self):
        if IS_WIN:
            import msvcrt
            self.msvcrt = msvcrt
            os.system("")                      # turns on VT escape processing in the console
            try:
                sys.stdout.reconfigure(encoding="utf-8")
            except Exception:
                pass
        else:
            import termios
            import tty
            self.termios = termios
            self.fd = sys.stdin.fileno()
            self.saved = termios.tcgetattr(self.fd)
            tty.setraw(self.fd)
        self.out("\x1b[?1049h\x1b[H\x1b[2J")
        return self

    def __exit__(self, *exc):
        self.out("\x1b[?25h\x1b[?1049l")
        if not IS_WIN:
            self.termios.tcsetattr(self.fd, self.termios.TCSADRAIN, self.saved)

    @staticmethod
    def out(s):
        sys.stdout.write(s)
        sys.stdout.flush()

    @staticmethod
    def size():
        return shutil.get_terminal_size((76, 24))

    def key(self, timeout):
        """One key, or None after `timeout` seconds without input."""
        return self._key_win(timeout) if IS_WIN else self._key_unix(timeout)

    def _read(self, n):
        buf = b""
        while len(buf) < n:
            chunk = os.read(self.fd, n - len(buf))
            if not chunk:
                break
            buf += chunk
        return buf

    def _key_unix(self, timeout):
        import select
        if not select.select([self.fd], [], [], timeout)[0]:
            return None
        b = os.read(self.fd, 1)
        if not b:
            return "CANCEL"
        c = b[0]
        if c == 0x1B:
            if not select.select([self.fd], [], [], 0.03)[0]:
                return "ESC"
            nxt = os.read(self.fd, 1)
            if nxt not in (b"[", b"O"):
                return "ESC"
            seq = b""
            while len(seq) < 8:
                ch = os.read(self.fd, 1)
                seq += ch
                if ch.isalpha() or ch == b"~":
                    break
            return {b"A": "UP", b"B": "DOWN", b"C": "RIGHT", b"D": "LEFT", b"Z": "BTAB"}.get(seq[-1:])
        simple = {0x0D: "ENTER", 0x0A: "ENTER", 0x09: "TAB", 0x7F: "BS", 0x08: "BS",
                  0x03: "CANCEL", 0x04: "CANCEL", 0x15: "CLEAR", 0x12: "REVEAL"}
        if c in simple:
            return simple[c]
        if c < 0x20:
            return None
        n = 1 if c < 0x80 else 2 if c >> 5 == 0b110 else 3 if c >> 4 == 0b1110 else 4 if c >> 3 == 0b11110 else 1
        if n > 1:
            b += self._read(n - 1)
        ch = b.decode("utf-8", "ignore")
        return ("CHAR", ch) if ch and ch.isprintable() else None

    def _key_win(self, timeout):
        m = self.msvcrt
        end = time.time() + timeout
        while not m.kbhit():
            if time.time() >= end:
                return None
            time.sleep(0.03)
        ch = m.getwch()
        if ch in ("\x00", "\xe0"):
            return {"H": "UP", "P": "DOWN", "K": "LEFT", "M": "RIGHT", "\x0f": "BTAB"}.get(m.getwch())
        simple = {"\r": "ENTER", "\n": "ENTER", "\t": "TAB", "\x08": "BS", "\x1b": "ESC",
                  "\x03": "CANCEL", "\x15": "CLEAR", "\x12": "REVEAL"}
        if ch in simple:
            return simple[ch]
        return ("CHAR", ch) if ch.isprintable() else None


def wrap(text, width):
    width = max(8, width)
    lines, cur = [], ""
    for word in text.split():
        while dwidth(word) > width:                 # hard-split words longer than a line
            head, _ = fit_head(word, width)
            if cur:
                lines.append(cur)
                cur = ""
            lines.append(head)
            word = word[len(head):]
        if cur and dwidth(cur) + 1 + dwidth(word) > width:
            lines.append(cur)
            cur = word
        elif word:
            cur = f"{cur} {word}" if cur else word
    return lines + [cur] if cur else (lines or [""])


class Form:
    def __init__(self, fields, name, who, purpose, notice, store=None):
        self.fields, self.name, self.who, self.purpose, self.notice = fields, name, who, purpose, notice
        self.store = store if store is not None else store_note()   # (text, encrypted)
        self.values = [[] for _ in fields]
        self.reveal = [False] * len(fields)
        self.focus = 0          # 0..n-1 fields, n = save, n+1 = cancel
        self.error = ""

    def build(self, cols, compact, pmax, nmax, blanks, show_help):
        n = len(self.fields)
        bw = max(24, min(cols - 4, 68))
        lines, cursor = [], None

        def add(text, style=""):
            text, _ = fit_head(text, max(10, cols - 3))
            lines.append(f"  {style}{text}{RESET}" if style else f"  {text}")

        add(T["title"] + self.name, BOLD)
        add(T["from"] + self.who + T["reported"], DIM)
        pad = " " * dwidth(T["for"])
        for i, part in enumerate(wrap(self.purpose, bw - len(pad))[:pmax]):
            add((T["for"] if i == 0 else pad) + part)
        text, encrypted = self.store
        for i, part in enumerate(wrap(text, bw - len(pad))[:max(1, pmax - 1)]):
            add((T["saved_to"] if i == 0 else pad) + part, GREEN if encrypted else YELLOW)
        if self.notice:
            for part in wrap(self.notice, bw)[:nmax]:
                add(part, YELLOW)
        if blanks:
            add("")
        for i, (_f, label, hidden) in enumerate(self.fields):
            style = CYAN + BOLD if self.focus == i else ""
            masked = hidden and not self.reveal[i]
            raw = "".join(self.values[i])
            full = ("•" * len(raw)) if masked else raw
            if compact:
                prefix = f"{'›' if self.focus == i else ' '} {label}{T['hidden'] if masked else ''}: "
                shown, w = fit_tail(full, max(4, bw - dwidth(prefix)))
                lines.append(f"  {style}{prefix}{RESET}{shown}")
                if self.focus == i:
                    cursor = (len(lines), 2 + dwidth(prefix) + w + 1)
            else:
                tag = f" {label}{T['hidden'] if masked else ''} "
                inner = bw - 4
                shown, w = fit_tail(full, inner)
                add("┌" + tag + "─" * max(0, bw - 2 - dwidth(tag)) + "┐", style)
                lines.append(f"  {style}│{RESET} {shown}{' ' * (inner - w)} {style}│{RESET}")
                if self.focus == i:
                    cursor = (len(lines), 2 + 2 + w + 1)
                add("└" + "─" * (bw - 2) + "┘", style)
        if self.error:
            add(self.error, RED)
        elif blanks:
            add("")
        save = f"{REV}{BOLD}{T['save']}{RESET}" if self.focus == n else f"{BOLD}{T['save']}{RESET}"
        cancel = f"{REV}{T['cancel']}{RESET}" if self.focus == n + 1 else T["cancel"]
        lines.append(f"  {save}  {cancel}")
        if show_help:
            if blanks:
                add("")
            add(T["help"], DIM)
        return lines, cursor

    def render(self, cols, rows):
        """Largest layout that fits. The title and "From" lines always come first and stay."""
        for cfg in ((False, 3, 2, True, True), (True, 3, 2, True, True), (True, 2, 1, False, True),
                    (True, 1, 1, False, True), (True, 1, 1, False, False)):
            lines, cursor = self.build(cols, *cfg)
            if len(lines) <= rows:
                break
        lines = lines[:rows]
        if cursor and cursor[0] > rows:
            cursor = None
        return lines, cursor

    def handle(self, k):
        """Returns "SAVE", "CANCEL" or None."""
        n = len(self.fields)
        self.error = ""
        if k in ("ESC", "CANCEL"):
            return "CANCEL"
        if k in ("TAB", "DOWN"):
            self.focus = (self.focus + 1) % (n + 2)
        elif k in ("BTAB", "UP"):
            self.focus = (self.focus - 1) % (n + 2)
        elif k in ("LEFT", "RIGHT") and self.focus >= n:
            self.focus = n + 1 if self.focus == n else n
        elif k == "ENTER":
            if self.focus == n + 1:
                return "CANCEL"
            if self.focus >= n - 1:
                empty = [i for i in range(n) if not self.values[i]]
                if not empty:
                    return "SAVE"
                self.focus, self.error = empty[0], T["required"]
            else:
                self.focus += 1
        elif self.focus < n:
            v = self.values[self.focus]
            if k == "BS" and v:
                v.pop()
            elif k == "CLEAR":
                v.clear()
            elif k == "REVEAL":
                self.reveal[self.focus] = not self.reveal[self.focus]
            elif isinstance(k, tuple):
                if len(v) >= MAX_VALUE:
                    self.error = T["toolong"].format(MAX_VALUE)
                else:
                    v.append(k[1])
        return None


def run_form(term, form, deadline):
    last = None
    while True:
        if deadline and time.time() >= deadline:
            return "TIMEOUT"
        cols, rows = term.size()
        lines, cursor = form.render(cols, rows)
        frame = "\x1b[H" + "\r\n".join(line + "\x1b[K" for line in lines) + "\x1b[J"
        frame += f"\x1b[{cursor[0]};{cursor[1]}H\x1b[?25h" if cursor else "\x1b[?25l"
        if frame != last:                         # only after a key or a resize
            term.out(frame)
            last = frame
        k = term.key(1.0)
        if k is None:
            continue
        action = form.handle(k)
        if action == "CANCEL":
            return None
        if action == "SAVE":
            return ["".join(v) for v in form.values]


def parse_fields(spec, value_label="Value"):
    if not spec:
        return [("value", value_label, True)], True
    out = []
    for part in spec.split(","):
        fname, _, kind = part.strip().partition(":")
        if not FIELD_RE.fullmatch(fname) or kind not in ("", "plain", "hidden"):
            raise ValueError(f"invalid field: {part.strip()!r}")
        hidden = (kind == "hidden") if kind else fname not in PLAIN_BY_DEFAULT
        out.append((fname, fname.replace("_", " ").capitalize(), hidden))
    if len(out) > MAX_FIELDS or len({f for f, _, _ in out}) != len(out):
        raise ValueError(f"up to {MAX_FIELDS} fields, no duplicates")
    return out, len(out) == 1 and out[0][0] == "value"


def cmd_form():
    name = os.environ.get("SECRET_NAME", "")
    req = os.environ.get("SECRET_REQ", "")
    try:
        deadline = float(os.environ.get("SECRET_DEADLINE") or 0)
    except ValueError:
        deadline = 0
    reported = []

    def status(s):
        if not reported and REQ_RE.fullmatch(req):
            write_atomic(os.path.join(state_dir(), req + ".status"), s + "\n")
            reported.append(s)

    def bail(*_):
        raise SystemExit(1)

    guarded = () if IS_WIN else (signal.SIGHUP, signal.SIGTERM)
    for sig in guarded:
        signal.signal(sig, bail)
    try:
        if not NAME_RE.fullmatch(name) or not REQ_RE.fullmatch(req):
            print(T["invalid"])
            time.sleep(2)
            return 1
        fields, single = parse_fields(os.environ.get("SECRET_FIELDS", "").strip(), T["value"])
        accts = [name if single else f"{name}.{f}" for f, _, _ in fields]
        prev = {a: fetch_any(a) for a in accts}
        existing = [a for a in accts if prev[a] is not None]
        notice = T["replaces"].format(", ".join(existing)) if existing else ""
        who = clean(os.environ.get("SECRET_FROM", ""), 80) or T["unknown"]
        form = Form(fields, name, who, clean(os.environ.get("SECRET_PROMPT", "")), notice)
        try:
            with Term() as t:
                result = run_form(t, form, deadline)
        except KeyboardInterrupt:
            result = None
        if result is None:
            status("CANCELLED")
            return 1
        if result == "TIMEOUT":
            status("TIMEOUT")
            print("\n  " + T["timeout"])
            time.sleep(1.5)
            return 1
        if guarded:   # a stored value must never be reported as CANCELLED
            signal.pthread_sigmask(signal.SIG_BLOCK, set(guarded))
        where, done = set(), []
        for a, value in zip(accts, result):
            label = store_any(a, value)
            if label is None:
                break
            where.add(label)
            done.append(a)
        ok = len(done) == len(accts)
        if not ok:    # roll back, so a login is never left half new and half old
            for a in done:
                if prev[a] is None:
                    remove_any(a)
                else:
                    store_any(a, prev[a])
        result[:] = []
        prev.clear()
        status(("OK " + ", ".join(sorted(where))) if ok else "FAILED")
        if guarded:
            signal.pthread_sigmask(signal.SIG_UNBLOCK, set(guarded))
        print("\n  " + (T["saved"].format(", ".join(sorted(where))) if ok else T["failed"]))
        time.sleep(0.8 if ok else 3)
        return 0 if ok else 1
    finally:
        status("CANCELLED")


# ---------------------------------------------------------------- agent-side commands

def cmd_ask(argv):
    if not argv:
        print(__doc__.strip(), file=sys.stderr)
        return 2
    name, rest = argv[0], argv[1:]
    purpose = rest.pop(0) if rest and not rest[0].startswith("--") else ""
    fields, timeout = "", 300
    while rest:
        a = rest.pop(0)
        if a == "--fields" and rest:
            fields = rest.pop(0)
        elif a == "--plain":
            fields = "value:plain"
        elif a == "--timeout" and rest:
            timeout = max(5, int(rest.pop(0)))
        else:
            print(f"unknown option {a!r}", file=sys.stderr)
            return 2
    if not NAME_RE.fullmatch(name):
        print("invalid NAME: letters, digits, _ . - and starting with a letter or digit", file=sys.stderr)
        return 2
    try:
        parse_fields(fields)
    except ValueError as e:
        print(str(e), file=sys.stderr)
        return 2
    req = f"{int(time.time())}-{os.getpid()}-{secrets.token_hex(4)}"
    deadline = time.time() + timeout
    herdr = os.environ.get("HERDR_BIN_PATH") or shutil.which("herdr") or "herdr"
    cmd = [herdr, "plugin", "pane", "open", "--plugin", PLUGIN_ID,
           "--entrypoint", "ask-windows" if IS_WIN else "ask", "--focus"]
    env = {"SECRET_NAME": name, "SECRET_PROMPT": clean(purpose), "SECRET_FIELDS": fields,
           "SECRET_REQ": req, "SECRET_DEADLINE": f"{deadline:.0f}",
           "SECRET_FROM": os.environ.get("HERDR_PANE_ID", "outside herdr")}
    for k, v in env.items():
        cmd += ["--env", f"{k}={v}"]
    tag = f" ({fields})" if fields and fields != "value:plain" else ""
    try:
        r = subprocess.run(cmd, stdout=DEVNULL, stderr=subprocess.PIPE, text=True)
    except OSError as e:
        print(f"FAILED {name}{tag} [herdr not runnable: {e}]")
        return 1
    if r.returncode != 0:
        print(f"FAILED {name}{tag} [herdr: {r.stderr.strip()[:200]}]")
        return 1
    path = os.path.join(state_dir(), req + ".status")
    while time.time() < deadline + 5:            # the form closes itself at the deadline
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                s = f.read().strip()
            os.unlink(path)
            word, _, where = s.partition(" ")
            print(f"{word} {name}{tag}" + (f" [{where}]" if where else ""))
            return 0 if word == "OK" else 1
        time.sleep(0.5)
    print(f"TIMEOUT {name}{tag}")
    return 1


# ---------------------------------------------------------------- setup wizard

ROOT = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
BEGIN, END = "<!-- herdr-secret:begin -->", "<!-- herdr-secret:end -->"
SH_BEGIN, SH_END = "# herdr-secret:begin", "# herdr-secret:end"
MIN_HERDR = (0, 9, 5)
TEST_NAME = "HERDR_SECRET_SETUP_TEST"
AGENTS = [  # label, config dir, command, instruction file, skill dir
    ("Claude Code", "~/.claude", "claude", "~/.claude/CLAUDE.md", "~/.claude/skills/herdr-secret"),
    ("Codex", "~/.codex", "codex", "~/.codex/AGENTS.md", None),
    ("OpenCode", "~/.config/opencode", "opencode", "~/.config/opencode/AGENTS.md", None),
    ("Gemini CLI", "~/.gemini", "gemini", "~/.gemini/GEMINI.md", None),
]


def herdr_bin():
    return os.environ.get("HERDR_BIN_PATH") or shutil.which("herdr") or "herdr"


def herdr_json(*args):
    import json
    try:
        r = subprocess.run([herdr_bin(), *args], capture_output=True, text=True, timeout=30)
        return json.loads(r.stdout) if r.returncode == 0 and r.stdout.strip() else None
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return None


def herdr_version():
    try:
        out = subprocess.run([herdr_bin(), "--version"], capture_output=True, text=True, timeout=15).stdout
    except (OSError, subprocess.TimeoutExpired):
        return None
    m = re.search(r"(\d+)\.(\d+)\.(\d+)", out)
    return tuple(int(x) for x in m.groups()) if m else None


def installed_plugin():
    d = herdr_json("plugin", "list", "--json") or {}
    for p in (d.get("result") or {}).get("plugins", []):
        if p.get("plugin_id") == PLUGIN_ID:
            return p
    return None


def plugin_version():
    try:
        with open(os.path.join(ROOT, "herdr-plugin.toml"), encoding="utf-8") as f:
            m = re.search(r'^version\s*=\s*"([^"]+)"', f.read(), re.M)
            return m.group(1) if m else "?"
    except OSError:
        return "?"


def tilde(path):
    home = os.path.expanduser("~")
    return "~" + path[len(home):] if path.startswith(home) else path


def put_block(path, body, begin=BEGIN, end=END):
    """Insert or replace our marked block, keeping the rest of the file and its permissions."""
    path = os.path.expanduser(path)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    try:
        with open(path, encoding="utf-8") as f:
            old = f.read()
        mode = os.stat(path).st_mode & 0o777
    except FileNotFoundError:
        old, mode = "", 0o644
    block = f"{begin}\n{body.strip()}\n{end}\n"
    pat = re.compile(re.escape(begin) + r".*?" + re.escape(end) + r"\n?", re.S)
    if pat.search(old):
        new = pat.sub(lambda _m: block, old)
    else:
        new = old + ("\n" if old and not old.endswith("\n") else "") + ("\n" if old.strip() else "") + block
    if new != old:
        write_atomic(path, new)
        if not IS_WIN:
            os.chmod(path, mode)
    return path


def drop_block(path, begin=BEGIN, end=END):
    path = os.path.expanduser(path)
    try:
        with open(path, encoding="utf-8") as f:
            old = f.read()
        mode = os.stat(path).st_mode & 0o777
    except FileNotFoundError:
        return False
    new = re.sub(r"\n?" + re.escape(begin) + r".*?" + re.escape(end) + r"\n?", "\n", old, flags=re.S)
    if new == old:
        return False
    new = new.strip("\n") + "\n" if new.strip() else ""
    write_atomic(path, new)
    if not IS_WIN:
        os.chmod(path, mode)
    return True


def shell_profile():
    sh = os.path.basename(os.environ.get("SHELL", ""))
    if sh == "zsh":
        return os.path.expanduser("~/.zshrc"), 'export PATH="$HOME/.local/bin:$PATH"'
    if sh == "bash":
        prof = "~/.bash_profile" if IS_MAC and os.path.exists(os.path.expanduser("~/.bash_profile")) else "~/.bashrc"
        return os.path.expanduser(prof), 'export PATH="$HOME/.local/bin:$PATH"'
    if sh == "fish":
        return os.path.expanduser("~/.config/fish/conf.d/herdr-secret.fish"), "fish_add_path -g $HOME/.local/bin"
    return None, None


def win_user_path(add=None, remove=None):
    import ctypes
    import winreg
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment", 0, winreg.KEY_READ | winreg.KEY_WRITE) as k:
        try:
            cur, kind = winreg.QueryValueEx(k, "Path")
        except FileNotFoundError:
            cur, kind = "", winreg.REG_EXPAND_SZ
        parts = [p for p in cur.split(";") if p]
        same = lambda a, b: os.path.normcase(a.rstrip("\\")) == os.path.normcase(b.rstrip("\\"))  # noqa: E731
        if add and not any(same(p, add) for p in parts):
            parts.append(add)
        if remove:
            parts = [p for p in parts if not same(p, remove)]
        new = ";".join(parts)
        if new == cur:
            return False
        winreg.SetValueEx(k, "Path", 0, kind, new)
    ctypes.windll.user32.SendMessageTimeoutW(0xFFFF, 0x1A, 0, "Environment", 0x2, 2000, None)
    return True


def linux_keyring_install():
    for tool, cmd in (("apt-get", ["sudo", "apt-get", "install", "-y", "libsecret-tools"]),
                      ("dnf", ["sudo", "dnf", "install", "-y", "libsecret"]),
                      ("pacman", ["sudo", "pacman", "-S", "--needed", "--noconfirm", "libsecret"]),
                      ("zypper", ["sudo", "zypper", "install", "-y", "libsecret-tools"])):
        if shutil.which(tool):
            return cmd
    return None


class Wizard:
    def __init__(self, interactive):
        self.interactive = interactive

    def step(self, i, total, title):
        print(f"\n{BOLD}{i}/{total}  {title}{RESET}")

    def ok(self, text):
        print(f"     {GREEN}✓{RESET} {text}")

    def warn(self, text):
        print(f"     {YELLOW}!{RESET} {text}")

    def bad(self, text):
        print(f"     {RED}✗{RESET} {text}")

    def note(self, text):
        print(f"       {DIM}{text}{RESET}")

    def yes(self, question, default=True):
        if not self.interactive:
            return default
        hint = "Y/n" if default else "y/N"
        while True:
            try:
                a = input(f"     {question} [{hint}] ").strip().lower()
            except EOFError:
                return default
            if not a:
                return default
            if a in ("y", "yes"):
                return True
            if a in ("n", "no"):
                return False


def setup_marker():
    return os.path.join(state_dir(), "setup.json")


def write_marker(status, **extra):
    import json
    write_atomic(setup_marker(), json.dumps(dict(status=status, version=plugin_version(), **extra)) + "\n")


def setup_store(w):
    v = secrets.token_hex(8)
    where = store_any(TEST_NAME, v)
    remove_any(TEST_NAME)
    if where is None:
        w.bad("Could not write to any credential store.")
        return False
    if where != FileStore.label:
        w.ok(f"{where} works")
        return True
    w.warn("No Secret Service (GNOME Keyring, KWallet) is reachable on this machine.")
    w.note("Without one, values are saved as unencrypted files that only your user can read (mode 0600).")
    cmd = None if shutil.which("secret-tool") else linux_keyring_install()
    if cmd and w.yes(f"Install secret-tool now ({' '.join(cmd)})?", default=False):
        subprocess.run(cmd)
        if shutil.which("secret-tool"):
            where = store_any(TEST_NAME, v)
            remove_any(TEST_NAME)
            if where == SecretService.label:
                w.ok("Secret Service works")
                return True
            w.warn("secret-tool is installed but no keyring is running (common on servers without a desktop).")
    if not w.yes("Use the 0600 file store on this machine?", default=True):
        w.bad("Setup stopped: no store you accepted.")
        return False
    w.ok("Using 0600 files in ~/.local/share/herdr-secret/store")
    return True


def setup_command(w):
    """Put herdr-secret on PATH. Returns what was changed, for uninstall."""
    changed = {}
    bindir_win = os.path.join(ROOT, "bin")
    if IS_WIN:
        if w.yes(f"Add {bindir_win} to your user PATH?"):
            if win_user_path(add=bindir_win):
                changed["winpath"] = bindir_win
                w.ok("Added to your user PATH. New terminals will find herdr-secret.")
            else:
                w.ok("Already on your user PATH.")
        else:
            w.warn(f"Skipped. Agents need {bindir_win} on PATH to run herdr-secret.")
        return changed
    target = os.path.join(ROOT, "bin", "herdr-secret")
    bindir = os.path.expanduser("~/.local/bin")
    link = os.path.join(bindir, "herdr-secret")
    os.makedirs(bindir, exist_ok=True)
    if os.path.lexists(link) and not (os.path.islink(link) and os.readlink(link).endswith(os.path.join("bin", "herdr-secret"))):
        w.bad(f"{tilde(link)} exists and is not ours; left it alone.")
        return changed
    if os.path.lexists(link):
        os.unlink(link)
    os.symlink(target, link)
    w.ok(f"Linked {tilde(link)}")
    if bindir in [os.path.expanduser(p) for p in os.environ.get("PATH", "").split(os.pathsep)]:
        w.ok("~/.local/bin is on PATH")
        return changed
    profile, line = shell_profile()
    if not profile:
        w.warn("~/.local/bin is not on PATH, and your shell's profile is unknown. Add it there.")
    elif w.yes(f"~/.local/bin is not on PATH. Add it in {tilde(profile)}?"):
        put_block(profile, line, SH_BEGIN, SH_END)
        changed["profile"] = profile
        w.ok(f"Added to {tilde(profile)}. New shells will find herdr-secret.")
    return changed


def setup_agents(w):
    with open(os.path.join(ROOT, "agents", "instructions.md"), encoding="utf-8") as f:
        instructions = f.read()
    with open(os.path.join(ROOT, "agents", "SKILL.md"), encoding="utf-8") as f:
        skill = f.read()
    found = [a for a in AGENTS if os.path.isdir(os.path.expanduser(a[1])) or shutil.which(a[2])]
    if not found:
        w.note("No supported agent found (Claude Code, Codex, OpenCode, Gemini CLI).")
        w.note(f"For another agent, add {tilde(os.path.join(ROOT, 'agents', 'instructions.md'))} to its instructions.")
        return []
    for label, _d, _c, instr, skill_dir in found:
        files = [instr] + ([skill_dir + "/SKILL.md"] if skill_dir else [])
        w.note(f"{label}: {', '.join(files)}")
    if not w.yes(f"Teach {', '.join(a[0] for a in found)} to ask through herdr-secret?"):
        w.warn("Skipped. Run setup again to add them later.")
        return []
    done = []
    for label, _d, _c, instr, skill_dir in found:
        put_block(instr, instructions)
        if skill_dir:
            d = os.path.expanduser(skill_dir)
            os.makedirs(d, exist_ok=True)
            write_atomic(os.path.join(d, "SKILL.md"), skill)
            if not IS_WIN:
                os.chmod(os.path.join(d, "SKILL.md"), 0o644)
        done.append(label)
        w.ok(label)
    w.note("Agent sessions that are already running pick this up when they restart.")
    return done


def setup_try(w):
    if not (w.interactive and sys.stdin.isatty()):
        w.note("Skipped (not interactive).")
        return
    if not w.yes("Try the form now? It saves a test value and deletes it right after."):
        return
    fields, _ = parse_fields("username,password")
    form = Form(fields, TEST_NAME, "herdr-secret setup", "Type anything. It is deleted right after.", "")
    try:
        with Term() as t:
            res = run_form(t, form, time.time() + 180)
    except KeyboardInterrupt:
        res = None
    if not isinstance(res, list):
        w.warn("Form closed without saving.")
        return
    where = {store_any(f"{TEST_NAME}.{f}", v) for (f, _l, _h), v in zip(fields, res)}
    res[:] = []
    for f, _l, _h in fields:
        remove_any(f"{TEST_NAME}.{f}")
    if None in where:
        w.bad("Saving failed.")
    else:
        w.ok(f"Saved to {', '.join(sorted(where))} and deleted again")


def needs_terminal(argv, what):
    """Without a terminal there is nobody to answer, so changes need an explicit --yes."""
    if sys.stdin.isatty() or "--yes" in argv or "-y" in argv:
        return False
    print(f"herdr-secret {what} asks before changing anything and needs a terminal. "
          f"Run it in one, or pass --yes to accept every default.", file=sys.stderr)
    return True


def cmd_setup(argv):
    if needs_terminal(argv, "setup"):
        return 2
    interactive = sys.stdin.isatty() and "--yes" not in argv and "-y" not in argv
    if IS_WIN:
        os.system("")
    w = Wizard(interactive)
    in_popup = bool(os.environ.get("HERDR_PLUGIN_ENTRYPOINT_ID"))
    print(f"\n{BOLD}herdr-secret {plugin_version()} setup{RESET}")
    print(f"{DIM}Lets your coding agents ask you for credentials in a herdr popup.{RESET}")
    try:
        w.step(1, 5, "Requirements")
        w.ok(f"Python {sys.version.split()[0]}")
        hv = herdr_version()
        if hv is None:
            w.bad("herdr not found. Install it from https://herdr.dev, then run this again.")
            return 1
        if hv < MIN_HERDR:
            w.bad(f"herdr {'.'.join(map(str, hv))} is too old; {'.'.join(map(str, MIN_HERDR))} or newer is needed. Run: herdr update")
            return 1
        w.ok(f"herdr {'.'.join(map(str, hv))}")
        if installed_plugin():
            w.ok("Plugin is registered with herdr")
        elif w.yes(f"The plugin is not registered with herdr. Link this copy ({tilde(ROOT)})?"):
            subprocess.run([herdr_bin(), "plugin", "link", ROOT], stdout=DEVNULL)
            (w.ok if installed_plugin() else w.bad)("Plugin linked" if installed_plugin() else "Linking failed")

        w.step(2, 5, "Credential store")
        if not setup_store(w):
            write_marker("incomplete")
            return 1

        w.step(3, 5, "The herdr-secret command")
        changed = setup_command(w)

        w.step(4, 5, "Your agents")
        agents = setup_agents(w)

        w.step(5, 5, "Try it")
        setup_try(w)

        write_marker("done", agents=agents, **changed)
        print(f"\n{GREEN}{BOLD}Done.{RESET} Agents can now run: herdr-secret ask NAME \"what it is for\"")
        print(f"{DIM}Run setup again any time: herdr plugin action invoke {PLUGIN_ID}.setup{RESET}")
        print(f"{DIM}Remove everything it added: herdr-secret uninstall{RESET}")
        return 0
    except KeyboardInterrupt:
        print(f"\n\nSetup cancelled. Run it again any time: herdr plugin action invoke {PLUGIN_ID}.setup")
        if not os.path.exists(setup_marker()):
            write_marker("skipped")
        return 1
    finally:
        if in_popup and sys.stdin.isatty():
            try:
                input("\nPress Enter to close.")
            except (EOFError, KeyboardInterrupt):
                pass


def cmd_uninstall(argv):
    if needs_terminal(argv, "uninstall"):
        return 2
    interactive = sys.stdin.isatty() and "--yes" not in argv and "-y" not in argv
    if IS_WIN:
        os.system("")
    w = Wizard(interactive)
    print(f"\n{BOLD}herdr-secret uninstall{RESET}")
    for label, _d, _c, instr, skill_dir in AGENTS:
        hit = drop_block(instr)
        if skill_dir:
            f = os.path.join(os.path.expanduser(skill_dir), "SKILL.md")
            if os.path.exists(f):
                os.unlink(f)
                try:
                    os.rmdir(os.path.dirname(f))
                except OSError:
                    pass
                hit = True
        if hit:
            w.ok(f"Removed instructions for {label}")
    for prof in ("~/.zshrc", "~/.bashrc", "~/.bash_profile"):
        if drop_block(prof, SH_BEGIN, SH_END):
            w.ok(f"Removed the PATH line from {prof}")
    fish = os.path.expanduser("~/.config/fish/conf.d/herdr-secret.fish")
    if os.path.exists(fish):
        os.unlink(fish)
        w.ok("Removed the fish PATH file")
    if IS_WIN:
        if win_user_path(remove=os.path.join(ROOT, "bin")):
            w.ok("Removed the plugin folder from your user PATH")
    else:
        link = os.path.expanduser("~/.local/bin/herdr-secret")
        if os.path.islink(link) and os.readlink(link).endswith(os.path.join("bin", "herdr-secret")):
            os.unlink(link)
            w.ok("Removed ~/.local/bin/herdr-secret")
    names = names_any()
    if names:
        w.note(f"Stored: {', '.join(names)}")
        if w.yes(f"Delete these {len(names)} stored credential(s) too?", default=False):
            for n in names:
                remove_any(n)
            w.ok("Deleted stored credentials")
        else:
            w.note("Kept. They stay in your credential store.")
    try:
        os.unlink(setup_marker())
    except OSError:
        pass
    p = installed_plugin()
    if p and w.yes("Remove the plugin from herdr as well?"):
        local = (p.get("source") or {}).get("kind") == "local"
        subprocess.run([herdr_bin(), "plugin", "unlink" if local else "uninstall", PLUGIN_ID], stdout=DEVNULL)
        w.ok("Plugin removed from herdr")
    print(f"\n{BOLD}Done.{RESET}")
    return 0


def cmd_open_setup():
    entry = "setup-windows" if IS_WIN else "setup"
    return subprocess.run([herdr_bin(), "plugin", "pane", "open", "--plugin", PLUGIN_ID,
                           "--entrypoint", entry, "--focus"], stdout=DEVNULL).returncode


def cmd_startup():
    """Runs when herdr starts: open the setup popup once if setup has never run."""
    if os.path.exists(setup_marker()):
        return 0
    return cmd_open_setup()


def main(argv):
    cmd, args = (argv[0], argv[1:]) if argv else ("", [])
    if cmd == "form":
        return cmd_form()
    if cmd == "ask":
        return cmd_ask(args)
    if cmd == "setup":
        return cmd_setup(args)
    if cmd == "uninstall":
        return cmd_uninstall(args)
    if cmd == "open-setup":
        return cmd_open_setup()
    if cmd == "startup":
        return cmd_startup()
    if cmd == "list":
        for n in names_any():
            print(n)
        return 0
    if cmd in ("get", "rm") and args:
        if not NAME_RE.fullmatch(args[0]):
            print(f"herdr-secret {cmd}: invalid NAME", file=sys.stderr)
            return 2
        if cmd == "rm":
            targets = [args[0]] + [n for n in names_any() if n.startswith(args[0] + ".")]
            gone = [t for t in dict.fromkeys(targets) if remove_any(t)]
            print(f"removed {', '.join(gone)}" if gone else f"not found {args[0]}")
            return 0 if gone else 1
        if sys.stdout.isatty():
            print("herdr-secret get: stdout is a terminal; pipe it into the target", file=sys.stderr)
            return 2
        v = fetch_any(args[0])
        if v is None:
            print(f"herdr-secret get: {args[0]} not found", file=sys.stderr)
            return 1
        sys.stdout.buffer.write(v.encode("utf-8"))
        sys.stdout.flush()
        return 0
    print(__doc__.strip(), file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
