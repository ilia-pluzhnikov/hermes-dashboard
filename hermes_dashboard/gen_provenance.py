"""provenance.html — the data audit: which dashboard.json values are real.

A static dashboard is only as honest as its config: a value copied from the
example file renders exactly like a value verified against the installation.
This page makes the difference visible. The operator keeps a hand-curated
audit file (paths.provenance, JSON) recording, for every key of
dashboard.json, whether the value was checked against the live system, is a
template leftover, waits for real data, or is deliberately switched off.

The engine only renders that file — it never guesses. No file → an honest
empty state (the audit was not filled in), never an invented "all green".

File shape (all human-facing strings accept {"en":…,"ru":…} or a plain str):

    {"generated": "2026-08-21",
     "intro": {"en": "…", "ru": "…"},
     "sections": [{"title": {…}, "note": {…}, "items": [
         {"key": "agent.name", "value": "Clawy", "status": "verified",
          "checked": {…}, "note": {…}}]}]}

status: verified | template | pending | disabled — mapped onto the existing
card palette (ok / no / part / avail) so the stripes and filters just work.
"""
from __future__ import annotations

import json
import os
import sys

from .common import esc
from .config import Config, current
from .gen_connectors import kpi
from .i18n import _, set_lang
from . import render, sysinfo


def load(cfg: Config) -> dict | None:
    rel = str(cfg.get("paths.provenance", "dashboard/provenance.json") or "")
    if not rel:
        return None
    p = cfg.path_in_home(rel)
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except OSError:
        return None
    except ValueError as e:
        sys.stderr.write(f"[provenance] {p} did not parse: {e}\n")
        return None
    return data if isinstance(data, dict) else None


def badge(status: str) -> tuple[str, str]:
    """(palette class, pill label). Unknown statuses stay visible, not fatal."""
    if status == "verified":
        return "ok", _("VERIFIED")
    if status == "template":
        return "no", _("TEMPLATE")
    if status == "pending":
        return "part", _("AWAITING DATA")
    if status == "disabled":
        return "avail", _("DISABLED")
    return "avail", esc(status or "?")


def card_html(cfg: Config, it: dict, lang: str) -> str:
    cls, lbl = badge(str(it.get("status", "")))
    val = esc(str(it.get("value", "")))
    note = esc(cfg.text(it.get("note"), lang))
    checked = esc(cfg.text(it.get("checked"), lang))
    ds = " · ".join(x for x in ((f"<b>{val}</b>" if val else ""), note) if x)
    how = f'<div class="how"><b>{_("checked against")}:</b> {checked}</div>' if checked else ""
    return (f'<div class="ccard" data-status="{cls}"><div class="top"><div>'
            f'<div class="nm">{esc(str(it.get("key", "")))}</div></div>'
            f'<span class="pill p-{cls}">{lbl}</span></div>'
            + (f'<div class="ds">{ds}</div>' if ds else "") + how + '</div>')


def section_html(cfg: Config, sec: dict, lang: str) -> str:
    items = sec.get("items", []) or []
    if not items:
        return ""
    title = esc(cfg.text(sec.get("title"), lang))
    note = esc(cfg.text(sec.get("note"), lang))
    return (f'<div class="sec filterable"><div class="sec-h"><h2>{title}</h2><span class="ln"></span>'
            f'<span class="note">{note}</span></div>'
            f'<div class="grid g3">{"".join(card_html(cfg, it, lang) for it in items)}</div></div>')


def build_page(cfg: Config, lang: str, host: dict) -> str:
    data = load(cfg)
    name = cfg.get("agent.name", "Hermes")
    o = [render.head(cfg, f'{name} — {_("data audit")}', lang, "provenance"),
         render.rail(cfg, lang, "", host["dot"], host["gwt"], host["sync"], host["sha"],
                     host["commit"], current_page="provenance"),
         '<main class="work"><div class="pad">']
    if data is None:
        o += [f'<div class="vh"><div class="kick">provenance.html · {_("data audit")}</div>'
              f'<h1>{_("Data audit")}</h1>'
              f'<p>{_("provenance.json is missing or unreadable — the audit has not been filled in for this installation.")}</p></div>']
    else:
        secs = data.get("sections", []) or []
        counts = {"ok": 0, "no": 0, "part": 0, "avail": 0}
        for s in secs:
            for it in s.get("items", []) or []:
                counts[badge(str(it.get("status", "")))[0]] += 1
        gen = esc(str(data.get("generated", "")))
        intro = esc(cfg.text(data.get("intro"), lang)) or _(
            "Every value on this dashboard was checked by hand against the live installation; this page records the verdict for each key of dashboard.json.")
        o += [f'<div class="vh"><div class="kick">provenance.html · {_("curated by hand in provenance.json")}</div>'
              f'<h1>{_("Data audit")}</h1><p>{intro}</p>'
              + (f'<div class="meta">{_("audit date")} <b>{gen}</b></div>' if gen else "") + '</div>',
              '<div class="sec"><div class="kpis">',
              kpi(counts["ok"], _("verified against the installation"), "ok", ("p-ok", _("VERIFIED"))),
              kpi(counts["no"], _("template / not verified"), "warn" if counts["no"] else "", ("p-no", _("TEMPLATE"))),
              kpi(counts["part"], _("awaiting real data"), "info", ("p-part", _("AWAITING DATA"))),
              kpi(counts["avail"], _("deliberately disabled"), "", ("p-avail", _("DISABLED"))),
              '</div></div>',
              f'<div class="controls"><input id="q" placeholder="{esc(_("Search by name / service / description…"))}">'
              f'<button class="fbtn on" data-f="all">{_("all")}</button>'
              f'<button class="fbtn" data-f="ok">{_("VERIFIED")}</button>'
              f'<button class="fbtn" data-f="no">{_("TEMPLATE")}</button>'
              f'<button class="fbtn" data-f="part">{_("AWAITING DATA")}</button>'
              f'<button class="fbtn" data-f="avail">{_("DISABLED")}</button></div>']
        o += [section_html(cfg, s, lang) for s in secs]
        o += [f'<div class="empty" id="empty">{_("Nothing matches the current filter.")}</div>']
    o += [f'<footer><span>{_("Private dashboard")} · {_("the audit is curated by hand and versioned in git")}</span>'
          f'<span>{esc(cfg.get("agent.config_repo_label", "")) or "hermes-dashboard"}</span></footer>',
          '</div></main>', render.bottom_tabs(cfg, lang, "", current_page="provenance"),
          render.script()]
    if data is not None:
        o.append("<script>" + render.CONNECTORS_JS + "</script>")
    o.append("</body></html>")
    return "\n".join(o)


if __name__ == "__main__":
    cfg = current()
    lang = os.environ.get("HERMES_DASHBOARD_LANG", cfg.default_lang)
    set_lang(lang)
    host = {"dot": "var(--ok)", "gwt": "", "sync": sysinfo.last_sync(), "sha": "", "commit": ""}
    sys.stdout.write(build_page(cfg, lang, host))
