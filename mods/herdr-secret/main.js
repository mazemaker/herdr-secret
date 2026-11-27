// herdr-secret for Herdr.app: the dialog an agent's `herdr-secret ask` opens.
//
// The request (who asks, what for, which fields, a one-time pad) comes from a file the
// herdr-secret engine writes in its 0700 state folder. Herdr.app mods can hand data to programs
// only as arguments, which other processes can list, so each typed value leaves this dialog
// XORed with its slice of the pad, as hex. The engine undoes that, stores the value in the OS
// credential store and deletes the request, pad included. Every answer ends the request, so the
// pad never covers two values. The agent only ever learns OK, CANCELLED, TIMEOUT or FAILED.
import { View, div } from "gpui-kit";
import { v_flex, h_flex, InputState, Input } from "gpui-base";
import { exec, home, props, closeOverlay, theme, readTextFile, playSound, setBadge, checking, notify } from "herdr";

const REQUEST = "/.local/state/herdr-secret/app-request.json";
const PAD_PER_FIELD = 4096;
const CONTROL = /[\u0000-\u001f\u007f-\u009f]/;

// What `herdr-app check` renders: the form, without reading a request or running anything.
const SAMPLE = {
  req: "check", name: "DB_LOGIN", purpose: "Staging database, read-only user", from: "terminal t1",
  fields: [{ name: "username", label: "Username", hidden: false }, { name: "password", label: "Password", hidden: true }],
  store: "macOS Keychain (encrypted) · never sent to the agent's chat", encrypted: true,
  replaces: [], deadline: 0, max: 1000, pad: "", python: "", script: "",
};

function utf8(text) {
  const out = [];
  for (const ch of text) {
    const c = ch.codePointAt(0);
    if (c < 0x80) out.push(c);
    else if (c < 0x800) out.push(0xc0 | (c >> 6), 0x80 | (c & 63));
    else if (c < 0x10000) out.push(0xe0 | (c >> 12), 0x80 | ((c >> 6) & 63), 0x80 | (c & 63));
    else out.push(0xf0 | (c >> 18), 0x80 | ((c >> 12) & 63), 0x80 | ((c >> 6) & 63), 0x80 | (c & 63));
  }
  return out;
}

function padded(text, padHex, field) {
  const bytes = utf8(text);
  let hex = "";
  for (let i = 0; i < bytes.length; i++) {
    const at = 2 * (field * PAD_PER_FIELD + i);
    const b = bytes[i] ^ parseInt(padHex.slice(at, at + 2), 16);
    hex += (b < 16 ? "0" : "") + b.toString(16);
  }
  return hex;
}

export default class Secret extends View {
  init(_props, cx) {
    this.state = "loading";         // loading | empty | form | saving | failed
    this.error = "";
    this.finished = false;
    this.inputs = [];
    this.revealed = [];
    this.focusIndex = 0;
    this.handle = cx.focus_handle();
    if (checking()) {
      this.show(SAMPLE);
      this.finished = true;         // nothing to answer
      return;
    }
    cx.spawn(async (cx) => {
      let r = null;
      try { r = JSON.parse((await readTextFile(home() + REQUEST)) || "null"); } catch (_e) { r = null; }
      if (!r || !Array.isArray(r.fields) || r.deadline * 1000 <= Date.now()) {
        this.state = "empty";
        cx.notify();
        // mod.open toggles, so a dialog left open would swallow the next request: close by itself.
        cx.timer.after(4000, () => closeOverlay(props().overlay));
        return;
      }
      this.show(r);
      cx.notify();
      playSound("blocked");
      setBadge("1");
      cx.timer.after(120, () => this.inputs[0]?.focus());
      cx.timer.after(Math.max(0, r.deadline * 1000 - Date.now()), (cx) => this.finish("TIMEOUT", cx));
    });
  }

  show(r) {
    this.req = r;
    this.inputs = r.fields.map((f, i) => {
      const s = InputState.new({});
      s.set_masked(Boolean(f.hidden));
      s.on("submit", (_e, cx) => this.next(i, cx));
      s.on("change", (_e, cx) => { if (this.error) { this.error = ""; cx.notify(); } });
      s.on("focus", (_e, cx) => { this.focusIndex = i; cx.notify(); });
      return s;
    });
    this.revealed = r.fields.map(() => false);
    this.state = "form";
  }

  next(i, cx) {
    if (i + 1 < this.inputs.length) this.inputs[i + 1].focus();
    else this.save(cx);
  }

  key(e, cx) {
    if (e.key === "tab" && this.inputs.length) {
      const step = e.modifiers?.shift ? -1 : 1;
      const n = this.inputs.length;
      this.inputs[(this.focusIndex + step + n) % n].focus();
      cx.stop_propagation();
    }
  }

  toggle(i, cx) {
    this.revealed[i] = !this.revealed[i];
    this.inputs[i].set_masked(!this.revealed[i]);
    cx.notify();
  }

