/* Shift timeline, roster administration and handover review. */
(function () {
  "use strict";
  const api = window.LogbookAPI;
  const book = () => window.Logbook;
  const can = permission => window.LogbookV4.can(permission);
  const request = (method, path, body) => api.shiftRequest(method, path, body);
  const withQuery = (path, filters) => path + "?" + new URLSearchParams(
    Object.entries(filters).filter(([, value]) => value !== "" && value != null)
  );
  const get = (path, filters) => request("GET", filters ? withQuery(path, filters) : path);
  const esc = value => String(value ?? "").replace(/[&<>"']/g, c =>
    ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"})[c]);
  const id = encodeURIComponent;
  const me = () => book().getState().me;
  const isOpen = () => me()?.mode === "open" || book().getState().auth_mode === "open" && !me();
  let rememberedName = "";
  try { rememberedName = localStorage.getItem("logentra-shift-operator") || ""; } catch (_) {}
  function nameField() {
    return isOpen() ? input("Your name", "author_name", "text", rememberedName, 'required maxlength="250"') : "";
  }
  function actor(form) {
    if (!isOpen()) return {};
    const name = form?.elements?.author_name?.value.trim() || rememberedName;
    if (!name) throw new Error("Enter your name to record this action.");
    rememberedName = name;
    try { localStorage.setItem("logentra-shift-operator", name); } catch (_) {}
    return {author_name:name};
  }
  function lead(row, incoming = false, manager = true) {
    return incoming ? (manager ? row.can_acknowledge : row.can_return) : row.can_edit;
  }
  const btn = (label, attrs = "", main = false) =>
    `<button type="button" class="btn ${main ? "btn-primary" : ""}" ${attrs}>${esc(label)}</button>`;
  const submit = label => `<button type="submit" class="btn btn-primary">${esc(label)}</button>`;
  const input = (label, name, type = "text", value = "", attrs = "") =>
    `<label class="fld"><span>${esc(label)}</span><input name="${name}" type="${type}" value="${esc(value)}" ${attrs}></label>`;
  const textarea = (label, name, value = "", attrs = "") =>
    `<label class="fld"><span>${esc(label)}</span><textarea name="${name}" rows="3" ${attrs}>${esc(value)}</textarea></label>`;
  const options = (rows, selected) => rows.map(([value, label]) =>
    `<option value="${esc(value)}"${String(value) === String(selected) ? " selected" : ""}>${esc(label)}</option>`).join("");
  const select = (label, name, rows, value = "", attrs = "") =>
    `<label class="fld"><span>${esc(label)}</span><select name="${name}" ${attrs}>${options(rows, value)}</select></label>`;
  const pairs = rows => rows.map(row => [row.id, row.name]);
  const local = (time, timezone) => time ? new Date(time).toLocaleString(undefined,
    {timeZone:timezone || "UTC", year:"numeric", month:"short", day:"numeric", hour:"2-digit", minute:"2-digit"}) : "—";
  const dateString = value => value.toISOString().slice(0,10);
  const labelStatus = value => String(value || "").replaceAll("_", " ");
  const formData = form => Object.fromEntries(new FormData(form));
  const hostHTML = (title, subtitle, extra = "") => `<div class="row spread"><div><h2>${title}</h2>
    <p class="page-lead">${subtitle}</p></div>${extra}</div><div id="shift-view" aria-live="polite">Loading…</div>`;
  const severityChoices = ["info","low","medium","high","critical"].map(value => [value, value]);
  const kinds = {oor_reading:"Out of range", work_order:"Work orders", punch:"Punch items", log_entry:"Log entries", note:"Notes"};
  const itemKind = item => item.category === "Safety" ? "Safety" : kinds[item.kind];
  const draftSummaries = new Map();
  const ui = {current:"", logPage:1, logFilter:"all", incomingFilter:"all", revisions:false, tab:"patterns", site:"",
    historyPage:1, historyQuery:{}, handoverPage:1, handoverQuery:{}};
  const bind = (host, target, fn) => host.querySelectorAll(target).forEach(element => {
    element.onclick = async () => {
      const original = element.disabled;
      element.disabled = element.tagName === "BUTTON";
      try { await fn(element); }
      catch (error) { book()._toast(error.message); }
      finally { if (element.isConnected) element.disabled = original; }
    };
  });
  function onForm(host, selector, handler) {
    const form = host.querySelector(selector);
    if (!form) return;
    form.onsubmit = async event => {
      event.preventDefault();
      const button = form.querySelector('button[type="submit"]');
      button.disabled = true;
      try { await handler(form); }
      catch (error) { book()._toast(error.message); }
      finally { if (button.isConnected) button.disabled = false; }
    };
  }
  function pager(page, pages) {
    return `<div class="row"><button type="button" class="btn btn-sm" data-page="${page - 1}" ${page <= 1 ? "disabled" : ""}>Previous</button>
      <span>Page ${page} of ${Math.max(1,pages)}</span>
      <button type="button" class="btn btn-sm" data-page="${page + 1}" ${page >= pages ? "disabled" : ""}>Next</button></div>`;
  }
  async function fileFetch(path, filename, print = false) {
    const popup = print ? window.open("about:blank","_blank") : null;
    if (print && !popup) throw new Error("Allow popups to open the print report.");
    try {
      const response = await api.authenticatedFetch(path);
      if (popup) {
        const html = await response.text();
        popup.document.open(); popup.document.write(html); popup.document.close();
      } else {
        const url = URL.createObjectURL(await response.blob());
        const link = document.createElement("a");
        link.href = url; link.download = filename; link.click();
        setTimeout(() => URL.revokeObjectURL(url), 2000);
      }
    } catch (error) { popup?.close(); throw error; }
  }

  const timestamp = value => value && !/(?:Z|[+-]\d{2}:\d{2})$/.test(value) ? value + "Z" : value;
  const clockTime = (value, zone) => value ? new Date(timestamp(value)).toLocaleTimeString(undefined,
    {timeZone:zone || "UTC", hour:"2-digit", minute:"2-digit", hourCycle:"h23"}) : "—";
  const rowTime = (value, severity, zone, content) => `<div class="timeline-row" data-severity="${esc(severity)}">
    <time class="timeline-clock" datetime="${esc(value)}">${esc(clockTime(value,zone))}</time>
    <span class="timeline-dot" aria-hidden="true"></span><div class="timeline-card">${content}</div></div>`;
  const filterChips = (choices, active, attribute) => `<div class="shift-chips" role="group" aria-label="Timeline filters">${choices.map(
    ([key,label,count]) => btn(label + " " + count,`${attribute}="${esc(key)}" aria-pressed="${key === active}"`)
  ).join("")}</div>`;
  const stepper = status => {
    const current = status === "acknowledged" ? 2 : ["signed_off","returned"].includes(status) ? 1 : 0;
    return `<ol class="shift-stepper" aria-label="Handover status">${["Draft","Signed off","Acknowledged"].map(
      (name,index) => `<li class="${index <= current ? "reached" : ""}" ${index === current ? 'aria-current="step"' : ""}>
        <span>${index + 1}</span>${name}</li>`).join("")}</ol>`;
  };
  const shiftName = shift => shift ? [shift.name,shift.crew_name].filter(Boolean).join(" · ") : "Not rostered";
  const minutesLeft = end => {
    const remaining = Math.ceil((new Date(end) - Date.now()) / 60000);
    return remaining > 0 ? remaining + " min left" : "Shift ended";
  };

  function recordCard(entry, row, timezone, editable) {
    const heading = esc(entry.title);
    const evidence = entry.equipment_id ? btn("Equipment", `data-equipment="${esc(entry.equipment_id)}"`) : "";
    const work = entry.work_order_id ? btn("Work order", `data-work="${esc(entry.work_order_id)}"`) : "";
    const attachments = entry.attachments.map(item => btn(item.filename,
      `data-attachment="${esc(item.id)}" data-filename="${esc(item.filename)}"`)).join("");
    const comments = entry.comments.map(msg => `<p><strong>${esc(msg.author_name)}</strong> ${esc(msg.body)}</p>`).join("");
    const included = row?.items.some(item => item.kind === "log_entry" && item.ref_id === entry.id);
    const controls = editable && !entry.voided_at ? [
      included ? "<small>In handover</small>" : row?.status === "draft" && lead(row) ? btn("Add to handover", `data-pin="${esc(entry.id)}"`) : "",
      btn("Comment", `data-comment="${esc(entry.id)}"`),
      btn("Amend", `data-amend="${esc(entry.id)}"`),
      btn("Void", `data-void="${esc(entry.id)}"`),
      btn("Attach", `data-attach="${esc(entry.id)}"`)
    ].join("") : "";
    return `<article class="card shift-entry ${included ? "shift-pinned" : ""}"><div class="row spread"><strong>${heading}</strong>
      <small>${row ? "" : esc(local(entry.event_at,timezone))}</small></div>
      <p class="shift-meta">${esc(entry.category)} · ${esc(entry.severity)} · ${esc(entry.author_name)}
      ${entry.after_handover ? " · After handover" : ""}${entry.version > 1 ? " · Version " + entry.version : ""}</p>
      <p class="shift-body">${esc(entry.body)}</p>
      ${entry.voided_at ? `<p>Voided: ${esc(entry.void_reason)}</p>` : ""}
      ${comments}<div class="row">${evidence}${work}${attachments}${controls}</div></article>`;
  }

  function composer(categories, entry = {}) {
    return `<form id="shift-entry-form" class="stack">${nameField()}
      ${input("Title","title","text",entry.title,'required maxlength="250"')}
      ${textarea("Shift entry","body",entry.body,'placeholder="Include #P-101 or WO-0001 to link evidence" maxlength="20000"')}
      <div class="row">${select("Category","category_id",pairs(categories),entry.category_id || "shift_cat_7")}
      ${select("Severity","severity",severityChoices, entry.severity || "info")}</div>
      <label><input type="checkbox" name="in_handover" ${entry.in_handover ? "checked" : ""}> Add to handover</label>
      ${input("Attachments","files","file","","multiple")}
      ${submit(entry.id ? "Save amendment" : "Add entry")}</form>`;
  }

  function captureEntry(form, entry) {
    const values = formData(form);
    delete values.files;
    return {...(entry ? Object.fromEntries(["equipment_id","work_order_id","reading_id","punch_id","event_at"]
      .map(key => [key,entry[key]])) : {}), ...values,
      ...actor(form), in_handover:form.elements.in_handover.checked};
  }
  async function uploadFiles(entry, files) {
    for (const file of files) {
      const data = new FormData();
      data.append("file",file);
      await request("POST", "/shift-log-entries/" + id(entry) + "/attachments", data);
    }
  }

  function dialogForm(title, contents, handler) {
    const dialog = document.createElement("dialog");
    dialog.className = "shift-dialog";
    dialog.innerHTML = `<h3>${esc(title)}</h3><form class="stack">${contents}
      <div class="row">${submit("Save")}${btn("Cancel","data-cancel")}</div></form>`;
    document.body.append(dialog);
    dialog.addEventListener("close", () => dialog.remove(), {once:true});
    dialog.querySelector("[data-cancel]").onclick = () => dialog.close();
    onForm(dialog,"form",async form => { await handler(form); dialog.close(); });
    dialog.showModal();
  }

  function outgoingPanel(row) {
    const editable = row.status === "draft" && lead(row);
    const groups = ["Safety","Out of range","Work orders","Punch items","Log entries","Notes"].map(label => {
      const items = row.items.filter(item => itemKind(item) === label);
      if (!items.length) return "";
      return `<section class="shift-item-group"><h4>${label}</h4>${items.map(item =>
        `<div class="shift-item"><div><strong>${esc(item.title)}</strong> ${item.auto ? '<small class="shift-auto">auto</small>' : ""}
        ${item.detail ? `<p>${esc(item.detail)}</p>` : ""}</div>
        ${editable ? btn("×", `data-remove="${esc(item.id)}" aria-label="Remove ${esc(item.title)}"`) : ""}</div>`).join("")}</section>`;
    }).join("");
    const returned = [...row.versions].reverse().find(item => item.return_reason);
    const end = clockTime(row.outgoing.ends_at,row.outgoing.timezone);
    const incoming = shiftName(row.incoming) + (row.incoming?.lead_name ? " · " + row.incoming.lead_name : "");
    return `<aside class="stack shift-handover"><section class="card">
      <div class="row spread"><h3>Sign off handover</h3><small>Version ${row.version}</small></div>
      ${stepper(row.status)}
      ${returned ? `<p class="shift-banner">Returned: ${esc(returned.return_reason)}</p>` : ""}
      <dl class="shift-facts"><dt>Pinned</dt><dd>${row.items.length} items</dd>
      <dt>Incoming</dt><dd>${esc(incoming)}</dd><dt>Shift ends</dt><dd>${esc(end)} · ${esc(minutesLeft(row.outgoing.ends_at))}</dd></dl>
      ${editable ? `<form id="shift-sign-form" class="stack">${nameField()}${submit("Sign off and hand over")}</form>`
        : btn("Open handover",`data-open="${esc(row.id)}"`)}
      <p class="shift-meta">If the handover is still unacknowledged ${row.ack_window_minutes} min after the incoming shift starts,
      it escalates to the ${esc(row.escalation_role)}.</p>
      </section><section class="card">
      <div class="row spread"><h3>In handover</h3><small>${row.items.length} items</small></div>
      ${groups || "<p>No items included.</p>"}
      ${editable ? `${textarea("Outgoing summary","summary",draftSummaries.get(row.id) ?? row.summary,'form="shift-sign-form" maxlength="20000"')}
        <div class="row">${btn("Add note","data-add-note")}${btn("Save draft","data-save-summary")}</div>`
        : `<p class="shift-body">${esc(row.summary)}</p>`}
      </section></aside>`;
  }

  async function renderLog(host) {
    const [current, notices] = await Promise.all([get("/shifts/current"),get("/handovers/notifications")]);
    if (!host.isConnected) return;
    const choices = new Map([...current.items, ...current.recent].map(row => [row.id,row]));
    notices.items.filter(item => item.kind === "returned").forEach(item => choices.set(item.shift.id,item.shift));
    if (!choices.size) {
      host.innerHTML = `<section class="card"><h3>No shift scheduled now</h3>
        <p>Add a roster entry for the area to start logging.</p>
        ${can("shifts.manage") ? btn("Shifts & rosters",'data-act="goto" data-view="rosters"') : ""}</section>`;
      return;
    }
    const shift = choices.get(ui.current) || choices.values().next().value;
    ui.current = shift.id;
    const path = "/shifts/" + id(shift.id);
    const [row,categories] = await Promise.all([
      get(path + "/handover"), get("/shift-log-categories",{site:shift.site_id})
    ]);
    const entries = await get(path + "/entries",{page:ui.logPage,page_size:25,revisions:ui.revisions,timeline_filter:ui.logFilter});
    if (!host.isConnected || ui.current !== shift.id) return;
    const visibleCategories = categories.filter(category => category.active);
    const extras = row.items.filter(item => item.kind !== "log_entry");
    const itemFilter = {oor_reading:"readings",work_order:"work_orders",note:"notes"};
    const counts = {...entries.counts};
    for (const item of extras) {
      counts.all++;
      counts.included++;
      if (itemFilter[item.kind]) counts[itemFilter[item.kind]]++;
    }
    const chips = [["all","All"],["included","In handover"],
      ...categories.filter(category => counts["category:" + category.id] || ui.logFilter === "category:" + category.id)
        .map(category => ["category:" + category.id,category.name]),
      ["readings","Readings"],["work_orders","Work orders"],["notes","Notes"]
    ].map(([key,label]) => [key,label,counts[key] || 0]);
    const timeline = entries.items.map(entry => [entry.event_at, entry.severity,
      recordCard(entry,row,shift.timezone,can("shiftlog.write"))]);
    if (entries.page === 1) {
      for (const item of extras) {
        if (!["all","included",itemFilter[item.kind]].includes(ui.logFilter)) continue;
        timeline.push([item.event_at,item.severity,`<article class="card shift-entry shift-pinned">
          <p class="shift-meta">${esc(itemKind(item))} · ${esc(item.severity)}</p><strong>${esc(item.title)}</strong>
          <p class="shift-body">${esc(item.detail)}</p><small class="shift-auto">In handover</small></article>`]);
      }
    }
    timeline.sort(([left],[right]) => String(timestamp(right) || "").localeCompare(String(timestamp(left) || "")));
    host.innerHTML = `<div class="shift-context">
      ${choices.size > 1 ? select("Shift","selection",[...choices.values()].map(item => [item.id,
        [item.area_name,item.name,item.crew_name].filter(Boolean).join(" · ")]),shift.id) : ""}
      <p>Outgoing <strong>${esc(shiftName(shift))}</strong> · Incoming <strong>${esc(shiftName(row.incoming))}</strong>
      ${row.incoming?.lead_name ? " lead " + esc(row.incoming.lead_name) : ""} · Handover due
      <strong>${esc(clockTime(shift.ends_at,shift.timezone))}</strong> · ${esc(minutesLeft(shift.ends_at))}</p></div>
      <div class="shift-layout"><div class="stack">
       ${can("shiftlog.write") ? `<section class="card"><h3>Add log entry</h3>${composer(visibleCategories)}</section>` : ""}
       <section class="card"><div class="row spread"><h3>Shift timeline</h3>
       <label><input type="checkbox" name="revisions" ${ui.revisions ? "checked" : ""}> Show revisions</label></div>
       ${filterChips(chips,ui.logFilter,"data-log-filter")}
       <div class="shift-timeline">${timeline.map(([at,severity,card]) => rowTime(at,severity,shift.timezone,card)).join("")
         || "<p>No log entries match.</p>"}</div>
       ${entries.pages > 1 ? pager(entries.page,entries.pages) : ""}</section></div>${outgoingPanel(row)}</div>`;
    const selection = host.querySelector("[name=selection]");
    if (selection) selection.onchange = event => { ui.current = event.target.value; ui.logPage = 1; refresh(host,renderLog); };
    bind(host,"[data-log-filter]", button => { ui.logFilter = button.dataset.logFilter; ui.logPage = 1; return renderLog(host); });
    host.querySelector("[name=revisions]").onchange = event => { ui.revisions = event.target.checked; ui.logPage = 1; refresh(host,renderLog); };
    bind(host,"[data-page]", button => { ui.logPage = Number(button.dataset.page); return renderLog(host); });
    bind(host,"[data-open]",button => openHandover(button.dataset.open));
    bindEvidence(host);
    onForm(host,"#shift-entry-form",async form => {
      const result = await request("POST",path + "/entries",captureEntry(form));
      try { await uploadFiles(result.id,form.elements.files.files); }
      finally { await renderLog(host); }
    });
    bind(host,"[data-comment]",button => dialogForm("Comment on entry", nameField() + textarea("Comment","body","", "required"), async form => {
      await request("POST","/shift-log-entries/" + id(button.dataset.comment) + "/comments",{...formData(form),...actor(form)});
      await renderLog(host);
    }));
    bind(host,"[data-void]",button => dialogForm("Void entry", nameField() + textarea("Reason","reason","", "required"), async form => {
      await request("POST","/shift-log-entries/" + id(button.dataset.void) + "/void",{...formData(form),...actor(form)});
      await renderLog(host);
    }));
    bind(host,"[data-amend]",button => {
      const entry = entries.items.find(item => item.id === button.dataset.amend);
      const markup = composer(visibleCategories,entry);
      dialogForm("Amend entry",new DOMParser().parseFromString(markup,"text/html").querySelector("form").innerHTML
        .replace(/<button type="submit".*?<\/button>/,""),async form => {
        const result = await request("POST","/shift-log-entries/" + id(entry.id) + "/amend",captureEntry(form,entry));
        try { await uploadFiles(result.id,form.elements.files.files); }
        finally { await renderLog(host); }
      });
    });
    bind(host,"[data-attach]",button => dialogForm("Attach to log entry",input("Attachment","files","file","","required multiple"),async form => {
      await uploadFiles(button.dataset.attach,form.elements.files.files);
      await renderLog(host);
    }));
    const handoverPath = "/handovers/" + id(row.id);
    const sign = host.querySelector("#shift-sign-form");
    let savedSummary = row.summary;
    if (sign) sign.elements.summary.oninput = () => draftSummaries.set(row.id, sign.elements.summary.value);
    async function preserveSummary() {
      if (sign && sign.elements.summary.value !== savedSummary) {
        const summary = sign.elements.summary.value;
        await request("PATCH",handoverPath,{summary,...actor(sign)});
        savedSummary = summary;
        draftSummaries.delete(row.id);
      }
    }
    bind(host,"[data-remove]",async button => {
      await preserveSummary();
      await request("DELETE", handoverPath + "/items/" + id(button.dataset.remove),actor(sign));
      await renderLog(host);
    });
    bind(host,"[data-pin]",async button => {
      await preserveSummary();
      await request("POST",handoverPath + "/items",{kind:"log_entry",ref_id:button.dataset.pin,...actor(sign)});
      await renderLog(host);
    });
    bind(host,"[data-add-note]",() => dialogForm("Add handover note",nameField() +
      input("Title","title","text","","required") + textarea("Details","detail"),async form => {
      const who = actor(form);
      await preserveSummary();
      await request("POST",handoverPath + "/items",{...formData(form),...who});
      await renderLog(host);
    }));
    bind(host,"[data-save-summary]",async () => { await preserveSummary(); book()._toast("Handover draft saved."); });
    onForm(host,"#shift-sign-form",async form => {
      const result = await request("POST",handoverPath + "/sign-off",{summary:form.elements.summary.value,...actor(form)});
      draftSummaries.delete(row.id);
      openHandover(result.id);
    });
  }

  function bindEvidence(host) {
    bind(host,"[data-attachment]",button => fileFetch("/api/shift-log-attachments/" + id(button.dataset.attachment), button.dataset.filename));
    bind(host,"[data-equipment]",button => book().goto("equipment",{q:book().eqTag(book().eqById(button.dataset.equipment) || {})}));
    bind(host,"[data-work]",button => book().goto("workorders",{woQ:button.dataset.work}));
  }

  function openHandover(identifier) {
    ui.incomingFilter = "all";
    book().goto("handoverDetail",{handoverId:identifier});
  }

  async function renderHandover(host) {
    const identifier = book()._ui.handoverId;
    const path = "/handovers/" + id(identifier);
    const row = await get(path);
    if (!host.isConnected) return;
    const editable = row.status === "signed_off" && lead(row,true);
    const canReturn = row.status === "signed_off" && lead(row,true,false);
    const outgoing = row.outgoing;
    const seenCount = row.items.filter(item => item.seen_at).length;
    const questions = new Set(row.comments.filter(message => message.kind === "question").map(message => message.item_id));
    const questionCount = row.comments.filter(message => message.kind === "question").length;
    const visibleItems = row.items.filter(item => ui.incomingFilter === "all" ||
      (ui.incomingFilter === "unseen" && !item.seen_at) || (ui.incomingFilter === "questions" && questions.has(item.id)));
    const eventLabels = {opened:"opened the handover",signed_off:"signed off",acknowledged:"acknowledged",
      returned:"returned the handover",question:"asked a question",answer:"answered a question",item_seen:"marked an item seen",
      item_added:"added an item",item_removed:"removed an item",summary_changed:"updated the summary",
      reminder_sent:"sent a reminder",escalated:"escalated the handover"};
    host.innerHTML = `<div class="row spread shift-context"><p>From <strong>${esc(shiftName(outgoing))}</strong>
      ${outgoing.lead_name ? " lead " + esc(outgoing.lead_name) : ""} · To <strong>${esc(shiftName(row.incoming))}</strong>
      · ${esc(outgoing.area_name)} · Version ${row.version}</p>${btn("Print / PDF","data-report")}</div>
      <section class="shift-banner ${row.status === "signed_off" ? "shift-waiting" : ""}">
      <strong>${row.status === "signed_off" ? (editable ? "Waiting for you" : "Awaiting acknowledgement") : esc(labelStatus(row.status))}</strong>
      ${row.signed_off_at ? " · Signed off by " + esc(row.signed_off_name) + " at " + esc(clockTime(row.signed_off_at,outgoing.timezone)) : ""}
      ${row.ack_due_at && row.status === "signed_off" ? " · Acknowledge by " + esc(clockTime(row.ack_due_at,outgoing.timezone))
        + " (" + row.ack_window_minutes + " min after shift start) or it escalates to the " + esc(row.escalation_role) + "." : ""}</section>
      <div class="shift-layout"><div class="stack">
      <section class="card"><div class="row spread"><h3>Outgoing summary</h3>
      ${row.signed_off_name ? `<small>${esc(row.signed_off_name)} · ${esc(clockTime(row.signed_off_at,outgoing.timezone))}</small>` : ""}</div>
      <p class="shift-body">${esc(row.summary || "No summary entered.")}</p></section>
      <section class="card"><div class="row spread"><h3>Handover items</h3><small>${seenCount} of ${row.items.length} seen</small></div>
      ${filterChips([["all","All",row.items.length],["unseen","Not seen",row.items.length-seenCount],
        ["questions","Questions",questionCount]],ui.incomingFilter,"data-incoming-filter")}
      <div class="shift-timeline">${visibleItems.map(item => rowTime(item.event_at,item.severity,outgoing.timezone,
        `<article class="card shift-entry shift-pinned"><div class="row spread">
        <p class="shift-meta">${esc(itemKind(item))} · ${esc(item.severity)}</p>
        ${item.seen_at ? '<small class="shift-seen">✓ Seen</small>' : editable ? btn("Mark seen",`data-seen="${esc(item.id)}"`) : '<small>Unseen</small>'}
        </div><h4>${esc(item.title)}</h4><p class="shift-body">${esc(item.detail)}</p>
        ${row.comments.filter(msg => msg.item_id === item.id).map(msg =>
          `<div class="shift-question"><strong>${esc(msg.author_name)}</strong>
          <small>${esc(clockTime(msg.created_at,outgoing.timezone))} · ${esc(msg.kind)}</small>
          <p class="shift-body">${esc(msg.body)}</p>
          ${msg.kind === "question" && lead(row) ? btn("Answer",`data-answer="${esc(msg.id)}"`) : ""}</div>`).join("")}
        ${editable ? btn("Ask a question",`data-question="${esc(item.id)}"`) : ""}</article>`
      )).join("") || "<p>No items match this filter.</p>"}</div></section></div>
      <aside class="stack">
      ${editable ? `<section class="card shift-handover"><h3>Acknowledge handover</h3>${stepper(row.status)}
      <form id="shift-ack-form" class="stack">${nameField()}${textarea("Comment (optional)","comment")}
      ${submit("Acknowledge")}</form>
      <p class="shift-meta">${row.items.length - seenCount} unseen items. Unseen items are recorded at acknowledgement.</p>
      ${canReturn ? `<form id="shift-return-form" class="shift-return stack">
        ${textarea("Return to outgoing lead · reason required","reason","","required")}
        ${submit("Return handover")}</form>` : ""}</section>` : ""}
      <section class="card"><h3>Audit trail</h3>
      <ul class="shift-audit">${row.audit_events.map(event => `<li><time>${esc(clockTime(event.at,outgoing.timezone))}</time>
        <span>${esc(event.actor)} ${esc(eventLabels[event.action.split(".").at(-1)] || labelStatus(event.action))}</span></li>`).join("")}</ul>
      ${row.acknowledged_at ? `<p>Acknowledged by ${esc(row.acknowledged_name)} · ${esc(local(row.acknowledged_at,outgoing.timezone))}
      ${row.late ? " · Late" : ""}${row.acknowledged_after_start ? " · After incoming shift start" : ""}</p><p>Unseen at acknowledgement · ${row.unseen_count}</p><p>${esc(row.ack_comment)}</p>` : ""}
      ${row.return_reason ? `<p>Returned by ${esc(row.returned_name)} · ${esc(row.return_reason)}</p>` : ""}
      ${row.snapshot_sha256 ? `<p>Snapshot SHA-256 <code>${esc(row.snapshot_sha256)}</code></p>` : ""}
      ${row.status === "draft" ? btn("Open shift log","data-outgoing") : ""}
      <h4>Versions</h4>${row.versions.map(version => `<p>${btn("Version " + version.version + " · " +
        labelStatus(version.status), `data-version="${esc(version.id)}"`)} ${esc(version.return_reason || "")}</p>`).join("")}
      </section></aside></div>`;
    bind(host,"[data-incoming-filter]",button => {
      ui.incomingFilter = button.dataset.incomingFilter;
      return renderHandover(host);
    });
    bind(host,"[data-report]",() => fileFetch("/api" + path + "/report","",true));
    bind(host,"[data-version]",button => openHandover(button.dataset.version));
    bind(host,"[data-outgoing]",() => { ui.current = row.outgoing_shift_id; book().goto("shiftLog"); });
    const ackForm = host.querySelector("#shift-ack-form");
    bind(host,"[data-seen]",async button => {
      await request("POST",path + "/items/" + id(button.dataset.seen) + "/seen",actor(ackForm));
      await renderHandover(host);
    });
    bind(host,"[data-question]",button => dialogForm("Ask a question",nameField() + textarea("Question","body","", "required"),async form => {
      await request("POST",path + "/items/" + id(button.dataset.question) + "/questions",{...formData(form),...actor(form)});
      await renderHandover(host);
    }));
    bind(host,"[data-answer]",button => dialogForm("Answer question",nameField() + textarea("Answer","body","", "required"),async form => {
      await request("POST","/handover-comments/" + id(button.dataset.answer) + "/answer",{...formData(form),...actor(form)});
      await renderHandover(host);
    }));
    onForm(host,"#shift-return-form",async form => {
      const draft = await request("POST",path + "/return",{...formData(form),...actor(ackForm)});
      openHandover(draft.id);
    });
    onForm(host,"#shift-ack-form",async form => {
      await request("POST",path + "/acknowledge",{...formData(form),...actor(form)});
      await renderHandover(host);
      refreshNotifications();
    });
  }

  async function renderHistory(host, logs) {
    const page = logs ? ui.historyPage : ui.handoverPage;
    const filters = logs ? ui.historyQuery : ui.handoverQuery;
    const path = logs ? "/shift-log-entries" : "/handovers";
    const [result, sites, categories, crews] = await Promise.all([
      get(path,{...filters,page,page_size:25}),api.listSites(),
      logs ? get("/shift-log-categories") : Promise.resolve([]),
      can("shifts.manage") ? get("/crews") : Promise.resolve([])
    ]);
    if (!host.isConnected) return;
    const areas = sites.flatMap(site => site.areas || []);
    const crewChoices = crews.length ? pairs(crews) : (logs ? [] : [...new Map(result.items.map(row =>
      [row.outgoing.crew_id,row.outgoing.crew_name])).entries()]);
    host.innerHTML = `<section class="card"><form id="shift-search" class="row">
      ${input("From","from","date",filters.from)}${input("To","to","date",filters.to)}
      ${select("Area","area",[["","All areas"],...pairs(areas)],filters.area)}
      ${select("Crew","crew",[["","All crews"],...crewChoices],filters.crew)}
      ${logs ? select("Category","category",[["","All categories"],...pairs(categories)],filters.category) +
        select("Severity","severity",[["","All severities"],...severityChoices],filters.severity) +
        select("Equipment","equipment",[["","All equipment"],...book().getState().equipmentIndex.map(row => [row.id,row.tag])],filters.equipment)
        : select("Status","status",[["","All statuses"],...["draft","signed_off","acknowledged","returned"].map(value => [value,labelStatus(value)])],filters.status)}
      ${input("Search","q","search",filters.q)}${submit("Search")}
      ${logs ? "" : btn("Export CSV","data-export")}</form></section>
      <div class="shift-timeline">${logs ? result.items.map(entry => recordCard(entry,null,"UTC",false)).join("")
        : result.items.map(row => `<article class="card row spread"><div><strong>${esc(row.outgoing.name)} · ${esc(row.outgoing.area_name)}</strong>
          <p>${esc(row.outgoing.date)} · ${esc(row.outgoing.crew_name)} · ${esc(labelStatus(row.status))} · Version ${row.version}</p>
          <p>${esc(row.summary)}</p></div>${btn("Open handover",`data-open="${esc(row.id)}"`)}</article>`).join("")}
      ${result.items.length ? "" : "<p>No entries match the selected filters.</p>"}</div>
      ${pager(result.page,result.pages)}`;
    bindEvidence(host);
    bind(host,"[data-open]",button => openHandover(button.dataset.open));
    bind(host,"[data-export]",() => fileFetch(withQuery("/api/handovers/export.csv",filters),"handovers.csv"));
    bind(host,"[data-page]",button => {
      if (logs) ui.historyPage = Number(button.dataset.page); else ui.handoverPage = Number(button.dataset.page);
      return renderHistory(host,logs);
    });
    onForm(host,"#shift-search",async form => {
      if (logs) { ui.historyQuery = formData(form); ui.historyPage = 1; }
      else { ui.handoverQuery = formData(form); ui.handoverPage = 1; }
      await renderHistory(host,logs);
    });
  }

  const adminTabs = [["patterns","Shift pattern"],["crews","Crews"],["roster","Roster"],
    ["escalation","Handover & escalation"],["categories","Log categories"]];
  const adminState = {pattern:"",crew:"",start:"",end:""};
  const setupForm = (key, fields, label) => `<form id="${key}" class="shift-admin-form stack">${fields}${submit(label)}</form>`;
  const section = (title, contents) => `<section class="card"><h3>${esc(title)}</h3>${contents}</section>`;

  async function renderAdmin(host) {
    const sites = await api.listSites();
    if (!host.isConnected) return;
    const site = sites.find(row => row.id === ui.site) || sites[0];
    ui.site = site?.id || "";
    host.innerHTML = `<div class="row shift-context">${select("Site","site",pairs(sites),ui.site)}
      <div role="group" aria-label="Shift administration" class="row">${adminTabs.map(([key,label]) =>
        btn(label,`data-tab="${key}" aria-pressed="${ui.tab === key}"`, ui.tab === key)).join("")}</div></div>
      <div id="shift-admin-content"></div>`;
    const designated = host.querySelector("#shift-admin-content");
    bind(host,"[data-tab]",button => { ui.tab = button.dataset.tab; return renderAdmin(host); });
    host.querySelector("[name=site]").onchange = event => {
      ui.site = event.target.value; adminState.pattern = adminState.crew = ""; refresh(host,renderAdmin);
    };
    if (!site) {
      designated.innerHTML = section("Create a site", can("users.manage")
        ? setupForm("shift-site-create", input("Site name","name","text","","required"), "Create site")
        : "<p>Ask an administrator to create a site.</p>");
      onForm(designated,"#shift-site-create",async form => { await api.createSite(formData(form)); await renderAdmin(host); });
      return;
    }
    const rerender = () => renderAdmin(host);
    if (ui.tab === "patterns") {
      const rows = (await get("/shift-patterns")).filter(row => row.site_id === site.id);
      const pattern = rows.find(row => row.id === adminState.pattern) || rows[0];
      adminState.pattern = pattern?.id || "";
      const path = pattern ? "/shift-patterns/" + id(pattern.id) : "";
      const definitions = pattern ? await get(path + "/definitions") : [];
      const patternFields = row => input("Pattern name","name","text",row?.name,"required") +
        input("IANA timezone","timezone","text",row?.timezone || Intl.DateTimeFormat().resolvedOptions().timeZone,"required");
      const definitionFields = row => input("Code","code","text",row?.code,"required") +
        input("Shift name","name","text",row?.name,"required") + input("Start time","start_time","time",row?.start_time,"required") +
        input("Duration in minutes","duration_minutes","number",row?.duration_minutes || 720,'required min="1" max="1440"');
      designated.innerHTML = `<div class="grid-2">
        ${section("Add shift pattern",setupForm("new-pattern",patternFields(),"Create pattern"))}
        ${section("Shift definitions",pattern ? select("Pattern","pattern",pairs(rows),pattern.id) +
          `<p>${esc(pattern.timezone)} ${btn("Edit pattern","data-edit-pattern")}</p>
          <div class="shift-table"><table class="sheet"><thead><tr><th>Code</th><th>Name</th><th>Start</th><th>Minutes</th><th></th></tr></thead>
          <tbody>${definitions.map(row => `<tr><td>${esc(row.code)}</td><td>${esc(row.name)}</td><td>${esc(row.start_time)}</td>
            <td>${row.duration_minutes}</td><td>${btn("Edit",`data-definition="${esc(row.id)}"`)}</td></tr>`).join("")}</tbody></table></div>` +
          setupForm("new-definition",definitionFields(),"Add shift") : "<p>Create a shift pattern first.</p>")}
        </div>`;
      onForm(designated,"#new-pattern",async form => {
        adminState.pattern = (await request("POST","/shift-patterns",{...formData(form),site_id:site.id})).id;
        await rerender();
      });
      if (!pattern) return;
      designated.querySelector("[name=pattern]").onchange = event => { adminState.pattern = event.target.value; refresh(host,renderAdmin); };
      bind(designated,"[data-edit-pattern]",() => dialogForm("Edit shift pattern",patternFields(pattern),async form => {
        await request("PATCH",path,formData(form)); await rerender();
      }));
      onForm(designated,"#new-definition",async form => {
        await request("POST",path + "/definitions",formData(form)); await rerender();
      });
      bind(designated,"[data-definition]",button => {
        const row = definitions.find(item => item.id === button.dataset.definition);
        dialogForm("Edit shift",definitionFields(row),async form => {
          await request("PATCH",path + "/definitions/" + id(row.id),formData(form)); await rerender();
        });
      });
    } else if (ui.tab === "crews") {
      const [all, users] = await Promise.all([get("/crews"),get("/shift-roster-users",{site:site.id})]);
      const rows = all.filter(row => row.site_id === site.id);
      const crew = rows.find(row => row.id === adminState.crew) || rows[0];
      adminState.crew = crew?.id || "";
      const path = crew ? "/crews/" + id(crew.id) : "";
      const members = crew ? await get(path + "/members") : [];
      const userNames = new Map(users.map(user => [user.id,user.name]));
      const memberFields = row => select("User","user_id",pairs(users),row?.user_id,"required") +
        `<label><input type="checkbox" name="is_lead" ${row?.is_lead ? "checked" : ""}> Shift lead</label>`;
      designated.innerHTML = `<div class="grid-2">
        ${section("Add crew",setupForm("new-crew",input("Crew name","name","text","","required"),"Create crew"))}
        ${section("Crew members",crew ? select("Crew","crew",pairs(rows),crew.id) +
          btn("Rename crew","data-edit-crew") + members.map(row => `<div class="row spread">
          <span>${esc(userNames.get(row.user_id) || row.user_id)}${row.is_lead ? " · Lead" : ""}</span><div class="row">
          ${btn("Edit",`data-member="${esc(row.user_id)}"`)}${btn("Remove",`data-unmember="${esc(row.user_id)}"`)}</div></div>`).join("") +
          (users.length ? setupForm("crew-member",memberFields(),"Save crew member") : "<p>No user accounts are available for this site.</p>")
          : "<p>Create a crew first.</p>")}</div>`;
      onForm(designated,"#new-crew",async form => {
        adminState.crew = (await request("POST","/crews",{...formData(form),site_id:site.id})).id; await rerender();
      });
      if (!crew) return;
      designated.querySelector("[name=crew]").onchange = event => { adminState.crew = event.target.value; refresh(host,renderAdmin); };
      bind(designated,"[data-edit-crew]",() => dialogForm("Rename crew",input("Crew name","name","text",crew.name,"required"),async form => {
        await request("PATCH",path,formData(form)); await rerender();
      }));
      const saveMember = async form => {
        await request("POST",path + "/members",{user_id:form.elements.user_id.value,is_lead:form.elements.is_lead.checked});
        await rerender();
      };
      onForm(designated,"#crew-member",saveMember);
      bind(designated,"[data-member]",button => dialogForm("Crew member",
        memberFields(members.find(row => row.user_id === button.dataset.member)),saveMember));
      bind(designated,"[data-unmember]",async button => {
        await request("DELETE",path + "/members/" + id(button.dataset.unmember)); await rerender();
      });
    } else if (ui.tab === "roster") {
      await renderRoster(host,designated,site);
    } else if (ui.tab === "escalation") {
      const path = "/sites/" + id(site.id) + "/handover-settings";
      const [config, roles] = await Promise.all([get(path),get("/shift-escalation-roles")]);
      designated.innerHTML = section("Handover & escalation",setupForm("handover-settings",
        input("Acknowledge within (minutes)","ack_within_minutes","number",config.ack_within_minutes,'min="1" max="1440" required') +
        input("Out-of-range reading lookback (days)","reading_lookback_days","number",config.reading_lookback_days,'min="1" max="365" required') +
        select("First escalation role","escalate_to_role",roles.map(role => [role,role]),config.escalate_to_role) +
        input("Sign-off reminder before shift end (minutes)","signoff_reminder_minutes","number",config.signoff_reminder_minutes,
          'min="0" max="1440" required'),"Save settings") +
        `<fieldset disabled class="shift-later"><legend>Coming later</legend>
        <label><input type="checkbox"> Second escalation level</label>
        <label><input type="checkbox"> Escalate when sign-off is overdue</label>
        <label><input type="checkbox"> Per-area timer overrides</label></fieldset>`);
      onForm(designated,"#handover-settings",async form => {
        await request("PUT",path,formData(form)); book()._toast("Handover settings saved.");
      });
    } else {
      const rows = await get("/shift-log-categories",{site:site.id});
      const fields = row => input("Category name","name","text",row?.name,"required") +
        input("Sort order","sort_order","number",row?.sort_order || 0) +
        `<label><input type="checkbox" name="active" ${row?.active === false ? "" : "checked"}> Active</label>`;
      designated.innerHTML = `<div class="grid-2">
      ${section("Log categories",rows.map(row => `<div class="row spread"><span>${esc(row.name)}
        ${row.active ? "" : " · Inactive"}${row.site_id ? "" : " · All sites"}</span>
        ${row.site_id || can("users.manage") ? btn("Edit",`data-category="${esc(row.id)}"`) : ""}</div>`).join(""))}
      ${section("Add category",setupForm("new-category",fields(),"Create category"))}</div>`;
      const data = form => ({...formData(form),active:form.elements.active.checked});
      onForm(designated,"#new-category",async form => {
        await request("POST","/shift-log-categories",{...data(form),site_id:site.id}); await rerender();
      });
      bind(designated,"[data-category]",button => {
        const row = rows.find(item => item.id === button.dataset.category);
        dialogForm("Edit category",fields(row),async form => {
          await request("PATCH","/shift-log-categories/" + id(row.id),data(form)); await rerender();
        });
      });
    }
  }

  async function renderRoster(host, target, site) {
    const today = new Date();
    adminState.start ||= dateString(today);
    adminState.end ||= dateString(new Date(today.getTime() + 6 * 86400000));
    const [roster, allCrews, patterns, users] = await Promise.all([
      get("/roster",{from:adminState.start,to:adminState.end}), get("/crews"),
      get("/shift-patterns"),get("/shift-roster-users",{site:site.id})
    ]);
    const crews = allCrews.filter(row => row.site_id === site.id);
    const shifts = (await Promise.all(patterns.filter(row => row.site_id === site.id).map(
      row => get("/shift-patterns/" + id(row.id) + "/definitions")
    ))).flat();
    const byId = (rows,key) => rows.find(row => row.id === key)?.name || "—";
    const areas = site.areas || [];
    const rosterFields = row => input("Date","date","date",row?.date || adminState.start,"required") +
      select("Shift","shift_definition_id",pairs(shifts),row?.shift_definition_id,"required") +
      select("Area","area_id",[["","Whole site"],...pairs(areas)],row?.area_id) +
      select("Crew","crew_id",pairs(crews),row?.crew_id,"required") +
      select("Lead override","lead_user_id",[["","Crew lead"],...pairs(users)],row?.lead_user_id);
    const selected = roster.filter(row => shifts.some(shift => shift.id === row.shift_definition_id));
    target.innerHTML = section("Roster grid",`<form id="roster-range" class="row">
      ${input("From","start","date",adminState.start,"required")}${input("To","end","date",adminState.end,"required")}${submit("Show roster")}</form>
      <div class="table-wrap"><table class="sheet"><thead><tr><th>Date</th><th>Shift</th><th>Area</th><th>Crew</th><th>Lead override</th><th></th></tr></thead>
      <tbody>${selected.map(row => `<tr><td>${esc(row.date)}</td><td>${esc(byId(shifts,row.shift_definition_id))}</td>
      <td>${row.area_id ? esc(byId(areas,row.area_id)) : "Whole site"}</td><td>${esc(byId(crews,row.crew_id))}</td>
      <td>${esc(byId(users,row.lead_user_id))}</td><td>${btn("Edit",`data-roster="${esc(row.id)}"`)}</td></tr>`).join("")
      || '<tr><td colspan="6">No roster entries in this range.</td></tr>'}</tbody></table></div>` +
      (can("users.manage") ? btn("Add area","data-add-area") : "")) +
      `<div class="grid-2">${section("Assign crew",shifts.length && crews.length ?
        setupForm("roster-assign",rosterFields(),"Save roster entry") : "<p>Create a shift definition and a crew first.</p>")}
        ${section("Repeat pattern",setupForm("roster-repeat",input("First source date","first_date","date",adminState.start,"required") +
        input("Last source date","last_date","date",adminState.end,"required") +
        select("Area","area_id",[["","All areas"],...pairs(areas)]) +
        input("Interval in days","interval_days","number",7,'required min="1" max="90"') +
        input("Repetitions","repetitions","number",1,'required min="1" max="52"'),"Repeat roster"))}</div>`;
    onForm(target,"#roster-range",async form => {
      adminState.start = form.start.value; adminState.end = form.end.value; await renderAdmin(host);
    });
    const save = async form => {
      const values = formData(form);
      values.area_id ||= null; values.lead_user_id ||= null;
      await request("PUT","/roster",[values]); await renderAdmin(host);
    };
    onForm(target,"#roster-assign",save);
    bind(target,"[data-roster]",button => dialogForm("Edit roster slot",
      rosterFields(selected.find(row => row.id === button.dataset.roster)),save));
    bind(target,"[data-add-area]",() => dialogForm("Add roster area",input("Area name","name","text","","required"),async form => {
      await request("POST","/sites/" + id(site.id) + "/areas",formData(form)); await renderAdmin(host);
    }));
    onForm(target,"#roster-repeat",async form => {
      const values = formData(form);
      values.area_id ||= null;
      const result = await request("POST","/roster/repeat",{...values,site_id:site.id});
      book()._toast(result.count + " roster entries saved."); await renderAdmin(host);
    });
  }

  async function refreshNotifications() {
    const badge = document.getElementById("nav-handover-count");
    if (!can("shiftlog.read")) { badge.hidden = true; return; }
    try {
      const notices = await get("/handovers/notifications");
      badge.hidden = !notices.items.length;
      badge.textContent = notices.items.length;
      const dashboard = document.getElementById("handover-dashboard");
      if (!dashboard) return;
      dashboard.hidden = !notices.items.length;
      dashboard.innerHTML = section("Handover waiting / overdue",`<p>${notices.waiting} waiting · ${notices.overdue} overdue</p>
        ${notices.items.map(item => `<div class="row spread"><span>${esc(item.shift.area_name)} · ${esc(item.shift.name)} ·
           ${esc(labelStatus(item.kind))}</span>${btn("View",`data-notice="${esc(item.id)}" data-kind="${item.kind}" data-shift="${esc(item.shift.id)}"`)}</div>`).join("")}`);
      bind(dashboard,"[data-notice]",button => {
        if (["reminder","returned"].includes(button.dataset.kind)) { ui.current = button.dataset.shift; book().goto("shiftLog"); }
        else openHandover(button.dataset.notice);
      });
    } catch (_) { badge.hidden = true; }
  }

  function refresh(host, render) {
    render(host).catch(error => { if (host.isConnected) host.textContent = error.message; });
  }

  window.LogentraShifts = {
    views:{
      shiftLog:() => hostHTML("Shift log and handover","Record operations and prepare the outgoing handover.",
        btn("Shift log history",'data-act="goto" data-view="shiftHistory"')),
      shiftHistory:() => hostHTML("Shift log history","Find entries, amendments and voided records.",
        btn("Current shift",'data-act="goto" data-view="shiftLog"')),
      handover:() => hostHTML("Handover","Find current and past shift handovers."),
      handoverDetail:() => hostHTML("Handover review","Review the signed package and record acknowledgement.",
        btn("Handover history",'data-act="goto" data-view="handover"')),
      rosters:() => hostHTML("Shifts & rosters","Set up shift patterns, crews and handover rules.")
    },
    bind(view) {
      const host = document.getElementById("shift-view");
      if (host) {
        const views = {shiftLog:renderLog, shiftHistory:element => renderHistory(element,true),
          handover:element => renderHistory(element,false), handoverDetail:renderHandover, rosters:renderAdmin};
        if (can(view === "rosters" ? "shifts.manage" : "shiftlog.read")) refresh(host,views[view]);
        else host.textContent = "You do not have permission to access this view.";
      }
      refreshNotifications();
    }
  };
  setInterval(() => { if (book()?.getState()) refreshNotifications(); },60000);
})();
