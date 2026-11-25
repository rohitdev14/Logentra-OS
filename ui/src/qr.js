/* QR label printing, settings and phone sessions. */
(function () {
  "use strict";
  const api = window.LogbookAPI;
  const selected = new Map();
  const esc = value => String(value ?? "").replace(/[&<>"']/g, char =>
    ({"&":"&amp;", "<":"&lt;", ">":"&gt;", '"':"&quot;", "'":"&#39;"})[char]);

  function viewTags() {
    return `<div class="row spread"><div><h2>Print QR tags</h2>
      <p class="page-lead">Select equipment and print on A4 label sheets. Staff scan with their phone camera.</p></div>
      <button class="btn" data-act="goto" data-view="equipment">Equipment</button></div>
      <div class="qr-desktop"><div class="stack">
        <section class="card"><div class="row spread"><h3>Choose equipment</h3><span id="qr-selected"></span></div>
          <div class="row"><label class="fld"><span>Site</span><select id="qr-site"><option value="">All sites</option></select></label>
          <label class="fld"><span>Area</span><select id="qr-area"><option value="">All areas</option></select></label>
          <label class="fld"><span>Search</span><input id="qr-query" type="search" placeholder="Tag or name"></label></div>
          <div class="row"><button class="btn btn-sm" id="qr-select-page">Select visible</button>
          <button class="btn btn-sm" id="qr-clear">Clear selection</button></div>
          <div class="table-wrap"><table class="sheet"><thead><tr><th></th><th>Tag</th><th>Name</th><th>Area</th><th>Label</th></tr></thead>
          <tbody id="qr-rows"></tbody></table></div>
          <div class="row"><button class="btn btn-sm" id="qr-prev">Previous</button><span id="qr-page"></span>
          <button class="btn btn-sm" id="qr-next">Next</button></div>
        </section>
        <section class="card"><h3>Label settings</h3><p>A4 · 21 per sheet · 63.5 × 38.1 mm (L7160-type)</p>
          <label class="fld"><span>Start at position</span><input id="qr-start" type="number" min="1" max="21" value="1"></label>
          <p>Server address in QR <strong id="qr-url"></strong>
          <button class="btn btn-sm" data-act="goto" data-view="settings" data-need-perm="settings.manage">Settings</button></p>
          <p>Reprinting replaces existing codes. Old labels direct users to the current tag.</p>
        </section></div>
        <section class="card"><div class="row spread"><h3>Preview</h3><span id="qr-sheet-count"></span></div>
          <p id="qr-feedback" role="status"></p><div id="qr-preview" aria-label="A4 preview"></div>
          <button class="btn btn-primary" id="qr-print" disabled>Print / Save as PDF</button>
          <p>Print at 100% scale.</p>
        </section></div>`;
  }

  function bindTags() {
    const get = id => document.getElementById("qr-" + id);
    const host = get("rows");
    let rows = [], sites = [], page = 1, pages = 1, searchTimer, loadVersion = 0, previewVersion = 0;
    const start = () => Math.max(1, Math.min(21, Number(get("start").value) || 1));
    const feedback = text => { if (host.isConnected) get("feedback").textContent = text; };
    const fields = window.Logbook.getState().equipmentFields;
    const nameKey = fields.find(field => field.key === "name")?.id;
    const areaName = row => sites.flatMap(site => site.areas || []).find(area => area.id === row.area_id)?.name || "";
    const url = (ids, preview) => "/tags/print?" + new URLSearchParams({ids:[...ids].join(","), start:start(), preview});
    const renderRows = () => {
      if (!host.isConnected) return;
      host.innerHTML = rows.map(row => `<tr><td><input type="checkbox" aria-label="Select ${esc(row.tag)}"
        data-equipment="${esc(row.id)}" ${selected.has(row.id) ? "checked" : ""}></td>
        <td><strong>${esc(row.tag)}</strong></td><td>${esc(row.values[nameKey])}</td>
        <td>${esc(areaName(row))}</td><td>${row.qr_code_issued ? "Reprint" : "New"}</td></tr>`).join("")
        || '<tr><td colspan="5">No equipment matches.</td></tr>';
      get("page").textContent = page + " / " + pages;
      get("prev").disabled = page <= 1;
      get("next").disabled = page >= pages;
    };
    async function preview() {
      const version = ++previewVersion;
      const count = selected.size;
      get("selected").textContent = count + " selected";
      get("print").disabled = !count;
      get("sheet-count").textContent = count ? Math.ceil((count + start() - 1) / 21) + " sheet(s) · " + count + " labels" : "";
      if (!count) {
        get("preview").textContent = "Select equipment to preview a sheet.";
        return;
      }
      try {
        const response = await api.authenticatedFetch(url(selected.keys(), true));
        const html = await response.text();
        if (version !== previewVersion || !host.isConnected) return;
        const frame = document.createElement("iframe");
        frame.title = "Label sheet preview";
        frame.setAttribute("sandbox", "");
        frame.srcdoc = html;
        get("preview").replaceChildren(frame);
      } catch (error) { feedback(error.message); }
    }
    async function load() {
      const version = ++loadVersion;
      try {
        const result = await api.listEquipment({page, page_size:50, q:get("query").value,
          site_id:get("site").value, area_id:get("area").value});
        if (version !== loadVersion || !host.isConnected) return;
        rows = result.items;
        pages = Math.max(1, result.pages);
        rows.forEach(row => { if (selected.has(row.id)) selected.set(row.id, row); });
        renderRows();
      } catch (error) { feedback(error.message); }
    }
    function setAreas() {
      const site = get("site").value;
      const areas = sites.filter(item => !site || item.id === site).flatMap(item => item.areas || []);
      get("area").innerHTML = '<option value="">All areas</option>' + areas.map(area =>
        `<option value="${esc(area.id)}">${esc(area.name)}</option>`).join("");
    }
    api.listSites().then(result => {
      if (!host.isConnected) return;
      sites = result;
      get("site").insertAdjacentHTML("beforeend", sites.map(site =>
        `<option value="${esc(site.id)}">${esc(site.name)}</option>`).join(""));
      setAreas();
      renderRows();
    }).catch(error => feedback(error.message));
    get("url").textContent = location.origin;
    if (window.LogbookV4.can("settings.manage")) api.getPublicURL().then(result => {
      if (host.isConnected && result.public_base_url) get("url").textContent = result.public_base_url;
    }).catch(error => feedback(error.message));
    get("site").onchange = () => { setAreas(); page = 1; load(); };
    get("area").onchange = () => { page = 1; load(); };
    get("query").oninput = () => {
      clearTimeout(searchTimer);
      searchTimer = setTimeout(() => { if (host.isConnected) { page = 1; load(); } }, 180);
    };
    get("prev").onclick = () => { page--; load(); };
    get("next").onclick = () => { page++; load(); };
    get("start").oninput = preview;
    get("clear").onclick = () => { selected.clear(); renderRows(); preview(); };
    get("select-page").onclick = () => {
      rows.forEach(row => { if (selected.size < 500) selected.set(row.id, row); });
      renderRows();
      preview();
    };
    host.onchange = event => {
      const input = event.target;
      const row = rows.find(item => item.id === input.dataset.equipment);
      if (!row) return;
      if (input.checked && selected.size < 500) selected.set(row.id, row);
      else { selected.delete(row.id); input.checked = false; }
      preview();
    };
    get("print").onclick = async () => {
      const ids = [...selected.keys()];
      if ([...selected.values()].some(row => row.qr_code_issued)
          && !confirm("Replace the existing QR codes for the selected equipment?")) return;
      const tab = window.open("about:blank", "_blank");
      if (!tab) { feedback("Allow popups to print the sheet."); return; }
      get("print").disabled = true;
      try {
        await api.issueQRCodes(ids, true);
        const response = await api.authenticatedFetch(url(ids, false));
        const html = await response.text();
        tab.document.open();
        tab.document.write(html);
        tab.document.close();
        tab.focus();
        tab.print();
        await load();
      } catch (error) { tab.close(); feedback(error.message); }
      finally { if (host.isConnected) get("print").disabled = !selected.size; }
    };
    load();
    preview();
  }

  function viewSettings() {
    return `<h2>Settings</h2><section class="card"><h3>QR server address</h3>
      <form id="qr-url-form"><label class="fld"><span>Public server URL</span>
      <input type="url" name="publicUrl" placeholder="http://logbook.plant.local" maxlength="500"></label>
      <p>Use a hostname that phones on your network can reach.</p>
      <p id="qr-url-warning" hidden>Changing this address can prevent previously printed labels from opening.</p>
      <p id="qr-settings-feedback" role="status"></p><button class="btn btn-primary" disabled>Save address</button></form></section>`;
  }

  async function bindSettings(form) {
    const status = form.querySelector('[role="status"]');
    const button = form.querySelector("button");
    try {
      let saved = await api.getPublicURL();
      form.publicUrl.value = saved.public_base_url;
      form.querySelector("#qr-url-warning").hidden = !saved.labels_printed;
      button.disabled = false;
      form.onsubmit = async event => {
        event.preventDefault();
        if (saved.labels_printed && saved.public_base_url !== form.publicUrl.value
            && !confirm("Change the server address printed on QR labels?")) return;
        button.disabled = true;
        try { saved = await api.setPublicURL(form.publicUrl.value); status.textContent = "Address saved."; }
        catch (error) { status.textContent = error.message; }
        finally { button.disabled = false; }
      };
    } catch (error) { status.textContent = error.message; }
  }

  async function sessions(host, user) {
    try {
      const rows = await api.phoneSessions(user);
      host.innerHTML = rows.map(row => `<div class="row spread"><span>${esc(row.label || "Phone")}
        <small>Expires ${esc(new Date(row.expires_at).toLocaleString())}</small></span>
        <button class="btn btn-sm" data-session="${esc(row.id)}">Sign out</button></div>`).join("")
        || "<p>No active phone sessions.</p>";
      host.querySelectorAll("[data-session]").forEach(button => {
        button.onclick = async () => {
          button.disabled = true;
          try { await api.revokePhone(user, button.dataset.session); await sessions(host, user); }
          catch (error) { window.Logbook._toast(error.message); button.disabled = false; }
        };
      });
    } catch (error) { host.textContent = error.message; }
  }

  async function storage(host) {
    try {
      const result = await api.storage();
      const labels = {reading_photos:"Reading photos", shift_log_attachments:"Shift log attachments", documents:"Documents"};
      host.innerHTML = "<h3>Attachment storage</h3>" + Object.entries(labels).map(([key, label]) => {
        const {files, bytes} = result[key];
        return `<p>${label} · ${files.toLocaleString()} files · ${bytes.toLocaleString()} bytes</p>`;
      }).join("");
    } catch (error) { host.textContent = error.message; }
  }

  window.LogentraQR = {
    views:{qrTags:viewTags, settings:viewSettings, profile:() =>
      '<h2>Profile</h2><section class="card"><h3>Phone sessions</h3><div id="qr-own-sessions"></div></section>'},
    bind(view) {
      document.getElementById("qr-profile").hidden = window.Logbook.getState().me?.mode !== "user";
      if (view === "qrTags") bindTags();
      if (view === "settings") bindSettings(document.getElementById("qr-url-form"));
      if (view === "profile") sessions(document.getElementById("qr-own-sessions"));
      if (view === "backup" && window.LogbookV4.can("settings.manage")) storage(document.getElementById("qr-storage"));
      if (view === "users") document.querySelectorAll("[data-qr-user]").forEach(button => {
        button.onclick = () => {
          const dialog = document.createElement("dialog");
          dialog.innerHTML = '<h3>Phone sessions</h3><div></div><form method="dialog"><button class="btn">Close</button></form>';
          document.body.append(dialog);
          dialog.addEventListener("close", () => dialog.remove(), {once:true});
          dialog.showModal();
          sessions(dialog.querySelector("div"), button.dataset.qrUser);
        };
      });
    }
  };
})();