  save(cx) {
    const r = this.req;
    if (!r || this.state !== "form" || this.finished) return;
    const values = this.inputs.map((s) => s.value());
    const bad = values.findIndex((v) => !v);
    if (bad >= 0) { this.error = "All fields are required."; this.inputs[bad].focus(); cx.notify(); return; }
    if (values.some((v) => [...v].length > r.max)) { this.error = `Too long (max ${r.max} characters).`; cx.notify(); return; }
    if (values.some((v) => CONTROL.test(v))) { this.error = "Control characters are not allowed."; cx.notify(); return; }
    const hexes = values.map((v, i) => padded(v, r.pad, i));
    this.state = "saving";
    cx.notify();
    cx.spawn(async (cx) => {
      let out = "";
      let code = 1;
      try {
        const res = await exec(r.python, ["-I", r.script, "app-store", r.req, ...hexes], { timeout: 30000 });
        out = res.stdout.trim();
        code = res.code;
      } catch (e) {
        out = "FAILED " + String(e);
      }
      this.finished = true;         // app-store ends the request whatever it answers
      for (const s of this.inputs) s.set_value("");
      setBadge(null);
      if (code === 0 && out.startsWith("OK")) {
        notify({ title: `Saved ${r.name}`, body: `In ${out.slice(3)}. The agent only learns that you answered.`, level: "success" });
        closeOverlay(props().overlay);
        return;
      }
      this.state = "failed";
      this.error = out.replace(/^FAILED ?/, "") || "Could not save.";
      cx.notify();
    });
  }

  finish(kind, _cx) {
    if (this.finished || !this.req) return;
    this.finished = true;
    const args = ["-I", this.req.script, "app-cancel", this.req.req];
    if (kind === "TIMEOUT") args.push("TIMEOUT");
    exec(this.req.python, args).catch(() => null);
    setBadge(null);
    closeOverlay(props().overlay);
  }

  async close(_cx) {
    setBadge(null);
    if (this.finished || !this.req) return;
    this.finished = true;                       // Esc or a closed window: the human said no
    await exec(this.req.python, ["-I", this.req.script, "app-cancel", this.req.req]).catch(() => null);
  }

  render() {
    const t = theme();
    const r = this.req;
    const label = (text) => div().pt(14).pb(6).text_size(11.5).text_color(t.dim).child(text);
    const button = (id, text, primary, action) => h_flex().id(id).h(32).px(14).items_center().rounded(8)
      .border_1().border_color(primary ? t.solid : t.input).bg(primary ? t.solid : t.raised)
      .text_color(primary ? t.solid_foreground : t.foreground).text_size(12.5).font_medium().cursor_pointer()
      .hover((s) => s.opacity(0.88)).on_click(action).child(text);
    const line = (key, value, color) => h_flex().gap(8).pt(4).text_size(12.5)
      .child(div().w(52).flex_none().text_color(t.dim).child(key))
      .child(div().flex_1().text_color(color ?? t.foreground).child(value));
    const close = button("hs-close", "Close", true, () => closeOverlay(props().overlay));
    const root = v_flex().id("herdr-secret").track_focus(this.handle).on_key_down((e, cx) => this.key(e, cx))
      .w_full().min_h(0).p(18).bg(t.popover).text_color(t.foreground);

    if (this.state === "loading") return root.child(div().text_color(t.dim).child("Loading the request…"));
    if (this.state === "empty") {
      return root
        .child(div().text_size(15).font_semibold().child("No credential request is waiting"))
        .child(div().pt(8).text_size(12.5).text_color(t.dim).child("Agents open this dialog with herdr-secret ask."))
        .child(h_flex().pt(18).justify_end().child(close));
    }
    if (this.state === "failed") {
      return root
        .child(div().text_size(15).font_semibold().child(`Not saved: ${r.name}`))
        .child(div().pt(8).text_size(12.5).text_color(t.ansi1).child(this.error))
        .child(div().pt(4).text_size(12.5).text_color(t.dim).child("The agent was told that it failed."))
        .child(h_flex().pt(18).justify_end().child(close));
    }

    return root
      .child(h_flex().items_center()
        .child(div().flex_1().text_size(15).font_semibold().child(`Credential requested: ${r.name}`))
        .child(div().text_size(11).text_color(t.dim).child("Esc")))
      .child(div().pt(8)
        .child(line("From", `${r.from}  (as reported by the requester)`, t.dim))
        .child(line("For", r.purpose || "(no description given)"))
        .child(line("Saved", `${r.store}`, r.encrypted ? t.done : t.blocked)))
      .when(r.replaces.length > 0, (el) => el.child(div().pt(8).text_size(12).text_color(t.blocked)
        .child(`Replaces the stored value of: ${r.replaces.join(", ")}`)))
      .children(r.fields.map((f, i) => v_flex()
        .child(label(f.label + (f.hidden ? " (hidden)" : "")))
        .child(h_flex().gap(8).items_center()
          .child(Input.new(this.inputs[i]).flex_1().h(36).px(10).rounded(9).border_1()
            .border_color(this.focusIndex === i ? t.accent : t.input).bg(t.background).text_size(12.8).text_color(t.foreground))
          .when(Boolean(f.hidden), (el) => el.child(
            h_flex().id(`hs-show-${i}`).h(36).px(10).items_center().rounded(9).border_1().border_color(t.input)
              .bg(t.raised).text_size(12).text_color(t.muted).cursor_pointer().hover((s) => s.opacity(0.88))
              .on_click((_e, cx) => this.toggle(i, cx)).child(this.revealed[i] ? "Hide" : "Show"))))))
      .when(Boolean(this.error), (el) => el.child(div().pt(10).text_size(12).text_color(t.ansi1).child(this.error)))
      .child(h_flex().pt(20).gap(8).justify_end()
        .child(button("hs-cancel", "Cancel", false, (_e, cx) => this.finish("CANCELLED", cx)))
        .child(button("hs-save", this.state === "saving" ? "Saving…" : "Save", true, (_e, cx) => this.save(cx))));
  }
}
