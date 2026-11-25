"""Server-rendered, standalone QR scan pages."""
from html import escape
import json
import re
from pathlib import Path

from fastapi.responses import HTMLResponse

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


def equipment_card(tag, name, area):
    return f"<section><h1>{escape(tag)}</h1><p>{escape(name)}</p><small>{escape(area)}</small></section>"


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
        unit = escape(parameter["unit"])
        minimum = parameter["min"] if parameter["min"] != "" else "—"
        maximum = parameter["max"] if parameter["max"] != "" else "—"
        limits = f"{minimum} to {maximum} {unit}"
        last = "—" if parameter["last"] is None else f"{parameter['last']:g}"
        field = (
            f'<input id="value-{index}" aria-label="{label}" inputmode="decimal" '
            f'data-parameter="{index}" placeholder="—"><div class="flag" id="flag-{index}" aria-live="polite"></div>'
        )
        if summary["layout"] == "table":
            rows.append(f"<tr><th scope='row'>{label}<br><small>{limits}</small></th><td>{last}</td><td>{field}</td></tr>")
        else:
            rows.append(f'<section><label for="value-{index}">{label}</label><p><small>Last {last} · Limits {limits}</small></p>{field}</section>')
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
    return f"""<form aria-label="Quick entry">
<div class="values">{values}</div>
<section>{name}
<label for="note">Note <small>(optional)</small></label><textarea id="note" name="note" maxlength="10000" rows="3"></textarea>
<label for="photos">Photos <small>(up to {photo_limit})</small></label><input id="photos" name="photos" type="file" accept="image/*" multiple>
<p id="error" class="error" role="alert"></p><button type="submit">Save readings</button></section></form>
<section id="done" hidden><h2>Readings saved</h2><p id="saved-summary"></p><ul id="saved-values"></ul>
<details><summary>Scan the next tag</summary><p>Open your phone camera and point it at the next QR label.</p></details>
{work}</section>"""
