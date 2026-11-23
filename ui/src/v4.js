/* v0.4 screens: login gate, work orders, users/roles, permission-aware chrome. */
(function (global) {
  "use strict";

  function esc(s) {
    return String(s ?? "").replace(/[&<>"']/g, (c) => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
    }[c]));
  }
  function product() {
    return (global.LOGBOOK_PRODUCT && global.LOGBOOK_PRODUCT.name) || "Logbook";
  }
  function can(perm) {
    const L = global.Logbook;
    const state = L && L.getState && L.getState();
    const me = state && state.me;
    if (!me || me.mode === "open" || me.mode === "legacy_key") return true;
    return (me.permissions || []).indexOf(perm) >= 0;
  }

  function applyChrome() {
    const L = global.Logbook;
    const state = L && L.getState && L.getState();
    const me = state && state.me;
    const authMode = (state && state.auth_mode) || (me && me.mode) || "open";
    document.querySelectorAll("[data-need-perm]").forEach((el) => {
      const need = el.getAttribute("data-need-perm");
      const ok = !need || need.split(/\s+/).every((p) => can(p));
      el.hidden = !ok;
      if (el.tagName === "BUTTON" || el.tagName === "A") {
        el.style.display = ok ? "" : "none";
      }
    });
    document.querySelectorAll("[data-need-any]").forEach((el) => {
      const need = el.getAttribute("data-need-any").split(/\s+/);
      const ok = need.some((p) => can(p));
      el.style.display = ok ? "" : "none";
    });
    const chip = document.getElementById("user-chip");
    const out = document.getElementById("logout-btn");
    if (chip) {
      if (me && me.mode === "user") {
        chip.hidden = false;
        chip.textContent = (me.username || "user") + " · " + (me.role || "");
      } else {
        chip.hidden = true;
      }
    }
    if (out) out.hidden = !(authMode === "users" && me && me.mode === "user");
  }

  /* ---------- login ---------- */
  function showLogin(message) {
    const gate = document.getElementById("login-gate");
    if (!gate) return;
    gate.hidden = false;
    document.body.classList.add("login-open");
    const err = document.getElementById("login-error");
    if (err) {
      if (message) { err.hidden = false; err.textContent = message; }
      else { err.hidden = true; err.textContent = ""; }
    }
  }
  function hideLogin() {
    const gate = document.getElementById("login-gate");
    if (gate) gate.hidden = true;
    document.body.classList.remove("login-open");
  }

  async function maybeRequireLogin() {
    const api = global.LogbookAPI;
    if (!api) return true;
    let health;
    try { health = await api.health(); } catch (e) { return true; }
    if (!health || health.auth_mode !== "users") return true;
    if (api.getToken()) {
      try {
        await api.me();
        hideLogin();
        return true;
      } catch (e) {
        api.setToken("");
      }
    }
    showLogin();
    return false;
  }

  function bindLogin() {
    const form = document.getElementById("login-form");
    if (!form || form._bound) return;
    form._bound = true;
    form.addEventListener("submit", async (ev) => {
      ev.preventDefault();
      const user = document.getElementById("login-user").value;
      const pass = document.getElementById("login-pass").value;
      const err = document.getElementById("login-error");
      try {
        await global.LogbookAPI.login(user, pass);
        hideLogin();
        if (global.Logbook && global.Logbook.loadStore) {
          const s = await global.Logbook.loadStore();
          // loadStore assigns state internally; re-render
          global.Logbook.render();
        }
      } catch (e) {
        if (err) { err.hidden = false; err.textContent = e.message || "Sign-in failed"; }
      }
    });
    const out = document.getElementById("logout-btn");
    if (out && !out._bound) {
      out._bound = true;
      out.addEventListener("click", async () => {
        try { await global.LogbookAPI.logout(); } catch (e) {}
        showLogin("Signed out.");
      });
    }
    global.addEventListener("logbook:auth-required", () => {
      if (global.LogbookAPI) global.LogbookAPI.setToken("");
      showLogin("Your session expired. Please sign in again.");
    });
  }

  /* ---------- work orders ---------- */
  const cache = { list: null, detail: null, users: [], rules: [], err: "" };

  function statusOptions(current) {
    return ["open", "in_progress", "on_hold", "done", "closed"].map((s) =>
      "<option value=\"" + s + "\"" + (s === current ? " selected" : "") + ">" + s.replace("_", " ") + "</option>"
    ).join("");
  }
  function prioOptions(current) {
    return ["low", "medium", "high", "urgent"].map((s) =>
      "<option" + (s === current ? " selected" : "") + ">" + s + "</option>"
    ).join("");
  }
  function eqOptions(state, selected) {
    const list = (state && (state.equipmentIndex || state.equipment)) || [];
    return '<option value=""></option>' + list.map((e) => {
      const tag = e.tag || (e.values && Object.values(e.values)[0]) || e.id;
      return "<option value=\"" + esc(e.id) + "\"" + (e.id === selected ? " selected" : "") + ">" + esc(tag) + "</option>";
    }).join("");
  }
  function userOptions(selected) {
    return '<option value=""></option>' + (cache.users || []).map((u) =>
      "<option value=\"" + esc(u.id) + "\"" + (u.id === selected ? " selected" : "") + ">" + esc(u.display_name || u.username) + "</option>"
    ).join("");
  }

  async function loadWorkOrderList(ui) {
    const api = global.LogbookAPI;
    const params = {
      page: ui.woPage || 1,
      page_size: 25,
      q: ui.woQ || "",
      status: ui.woStatus || "",
      priority: ui.woPrio || "",
    };
    cache.list = await api.listWorkOrders(params);
    return cache.list;
  }

  function viewWorkOrders() {
    const L = global.Logbook;
    const state = L.getState();
    const ui = L._ui || (L._ui = {});
    const data = cache.list || { items: [], total: 0, page: 1, pages: 1 };
    const items = data.items || [];
    const canCreate = can("workorders.create");
    return `
      <div class="row spread">
        <div>
          <h2>Work orders</h2>
          <p class="page-lead">Create, assign, and close maintenance or commissioning work. Status flows from open → in progress → on hold → done → closed.</p>
        </div>
        <div class="row">
          ${canCreate ? '<button class="btn btn-primary" data-act="wo-new">New work order</button>' : ""}
          ${can("workorders.assign") ? '<button class="btn" data-act="wo-rules">Auto-create rules</button>' : ""}
        </div>
      </div>
      <div class="card">
        <div class="grid-toolbar">
          <label class="fld" style="min-width:10rem"><span>Search</span>
            <input type="search" id="wo-q" value="${esc(ui.woQ || "")}" placeholder="Title or id…">
          </label>
          <label class="fld"><span>Status</span>
            <select id="wo-status"><option value="">All</option>${statusOptions(ui.woStatus || "")}</select>
          </label>
          <label class="fld"><span>Priority</span>
            <select id="wo-prio"><option value="">All</option>${prioOptions(ui.woPrio || "")}</select>
          </label>
          <span class="tiny" style="margin-left:auto">${data.total || 0} orders</span>
        </div>
        <div class="table-wrap">
          <table class="sheet">
            <thead><tr><th>Title</th><th>Status</th><th>Priority</th><th>Equipment</th><th>Assignee</th><th>Due</th><th></th></tr></thead>
            <tbody>
              ${items.length ? items.map((w) =>
                "<tr>" +
                "<td><button class='linkish' data-act='wo-open' data-id='" + esc(w.id) + "'>" + esc(w.title) + "</button></td>" +
                "<td><span class='kind-pill st-" + esc(w.status) + "'>" + esc(w.status.replace("_", " ")) + "</span></td>" +
                "<td>" + esc(w.priority) + "</td>" +
                "<td>" + esc(w.tag || w.equipment_id || "—") + "</td>" +
                "<td>" + esc(w.assignee || "—") + "</td>" +
                "<td>" + esc((w.due_at || "").slice(0, 10) || "—") + "</td>" +
                "<td class='no-print'><button class='btn btn-sm' data-act='wo-open' data-id='" + esc(w.id) + "'>Open</button></td>" +
                "</tr>"
              ).join("") : '<tr><td colspan="7"><p class="empty">No work orders yet. Create one, or enable an auto-create rule on out-of-range readings.</p></td></tr>'}
            </tbody>
          </table>
        </div>
        <div class="pager">${pager("wo-page", data)}</div>
      </div>
    `;
  }

  function pager(act, data) {
    const page = data.page || 1, pages = data.pages || 1;
    if (pages <= 1) return "";
    return '<button class="btn btn-sm" data-act="' + act + '" data-page="' + (page - 1) + '" ' + (page <= 1 ? "disabled" : "") + ">Previous</button> " +
      "<span class='tiny'>Page " + page + " of " + pages + "</span> " +
      '<button class="btn btn-sm" data-act="' + act + '" data-page="' + (page + 1) + '" ' + (page >= pages ? "disabled" : "") + ">Next</button>";
  }

  function viewWorkOrderDetail() {
    const w = cache.detail;
    if (!w) return '<p class="empty">Work order not found. <button class="btn" data-act="goto" data-view="workorders">Back</button></p>';
    const comments = (w.comments || []).map((c) =>
      "<li><strong>" + esc(c.author || "someone") + "</strong> <span class='tiny'>" + esc((c.created_at || "").replace("T", " ").slice(0, 16)) + "</span><div>" + esc(c.body) + "</div></li>"
    ).join("") || "<li class='tiny'>No comments yet.</li>";
    const labour = (w.labour || []).map((e) =>
      "<li>" + esc(String(e.minutes)) + " min · " + esc(e.author || "") + " · " + esc(e.worked_on || "") + (e.note ? " — " + esc(e.note) : "") + "</li>"
    ).join("") || "<li class='tiny'>No labour entries.</li>";
    const atts = (w.attachments || []).map((a) =>
      "<li>" + esc(a.filename) + (a.size ? " (" + a.size + " bytes)" : "") + " · " + esc(a.uploaded_by || "") + "</li>"
    ).join("") || "<li class='tiny'>No attachments. Metadata can also be posted via the API.</li>";
    const canMove = can("workorders.create") || can("workorders.assign") || can("workorders.close");
    return `
      <div class="row spread">
        <div>
          <h2>${esc(w.title)}</h2>
          <p class="page-lead">${esc(w.id)} · source ${esc(w.source || "manual")}</p>
        </div>
        <div class="row">
          <button class="btn" data-act="goto" data-view="workorders">← All work orders</button>
        </div>
      </div>
      <div class="card">
        <div class="form-grid">
          <label class="fld"><span>Status</span>
            <select id="wo-d-status" ${canMove ? "" : "disabled"}>${statusOptions(w.status)}</select>
          </label>
          <label class="fld"><span>Priority</span>
            <select id="wo-d-prio" ${canMove ? "" : "disabled"}>${prioOptions(w.priority)}</select>
          </label>
          <label class="fld"><span>Due</span>
            <input type="date" id="wo-d-due" value="${esc((w.due_at || "").slice(0, 10))}" ${canMove ? "" : "disabled"}>
          </label>
          <label class="fld"><span>Equipment</span>
            <select id="wo-d-eq" ${canMove ? "" : "disabled"}>${eqOptions(global.Logbook.getState(), w.equipment_id)}</select>
          </label>
          <label class="fld"><span>Assignee</span>
            <select id="wo-d-assignee" ${can("workorders.assign") ? "" : "disabled"}>${userOptions(w.assignee_id)}</select>
          </label>
        </div>
        <label class="fld"><span>Description</span>
          <textarea id="wo-d-desc" rows="3" ${canMove ? "" : "disabled"}>${esc(w.description || "")}</textarea>
        </label>
        ${canMove ? '<button class="btn btn-primary" data-act="wo-save">Save changes</button>' : ""}
        <p class="tiny" style="margin-top:.6rem">Linked reading ${esc(w.linked_reading_id || "—")} · punch ${esc(w.linked_punch_id || "—")} · labour ${esc(String(w.labour_minutes || 0))} min</p>
      </div>
      <div class="split-2">
        <div class="card">
          <h3>Comments</h3>
          <ul class="plain-list">${comments}</ul>
          ${canMove ? '<label class="fld"><span>Add comment</span><textarea id="wo-c-body" rows="2"></textarea></label><button class="btn" data-act="wo-comment">Add comment</button>' : ""}
        </div>
        <div class="card">
          <h3>Labour / time</h3>
          <ul class="plain-list">${labour}</ul>
          ${canMove ? '<div class="row"><label class="fld"><span>Minutes</span><input type="number" id="wo-l-min" min="0" step="15" value="30"></label><label class="fld" style="flex:1"><span>Note</span><input type="text" id="wo-l-note"></label></div><button class="btn" data-act="wo-labour">Add time</button>' : ""}
          <h3 style="margin-top:1rem">Attachments</h3>
          <ul class="plain-list">${atts}</ul>
          ${canMove ? '<div class="row"><label class="fld" style="flex:1"><span>Filename (metadata)</span><input type="text" id="wo-a-name" placeholder="photo.jpg"></label></div><button class="btn" data-act="wo-attach">Add attachment metadata</button>' : ""}
        </div>
      </div>
    `;
  }

  function viewWorkOrderNew() {
    const state = global.Logbook.getState();
    return `
      <div class="row spread">
        <div>
          <h2>New work order</h2>
          <p class="page-lead">Describe the job, link it to equipment, and optionally set a due date and assignee.</p>
        </div>
        <button class="btn" data-act="goto" data-view="workorders">Cancel</button>
      </div>
      <div class="card">
        <label class="fld"><span>Title</span><input type="text" id="wo-n-title" required></label>
        <label class="fld"><span>Description</span><textarea id="wo-n-desc" rows="3"></textarea></label>
        <div class="form-grid">
          <label class="fld"><span>Priority</span><select id="wo-n-prio">${prioOptions("medium")}</select></label>
          <label class="fld"><span>Due</span><input type="date" id="wo-n-due"></label>
          <label class="fld"><span>Equipment</span><select id="wo-n-eq">${eqOptions(state, "")}</select></label>
          <label class="fld"><span>Assignee</span><select id="wo-n-assignee">${userOptions("")}</select></label>
        </div>
        <button class="btn btn-primary" data-act="wo-create">Create work order</button>
      </div>
    `;
  }

  function viewRules() {
    const rules = cache.rules || [];
    const state = global.Logbook.getState();
    return `
      <div class="row spread">
        <div>
          <h2>Auto-create rules</h2>
          <p class="page-lead">Optionally open a work order when a reading is out of range. Rules are off by default.</p>
        </div>
        <button class="btn" data-act="goto" data-view="workorders">← Work orders</button>
      </div>
      <div class="card">
        <div class="form-grid">
          <label class="fld"><span>Equipment (blank = any)</span><select id="wor-eq">${eqOptions(state, "")}</select></label>
          <label class="fld"><span>Parameter key (blank = any)</span><input type="text" id="wor-param" placeholder="vibration"></label>
          <label class="fld"><span>Priority</span><select id="wor-prio">${prioOptions("high")}</select></label>
          <label class="fld"><span>Cooldown (minutes)</span><input type="number" id="wor-cd" value="1440"></label>
        </div>
        <label class="fld"><span>Title template</span><input type="text" id="wor-title" value="Out of range: {tag} {parameter} = {value}"></label>
        <label class="check"><input type="checkbox" id="wor-on"> Enable this rule</label>
        <button class="btn btn-primary" data-act="wor-create">Add rule</button>
      </div>
      <div class="card">
        <table class="sheet">
          <thead><tr><th>Equipment</th><th>Parameter</th><th>Priority</th><th>Enabled</th><th>Cooldown</th><th></th></tr></thead>
          <tbody>
            ${rules.length ? rules.map((r) =>
              "<tr><td>" + esc(r.equipment_id || "any") + "</td><td>" + esc(r.parameter_key || "any") +
              "</td><td>" + esc(r.priority) + "</td><td>" + (r.enabled ? "yes" : "no") +
              "</td><td>" + esc(String(r.cooldown_minutes)) + "</td>" +
              "<td><button class='btn btn-sm' data-act='wor-toggle' data-id='" + esc(r.id) + "' data-on='" + (r.enabled ? "0" : "1") + "'>" +
              (r.enabled ? "Disable" : "Enable") + "</button> " +
              "<button class='btn btn-sm btn-danger' data-act='wor-del' data-id='" + esc(r.id) + "'>Delete</button></td></tr>"
            ).join("") : '<tr><td colspan="6"><p class="empty">No rules.</p></td></tr>'}
          </tbody>
        </table>
      </div>
    `;
  }

  /* ---------- users / roles ---------- */
  function viewUsers() {
    const users = cache.users || [];
    const roles = cache.roles || [];
    const perms = (cache.permissions || []).slice();
    return `
      <div class="row spread">
        <div>
          <h2>Users and roles</h2>
          <p class="page-lead">Built-in roles: admin, supervisor, technician, viewer. You can also create a custom role from the permission list and optionally scope a user to a site or area.</p>
        </div>
      </div>
      <div class="card">
        <h3>Users</h3>
        <div class="table-wrap">
          <table class="sheet">
            <thead><tr><th>Username</th><th>Name</th><th>Role</th><th>Site</th><th>Active</th><th></th></tr></thead>
            <tbody>
              ${users.map((u) =>
                "<tr><td>" + esc(u.username) + "</td><td>" + esc(u.display_name) + "</td><td>" + esc(u.role || "") +
                "</td><td>" + esc(u.site_id || "—") + "</td><td>" + (u.is_active ? "yes" : "no") +
                "</td><td class='no-print'><button class='btn btn-sm' data-act='usr-key' data-id='" + esc(u.id) + "'>API key</button> " +
                "<button class='btn btn-sm btn-danger' data-act='usr-del' data-id='" + esc(u.id) + "'>Delete</button></td></tr>"
              ).join("") || '<tr><td colspan="6"><p class="empty">No users in the directory. In single-user mode the app works without accounts.</p></td></tr>'}
            </tbody>
          </table>
        </div>
        <h3 style="margin-top:1rem">Add user</h3>
        <div class="form-grid">
          <label class="fld"><span>Username</span><input type="text" id="usr-name"></label>
          <label class="fld"><span>Display name</span><input type="text" id="usr-disp"></label>
          <label class="fld"><span>Password</span><input type="password" id="usr-pass" minlength="8"></label>
          <label class="fld"><span>Role</span>
            <select id="usr-role">${roles.map((r) => "<option value=\"" + esc(r.name) + "\">" + esc(r.name) + "</option>").join("")}</select>
          </label>
        </div>
        <button class="btn btn-primary" data-act="usr-create">Create user</button>
      </div>
      <div class="card">
        <h3>Roles</h3>
        ${roles.map((r) =>
          "<div class='role-block'><strong>" + esc(r.name) + "</strong> " +
          (r.built_in ? "<span class='kind-pill'>built-in</span>" : "<span class='kind-pill'>custom</span>") +
          "<p class='tiny'>" + esc(r.description || "") + "</p>" +
          "<p class='tiny'>" + esc((r.permissions || []).join(", ")) + "</p></div>"
        ).join("")}
        <h3 style="margin-top:1rem">Custom role</h3>
        <label class="fld"><span>Name</span><input type="text" id="role-name" placeholder="planner"></label>
        <div class="perm-grid">
          ${perms.map((p) => "<label class='check'><input type='checkbox' data-perm='" + esc(p) + "'> " + esc(p) + "</label>").join("")}
        </div>
        <button class="btn" data-act="role-create">Create role</button>
      </div>
    `;
  }

  async function refreshUsers() {
    const api = global.LogbookAPI;
    const jobs = [];
    jobs.push(api.listUsers().then((r) => { cache.users = Array.isArray(r) ? r : (r.items || []); }).catch(() => { cache.users = []; }));
    if (can("users.manage")) {
      jobs.push(api.listRoles().then((r) => { cache.roles = r; }).catch(() => { cache.roles = []; }));
      jobs.push(api.listPermissions().then((r) => { cache.permissions = r.permissions || []; }).catch(() => {}));
    }
    await Promise.all(jobs);
  }

  /* ---------- actions ---------- */
  function toast(msg) {
    if (global.Logbook && global.Logbook._toast) return global.Logbook._toast(msg);
    console.log(msg);
  }

  async function handleAction(act, el, ev) {
    const api = global.LogbookAPI;
    const L = global.Logbook;
    const ui = L._ui || (L._ui = {});
    try {
      if (act === "wo-new") { ui.view = "woNew"; if (cache.users.length === 0) await refreshUsers(); L.render(); return true; }
      if (act === "wo-open") {
        cache.detail = await api.getWorkOrder(el.dataset.id);
        if (cache.users.length === 0) await refreshUsers();
        ui.view = "woDetail";
        L.render();
        return true;
      }
      if (act === "wo-page") {
        ui.woPage = Number(el.dataset.page || 1);
        await loadWorkOrderList(ui);
        L.render();
        return true;
      }
      if (act === "wo-create") {
        const body = {
          title: document.getElementById("wo-n-title").value,
          description: document.getElementById("wo-n-desc").value,
          priority: document.getElementById("wo-n-prio").value,
          due_at: document.getElementById("wo-n-due").value || null,
          equipment_id: document.getElementById("wo-n-eq").value || null,
          assignee_id: document.getElementById("wo-n-assignee").value || null,
        };
        const created = await api.createWorkOrder(body);
        cache.detail = created;
        ui.view = "woDetail";
        L.render();
        return true;
      }
      if (act === "wo-save") {
        const w = cache.detail;
        const body = {
          title: w.title,
          description: document.getElementById("wo-d-desc").value,
          priority: document.getElementById("wo-d-prio").value,
          due_at: document.getElementById("wo-d-due").value || null,
          equipment_id: document.getElementById("wo-d-eq").value || null,
          status: document.getElementById("wo-d-status").value,
        };
        cache.detail = await api.patchWorkOrder(w.id, body);
        const assignee = document.getElementById("wo-d-assignee").value || null;
        if (can("workorders.assign") && assignee !== (w.assignee_id || null)) {
          cache.detail = await api.assignWorkOrder(w.id, assignee);
        }
        L.render();
        return true;
      }
      if (act === "wo-comment") {
        const body = document.getElementById("wo-c-body").value;
        await api.commentWorkOrder(cache.detail.id, body);
        cache.detail = await api.getWorkOrder(cache.detail.id);
        L.render();
        return true;
      }
      if (act === "wo-labour") {
        await api.labourWorkOrder(cache.detail.id, {
          minutes: Number(document.getElementById("wo-l-min").value || 0),
          note: document.getElementById("wo-l-note").value,
        });
        cache.detail = await api.getWorkOrder(cache.detail.id);
        L.render();
        return true;
      }
      if (act === "wo-attach") {
        const filename = document.getElementById("wo-a-name").value;
        await api.attachWorkOrderMeta(cache.detail.id, { filename });
        cache.detail = await api.getWorkOrder(cache.detail.id);
        L.render();
        return true;
      }
      if (act === "wo-rules") {
        cache.rules = await api.listRules();
        ui.view = "woRules";
        L.render();
        return true;
      }
      if (act === "wor-create") {
        await api.createRule({
          equipment_id: document.getElementById("wor-eq").value || null,
          parameter_key: document.getElementById("wor-param").value || null,
          priority: document.getElementById("wor-prio").value,
          cooldown_minutes: Number(document.getElementById("wor-cd").value || 1440),
          title_template: document.getElementById("wor-title").value,
          enabled: document.getElementById("wor-on").checked,
        });
        cache.rules = await api.listRules();
        L.render();
        return true;
      }
      if (act === "wor-toggle") {
        await api.patchRule(el.dataset.id, { enabled: el.dataset.on === "1" });
        cache.rules = await api.listRules();
        L.render();
        return true;
      }
      if (act === "wor-del") {
        await api.deleteRule(el.dataset.id);
        cache.rules = await api.listRules();
        L.render();
        return true;
      }
      if (act === "usr-create") {
        await api.createUser({
          username: document.getElementById("usr-name").value,
          display_name: document.getElementById("usr-disp").value,
          password: document.getElementById("usr-pass").value,
          role: document.getElementById("usr-role").value,
        });
        await refreshUsers();
        L.render();
        return true;
      }
      if (act === "usr-del") {
        if (!confirm("Delete this user?")) return true;
        await api.deleteUser(el.dataset.id);
        await refreshUsers();
        L.render();
        return true;
      }
      if (act === "usr-key") {
        const created = await api.createApiKey(el.dataset.id, { name: "UI key" });
        alert("API key (shown only once):\n\n" + created.key);
        return true;
      }
      if (act === "role-create") {
        const permissions = Array.from(document.querySelectorAll("[data-perm]:checked")).map((i) => i.getAttribute("data-perm"));
        await api.createRole({ name: document.getElementById("role-name").value, permissions });
        await refreshUsers();
        L.render();
        return true;
      }
    } catch (e) {
      alert(e.message || String(e));
      return true;
    }
    return false;
  }

  function bindFilters() {
    const q = document.getElementById("wo-q");
    const st = document.getElementById("wo-status");
    const pr = document.getElementById("wo-prio");
    const L = global.Logbook;
    if (!q && !st) return;
    const run = async () => {
      const ui = L._ui;
      ui.woQ = q ? q.value : "";
      ui.woStatus = st ? st.value : "";
      ui.woPrio = pr ? pr.value : "";
      ui.woPage = 1;
      await loadWorkOrderList(ui);
      L.render();
    };
    if (q && !q._bound) { q._bound = true; q.addEventListener("change", run); q.addEventListener("keydown", (e) => { if (e.key === "Enter") run(); }); }
    if (st && !st._bound) { st._bound = true; st.addEventListener("change", run); }
    if (pr && !pr._bound) { pr._bound = true; pr.addEventListener("change", run); }
  }

  const views = {
    workorders: viewWorkOrders,
    woDetail: viewWorkOrderDetail,
    woNew: viewWorkOrderNew,
    woRules: viewRules,
    users: viewUsers,
  };

  async function beforeView(name, ui) {
    if (name === "workorders") {
      if (cache.users.length === 0) refreshUsers();
      await loadWorkOrderList(ui);
    }
    if (name === "users") {
      await refreshUsers();
    }
  }

  global.LogbookV4 = {
    can, applyChrome, showLogin, hideLogin, maybeRequireLogin, bindLogin,
    views, handleAction, beforeView, bindFilters, refreshUsers, cache,
    product,
  };

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", bindLogin);
  else bindLogin();
})(typeof window !== "undefined" ? window : globalThis);
