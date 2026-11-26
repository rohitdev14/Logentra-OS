/* v0.5: themed dashboard, document approvals, routes, chrome badges. */
(function (global) {
  "use strict";

  function esc(s) {
    return String(s ?? "").replace(/[&<>"']/g, (c) => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
    }[c]));
  }
  function can(perm) {
    const L = global.Logbook;
    const state = L && L.getState && L.getState();
    const me = state && state.me;
    if (!me || me.mode === "open" || me.mode === "legacy_key") return true;
    return (me.permissions || []).indexOf(perm) >= 0;
  }
  function toast(msg) {
    if (global.Logbook && global.Logbook._toast) return global.Logbook._toast(msg);
    console.log(msg);
  }
  function fmtWhen(ts, timeZone = global.Logbook.getState().meta.timezone) {
    if (!ts) return "";
    let iso = String(ts).replace(" ", "T");
    if (iso.length === 10) iso += "T00:00:00";
    if (!/(Z|[+-]\d{2}:?\d{2})$/i.test(iso)) iso += "Z";
    const instant = new Date(iso);
    if (Number.isNaN(instant.valueOf())) return String(ts);
    const { zonedDateTime } = global.Logbook;
    const { date, time } = zonedDateTime(instant, timeZone);
    const today = zonedDateTime(new Date(), timeZone).date;
    const yesterday = new Date(Date.parse(today) - 86400000).toISOString().slice(0, 10);
    if (date === today) return time;
    if (date === yesterday) return "Yesterday";
    return date + " " + time;
  }
  function initials(name) {
    const parts = String(name || "?").trim().split(/\s+/);
    return ((parts[0] || "?")[0] + (parts[1] ? parts[1][0] : "")).toUpperCase();
  }
  function spark(values, color) {
    const w = 88, h = 28;
    if (!values || values.length < 2) return "";
    const mn = Math.min.apply(null, values), mx = Math.max.apply(null, values);
    const span = (mx - mn) || 1;
    const pts = values.map((v, i) => {
      const x = 1 + i * (w - 2) / (values.length - 1);
      const y = h - 2 - (v - mn) / span * (h - 4);
      return x.toFixed(1) + " " + y.toFixed(1);
    });
    const d = "M " + pts.join(" L ");
    const last = pts[pts.length - 1].split(" ");
    const first = pts[0].split(" ");
    const area = d + " L " + last[0] + " " + h + " L " + first[0] + " " + h + " Z";
    color = color || "currentColor";
    return '<svg class="spark" width="' + w + '" height="' + h + '" viewBox="0 0 ' + w + " " + h + '" fill="none" aria-hidden="true">' +
      '<path d="' + area + '" fill="' + color + '" opacity="0.15"/>' +
      '<path d="' + d + '" stroke="' + color + '" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></svg>';
  }
  function statusLabel(st) {
    return ({ draft: "Draft", in_review: "In review", approved: "Approved", rejected: "Rejected", returned: "Returned" }[st] || st || "—");
  }
  function statusPill(st) {
    const cls = ({ draft: "done", in_review: "review", approved: "ok", rejected: "urgent", returned: "high" }[st] || "done");
    return '<span class="pill ' + cls + '">' + esc(statusLabel(st)) + "</span>";
  }

  const cache = { dash: null, docs: null, queue: null, detail: null, routes: null, err: "" };

  async function loadDash() {
    const api = global.LogbookAPI;
    try { cache.dash = await api.dashboard(); }
    catch (e) { cache.dash = null; cache.err = e.message; }
    return cache.dash;
  }
  async function loadDocs(ui) {
    const api = global.LogbookAPI;
    const [list, queue, routes] = await Promise.all([
      api.listDocuments({ page: ui.docPage || 1, page_size: 50, q: ui.docQ || "", status: ui.docStatus || "" }),
      api.documentsQueue({ page: 1, page_size: 20 }),
      api.listRoutes({ enabled: false }),
    ]);
    cache.docs = list;
    cache.queue = queue;
    cache.routes = routes;
    return list;
  }
  async function loadDetail(id) {
    cache.detail = await global.LogbookAPI.getDocument(id);
    if (!cache.routes) cache.routes = await global.LogbookAPI.listRoutes();
    return cache.detail;
  }

  function paintDashChart() {
    const L = global.Logbook;
    if (!L || !L.drawTrend) return;
    const el = document.getElementById("dash-chart");
    if (!el) return;
    const state = L.getState();
    const ui = L._ui || {};
    let eq = L.eqById(ui.logEq || "eq_p101") || (state.equipment && state.equipment[0]);
    if (!eq) { el.innerHTML = '<p class="empty">Load the demo or add equipment to see a trend.</p>'; return; }
    const params = L.paramsFor(eq) || [];
    // prefer a parameter that has an out-of-range point
    let param = params.find((p) => p.key === (ui.trendParam || "vibration")) || params[0];
    const prefer = params.find((p) => {
      const pts = L.trendPoints(eq, p, "", "");
      return pts.some((x) => x.oor);
    });
    if (prefer) param = prefer;
    if (!param) { el.innerHTML = '<p class="empty">No parameters on this asset.</p>'; return; }
    const title = document.getElementById("dash-chart-title");
    if (title) title.textContent = (L.eqTag(eq) || eq.id) + " — " + (param.label || param.key) + (param.unit ? " (" + param.unit + ")" : "");
    L.drawTrend(el, L.trendPoints(eq, param, "", ""), param);
    el.querySelectorAll("[data-log-id]").forEach((g) => {
      g.addEventListener("click", () => L.goto("trends", { logEq: eq.id, trendParam: param.key, from: "", to: "" }));
    });
  }

  function viewDashboard() {
    const L = global.Logbook;
    const state = L.getState();
    const dash = cache.dash || {};
    const eqN = dash.equipment_count != null ? dash.equipment_count : (state.equipment || []).length;
    const openWo = dash.open_work_orders || 0;
    const overdue = dash.overdue_work_orders || 0;
    const oor = dash.out_of_range_alerts || 0;
    const pending = dash.pending_approvals || 0;
    const myN = dash.my_pending_approvals || 0;
    const alerts = dash.recent_alerts || [];
    const queue = dash.my_approvals || [];
    const loc = (global.LogbookV4 && global.LogbookV4.plantLabel)
      ? global.LogbookV4.plantLabel(state)
      : ((state.equipment || []).map((e) => (e.values && (e.values.fld_location || e.values.location)) || "").find((x) => x) || "your plant");
    return `
      <div class="page-head">
        <div>
          <h2>Dashboard</h2>
          <p class="page-lead">${esc(String(loc))} · ${eqN.toLocaleString()} assets in the register · live readings from the plant bus</p>
        </div>
        <div class="row no-print">
          <button class="btn" data-act="demo" data-need-perm="settings.manage">${state.equipment.length ? "Reload fictional demo" : "Load fictional demo"}</button>
          <button class="btn btn-primary" data-act="goto" data-view="workorders">+ New work order</button>
        </div>
      </div>
      <div class="kpis">
        <div class="kpi">
          <div class="k">Pending approvals</div>
          <div class="v">${pending}</div>
          <div class="meta"><span>${myN} waiting on you</span>${spark([Math.max(0,pending-4),pending-2,pending-1,pending], "currentColor")}</div>
        </div>
        <div class="kpi">
          <div class="k">Open work orders</div>
          <div class="v">${openWo}</div>
          <div class="meta"><span class="${overdue ? "delta up" : ""}">${overdue} overdue</span>${spark([Math.max(0,openWo-5),openWo-3,openWo-1,openWo], "currentColor")}</div>
        </div>
        <div class="kpi">
          <div class="k">Out-of-range alerts</div>
          <div class="v">${oor}</div>
          <div class="meta"><span>Unacknowledged</span>${spark([oor+2,oor+1,oor+3,oor], "currentColor")}</div>
        </div>
        <div class="kpi">
          <div class="k">Equipment</div>
          <div class="v">${eqN.toLocaleString()}</div>
          <div class="meta"><span>in the register</span>${spark([Math.max(0,eqN-3),eqN-2,eqN-1,eqN], "currentColor")}</div>
        </div>
      </div>
      <div class="dash-grid">
        <div class="card chart-card">
          <div class="card-h">
            <h3 id="dash-chart-title">Trend</h3>
            <button class="linkish" data-act="goto" data-view="trends">Open in trends</button>
          </div>
          <div id="dash-chart" class="chart-wrap"></div>
        </div>
        <div class="card alerts-card">
          <div class="card-h">
            <h3>Recent alerts</h3>
            <button class="linkish" data-act="goto" data-view="logbook">View all</button>
          </div>
          ${alerts.length ? '<div class="table-wrap"><table class="data"><tbody>' + alerts.map((a) => {
            const limit = a.max != null ? " · max " + a.max : (a.min != null ? " · min " + a.min : "");
            return "<tr class='" + (a.acknowledged ? "" : "unack-row") + "'><td><b>" + esc(a.tag) + "</b> " + esc(a.parameter) + " " + esc(a.value) + esc(limit) +
              "<div class='tiny'>" + (a.acknowledged ? esc(a.cause || "Acknowledged") : "Unacknowledged") + "</div></td>" +
              "<td class='tiny' style='text-align:right;white-space:nowrap'>" + esc(fmtWhen(a.ts, global.Logbook.equipmentTimezone(global.Logbook.getState().equipmentIndex.find(e => e.id === a.equipment_id)))) + "</td></tr>";
          }).join("") + "</tbody></table></div>" : '<p class="empty">No out-of-range alerts.</p>'}
        </div>
        <div class="card queue-card">
          <div class="card-h">
            <h3>My approvals queue</h3>
            <span class="pill review">${myN} waiting on you</span>
          </div>
          ${queue.length ? queue.map((d) => {
            const pending = d.pending_step;
            return '<div class="row spread" style="padding:8px 0;border-bottom:1px solid var(--line-2)">' +
              '<div><div class="row" style="gap:8px"><span class="avatar sm">' + esc(initials(d.submitted_by || "DOC")) + '</span>' +
              '<div><b>' + esc(d.title) + '</b><div class="tiny">' + esc(d.equipment_tag || "") + (d.document_type ? " · " + esc(d.document_type) : "") +
              (pending ? " · " + esc(pending.label) : "") + '</div></div></div></div>' +
              '<button class="btn btn-sm" data-act="doc-open" data-id="' + esc(d.id) + '">Review</button></div>';
          }).join("") : '<p class="empty">Nothing waiting on you.</p>'}
          <div style="margin-top:10px"><button class="linkish" data-act="goto" data-view="approvals">Open approvals</button></div>
        </div>
      </div>
    `;
  }

  function viewApprovals() {
    const L = global.Logbook;
    const ui = L._ui || (L._ui = {});
    const list = cache.docs || { items: [], total: 0 };
    const queue = cache.queue || { items: [], total: 0 };
    const items = list.items || [];
    return `
      <div class="page-head">
        <div>
          <h2>Approvals</h2>
          <p class="page-lead">Upload a test sheet, report, or photos against equipment, a record, or a work order, and route it for sequential sign-off.</p>
        </div>
        <div class="row">
          ${can("routes.manage") ? '<button class="btn" data-act="goto" data-view="routes">Routing rules</button>' : ""}
          ${can("documents.upload") ? '<button class="btn btn-primary" data-act="doc-new">Submit for approval</button>' : ""}
        </div>
      </div>
      <div class="card">
        <div class="card-h"><h3>Waiting on you</h3><span class="pill review">${queue.total || 0}</span></div>
        ${queue.items && queue.items.length ? '<div class="table-wrap"><table class="data"><thead><tr><th>Document</th><th>Asset</th><th>Step</th><th>Submitted</th><th></th></tr></thead><tbody>' +
          queue.items.map((d) => "<tr><td><b>" + esc(d.title) + "</b><div class='tiny'>" + statusPill(d.status) + "</div></td><td class='tag'>" +
            esc(d.equipment_tag || "—") + "</td><td>" + esc((d.pending_step && d.pending_step.label) || "—") +
            "</td><td class='tiny'>" + esc(fmtWhen(d.submitted_at)) + "</td><td><button class='btn btn-sm' data-act='doc-open' data-id='" + esc(d.id) + "'>Review</button></td></tr>").join("") +
          "</tbody></table></div>" : '<p class="empty">No documents are waiting on your approval.</p>'}
      </div>
      <div class="card">
        <div class="grid-toolbar">
          <h3 style="margin:0">All documents</h3>
          <label class="fld" style="min-width:10rem;margin-left:auto"><span>Search</span>
            <input type="search" id="doc-q" value="${esc(ui.docQ || "")}" placeholder="Title or type…">
          </label>
          <label class="fld"><span>Status</span>
            <select id="doc-status">
              <option value="">All</option>
              ${["draft","in_review","returned","approved","rejected"].map((s) =>
                "<option value='" + s + "'" + (ui.docStatus === s ? " selected" : "") + ">" + statusLabel(s) + "</option>").join("")}
            </select>
          </label>
        </div>
        <div class="table-wrap">
          <table class="data">
            <thead><tr><th>Title</th><th>Status</th><th>Type</th><th>Asset</th><th>Updated</th><th></th></tr></thead>
            <tbody>
              ${items.length ? items.map((d) =>
                "<tr><td><button class='linkish' data-act='doc-open' data-id='" + esc(d.id) + "'>" + esc(d.title) + "</button></td>" +
                "<td>" + statusPill(d.status) + (d.locked ? " <span class='tiny'>locked</span>" : "") + "</td>" +
                "<td>" + esc(d.document_type || "—") + "</td>" +
                "<td class='tag'>" + esc(d.equipment_tag || "—") + "</td>" +
                "<td class='tiny'>" + esc(fmtWhen(d.updated_at)) + "</td>" +
                "<td><button class='btn btn-sm' data-act='doc-open' data-id='" + esc(d.id) + "'>Open</button></td></tr>"
              ).join("") : '<tr><td colspan="6"><p class="empty">No documents yet. Upload a file to start a review.</p></td></tr>'}
            </tbody>
          </table>
        </div>
      </div>
    `;
  }

  function stepperHtml(doc) {
    const steps = doc.steps || [];
    const submitted = {
      label: "Submitted",
      status: doc.submitted_at ? "approved" : "waiting",
      actor_name: doc.submitted_by,
      acted_at: doc.submitted_at,
    };
    const all = [submitted].concat(steps);
    return '<div class="stepper">' + all.map((s, i) => {
      const done = s.status === "approved";
      const pending = s.status === "pending";
      const failed = s.status === "rejected" || s.status === "returned";
      const cls = done ? "done" : (pending ? "current" : "pending");
      const meta = done ? esc(s.actor_name || "") + "<br>" + esc(fmtWhen(s.acted_at || (i === 0 ? doc.submitted_at : "")))
        : (pending ? "Waiting · " + esc(s.label || "") : (failed ? esc(s.status) : "Not started"));
      return '<div class="step ' + cls + '"><div class="step-top"><div class="step-num">' + (done ? "✓" : (i + 1)) + '</div><div class="step-line"></div></div>' +
        '<div class="stitle">' + esc(s.label || (i === 0 ? "Submitted" : "Step")) + "</div>" +
        '<div class="smeta">' + meta + "</div></div>";
    }).join("") + "</div>";
  }

  function previewHtml(doc) {
    const ver = doc.current_version;
    if (!ver) return '<div class="doc-preview"><p class="no-preview">No file uploaded.</p></div>';
    const url = global.LogbookAPI.documentFileUrl(doc.id, ver.id);
    const mime = (ver.mime || "").toLowerCase();
    const token = global.LogbookAPI.getToken && global.LogbookAPI.getToken();
    // iframe/img can use same-origin cookie/session; token is header-only so append is not used.
    // File endpoint uses Authorization; iframe won't send it. Open-mode is fine.
    // For users-mode we still try iframe (cookie not used). Fallback: download link always.
    if (mime.indexOf("pdf") >= 0) {
      return '<div class="doc-preview"><iframe title="Document preview" src="' + esc(url) + (token ? "" : "") + '"></iframe></div>';
    }
    if (mime.indexOf("image/") === 0) {
      return '<div class="doc-preview"><img alt="' + esc(ver.filename) + '" src="' + esc(url) + '"></div>';
    }
    return '<div class="doc-preview"><div class="no-preview">No inline preview for ' + esc(ver.filename) +
      ' (' + esc(ver.mime || "unknown") + '). <a href="' + esc(url) + '" target="_blank" rel="noopener">Download</a></div></div>';
  }

  // For authenticated preview, fetch as blob after render.
  async function hydratePreview(doc) {
    const box = document.querySelector(".doc-preview");
    if (!box || !doc || !doc.current_version) return;
    const ver = doc.current_version;
    const mime = (ver.mime || "").toLowerCase();
    if (mime.indexOf("pdf") < 0 && mime.indexOf("image/") !== 0) return;
    const token = global.LogbookAPI.getToken && global.LogbookAPI.getToken();
    if (!token) return; // iframe already pointed at the file
    try {
      const res = await fetch(global.LogbookAPI.documentFileUrl(doc.id, ver.id), {
        headers: { Authorization: "Bearer " + token, Accept: mime || "*/*" }
      });
      if (!res.ok) return;
      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      if (mime.indexOf("pdf") >= 0) box.innerHTML = '<iframe title="Document preview" src="' + url + '"></iframe>';
      else box.innerHTML = '<img alt="' + esc(ver.filename) + '" src="' + url + '">';
    } catch (e) { /* keep fallback */ }
  }

  function viewApprovalDetail() {
    const doc = cache.detail;
    if (!doc) return '<p class="empty">Document not found. <button class="btn" data-act="goto" data-view="approvals">Back</button></p>';
    const ver = doc.current_version;
    const comments = doc.comments || [];
    const canAct = can("documents.approve") && doc.status === "in_review" && !doc.locked;
    const canSubmit = can("documents.upload") && (doc.status === "draft" || doc.status === "returned") && !doc.locked;
    const routes = cache.routes || [];
    return `
      <div class="page-head">
        <div>
          <div class="crumb">Approvals / ${esc(doc.document_type || "Document")} / ${esc(doc.id)}</div>
          <h2>${esc(doc.title)}</h2>
          <p class="page-lead">${esc(doc.equipment_tag || "")}${doc.work_order_id ? " · work order " + esc(doc.work_order_id) : ""}${doc.record_id ? " · record " + esc(doc.record_id) : ""}
            ${doc.submitted_at ? " · submitted " + esc(fmtWhen(doc.submitted_at)) : ""} · ${statusPill(doc.status)}${doc.locked ? " · locked" : ""}</p>
        </div>
        <div class="row">
          <button class="btn" data-act="goto" data-view="approvals">← All documents</button>
          ${ver ? '<a class="btn" href="' + esc(global.LogbookAPI.documentFileUrl(doc.id, ver.id)) + '" target="_blank" rel="noopener">Download</a>' : ""}
          ${canAct ? '<button class="btn success btn-primary" data-act="doc-approve">Approve</button>' +
            '<button class="btn warn" data-act="doc-return">Return for changes</button>' +
            '<button class="btn danger" data-act="doc-reject">Reject</button>' : ""}
        </div>
      </div>
      ${stepperHtml(doc)}
      <div class="approve-grid">
        <div class="preview-col card">
          <div class="card-h"><h3>Preview${ver ? " · " + esc(ver.filename) : ""}</h3>
            <span class="tiny">${ver ? "Rev " + ver.version_no + " · " + (ver.size || 0) + " bytes" : ""}</span></div>
          ${previewHtml(doc)}
          ${canSubmit ? submitPanel(doc, routes) : ""}
        </div>
        <div class="thread-col card">
          <div class="card-h"><h3>Comment thread</h3><span class="tiny">Audit trail</span></div>
          <div class="thread">
            ${comments.length ? comments.map((c) =>
              '<div class="comment"><div class="avatar sm">' + esc(initials(c.author || "SYS")) + '</div>' +
              '<div><div><span class="nm">' + esc(c.author || "System") + '</span> <span class="tiny">' + esc(fmtWhen(c.created_at)) + "</span></div>" +
              '<div class="tx">' + esc(c.body) + "</div></div></div>"
            ).join("") : '<p class="empty">No comments yet.</p>'}
          </div>
          <form id="doc-comment-form" class="row" style="margin:10px 12px 0" data-act="doc-comment">
            <input type="text" id="doc-comment" placeholder="Add a comment…" style="flex:1">
            <button class="btn" type="submit" data-act="doc-comment">Comment</button>
          </form>
          ${canAct ? '<div class="actions">' +
            '<button class="btn success btn-primary" data-act="doc-approve">Approve</button>' +
            '<button class="btn warn" data-act="doc-return">Return for changes</button>' +
            '<button class="btn danger" data-act="doc-reject">Reject</button></div>' : ""}
        </div>
      </div>
    `;
  }

  function submitPanel(existing, routes) {
    const L = global.Logbook;
    const state = L.getState();
    const eqs = state.equipment || [];
    const doc = existing;
    return `
      <div class="card submit-card">
        <h3>Submit for approval</h3>
        <div class="fields">
          <div class="fld"><label>Title</label>
            <input id="sub-title" class="ctrl" value="${esc(doc ? doc.title : "")}" placeholder="Commissioning test sheet">
          </div>
          <div class="fld"><label>Document type</label>
            <input id="sub-type" class="ctrl" value="${esc(doc ? (doc.document_type || "") : "")}" placeholder="commissioning_test_sheet">
          </div>
          <div class="fld"><label>Link to equipment</label>
            <select id="sub-eq"><option value=""></option>${eqs.map((e) => {
              const tag = (L.eqTag && L.eqTag(e)) || e.tag || e.id;
              const sel = doc && doc.equipment_id === e.id ? " selected" : "";
              return "<option value='" + esc(e.id) + "'" + sel + ">" + esc(tag) + "</option>";
            }).join("")}</select>
          </div>
          <div class="fld"><label>Work order id</label>
            <input id="sub-wo" class="ctrl" value="${esc(doc ? (doc.work_order_id || "") : "")}" placeholder="optional">
          </div>
        </div>
        <div class="fld" style="margin-top:8px"><label>Routing rule</label>
          <select id="sub-route">
            <option value="">Auto-match by type / site</option>
            ${(routes || []).map((r) => "<option value='" + esc(r.id) + "'" + (doc && doc.route_id === r.id ? " selected" : "") + ">" +
              esc(r.name) + (r.document_type ? " (" + esc(r.document_type) + ")" : "") + "</option>").join("")}
          </select>
        </div>
        <div class="drop" id="sub-drop" style="margin-top:8px">
          Drop a PDF, sheet, or photos — or <label class="linkish" style="display:inline">choose file
            <input type="file" id="sub-file" hidden>
          </label>
          <div class="tiny" id="sub-file-name">${doc && doc.current_version ? esc(doc.current_version.filename) : ""}</div>
        </div>
        <div class="row" style="justify-content:flex-end;margin-top:10px">
          ${doc ? '<button class="btn btn-primary" data-act="doc-submit-existing" data-id="' + esc(doc.id) + '">Submit for approval</button>'
                 : '<button class="btn btn-primary" data-act="doc-submit-new">Submit for approval</button>'}
        </div>
      </div>
    `;
  }

  function viewSubmit() {
    return `
      <div class="page-head">
        <div>
          <h2>Submit for approval</h2>
          <p class="page-lead">Upload a document and pick a routing rule. The first approver is notified when you submit.</p>
        </div>
        <button class="btn" data-act="goto" data-view="approvals">Cancel</button>
      </div>
      ${submitPanel(null, cache.routes || [])}
    `;
  }

  function viewRoutes() {
    const routes = cache.routes || [];
    return `
      <div class="page-head">
        <div>
          <h2>Approval routes</h2>
          <p class="page-lead">Admin-defined sequences of approvers (a role or a named user). Optional filters match a document type or site. The most specific matching enabled route is used when a submitter does not pick one.</p>
        </div>
        ${can("routes.manage") ? '<button class="btn btn-primary" data-act="rt-new">New route</button>' : ""}
      </div>
      <div class="card">
        ${routes.length ? '<div class="table-wrap"><table class="data"><thead><tr><th>Name</th><th>Type filter</th><th>Steps</th><th>Enabled</th><th></th></tr></thead><tbody>' +
          routes.map((r) => "<tr><td><b>" + esc(r.name) + "</b><div class='tiny'>" + esc(r.description || "") + "</div></td>" +
            "<td>" + esc(r.document_type || "any") + "</td>" +
            "<td>" + esc((r.steps || []).map((s) => s.label || s.role_name || s.user_id).join(" → ")) + "</td>" +
            "<td>" + (r.enabled ? "Yes" : "No") + "</td>" +
            "<td>" + (can("routes.manage") ? "<button class='btn btn-sm' data-act='rt-edit' data-id='" + esc(r.id) + "'>Edit</button> " +
              "<button class='btn btn-sm btn-danger' data-act='rt-del' data-id='" + esc(r.id) + "'>Delete</button>" : "") +
            "</td></tr>").join("") + "</tbody></table></div>" : '<p class="empty">No routes defined yet.</p>'}
      </div>
    `;
  }

  function routeForm(route) {
    const steps = (route && route.steps) || [{ assignee_type: "role", role_name: "supervisor", label: "Supervisor" }, { assignee_type: "role", role_name: "admin", label: "Admin" }];
    return '<div class="stack">' +
      '<label class="fld"><span>Name</span><input id="rt-name" value="' + esc(route ? route.name : "") + '" required></label>' +
      '<label class="fld"><span>Description</span><input id="rt-desc" value="' + esc(route ? (route.description || "") : "") + '"></label>' +
      '<label class="fld"><span>Document type filter (optional)</span><input id="rt-type" value="' + esc(route ? (route.document_type || "") : "") + '" placeholder="commissioning_test_sheet"></label>' +
      '<label class="fld check"><input type="checkbox" id="rt-enabled"' + (!route || route.enabled ? " checked" : "") + '> Enabled</label>' +
      '<p class="tiny">Steps (role name or leave role and set a user id). One per line: <code>role,supervisor,Supervisor</code> or <code>user,usr_…,Maya Chen</code></p>' +
      '<textarea id="rt-steps" rows="5">' + esc(steps.map((s) => [s.assignee_type, s.role_name || s.user_id || "", s.label || ""].join(",")).join("\\n")) + "</textarea></div>";
  }

  function parseSteps(text) {
    return String(text || "").split("\\n").map((line) => line.trim()).filter(Boolean).map((line, i) => {
      const parts = line.split(",").map((x) => x.trim());
      const type = (parts[0] || "role").toLowerCase() === "user" ? "user" : "role";
      const step = { sort_order: i, assignee_type: type, label: parts[2] || "" };
      if (type === "user") step.user_id = parts[1];
      else step.role_name = parts[1];
      return step;
    });
  }

  async function collectAndSaveRoute(id) {
    const body = {
      name: document.getElementById("rt-name").value,
      description: document.getElementById("rt-desc").value,
      document_type: document.getElementById("rt-type").value,
      enabled: document.getElementById("rt-enabled").checked,
      steps: parseSteps(document.getElementById("rt-steps").value),
    };
    if (id) await global.LogbookAPI.patchRoute(id, body);
    else await global.LogbookAPI.createRoute(body);
  }

  async function doSubmitNew() {
    const fileEl = document.getElementById("sub-file");
    const file = fileEl && fileEl.files && fileEl.files[0];
    if (!file) { toast("Choose a file to upload."); return; }
    const form = new FormData();
    form.append("file", file);
    form.append("title", (document.getElementById("sub-title") || {}).value || file.name);
    form.append("document_type", (document.getElementById("sub-type") || {}).value || "");
    form.append("equipment_id", (document.getElementById("sub-eq") || {}).value || "");
    form.append("work_order_id", (document.getElementById("sub-wo") || {}).value || "");
    form.append("route_id", (document.getElementById("sub-route") || {}).value || "");
    form.append("submit", "true");
    const doc = await global.LogbookAPI.uploadDocument(form);
    toast("Submitted for approval.");
    const L = global.Logbook;
    cache.detail = doc;
    L.goto("approvalDetail", { docId: doc.id });
  }

  async function doSubmitExisting(id) {
    const fileEl = document.getElementById("sub-file");
    const file = fileEl && fileEl.files && fileEl.files[0];
    const api = global.LogbookAPI;
    if (file) {
      const form = new FormData();
      form.append("file", file);
      await api.addDocumentVersion(id, form);
    }
    const patch = {
      title: (document.getElementById("sub-title") || {}).value,
      document_type: (document.getElementById("sub-type") || {}).value,
      equipment_id: (document.getElementById("sub-eq") || {}).value || null,
      work_order_id: (document.getElementById("sub-wo") || {}).value || null,
    };
    try { await api.patchDocument(id, patch); } catch (e) { /* may already be submitted */ }
    const doc = await api.submitDocument(id, { route_id: (document.getElementById("sub-route") || {}).value || null });
    toast("Submitted for approval.");
    cache.detail = doc;
    global.Logbook.goto("approvalDetail", { docId: doc.id });
  }

  async function commentRequired(action) {
    const existing = (document.getElementById("doc-comment") || {}).value;
    if (action === "approve") return existing || "";
    const text = existing || prompt(action === "reject" ? "Rejection comment (required)" : "Describe the changes required");
    if (!text || !String(text).trim()) {
      toast("A comment is required.");
      return null;
    }
    return String(text).trim();
  }

  const ACTS = new Set(["doc-open","doc-new","doc-submit-new","doc-submit-existing","doc-approve","doc-reject","doc-return","doc-comment","rt-new","rt-edit","rt-del"]);

  async function handleAction(act, el, ev) {
    if (!ACTS.has(act)) return false;
    const L = global.Logbook;
    const api = global.LogbookAPI;
    const ui = L._ui || (L._ui = {});
    try {
      if (act === "doc-open") {
        ui.docId = el.dataset.id;
        await loadDetail(ui.docId);
        L.goto("approvalDetail", { docId: ui.docId });
        return true;
      }
      if (act === "doc-new") {
        if (!cache.routes) cache.routes = await api.listRoutes();
        L.goto("docSubmit");
        return true;
      }
      if (act === "doc-submit-new") { await doSubmitNew(); return true; }
      if (act === "doc-submit-existing") { await doSubmitExisting(el.dataset.id); return true; }
      if (act === "doc-approve" || act === "doc-reject" || act === "doc-return") {
        const id = (cache.detail && cache.detail.id) || ui.docId;
        const comment = await commentRequired(act === "doc-approve" ? "approve" : (act === "doc-reject" ? "reject" : "return"));
        if (act !== "doc-approve" && comment == null) return true;
        if (act === "doc-approve") cache.detail = await api.approveDocument(id, comment);
        if (act === "doc-reject") cache.detail = await api.rejectDocument(id, comment);
        if (act === "doc-return") cache.detail = await api.returnDocument(id, comment);
        toast(act === "doc-approve" ? "Approved." : (act === "doc-reject" ? "Rejected." : "Returned for changes."));
        L.render();
        hydratePreview(cache.detail);
        return true;
      }
      if (act === "doc-comment") {
        ev && ev.preventDefault();
        const id = (cache.detail && cache.detail.id) || ui.docId;
        const text = (document.getElementById("doc-comment") || {}).value;
        if (!text) { toast("Write a comment first."); return true; }
        cache.detail = await api.commentDocument(id, text);
        L.render();
        hydratePreview(cache.detail);
        return true;
      }
      if (act === "rt-new" || act === "rt-edit") {
        const route = act === "rt-edit" ? (cache.routes || []).find((r) => r.id === el.dataset.id) : null;
        L.openModal ? null : null;
        const html = '<h2>' + (route ? "Edit route" : "New route") + "</h2>" + routeForm(route) +
          '<div class="row" style="justify-content:flex-end;margin-top:12px"><button class="btn" data-act="close-modal">Cancel</button> ' +
          '<button class="btn btn-primary" id="rt-save">Save</button></div>';
        if (L && document.getElementById) {
          // use the existing modal helper via a lightweight overlay
          let back = document.getElementById("modal-root");
          if (back) back.remove();
          back = document.createElement("div");
          back.id = "modal-root";
          back.className = "modal-back";
          back.innerHTML = '<div class="modal">' + html + "</div>";
          back.addEventListener("click", (e) => { if (e.target === back) back.remove(); });
          document.body.appendChild(back);
          back.querySelector("#rt-save").addEventListener("click", async () => {
            try {
              await collectAndSaveRoute(route && route.id);
              back.remove();
              cache.routes = await api.listRoutes();
              toast("Route saved.");
              L.render();
            } catch (err) { toast(err.message || "Save failed"); }
          });
          back.querySelector("[data-act=close-modal]").addEventListener("click", () => back.remove());
        }
        return true;
      }
      if (act === "rt-del") {
        if (!confirm("Delete this approval route?")) return true;
        await api.deleteRoute(el.dataset.id);
        cache.routes = await api.listRoutes();
        toast("Route deleted.");
        L.render();
        return true;
      }
    } catch (err) {
      toast(err.message || "Action failed");
    }
    return true;
  }

  function bindFilters() {
    const L = global.Logbook;
    const ui = L && (L._ui || (L._ui = {}));
    const q = document.getElementById("doc-q");
    const st = document.getElementById("doc-status");
    function go() {
      if (q) ui.docQ = q.value;
      if (st) ui.docStatus = st.value;
      Promise.resolve(loadDocs(ui)).finally(() => L.render());
    }
    if (q && !q._bound) { q._bound = true; q.addEventListener("change", go); q.addEventListener("keydown", (e) => { if (e.key === "Enter") go(); }); }
    if (st && !st._bound) { st._bound = true; st.addEventListener("change", go); }
    const drop = document.getElementById("sub-drop");
    const file = document.getElementById("sub-file");
    if (drop && file && !drop._bound) {
      drop._bound = true;
      ["dragenter", "dragover"].forEach((ev) => drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.add("over"); }));
      ["dragleave", "drop"].forEach((ev) => drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.remove("over"); }));
      drop.addEventListener("drop", (e) => {
        const f = e.dataTransfer.files[0];
        if (f) {
          const dt = new DataTransfer();
          dt.items.add(f);
          file.files = dt.files;
          const name = document.getElementById("sub-file-name");
          if (name) name.textContent = f.name;
        }
      });
      file.addEventListener("change", () => {
        const name = document.getElementById("sub-file-name");
        if (name && file.files[0]) name.textContent = file.files[0].name;
      });
    }
    const cform = document.getElementById("doc-comment-form");
    if (cform && !cform._bound) {
      cform._bound = true;
      cform.addEventListener("submit", (e) => { e.preventDefault(); handleAction("doc-comment", cform, e); });
    }
  }

  function applyBadges() {
    const dash = cache.dash;
    const wo = document.getElementById("nav-wo-count");
    const ap = document.getElementById("nav-appr-count");
    if (dash && wo) {
      wo.hidden = !dash.open_work_orders;
      wo.textContent = dash.open_work_orders || "";
    }
    if (dash && ap) {
      const n = dash.my_pending_approvals || dash.pending_approvals || 0;
      ap.hidden = !n;
      ap.textContent = n || "";
    }
    paintDashChart();
    if (cache.detail) hydratePreview(cache.detail);
  }

  // Hook into v4
  const v4 = global.LogbookV4 || {};
  const prevChrome = v4.applyChrome;
  const prevBefore = v4.beforeView;
  const prevHandle = v4.handleAction;
  const prevBind = v4.bindFilters;
  const prevViews = v4.views || {};

  v4.views = Object.assign({}, prevViews, {
    dashboard: viewDashboard,
    approvals: viewApprovals,
    approvalDetail: viewApprovalDetail,
    docSubmit: viewSubmit,
    routes: viewRoutes,
  });
  v4.applyChrome = function () {
    if (prevChrome) prevChrome();
    applyBadges();
  };
  v4.beforeView = async function (name, ui) {
    if (prevBefore) await prevBefore(name, ui);
    if (name === "dashboard") await loadDash();
    if (name === "approvals" || name === "docSubmit" || name === "routes") await loadDocs(ui || {});
    if (name === "approvalDetail") {
      const id = (ui && ui.docId) || (cache.detail && cache.detail.id);
      if (id) await loadDetail(id);
    }
  };
  v4.handleAction = function (act, el, ev) {
    if (ACTS.has(act)) return handleAction(act, el, ev);
    return prevHandle ? prevHandle(act, el, ev) : false;
  };
  v4.bindFilters = function () {
    if (prevBind) prevBind();
    bindFilters();
  };
  // preload dashboard counts so badges show after first render
  if (global.LogbookAPI) {
    loadDash().catch(() => {});
  }
  global.LogbookV5 = { cache, loadDash, loadDocs, viewDashboard };
})(typeof window !== "undefined" ? window : globalThis);
