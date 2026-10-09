"""Exercise generated consent scripts in a minimal browser-like Node runtime."""

from __future__ import annotations

import json
import shutil
import subprocess

import pytest
from site_panel_ssg.builder import TELEMETRY_SCRIPT
from site_panel_ssg.legal import COOKIE_BANNER_JS


@pytest.mark.skipif(
    shutil.which("node") is None, reason="Node is needed for generated JS execution"
)
def test_browser_consent_withdrawal_retry_and_gpc():
    script = (
        "const COOKIE = "
        + json.dumps(COOKIE_BANNER_JS)
        + ";\nconst TELEMETRY = "
        + json.dumps(TELEMETRY_SCRIPT)
        + ";\n"
        + r"""
const assert = require("node:assert/strict");
const vm = require("node:vm");
class Store {
  constructor() { this.items = new Map(); }
  getItem(key) { return this.items.get(key) ?? null; }
  setItem(key, value) { this.items.set(key, String(value)); }
  removeItem(key) { this.items.delete(key); }
}
class Element {
  constructor(tagName) {
    this.tagName = tagName; this.children = []; this.handlers = {}; this.style = {};
  }
  setAttribute(name, value) { this[name] = value; }
  addEventListener(name, callback) { this.handlers[name] = callback; }
  appendChild(child) { this.children.push(child); child.parent = this; return child; }
  remove() {
    if (this.parent) this.parent.children.splice(this.parent.children.indexOf(this), 1);
  }
  querySelector(tagName) { return this.children.find((child) => child.tagName === tagName); }
  focus() {}
  click() { this.handlers.click?.(); }
}
const button = (root, label) => root.children.find((node) => node.textContent === label);
const flush = () => new Promise((resolve) => setImmediate(resolve));
const setup = (consent, session, gpc = false, failRevoke = false) => {
  const requests = [];
  const listeners = new Map();
  const body = new Element("body");
  const document = {
    body,
    currentScript: { getAttribute: () => "public-site-token" },
    createElement: (tag) => new Element(tag),
    addEventListener: (name, handler) => listeners.set(name, handler),
    dispatchEvent: (event) => listeners.get(event.type)?.(event),
  };
  let sequence = 0;
  const window = {
    crypto: { randomUUID: () => `00000000-0000-4000-a000-${String(++sequence).padStart(12, "0")}` },
  };
  const context = {
    document, window, localStorage: consent, sessionStorage: session,
    location: { pathname: "/test/" },
    navigator: { globalPrivacyControl: gpc, doNotTrack: "0" },
    CustomEvent: class {
      constructor(type, options) { this.type = type; this.detail = options.detail; }
    },
    fetch: (path, options) => {
      requests.push({ path, payload: JSON.parse(options.body) });
      if (failRevoke && path.endsWith("/revoke")) return Promise.reject(Error("offline"));
      return Promise.resolve({ ok: true });
    },
  };
  vm.runInNewContext(COOKIE, context);
  vm.runInNewContext(TELEMETRY, context);
  return { requests, body, window };
};
(async () => {
  const consent = new Store();
  const session = new Store();
  const first = setup(consent, session);
  assert.equal(first.requests.length, 0); // Default is no tracking.
  const initialDialog = first.body.children[1];
  button(initialDialog, "Разрешить аналитику").click();
  assert.equal(first.requests.filter((r) => r.path.endsWith("/collect")).length, 1);
  first.body.children[0].click(); // Reopen persistent privacy settings.
  button(first.body.children[1], "Только необходимые").click();
  assert.equal(session.getItem("sp_telemetry_session"), null);
  assert.equal(first.requests.filter((r) => r.path.endsWith("/revoke")).length, 1);
  await flush();
  assert.equal(session.getItem("sp_telemetry_revoke_pending"), null);
  assert.equal(first.window.__spConsent.analytics, false);
  first.body.children[0].click();
  button(first.body.children[1], "Разрешить аналитику").click();
  const collected = first.requests.filter((r) => r.path.endsWith("/collect"));
  assert.equal(collected.length, 2);
  assert.notEqual(collected[0].payload.session_id, collected[1].payload.session_id);

  consent.setItem("sp_consent", JSON.stringify({ analytics: true }));
  session.setItem("sp_telemetry_session", "prior-consented-session-12345678");
  const protectedPage = setup(consent, session, true);
  assert.equal(protectedPage.requests.filter((r) => r.path.endsWith("/collect")).length, 0);
  assert.equal(protectedPage.requests.filter((r) => r.path.endsWith("/revoke")).length, 1);
  await flush();

  consent.setItem("sp_consent", JSON.stringify({ analytics: true }));
  const offline = setup(consent, session, false, true);
  offline.body.children[0].click();
  button(offline.body.children[1], "Только необходимые").click();
  await flush();
  assert.ok(session.getItem("sp_telemetry_revoke_pending"));
  const recovered = setup(consent, session);
  assert.equal(recovered.requests.filter((r) => r.path.endsWith("/collect")).length, 0);
  assert.equal(recovered.requests.filter((r) => r.path.endsWith("/revoke")).length, 1);
  await flush();
  assert.equal(session.getItem("sp_telemetry_revoke_pending"), null);
})().catch((error) => { console.error(error); process.exitCode = 1; });
"""
    )
    result = subprocess.run(
        ["node", "-"],
        input=script,
        text=True,
        encoding="utf-8",
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
