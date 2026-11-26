"""Server-rendered, standalone QR scan pages."""
from html import escape
import json
import re
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi.responses import HTMLResponse

from logentra_server.sitetime import utc_text_to_local
from logentra_server.phone_auth import csrf_secret, csrf_token, set_csrf_cookie

TEMPLATE = (Path(__file__).parent / "templates" / "qr_phone.html").read_text()


def render_phone(request, content, data, *, status=200):
    secret = None
    if data.get("screen") in {"login", "entry"}:
        secret = csrf_secret(request)
        data = {**data, "csrf": csrf_token(secret)}
    encoded = json.dumps(data, allow_nan=False).replace("<", "\\u003c")
    replacements = {"@@DATA@@": encoded, "@@CONTENT@@": content}
    html = re.sub(r"@@(?:DATA|CONTENT)@@", lambda token: replacements[token.group()], TEMPLATE)
    response = HTMLResponse(html, status_code=status, headers={"Cache-Control": "no-store"})
    if secret:
        set_csrf_cookie(response, request, secret)
    return response


def equipment_card(summary):
    location = " · ".join(dict.fromkeys(value for value in (summary.get("site"), summary.get("area")) if value))
    timestamp = summary.get("last_logged_at")
    last = ""
    if timestamp:
        by = summary.get("last_logged_by")
        day, clock = utc_text_to_local(timestamp, ZoneInfo(summary.get("timezone", "UTC")))
        last = f'<span>Last logged <time datetime="{escape(timestamp)}">{day} {clock}</time>'
        last += f" by {escape(by)}" if by else ""
        last += "</span>"
    metadata = " · ".join(filter(None, (escape(location), last)))
    return (f'<section class="equipment-card"><h1>{escape(summary["tag"])}</h1>'
            f'<p>{escape(summary.get("name", ""))}</p><small>{metadata}</small></section>')


def login_form(days, secure):
    network_note = "" if secure else "<p><small>This server uses HTTP on your network.</small></p>"
    return f"""<section><h2>Sign in to log readings</h2>
<form>
<label for="username">Username</label><input name="username" id="username" autocomplete="username" required>
<label for="password">Password</label><input name="password" id="password" type="password" autocomplete="current-password" required>
<label><input type="checkbox" name="keep_signed_in" checked> Keep me signed in on this phone for {days} days</label>{network_note}
<p id="error" class="error" role="alert"></p><button type="submit">Sign in</button>
</form></section>"""


def reading_form(summary, *, open_mode, photo_limit, can_create_work):
    params = summary["parameters"]
    rows = []
    for index, parameter in enumerate(params):
        label = escape(parameter["label"])
        unit = parameter["unit"]
        minimum = parameter["min"] if parameter["min"] != "" else "—"
        maximum = parameter["max"] if parameter["max"] != "" else "—"
        if minimum != "—" and maximum != "—":
            limits = f"{minimum}–{maximum} {unit}"
        elif minimum != "—":
            limits = f"min {minimum} {unit}"
        elif maximum != "—":
            limits = f"max {maximum} {unit}"
        else:
            limits = ""
        limits = escape(limits.rstrip())
        last = "—" if parameter["last"] is None else f"{parameter['last']:g}"
        field = (
            f'<span class="reading-input"><input id="value-{index}" aria-label="{label}" inputmode="decimal" '
            f'data-parameter="{index}" placeholder="—"><small>{escape(unit)}</small></span>'
        )
        flag = f'<span class="flag" id="flag-{index}" aria-live="polite"></span>'
        if summary["layout"] == "table":
            rows.append(f"<tr class='reading-row'><th scope='row'>{label}<br><small>{limits}</small></th><td>{last}</td><td>{field}{flag}</td></tr>")
        else:
            rows.append(
                f'<section class="reading-row"><div class="reading-meta"><label for="value-{index}">{label}</label>'
                f'<small>{limits}</small></div>{field}<div class="reading-meta">'
                f'<small>Last {last} {escape(unit)}</small>{flag}</div></section>'
            )
    values = "".join(rows)
    if summary["layout"] == "table":
        values = f"<section><table><thead><tr><th>Parameter</th><th>Last</th><th>Now</th></tr></thead><tbody>{values}</tbody></table></section>"
    if not params:
        values = "<section>No parameters set up. You can still save a note or photo.</section>"
    name = '<label for="entered_by">Your name</label><input id="entered_by" name="entered_by" maxlength="250" required>' if open_mode else ""
    work = """
<button type="button" id="raise-work">Raise work order</button>
<form id="work-form" hidden>
<label for="work-title">Title</label><input id="work-title" name="title" required maxlength="250">
<label for="work-description">Description</label><textarea id="work-description" name="description" maxlength="5000"></textarea>
<label for="work-priority">Priority</label><select id="work-priority" name="priority">
<option value="low">Low</option><option value="medium" selected>Medium</option>
<option value="high">High</option><option value="urgent">Urgent</option></select>
<p id="work-error" class="error" role="alert"></p><button type="submit">Create work order</button>
</form><p id="work-result" role="status"></p>""" if can_create_work else ""
    table_alert = '<section id="table-alert" class="table-alert" role="status" hidden></section>' if summary["layout"] == "table" else ""
    return f"""<form aria-label="Quick entry">
<h2 class="reading-heading">Readings</h2><div class="values">{values}</div>{table_alert}
<section>{name}
<label for="note">Note <small>(optional)</small></label><textarea id="note" name="note" maxlength="10000" rows="3"></textarea>
<h2>Photos</h2><div id="photo-list" class="photo-list"></div>
<input id="photos" aria-label="Choose photos" type="file" accept="image/*" multiple hidden>
<button type="button" id="add-photo" class="secondary">Add photo (0 of {photo_limit})</button>
<p id="error" class="error" role="alert"></p></section>
<div class="save-bar"><button type="submit">Save entry</button><small id="reading-warning" aria-live="polite" hidden></small></div></form>
<section id="done" hidden><h2>Readings saved</h2><p id="saved-summary"></p><ul id="saved-values"></ul>
<details><summary>Scan the next tag</summary><p>Open your phone camera and point it at the next QR label.</p></details>
{work}</section>"""
