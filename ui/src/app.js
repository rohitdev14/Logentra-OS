/* Logbook v0.3 — Equipment & Records with a local database/API. Early release. */
(function () {
  "use strict";

  const STORE_KEY = "logbook.v2"; // used only as a fallback cache if the API is unreachable
  const LEGACY_KEY = "facilityLogbook.v1";
  const APP_VERSION = "0.3.0";

  const FIELD_TYPES = [
    { id: "text", label: "Text" },
    { id: "number", label: "Number" },
    { id: "date", label: "Date" },
    { id: "select", label: "Dropdown" }
  ];
  const COL_TYPES = FIELD_TYPES.concat([
    { id: "passfail", label: "Pass / Fail / N/A" },
    { id: "unit", label: "Unit" }
  ]);
  const KINDS = [
    { id: "commissioning", label: "Commissioning" },
    { id: "inspection", label: "Inspection" },
    { id: "maintenance", label: "Maintenance" },
    { id: "custom", label: "Custom" }
  ];

  function defaultFields() {
    return [
      { id: "fld_tag", key: "tag", label: "Tag", type: "text", locked: true },
      { id: "fld_category", key: "category", label: "Category", type: "text" }
    ];
  }
  function emptyState() {
    return {
      meta: { version: 3, app: APP_VERSION, updatedAt: null, demo: false, store: "database" },
      equipmentFields: defaultFields(),
      equipment: [],
      templates: [],
      records: [],
      paramPresets: {},
      logs: []
    };
  }

  function esc(s) {
    return String(s ?? "").replace(/[&<>"']/g, (c) => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
    }[c]));
  }
  function uid(prefix) {
    return (prefix || "id") + "_" + Math.random().toString(36).slice(2, 7) + Date.now().toString(36);
  }
  function todayISO() {
    const d = new Date();
    const z = (n) => String(n).padStart(2, "0");
    return d.getFullYear() + "-" + z(d.getMonth() + 1) + "-" + z(d.getDate());
  }
  function nowTime() {
    const d = new Date();
    const z = (n) => String(n).padStart(2, "0");
    return z(d.getHours()) + ":" + z(d.getMinutes());
  }
  function nowDateTime() {
    return todayISO() + "T" + nowTime();
  }
  function clone(o) { return JSON.parse(JSON.stringify(o)); }
  function num(v) {
    if (v === "" || v === null || v === undefined) return null;
    const n = Number(v);
    return Number.isFinite(n) ? n : null;
  }
  function addDays(iso, delta) {
    const [y, m, d] = iso.split("-").map(Number);
    const dt = new Date(y, m - 1, d);
    dt.setDate(dt.getDate() + delta);
    const z = (n) => String(n).padStart(2, "0");
    return dt.getFullYear() + "-" + z(dt.getMonth() + 1) + "-" + z(dt.getDate());
  }
  function seeded(n) {
    const x = Math.sin(n * 12.9898) * 43758.5453;
    return x - Math.floor(x);
  }
  function slug(s, fallback) {
    const t = String(s || "").toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_|_$/g, "");
    return t || fallback || uid("k");
  }
  function annotOf(log, key) { return (log && log.annotations && log.annotations[key]) || null; }
  function annotFilled(a) {
    return !!(a && (String(a.cause || "").trim() || String(a.action || "").trim() || String(a.by || "").trim()));
  }
  function fmtAnnotAt(s) { return s ? String(s).replace("T", " ") : "—"; }

  function fieldById(id) { return state.equipmentFields.find((f) => f.id === id); }
  function fieldByKey(key) { return state.equipmentFields.find((f) => f.key === key); }
  function eqVal(eq, keyOrId) {
    if (!eq || !eq.values) return "";
    if (Object.prototype.hasOwnProperty.call(eq.values, keyOrId)) return eq.values[keyOrId] ?? "";
    const f = fieldByKey(keyOrId);
    return f ? (eq.values[f.id] ?? "") : "";
  }
  function setEqVal(eq, keyOrId, v) {
    if (!eq.values) eq.values = {};
    const f = fieldById(keyOrId) || fieldByKey(keyOrId);
    eq.values[f ? f.id : keyOrId] = v;
  }
  function eqTag(eq) { return String(eqVal(eq, "tag") || "").trim(); }
  function eqCat(eq) { return String(eqVal(eq, "category") || "").trim(); }
  function eqById(id) { return state.equipment.find((e) => e.id === id); }
  function eqByTag(tag) {
    const t = String(tag || "").trim().toLowerCase();
    return state.equipment.find((e) => eqTag(e).toLowerCase() === t);
  }
  function paramsFor(eq) {
    if (!eq) return [];
    if (eq.params && eq.params.length) return eq.params;
    const cat = eqCat(eq);
    return clone(state.paramPresets[cat] || []);
  }
  function categories() {
    const set = new Set();
    state.equipment.forEach((e) => { if (eqCat(e)) set.add(eqCat(e)); });
    Object.keys(state.paramPresets || {}).forEach((k) => set.add(k));
    return Array.from(set).sort();
  }
  function kindLabel(k) { return (KINDS.find((x) => x.id === k) || {}).label || k || "Custom"; }

  let state = emptyState();
  let ui = {
    view: "dashboard",
    equipmentId: "",
    recordId: "",
    templateId: "",
    logEq: "",
    trendParam: "",
    from: "",
    to: "",
    q: "",
    filterCat: "",
    import: null
  };

  function toast(msg) {
    let el = document.getElementById("toast");
    if (!el) {
      el = document.createElement("div");
      el.id = "toast";
      el.className = "toast";
      document.body.appendChild(el);
    }
    el.textContent = msg;
    el.style.display = "block";
    clearTimeout(toast._t);
    toast._t = setTimeout(() => { el.style.display = "none"; }, 2800);
  }

  function setApiBadge(ok, text) {
    const el = document.getElementById("api-badge");
    if (!el) return;
    el.textContent = text || (ok ? "Database connected" : "API offline");
    el.classList.toggle("ok", !!ok);
    el.classList.toggle("err", !ok);
  }

  function persist() {
    state.meta.updatedAt = new Date().toISOString();
    state.meta.version = 3;
    const api = window.LogbookAPI;
    if (!api) {
      try { localStorage.setItem(STORE_KEY, JSON.stringify(state)); } catch (err) { toast("Could not save: " + err.message); }
      return;
    }
    api.sync(state).then(() => setApiBadge(true, "Database connected")).catch((err) => {
      setApiBadge(false, "Save failed");
      toast("Could not save to the database: " + err.message);
    });
  }

  function migrateV1(raw) {
    const next = emptyState();
    next.meta.migratedFrom = 1;
    next.equipmentFields = defaultFields().concat([
      { id: "fld_location", key: "location", label: "Location", type: "text" },
      { id: "fld_notes", key: "notes", label: "Notes", type: "text" }
    ]);
    (raw.equipment || []).forEach((e) => {
      next.equipment.push({
        id: e.id || uid("eq"),
        values: {
          fld_tag: e.tag || "",
          fld_category: e.systemType || e.category || "",
          fld_location: e.location || "",
          fld_notes: e.notes || ""
        },
        params: e.params || [],
        createdAt: e.createdAt || todayISO()
      });
    });
    next.paramPresets = raw.paramDefs || {};
    const colRes = { id: "col_result", key: "result", label: "Result", type: "passfail" };
    const colRem = { id: "col_remarks", key: "remarks", label: "Remarks", type: "text" };
    const seenTpl = {};
    (raw.checklists || []).forEach((c) => {
      const tname = (c.type || "Record") + " (migrated)";
      if (!seenTpl[tname]) {
        const tpl = {
          id: uid("tpl"),
          name: tname,
          kind: "commissioning",
          category: "",
          columns: [clone(colRes), clone(colRem)],
          rows: (c.items || []).map((it) => ({ id: uid("ti"), label: it.text || it.id || "Item", hint: it.method || "" }))
        };
        next.templates.push(tpl);
        seenTpl[tname] = tpl.id;
      }
      next.records.push({
        id: c.id || uid("rec"),
        templateId: seenTpl[tname],
        templateName: tname,
        kind: "commissioning",
        equipmentId: c.equipmentId,
        createdAt: c.createdAt || todayISO(),
        columns: [clone(colRes), clone(colRem)],
        items: (c.items || []).map((it) => ({
          id: it.id || uid("it"),
          label: it.text || it.id || "Item",
          hint: it.method || "",
          values: { col_result: it.result || "", col_remarks: it.remarks || "" }
        })),
        punches: c.punches || [],
        signoffName: c.signoffName || "",
        signoffDate: c.signoffDate || "",
        notes: c.notes || ""
      });
    });
    next.logs = raw.logs || [];
    return next;
  }

  function normalize(data) {
    if (!data || typeof data !== "object") return emptyState();
    if (!data.meta || data.meta.version < 2 || !data.equipmentFields) {
      if (Array.isArray(data.equipment) || Array.isArray(data.checklists)) return migrateV1(data);
      return emptyState();
    }
    data.equipmentFields = data.equipmentFields.length ? data.equipmentFields : defaultFields();
    data.equipment = data.equipment || [];
    data.templates = data.templates || [];
    data.records = data.records || data.checklists || [];
    data.paramPresets = data.paramPresets || {};
    data.logs = data.logs || [];
    data.meta.version = 2;
    return data;
  }

  async function loadStore() {
    const api = window.LogbookAPI;
    if (api) {
      try {
        const data = await api.bootstrap();
        setApiBadge(true, (data.meta && data.meta.backend ? data.meta.backend + " · " : "") + "connected");
        return normalize(data);
      } catch (err) {
        setApiBadge(false, "API offline");
        toast("Could not reach the Logbook API. Start it with python run.py. " + err.message);
        console.warn("API load failed", err);
      }
    }
    return emptyState();
  }

  /* ---------- demo (generic plant, fictional) ---------- */
  async function loadDemo() {
    if (window.LogbookAPI) {
      await window.LogbookAPI.loadDemo();
      state = normalize(await window.LogbookAPI.bootstrap());
      setApiBadge(true, "Database connected");
      return;
    }
    const base = todayISO();
    const start = addDays(base, -20);
    const fields = defaultFields().concat([
      { id: "fld_location", key: "location", label: "Location", type: "text" },
      { id: "fld_status", key: "status", label: "Status", type: "select", options: ["In service", "Spare", "Out of service"] }
    ]);
    const pumpParams = [
      { key: "flow", label: "Flow", unit: "m³/h", min: 40, max: 60 },
      { key: "discharge_p", label: "Discharge pressure", unit: "bar", min: 2.5, max: 4.0 },
      { key: "vibration", label: "Vibration", unit: "mm/s", min: "", max: 4.5 },
      { key: "bearing_t", label: "Bearing temperature", unit: "°C", min: "", max: 80 }
    ];
    const motorParams = [
      { key: "current", label: "Current", unit: "A", min: 10, max: 18 },
      { key: "winding_t", label: "Winding temperature", unit: "°C", min: "", max: 90 },
      { key: "vibration", label: "Vibration", unit: "mm/s", min: "", max: 4.5 }
    ];
    const hvacParams = [
      { key: "supply_t", label: "Supply air temp", unit: "°C", min: 16, max: 22 },
      { key: "filter_dp", label: "Filter DP", unit: "Pa", min: "", max: 250 },
      { key: "rh", label: "Relative humidity", unit: "% RH", min: 35, max: 60 }
    ];
    const eqs = [
      { id: "eq_p101", values: { fld_tag: "P-101", fld_category: "Pump", fld_location: "Pump house — Northbrook Works (fictional)", fld_status: "In service" }, params: clone(pumpParams) },
      { id: "eq_m204", values: { fld_tag: "M-204", fld_category: "Motor", fld_location: "Compressor hall", fld_status: "In service" }, params: clone(motorParams) },
      { id: "eq_ahu12", values: { fld_tag: "AHU-12", fld_category: "HVAC", fld_location: "Roof plant", fld_status: "In service" }, params: clone(hvacParams) },
      { id: "eq_tk03", values: { fld_tag: "TK-03", fld_category: "Tank", fld_location: "Yard", fld_status: "Spare" }, params: [{ key: "level", label: "Level", unit: "%", min: 10, max: 90 }] }
    ];
    const inspectCols = [
      { id: "col_result", key: "result", label: "Result", type: "passfail" },
      { id: "col_reading", key: "reading", label: "Reading", type: "number" },
      { id: "col_unit", key: "unit", label: "Unit", type: "text" },
      { id: "col_remarks", key: "remarks", label: "Remarks", type: "text" }
    ];
    const pumpTpl = {
      id: "tpl_pump",
      name: "Pump inspection",
      kind: "inspection",
      category: "Pump",
      columns: clone(inspectCols),
      rows: [
        { id: "ti1", label: "Visual condition and leaks", hint: "Walk around the unit, base, and seals." },
        { id: "ti2", label: "Unusual noise or vibration", hint: "Compare to the last running condition." },
        { id: "ti3", label: "Isolation valves in the intended position", hint: "Record as-left in remarks." },
        { id: "ti4", label: "Coupling / guard in place and secure", hint: "Do not remove a guard while running." },
        { id: "ti5", label: "Operating readings recorded in the log book", hint: "Flow, pressure, temperature if fitted." }
      ]
    };
    const recDone = {
      id: "rec_demo_1",
      templateId: "tpl_pump",
      templateName: "Pump inspection",
      kind: "inspection",
      equipmentId: "eq_p101",
      createdAt: addDays(base, -3),
      columns: clone(inspectCols),
      items: pumpTpl.rows.map((r, i) => ({
        id: r.id,
        label: r.label,
        hint: r.hint,
        values: {
          col_result: "Pass",
          col_reading: i === 4 ? "52" : "",
          col_unit: i === 4 ? "m³/h" : "",
          col_remarks: i === 0 ? "No visible leak (fictional demo)." : ""
        }
      })),
      punches: [],
      signoffName: "A. Rahman (demo)",
      signoffDate: addDays(base, -3),
      notes: "Fictional completed inspection."
    };
    const recOpen = {
      id: "rec_demo_2",
      templateId: "tpl_pump",
      templateName: "Pump inspection",
      kind: "inspection",
      equipmentId: "eq_ahu12",
      createdAt: addDays(base, -1),
      columns: clone(inspectCols),
      items: [
        { id: "h1", label: "Filter condition and differential pressure", hint: "Record DP if a gauge is fitted.", values: { col_result: "Pass", col_reading: "140", col_unit: "Pa", col_remarks: "" } },
        { id: "h2", label: "Drains clear; no standing water", hint: "Visual check.", values: { col_result: "", col_reading: "", col_unit: "", col_remarks: "" } },
        { id: "h3", label: "Fans run; no abnormal noise", hint: "Listen and observe.", values: { col_result: "", col_reading: "", col_unit: "", col_remarks: "" } }
      ],
      punches: [{ id: "pn_demo", description: "Drain pan access panel stiff — fictional punch.", priority: "C - Deferred / minor", status: "Open", raised: addDays(base, -1), owner: "Day shift (demo)", closed: "" }],
      signoffName: "",
      signoffDate: "",
      notes: "Started from a pump template and edited items — showing that records are just tables."
    };

    const logs = [];
    const series = {
      eq_p101: { flow: 52, discharge_p: 3.2, vibration: 2.1, bearing_t: 62 },
      eq_m204: { current: 14.2, winding_t: 68, vibration: 1.8 },
      eq_ahu12: { supply_t: 18.5, filter_dp: 140, rh: 48 }
    };
    Object.keys(series).forEach((eid, ei) => {
      for (let day = 0; day < 21; day++) {
        const date = addDays(start, day);
        const readings = {};
        Object.keys(series[eid]).forEach((k, ki) => {
          const baseV = series[eid][k];
          let v = baseV + (seeded(day * 10 + ei * 3 + ki) - 0.5) * baseV * 0.06;
          if (eid === "eq_p101" && k === "vibration" && (day === 8 || day === 15)) v = 6.2;
          if (eid === "eq_ahu12" && k === "filter_dp" && day === 18) v = 310;
          readings[k] = Math.round(v * 100) / 100;
        });
        logs.push({
          id: "log_" + eid + "_" + day,
          equipmentId: eid,
          date,
          time: "08:30",
          readings,
          remarks: "",
          enteredBy: "A. Rahman (demo)"
        });
      }
    });
    const ex1 = logs.find((l) => l.id === "log_eq_p101_8");
    if (ex1) {
      ex1.annotations = {
        vibration: {
          cause: "Coupling slightly loose after a weekend restart (fictional demo).",
          action: "Tightened coupling bolts and rechecked vibration on the next shift; reading returned to band.",
          by: "A. Rahman",
          at: addDays(start, 8) + "T09:20"
        }
      };
      ex1.remarks = "See vibration annotation.";
    }

    state = {
      meta: { version: 2, app: APP_VERSION, updatedAt: new Date().toISOString(), demo: true },
      equipmentFields: fields,
      equipment: eqs,
      templates: [pumpTpl],
      records: [recDone, recOpen],
      paramPresets: {
        Pump: pumpParams.map(({ key, label, unit }) => ({ key, label, unit, min: "", max: "" })),
        Motor: motorParams.map(({ key, label, unit }) => ({ key, label, unit, min: "", max: "" })),
        HVAC: hvacParams.map(({ key, label, unit }) => ({ key, label, unit, min: "", max: "" }))
      },
      logs
    };
    persist();
  }

  function applyPack(pack, mode) {
    // mode: 'add' (default)
    const existingNames = new Set(state.templates.map((t) => t.name));
    (pack.templates || []).forEach((t) => {
      if (existingNames.has(t.name)) return;
      const cols = (t.columns || []).map((c) => ({
        id: uid("col"),
        key: c.key || slug(c.label, "col"),
        label: c.label,
        type: c.type || "text",
        options: c.options || []
      }));
      state.templates.push({
        id: uid("tpl"),
        name: t.name,
        kind: t.kind || "custom",
        category: t.category || "",
        columns: cols,
        rows: (t.rows || []).map((r) => ({
          id: uid("ti"),
          label: r.label,
          hint: r.hint || ""
        }))
      });
    });
    Object.keys(pack.paramPresets || {}).forEach((cat) => {
      if (!state.paramPresets[cat] || !state.paramPresets[cat].length) {
        state.paramPresets[cat] = clone(pack.paramPresets[cat]);
      }
    });
    persist();
  }

  /* ---------- files ---------- */
  function download(filename, data, mime) {
    const blob = data instanceof Blob ? data : new Blob([data], { type: mime || "text/plain" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = filename;
    a.click();
    setTimeout(() => URL.revokeObjectURL(a.href), 1000);
  }
  function csvEscape(v) {
    const s = String(v ?? "");
    if (/[",\n]/.test(s)) return '"' + s.replace(/"/g, '""') + '"';
    return s;
  }
  function toCSV(rows) { return rows.map((r) => r.map(csvEscape).join(",")).join("\n"); }
  function parseCSV(text) {
    if (text.charCodeAt(0) === 0xfeff) text = text.slice(1);
    const rows = [];
    let row = [], cur = "", q = false;
    for (let i = 0; i < text.length; i++) {
      const c = text[i];
      if (q) {
        if (c === '"') {
          if (text[i + 1] === '"') { cur += '"'; i++; }
          else q = false;
        } else cur += c;
      } else if (c === '"') q = true;
      else if (c === ",") { row.push(cur); cur = ""; }
      else if (c === "\n") { row.push(cur); rows.push(row); row = []; cur = ""; }
      else if (c !== "\r") cur += c;
    }
    if (cur.length || row.length) { row.push(cur); rows.push(row); }
    return rows.filter((r) => r.some((c) => String(c).trim() !== ""));
  }
  function exportJSON() {
    if (window.LogbookAPI) {
      window.location.href = window.LogbookAPI.exportUrl("json");
      toast("JSON backup download started.");
      return;
    }
    download("logbook-backup.json", JSON.stringify(state, null, 2), "application/json");
    toast("JSON backup downloaded.");
  }
  function importJSONText(text, mode) {
    const data = JSON.parse(text);
    const next = normalize(data);
    if (mode === "replace") state = next;
    else {
      const remap = {};
      (next.equipmentFields || []).forEach((f) => {
        if (f.locked) return;
        if (!state.equipmentFields.some((x) => x.key === f.key || x.label === f.label)) {
          const nf = clone(f); nf.id = uid("fld");
          state.equipmentFields.push(nf);
        }
      });
      (next.equipment || []).forEach((e) => {
        const tag = eqTag(e);
        if (tag && eqByTag(tag)) {
          const copy = clone(e); copy.id = uid("eq");
          setEqVal(copy, "tag", tag + " (imported)");
          state.equipment.push(copy);
        } else {
          if (state.equipment.some((x) => x.id === e.id)) e.id = uid("eq");
          state.equipment.push(e);
        }
      });
      (next.templates || []).forEach((t) => { t.id = uid("tpl"); state.templates.push(t); });
      (next.records || []).forEach((r) => { r.id = uid("rec"); state.records.push(r); });
      (next.logs || []).forEach((l) => { l.id = uid("log"); state.logs.push(l); });
      Object.keys(next.paramPresets || {}).forEach((k) => {
        if (!state.paramPresets[k]) state.paramPresets[k] = next.paramPresets[k];
      });
    }
    persist();
  }

  function equipmentHeader() {
    return state.equipmentFields.map((f) => f.label);
  }
  function equipmentRows() {
    return state.equipment.map((e) => state.equipmentFields.map((f) => eqVal(e, f.key)));
  }
  function exportEquipmentTable(fmt) {
    const rows = [equipmentHeader()].concat(equipmentRows());
    if (fmt === "xlsx") download("logbook-equipment.xlsx", XlsxUtil.build("Equipment", rows));
    else download("logbook-equipment.csv", toCSV(rows), "text/csv");
    toast("Equipment table downloaded.");
  }
  function exportLogsTable(fmt) {
    const rows = [["tag", "category", "date", "time", "parameter", "label", "value", "unit", "remarks", "enteredBy", "annot_cause", "annot_action", "annot_by", "annot_at"]];
    state.logs.forEach((l) => {
      const eq = eqById(l.equipmentId);
      if (!eq) return;
      const params = paramsFor(eq);
      Object.keys(l.readings || {}).forEach((k) => {
        const p = params.find((x) => x.key === k) || { key: k, label: k, unit: "" };
        const an = annotOf(l, k) || {};
        rows.push([eqTag(eq), eqCat(eq), l.date, l.time, k, p.label, l.readings[k], p.unit, l.remarks || "", l.enteredBy || "", an.cause || "", an.action || "", an.by || "", an.at || ""]);
      });
    });
    if (fmt === "xlsx") download("logbook-logs.xlsx", XlsxUtil.build("Logs", rows));
    else download("logbook-logs.csv", toCSV(rows), "text/csv");
    toast("Logs table downloaded.");
  }
  function blankTemplate(kind) {
    if (kind === "equipment") {
      const rows = [equipmentHeader(), state.equipmentFields.map(() => "")];
      return { csv: "logbook-blank-equipment.csv", xlsx: "logbook-blank-equipment.xlsx", sheet: "Equipment", rows };
    }
    if (kind === "template") {
      const rows = [["item_label", "item_hint"], ["Visual condition and leaks", "Walk around the unit."], ["", ""]];
      return { csv: "logbook-blank-template-items.csv", xlsx: "logbook-blank-template-items.xlsx", sheet: "Items", rows };
    }
    const rows = [["tag", "date", "time", "parameter", "value", "remarks", "enteredBy"], ["P-101", todayISO(), "08:30", "flow", "50", "", ""]];
    return { csv: "logbook-blank-logs.csv", xlsx: "logbook-blank-logs.xlsx", sheet: "Logs", rows };
  }
  function downloadBlank(kind, fmt) {
    const b = blankTemplate(kind);
    if (fmt === "xlsx") download(b.xlsx, XlsxUtil.build(b.sheet, b.rows));
    else download(b.csv, toCSV(b.rows), "text/csv");
  }

  async function tableFromFile(file) {
    const name = file.name || "file";
    if (/\.xlsx$/i.test(name) || /spreadsheetml/.test(file.type)) {
      const buf = await file.arrayBuffer();
      const parsed = await XlsxUtil.parse(buf);
      if (!parsed.sheets.length) throw new Error("No worksheets found in that Excel file.");
      return { fileName: name, sheets: parsed.sheets };
    }
    const text = await file.text();
    const rows = parseCSV(text);
    return { fileName: name, sheets: [{ name: name, rows }] };
  }

  function autoMap(headers, targets) {
    const map = {};
    headers.forEach((h, i) => {
      const n = String(h || "").trim().toLowerCase().replace(/[\s_]+/g, " ");
      const hit = targets.find((t) => {
        const aliases = (t.aliases || []).concat([t.id, t.label]).map((x) => String(x).toLowerCase().replace(/[\s_]+/g, " "));
        return aliases.includes(n);
      });
      map[i] = hit ? hit.id : "";
    });
    return map;
  }

  function importTargets(kind, logMode) {
    if (kind === "equipment") {
      return state.equipmentFields.map((f) => ({ id: "f:" + f.id, label: f.label, aliases: [f.key, f.label, f.id] }));
    }
    if (kind === "template") {
      return [
        { id: "item_label", label: "Item label", aliases: ["item", "item_label", "label", "check", "description", "text"] },
        { id: "item_hint", label: "Hint / method (optional)", aliases: ["hint", "method", "notes", "item_hint"] }
      ];
    }
    const base = [
      { id: "tag", label: "Equipment tag", aliases: ["tag", "equipment", "asset"] },
      { id: "date", label: "Date", aliases: ["date", "day"] },
      { id: "time", label: "Time", aliases: ["time"] },
      { id: "remarks", label: "Remarks", aliases: ["remarks", "comment", "notes"] },
      { id: "enteredBy", label: "Entered by", aliases: ["enteredby", "entered by", "by", "user"] }
    ];
    if (logMode === "wide") {
      const extras = [];
      const seen = new Set();
      state.equipment.forEach((e) => paramsFor(e).forEach((p) => {
        if (!seen.has(p.key)) {
          seen.add(p.key);
          extras.push({ id: "p:" + p.key, label: "Reading: " + p.label, aliases: [p.key, p.label] });
        }
      }));
      extras.push({ id: "pnew:", label: "Reading: use this column header as a new parameter", aliases: [] });
      return base.concat(extras);
    }
    return base.concat([
      { id: "parameter", label: "Parameter key or name", aliases: ["parameter", "param", "point", "key"] },
      { id: "value", label: "Value", aliases: ["value", "reading"] },
      { id: "unit", label: "Unit", aliases: ["unit"] },
      { id: "annot_cause", label: "Annotation — cause", aliases: ["annot_cause", "cause"] },
      { id: "annot_action", label: "Annotation — action taken", aliases: ["annot_action", "action"] },
      { id: "annot_by", label: "Annotation — initials / name", aliases: ["annot_by"] },
      { id: "annot_at", label: "Annotation — timestamp", aliases: ["annot_at"] }
    ]);
  }

  function applyEquipmentImport(rows, mapping, createMissing) {
    if (!rows.length) return { added: 0, updated: 0, skipped: 0 };
    const header = rows[0];
    const body = rows.slice(1);
    // optionally create fields
    if (createMissing) {
      header.forEach((h, i) => {
        if (mapping[i]) return;
        const label = String(h || "").trim();
        if (!label) return;
        const f = { id: uid("fld"), key: slug(label, "field"), label, type: "text" };
        state.equipmentFields.push(f);
        mapping[i] = "f:" + f.id;
      });
    }
    let added = 0, updated = 0, skipped = 0;
    body.forEach((row) => {
      const vals = {};
      Object.keys(mapping).forEach((i) => {
        const t = mapping[i];
        if (!t || !t.startsWith("f:")) return;
        vals[t.slice(2)] = row[i] == null ? "" : String(row[i]).trim();
      });
      const tagF = fieldByKey("tag");
      const tag = tagF ? String(vals[tagF.id] || "").trim() : "";
      if (!tag) { skipped += 1; return; }
      let eq = eqByTag(tag);
      if (eq) {
        Object.keys(vals).forEach((fid) => { if (fid !== (tagF && tagF.id) || !eqVal(eq, "tag")) eq.values[fid] = vals[fid]; });
        Object.keys(vals).forEach((fid) => { eq.values[fid] = vals[fid]; });
        updated += 1;
      } else {
        state.equipment.push({ id: uid("eq"), values: vals, params: [], createdAt: todayISO() });
        added += 1;
      }
    });
    persist();
    return { added, updated, skipped };
  }

  function applyTemplateImport(rows, mapping, templateId, newName) {
    const header = rows[0] || [];
    const body = rows.slice(1);
    let tpl;
    if (templateId && templateId !== "__new__") tpl = state.templates.find((t) => t.id === templateId);
    if (!tpl) {
      tpl = {
        id: uid("tpl"),
        name: newName || "Imported template",
        kind: "custom",
        category: "",
        columns: [
          { id: uid("col"), key: "result", label: "Result", type: "passfail" },
          { id: uid("col"), key: "remarks", label: "Remarks", type: "text" }
        ],
        rows: []
      };
      state.templates.push(tpl);
    }
    let added = 0;
    body.forEach((row) => {
      const rec = {};
      Object.keys(mapping).forEach((i) => { if (mapping[i]) rec[mapping[i]] = row[i] == null ? "" : String(row[i]).trim(); });
      if (!rec.item_label) return;
      tpl.rows.push({ id: uid("ti"), label: rec.item_label, hint: rec.item_hint || "" });
      added += 1;
    });
    persist();
    return { added, templateName: tpl.name };
  }

  function applyLogsImport(rows, mapping, logMode) {
    const body = rows.slice(1);
    let added = 0, skipped = 0;
    if (!ui.import) ui.import = {};
    ui.import._importedLogs = [];
    if (logMode === "wide") {
      body.forEach((row) => {
        const get = (id) => {
          const idx = Object.keys(mapping).find((i) => mapping[i] === id);
          return idx != null ? String(row[idx] ?? "").trim() : "";
        };
        const tag = get("tag");
        const eq = eqByTag(tag);
        if (!eq) { skipped += 1; return; }
        const readings = {};
        Object.keys(mapping).forEach((i) => {
          const t = mapping[i];
          if (!t) return;
          const val = num(row[i]);
          if (val === null) return;
          if (t.startsWith("p:") && t !== "pnew:") readings[t.slice(2)] = val;
          if (t === "pnew:" || t.startsWith("pnew:")) {
            const label = String(rows[0][i] || "param").trim();
            const key = slug(label, "p");
            readings[key] = val;
            if (!(eq.params || []).some((p) => p.key === key)) {
              if (!eq.params) eq.params = paramsFor(eq);
              eq.params.push({ key, label, unit: "", min: "", max: "" });
            }
          }
        });
        if (!Object.keys(readings).length) { skipped += 1; return; }
        const entryW = {
          id: uid("log"),
          equipmentId: eq.id,
          date: get("date") || todayISO(),
          time: get("time") || "08:00",
          readings,
          remarks: get("remarks"),
          enteredBy: get("enteredBy"),
          annotations: {}
        };
        state.logs.push(entryW);
        (ui.import._importedLogs = ui.import._importedLogs || []).push(entryW);
        added += 1;
      });
    } else {
      const grouped = {};
      body.forEach((row) => {
        const get = (id) => {
          const idx = Object.keys(mapping).find((i) => mapping[i] === id);
          return idx != null ? String(row[idx] ?? "").trim() : "";
        };
        const tag = get("tag");
        const eq = eqByTag(tag);
        if (!eq) { skipped += 1; return; }
        const date = get("date") || todayISO();
        const time = get("time") || "08:00";
        const keyRaw = get("parameter");
        if (!keyRaw) { skipped += 1; return; }
        const paramMatch = paramsFor(eq).find((p) => p.key === keyRaw || p.label.toLowerCase() === keyRaw.toLowerCase());
        const key = paramMatch ? paramMatch.key : slug(keyRaw, "p");
        if (!paramMatch) {
          if (!eq.params) eq.params = paramsFor(eq);
          if (!eq.params.some((p) => p.key === key)) eq.params.push({ key, label: keyRaw, unit: get("unit"), min: "", max: "" });
        }
        const value = num(get("value"));
        const gk = eq.id + "|" + date + "|" + time;
        if (!grouped[gk]) grouped[gk] = { id: uid("log"), equipmentId: eq.id, date, time, readings: {}, remarks: get("remarks"), enteredBy: get("enteredBy"), annotations: {} };
        if (value !== null) grouped[gk].readings[key] = value;
        if (get("remarks")) grouped[gk].remarks = get("remarks");
        const cause = get("annot_cause"), action = get("annot_action"), by = get("annot_by"), at = get("annot_at");
        if (cause || action || by || at) grouped[gk].annotations[key] = { cause, action, by, at };
      });
      const list = Object.values(grouped);
      state.logs.push(...list);
      (ui.import._importedLogs = ui.import._importedLogs || []).push(...list);
      added = list.length;
    }
    persist();
    return { added, skipped };
  }

  /* ---------- chart ---------- */
  function outOfRange(val, p) {
    if (val === null || !p) return false;
    if (p.min !== "" && p.min != null && val < Number(p.min)) return true;
    if (p.max !== "" && p.max != null && val > Number(p.max)) return true;
    return false;
  }
  function trendPoints(eq, param, from, to) {
    if (!eq || !param) return [];
    const rows = state.logs
      .filter((l) => l.equipmentId === eq.id && l.date >= (from || "0000") && l.date <= (to || "9999"))
      .sort((a, b) => (a.date + (a.time || "")).localeCompare(b.date + (b.time || "")))
      .map((l) => {
        const value = l.readings ? num(l.readings[param.key]) : null;
        const annotation = annotOf(l, param.key);
        const filled = annotFilled(annotation);
        const bad = outOfRange(value, param);
        return { date: l.date, time: l.time || "", value, logId: l.id, annotation: filled ? annotation : null, unack: !!(bad && !filled), oor: bad };
      });
    let n = 0;
    rows.forEach((p) => { if (p.annotation) { n += 1; p.flag = n; } });
    return rows;
  }
  function annotTooltip(p) {
    let t = (p.date || "") + (p.time ? " " + p.time : "") + ": " + p.value;
    if (p.oor) t += " (out of range)";
    if (p.annotation) {
      t += " — note #" + p.flag;
      if (p.annotation.cause) t += " | Cause: " + p.annotation.cause;
      if (p.annotation.action) t += " | Action: " + p.annotation.action;
      if (p.annotation.by || p.annotation.at) t += " | " + (p.annotation.by || "") + (p.annotation.at ? " at " + fmtAnnotAt(p.annotation.at) : "");
    } else if (p.unack) t += " — unacknowledged; click to add a note";
    else t += " — click to annotate";
    return t;
  }
  function drawTrend(el, points, param) {
    el.innerHTML = "";
    const data = points.filter((p) => p.value !== null);
    if (!data.length) { el.innerHTML = '<p class="empty">No numeric readings in this range.</p>'; return; }
    const W = 860, H = 336, L = 54, R = 18, T = 32, B = 44;
    const innerW = W - L - R, innerH = H - T - B;
    const values = data.map((p) => p.value);
    let vmin = Math.min(...values), vmax = Math.max(...values);
    const hasMin = param && param.min !== "" && param.min != null;
    const hasMax = param && param.max !== "" && param.max != null;
    if (hasMin) vmin = Math.min(vmin, Number(param.min));
    if (hasMax) vmax = Math.max(vmax, Number(param.max));
    if (vmin === vmax) { vmin -= 1; vmax += 1; }
    const pad = (vmax - vmin) * 0.12;
    vmin -= pad; vmax += pad;
    const x = (i) => L + (data.length === 1 ? innerW / 2 : (i / (data.length - 1)) * innerW);
    const y = (v) => T + (1 - (v - vmin) / (vmax - vmin)) * innerH;
    let grid = "";
    for (let t = 0; t <= 5; t++) {
      const v = vmin + (t / 5) * (vmax - vmin);
      const yy = y(v).toFixed(1);
      grid += '<line x1="' + L + '" y1="' + yy + '" x2="' + (W - R) + '" y2="' + yy + '" stroke="#e4e9ef" />';
      grid += '<text x="' + (L - 8) + '" y="' + (Number(yy) + 3) + '" text-anchor="end" font-size="10" fill="#5c6778">' +
        (Math.abs(v) >= 100 ? v.toFixed(0) : v.toFixed(2)) + "</text>";
    }
    let limitLines = "";
    if (hasMax) {
      const yy = y(Number(param.max)).toFixed(1);
      limitLines += '<line x1="' + L + '" y1="' + yy + '" x2="' + (W - R) + '" y2="' + yy + '" stroke="#b42318" stroke-dasharray="5 4" />';
      limitLines += '<text x="' + (W - R) + '" y="' + (Number(yy) - 4) + '" text-anchor="end" font-size="10" fill="#b42318">max ' + param.max + "</text>";
    }
    if (hasMin) {
      const yy = y(Number(param.min)).toFixed(1);
      limitLines += '<line x1="' + L + '" y1="' + yy + '" x2="' + (W - R) + '" y2="' + yy + '" stroke="#b42318" stroke-dasharray="5 4" />';
      limitLines += '<text x="' + (W - R) + '" y="' + (Number(yy) + 12) + '" text-anchor="end" font-size="10" fill="#b42318">min ' + param.min + "</text>";
    }
    const path = data.map((p, i) => (i ? "L" : "M") + x(i).toFixed(1) + " " + y(p.value).toFixed(1)).join(" ");
    const dots = data.map((p, i) => {
      const cx = x(i).toFixed(1), cy = y(p.value).toFixed(1), bad = !!p.oor;
      let mark = "";
      if (p.flag) {
        mark = '<line x1="' + cx + '" y1="' + (y(p.value) - 5).toFixed(1) + '" x2="' + cx + '" y2="' + (y(p.value) - 20).toFixed(1) + '" stroke="#1b365d" stroke-width="1.4"/>' +
          '<rect x="' + (x(i) + 0.5).toFixed(1) + '" y="' + (y(p.value) - 29).toFixed(1) + '" width="15" height="13" rx="2" fill="#1b365d"/>' +
          '<text x="' + (x(i) + 8).toFixed(1) + '" y="' + (y(p.value) - 19.5).toFixed(1) + '" text-anchor="middle" font-size="9" font-weight="700" fill="#fff">' + p.flag + "</text>";
      } else if (p.unack) {
        mark = '<circle cx="' + cx + '" cy="' + (y(p.value) - 13).toFixed(1) + '" r="7" fill="#9a6700" stroke="#fff" stroke-width="1"/>' +
          '<text x="' + cx + '" y="' + (y(p.value) - 9.2).toFixed(1) + '" text-anchor="middle" font-size="10" font-weight="700" fill="#fff">!</text>';
      }
      return '<g class="pt" tabindex="0" role="button" data-log-id="' + esc(p.logId) + '" data-param="' + esc(param.key) + '">' +
        '<circle class="hit" cx="' + cx + '" cy="' + cy + '" r="14" fill="transparent"/>' +
        '<circle class="dot" cx="' + cx + '" cy="' + cy + '" r="' + (bad ? 5.2 : 3.2) + '" fill="' + (bad ? "#b42318" : "#0f6c8c") + '"/>' +
        mark + "<title>" + esc(annotTooltip(p)) + "</title></g>";
    }).join("");
    const labels = data.map((p, i) => {
      const show = data.length <= 12 || i === 0 || i === data.length - 1 || i % Math.ceil(data.length / 8) === 0;
      if (!show) return "";
      return '<text x="' + x(i).toFixed(1) + '" y="' + (H - 14) + '" text-anchor="middle" font-size="10" fill="#5c6778">' + esc((p.date || "").slice(5)) + "</text>";
    }).join("");
    el.innerHTML = '<svg viewBox="0 0 ' + W + " " + H + '" role="img" aria-label="Trend chart. Click a point to annotate.">' +
      '<rect x="0" y="0" width="' + W + '" height="' + H + '" fill="#fff"/>' + grid + limitLines +
      '<path d="' + path + '" fill="none" stroke="#0f6c8c" stroke-width="2"/>' + dots + labels +
      '<text x="' + (W / 2) + '" y="' + (H - 2) + '" text-anchor="middle" font-size="10" fill="#5c6778">Date</text></svg>';
  }
  function stats(values) {
    const v = values.filter((x) => x !== null);
    if (!v.length) return null;
    const sum = v.reduce((a, b) => a + b, 0);
    return { count: v.length, min: Math.min(...v), max: Math.max(...v), avg: sum / v.length };
  }

  function statusPill(st) {
    const map = {
      signed: ["pill-pass", "Signed"], ready: ["pill-pass", "Ready for sign-off"],
      wip: ["pill-wip", "In progress"], not: ["pill-not", "Not started"],
      Open: ["pill-open", "Open"], "In progress": ["pill-wip", "In progress"],
      Closed: ["pill-closed", "Closed"], Deferred: ["pill-na", "Deferred"],
      Pass: ["pill-pass", "Pass"], Fail: ["pill-fail", "Fail"], NA: ["pill-na", "N/A"]
    };
    const m = map[st] || ["pill-na", st || "—"];
    return '<span class="pill ' + m[0] + '">' + esc(m[1]) + "</span>";
  }
  function recordProgress(rec) {
    const items = rec.items || [];
    const resCol = (rec.columns || []).find((c) => c.type === "passfail");
    const done = resCol ? items.filter((it) => it.values && it.values[resCol.id]).length : items.filter((it) => Object.values(it.values || {}).some(Boolean)).length;
    return { done, total: items.length, pct: items.length ? Math.round((done / items.length) * 100) : 0 };
  }
  function recordStatus(rec) {
    if (rec.signoffName && rec.signoffDate) return "signed";
    const p = recordProgress(rec);
    if (p.done === 0) return "not";
    if (p.done < p.total) return "wip";
    const resCol = (rec.columns || []).find((c) => c.type === "passfail");
    const failed = resCol && (rec.items || []).some((it) => it.values && it.values[resCol.id] === "Fail");
    const openP = (rec.punches || []).some((x) => x.status === "Open" || x.status === "In progress");
    if (failed || openP) return "wip";
    return "ready";
  }

  /* ---------- views ---------- */
  function renderSoon() {
    if (renderSoon._t) return;
    renderSoon._t = setTimeout(() => { renderSoon._t = 0; render(); }, 0);
  }
  function render() {
    const root = document.getElementById("view");
    const views = {
      dashboard: viewDashboard, equipment: viewEquipment, templates: viewTemplates,
      records: viewRecords, recordEdit: viewRecordEdit, logbook: viewLogbook,
      trends: viewTrends, import: viewImport, backup: viewBackup
    };
    root.innerHTML = (views[ui.view] || viewDashboard)();
    document.querySelectorAll("nav.tabs button").forEach((b) => {
      const group = ui.view === "recordEdit" ? "records" : ui.view;
      b.classList.toggle("active", b.dataset.view === group);
    });
    bindView();
  }
  function openPunches() {
    const list = [];
    state.records.forEach((c) => (c.punches || []).forEach((p) => {
      if (p.status === "Open" || p.status === "In progress") list.push({ punch: p, rec: c });
    }));
    return list;
  }

  function viewDashboard() {
    const punches = openPunches();
    const openR = state.records.filter((c) => recordStatus(c) !== "signed").length;
    const weekStart = addDays(todayISO(), -6);
    const weekLogs = state.logs.filter((l) => l.date >= weekStart).length;
    let unack = 0;
    state.logs.forEach((l) => {
      const eq = eqById(l.equipmentId);
      if (!eq) return;
      paramsFor(eq).forEach((p) => {
        const v = l.readings ? num(l.readings[p.key]) : null;
        if (outOfRange(v, p) && !annotFilled(annotOf(l, p.key))) unack += 1;
      });
    });
    return `
      <div class="row spread">
        <div>
          <h2>Dashboard</h2>
          <p class="page-lead">A private logbook for any industry, now backed by a local database and a REST API. You define the equipment columns, record templates, and log parameters — or import a table, subscribe to MQTT, or POST readings from a gateway.</p>
        </div>
        <div class="row no-print">
          <button class="btn" data-act="demo">${state.equipment.length ? "Reload fictional demo" : "Load fictional demo"}</button>
          <button class="btn btn-primary" data-act="goto" data-view="equipment">Add equipment</button>
        </div>
      </div>
      <div class="grid-cards">
        <div class="card stat"><div class="label">Equipment</div><div class="value">${state.equipment.length}</div><div class="hint">${state.equipmentFields.length} columns</div></div>
        <div class="card stat"><div class="label">Templates</div><div class="value">${state.templates.length}</div><div class="hint">record layouts you can reuse</div></div>
        <div class="card stat"><div class="label">Open records</div><div class="value">${openR}</div><div class="hint">${state.records.length} total</div></div>
        <div class="card stat"><div class="label">Log entries (7d)</div><div class="value">${weekLogs}</div><div class="hint">${state.logs.length} all time${unack ? " · " + unack + " unacknowledged out-of-range" : ""}</div></div>
      </div>
      <div class="grid-2" style="margin-top:1rem">
        <div class="card">
          <h3>Recent equipment</h3>
          ${state.equipment.length ? '<div class="table-wrap"><table class="data"><thead><tr><th>Tag</th><th>Category</th><th>Location</th></tr></thead><tbody>' +
            state.equipment.slice(0, 6).map((e) => "<tr><td class='tag'>" + esc(eqTag(e)) + "</td><td>" + esc(eqCat(e) || "—") + "</td><td>" + esc(eqVal(e, "location") || "—") + "</td></tr>").join("") +
            "</tbody></table></div>" : '<p class="empty">No equipment yet. Add a row or load the demo.</p>'}
        </div>
        <div class="card">
          <h3>Records</h3>
          ${state.records.length ? '<div class="table-wrap"><table class="data"><thead><tr><th>Template</th><th>Tag</th><th>Status</th></tr></thead><tbody>' +
            state.records.slice().sort((a, b) => (b.createdAt || "").localeCompare(a.createdAt || "")).slice(0, 6).map((r) => {
              const eq = eqById(r.equipmentId);
              return "<tr><td>" + esc(r.templateName || "") + "</td><td class='tag'>" + esc(eq ? eqTag(eq) : "?") + "</td><td>" + statusPill(recordStatus(r)) + "</td></tr>";
            }).join("") + "</tbody></table></div>" : '<p class="empty">No records yet. Build a template, then start a record.</p>'}
          ${punches.length ? "<p class='tiny' style='margin-top:.7rem'><b>Open punches</b></p><ul class='tiny'>" +
            punches.slice(0, 4).map(({ punch, rec }) => {
              const eq = eqById(rec.equipmentId);
              return "<li>" + esc(eq ? eqTag(eq) : "") + " — " + esc(punch.description) + "</li>";
            }).join("") + "</ul>" : ""}
        </div>
      </div>
      <p class="help" style="margin-top:1rem">This is <b>Logbook v0.3 – early release</b>. Data lives in a local SQLite (or PostgreSQL) database and is available over the API at <a href="/docs">/docs</a>. Optional starter packs are on the Backup tab. Do not store passwords or confidential site data here.</p>
    `;
  }

  function viewEquipment() {
    const q = (ui.q || "").toLowerCase();
    const rows = state.equipment.filter((e) => {
      if (ui.filterCat && eqCat(e) !== ui.filterCat) return false;
      if (!q) return true;
      return state.equipmentFields.some((f) => String(eqVal(e, f.key)).toLowerCase().includes(q));
    });
    const cats = categories();
    return `
      <div class="row spread">
        <div>
          <h2>Equipment</h2>
          <p class="page-lead">A simple table you can extend. Add columns for whatever your industry records — serial number, area, customer, anything — and type directly in the grid.</p>
        </div>
      </div>
      <div class="card">
        <div class="grid-toolbar">
          <button class="btn btn-primary" data-act="eq-add">Add row</button>
          <button class="btn" data-act="col-manage">Columns…</button>
          <button class="btn" data-act="goto" data-view="import">Import table</button>
          <button class="btn" data-act="export-eq-csv">Export CSV</button>
          <button class="btn" data-act="export-eq-xlsx">Export Excel</button>
          <label class="fld" style="min-width:10rem;margin-left:auto"><span>Search</span>
            <input type="search" id="eq-q" value="${esc(ui.q)}" placeholder="Find…">
          </label>
          <label class="fld"><span>Category</span>
            <select id="eq-cat"><option value="">All</option>${cats.map((c) =>
              '<option' + (ui.filterCat === c ? " selected" : "") + ">" + esc(c) + "</option>").join("")}</select>
          </label>
        </div>
        <div class="table-wrap" style="max-height:62vh">
          <table class="sheet" id="eq-grid">
            <thead><tr><th class="rowhead">#</th>${state.equipmentFields.map((f) =>
              "<th>" + esc(f.label) + "</th>").join("")}<th class="no-print">Actions</th></tr></thead>
            <tbody>
              ${rows.length ? rows.map((e, i) => {
                const cells = state.equipmentFields.map((f) => {
                  const val = esc(eqVal(e, f.key));
                  const catsList = f.key === "category" ? ' list="cat-list"' : "";
                  if (f.type === "select") {
                    const opts = (f.options || []).map((o) => '<option' + (String(eqVal(e, f.key)) === o ? " selected" : "") + ">" + esc(o) + "</option>").join("");
                    return '<td><select data-eq="' + e.id + '" data-fld="' + f.id + '"><option value=""></option>' + opts + "</select></td>";
                  }
                  const typ = f.type === "number" ? "number" : f.type === "date" ? "date" : "text";
                  return '<td><input type="' + typ + '" value="' + val + '" data-eq="' + e.id + '" data-fld="' + f.id + '"' + catsList + (f.type === "number" ? ' step="any"' : "") + "></td>";
                }).join("");
                return "<tr><td class='rowhead'>" + (i + 1) + "</td>" + cells +
                  "<td class='no-print'><button class='btn btn-sm' data-act='params-edit' data-id='" + e.id + "'>Logs params</button> " +
                  "<button class='btn btn-sm btn-danger' data-act='eq-del' data-id='" + e.id + "'>Delete</button></td></tr>";
              }).join("") : '<tr><td class="rowhead"></td><td colspan="' + (state.equipmentFields.length + 1) + '"><p class="empty">No rows. Add one, import a table, or load the fictional demo.</p></td></tr>'}
            </tbody>
          </table>
        </div>
        <datalist id="cat-list">${cats.map((c) => "<option value=\"" + esc(c) + "\">").join("")}</datalist>
        <p class="tiny" style="margin-top:.6rem">Changes save automatically as you leave a cell. Category is a free label — use it to group log-parameter presets (Pump, Motor, or your own names).</p>
      </div>
    `;
  }

  function viewTemplates() {
    const tpl = state.templates.find((t) => t.id === ui.templateId) || state.templates[0];
    if (tpl && !ui.templateId) ui.templateId = tpl.id;
    const list = state.templates.map((t) =>
      '<button type="button" class="tpl-item' + (tpl && t.id === tpl.id ? " active" : "") + '" data-act="tpl-select" data-id="' + t.id + '">' +
      '<span class="nm">' + esc(t.name) + '</span><span class="kind-pill">' + esc(kindLabel(t.kind)) +
      (t.category ? " · " + esc(t.category) : "") + " · " + (t.rows || []).length + " items</span></button>"
    ).join("");
    return `
      <div class="row spread">
        <div>
          <h2>Record templates</h2>
          <p class="page-lead">Design a commissioning, inspection, maintenance, or any other record. You control the rows (items) and the columns (result fields). Then start a record on a piece of equipment.</p>
        </div>
        <div class="row">
          <button class="btn btn-primary" data-act="tpl-new">New template</button>
        </div>
      </div>
      <div class="builder">
        <div class="card tpl-list">
          <h3>Templates</h3>
          ${list || '<p class="empty">None yet. Create one or load a starter pack from Backup.</p>'}
        </div>
        <div class="card" id="tpl-editor">
          ${tpl ? renderTplEditor(tpl) : '<p class="empty">Select or create a template.</p>'}
        </div>
      </div>
    `;
  }
  function renderTplEditor(tpl) {
    const colChips = (tpl.columns || []).map((c, i) =>
      '<span class="col-chip"><b>' + esc(c.label) + '</b> <span class="tiny">' + esc(c.type) + "</span> " +
      '<button type="button" title="Edit" data-act="tpl-col-edit" data-i="' + i + '">✎</button>' +
      '<button type="button" title="Move left" data-act="tpl-col-up" data-i="' + i + '"' + (i === 0 ? " disabled" : "") + ">←</button>" +
      '<button type="button" title="Move right" data-act="tpl-col-dn" data-i="' + i + '"' + (i === tpl.columns.length - 1 ? " disabled" : "") + ">→</button>" +
      '<button type="button" title="Remove" data-act="tpl-col-del" data-i="' + i + '">×</button></span>'
    ).join(" ");
    const body = (tpl.rows || []).map((r, i) =>
      "<tr><td class='rowhead'>" + (i + 1) + "</td>" +
      '<td><input data-tpl-row="' + i + '" data-k="label" value="' + esc(r.label) + '"></td>' +
      '<td><input data-tpl-row="' + i + '" data-k="hint" value="' + esc(r.hint || "") + '"></td>' +
      '<td class="no-print"><button class="btn btn-sm" data-act="tpl-row-up" data-i="' + i + '"' + (i === 0 ? " disabled" : "") + ">↑</button> " +
      '<button class="btn btn-sm" data-act="tpl-row-dn" data-i="' + i + '"' + (i === tpl.rows.length - 1 ? " disabled" : "") + ">↓</button> " +
      '<button class="btn btn-sm btn-danger" data-act="tpl-row-del" data-i="' + i + '">×</button></td></tr>'
    ).join("");
    return `
      <div class="form-grid">
        <label class="fld span-2"><span>Name</span><input id="tpl-name" value="${esc(tpl.name)}"></label>
        <label class="fld"><span>Kind</span>
          <select id="tpl-kind">${KINDS.map((k) => '<option value="' + k.id + '"' + (tpl.kind === k.id ? " selected" : "") + ">" + esc(k.label) + "</option>").join("")}</select>
        </label>
        <label class="fld"><span>Typical category (optional)</span>
          <input id="tpl-cat" list="cat-list" value="${esc(tpl.category || "")}" placeholder="e.g. Pump">
        </label>
      </div>
      <h3 style="margin-top:1rem">Columns</h3>
      <div class="chip-row">${colChips} <button class="btn btn-sm" data-act="tpl-col-add">Add column</button></div>
      <h3 style="margin-top:1rem">Items (rows)</h3>
      <div class="table-wrap"><table class="sheet"><thead><tr><th>#</th><th>Item</th><th>Hint / method</th><th class="no-print"></th></tr></thead>
      <tbody>${body || '<tr><td colspan="4"><p class="empty">No items yet.</p></td></tr>'}</tbody></table></div>
      <div class="row" style="margin-top:.8rem">
        <button class="btn" data-act="tpl-row-add">Add item</button>
        <button class="btn" data-act="tpl-import-items">Import items…</button>
        <span style="flex:1"></span>
        <button class="btn" data-act="tpl-dup">Duplicate</button>
        <button class="btn btn-danger" data-act="tpl-del">Delete template</button>
        <button class="btn btn-navy" data-act="rec-start" ${state.equipment.length ? "" : "disabled"}>Start a record…</button>
      </div>
      <datalist id="cat-list">${categories().map((c) => "<option value=\"" + esc(c) + "\">").join("")}</datalist>
    `;
  }

  function viewRecords() {
    const rows = state.records.slice().sort((a, b) => (b.createdAt || "").localeCompare(a.createdAt || ""));
    return `
      <div class="row spread">
        <div>
          <h2>Records</h2>
          <p class="page-lead">Filled-in instances of your templates — inspections, commissioning, maintenance, or anything else — tied to a piece of equipment.</p>
        </div>
        <button class="btn btn-primary" data-act="rec-start" ${state.templates.length && state.equipment.length ? "" : "disabled"}>New record</button>
      </div>
      <div class="card">
        ${rows.length ? '<div class="table-wrap"><table class="data"><thead><tr><th>Opened</th><th>Template</th><th>Tag</th><th>Progress</th><th>Status</th><th></th></tr></thead><tbody>' +
          rows.map((r) => {
            const eq = eqById(r.equipmentId);
            const p = recordProgress(r);
            return "<tr><td>" + esc(r.createdAt || "—") + "</td><td>" + esc(r.templateName || "") + "</td><td class='tag'>" +
              esc(eq ? eqTag(eq) : "?") + "</td><td>" + p.done + "/" + p.total + "</td><td>" + statusPill(recordStatus(r)) +
              "</td><td class='no-print'><button class='btn btn-sm btn-primary' data-act='rec-open' data-id='" + r.id + "'>Open</button> " +
              "<button class='btn btn-sm btn-danger' data-act='rec-del' data-id='" + r.id + "'>Delete</button></td></tr>";
          }).join("") + "</tbody></table></div>" : '<p class="empty">No records yet.</p>'}
      </div>
    `;
  }

  function fieldControl(col, value, attrs) {
    const v = value == null ? "" : String(value);
    if (col.type === "passfail") {
      return '<div class="result-row">' + ["Pass", "Fail", "NA"].map((r) =>
        '<label><input type="radio" name="' + attrs + '" value="' + r + '"' + (v === r ? " checked" : "") + "> " + r + "</label>"
      ).join("") + "</div>";
    }
    if (col.type === "select") {
      return '<select data-col="' + col.id + '"><option value=""></option>' +
        (col.options || []).map((o) => '<option' + (v === o ? " selected" : "") + ">" + esc(o) + "</option>").join("") + "</select>";
    }
    const typ = col.type === "number" ? "number" : col.type === "date" ? "date" : "text";
    return '<input type="' + typ + '" data-col="' + col.id + '" value="' + esc(v) + '"' + (col.type === "number" ? ' step="any"' : "") + ">";
  }

  function viewRecordEdit() {
    const rec = state.records.find((r) => r.id === ui.recordId);
    if (!rec) return '<p class="empty">Record not found. <button class="btn" data-act="goto" data-view="records">Back</button></p>';
    const eq = eqById(rec.equipmentId);
    const p = recordProgress(rec);
    const cols = rec.columns || [];
    return `
      <div class="print-only print-head">
        <h1>Record — ${esc(eq ? eqTag(eq) : "")} · ${esc(rec.templateName || "")}</h1>
        <p class="tiny">Logbook v0.3 – early release · printed ${esc(todayISO())}</p>
      </div>
      <div class="row spread no-print">
        <div>
          <button class="btn btn-sm" data-act="goto" data-view="records">← All records</button>
          <h2 style="margin-top:.4rem">${esc(eq ? eqTag(eq) : "Unknown")} — ${esc(rec.templateName || "")}</h2>
          <p class="tiny">${esc(eq ? eqCat(eq) : "")} · ${esc(eq ? eqVal(eq, "location") : "")} · opened ${esc(rec.createdAt || "—")} · ${esc(kindLabel(rec.kind))}</p>
        </div>
        <div class="row">
          <button class="btn" data-act="rec-print">Print / PDF</button>
          <button class="btn btn-primary" data-act="rec-save">Save</button>
        </div>
      </div>
      <div class="card" style="margin-bottom:1rem">
        <div class="tiny">Progress</div>
        <div class="progress" style="margin-top:.3rem"><span style="width:${p.pct}%"></span></div>
        <p class="tiny" style="margin-top:.3rem">${p.done} of ${p.total} items with a result · ${statusPill(recordStatus(rec))}</p>
        <label class="fld" style="margin-top:.7rem"><span>Notes</span><textarea id="rec-notes">${esc(rec.notes || "")}</textarea></label>
      </div>
      <div class="card">
        <h3>Items</h3>
        ${(rec.items || []).map((it, i) => `
          <div class="check-item" data-idx="${i}">
            <div class="check-id">${i + 1}</div>
            <div>
              <div class="check-text">${esc(it.label)}</div>
              ${it.hint ? '<div class="check-method">' + esc(it.hint) + "</div>" : ""}
              <div class="form-grid" style="margin-top:.4rem">
                ${cols.map((c) => '<label class="fld"><span>' + esc(c.label) + "</span>" +
                  fieldControl(c, it.values ? it.values[c.id] : "", "res-" + i + "-" + c.id) + "</label>").join("")}
              </div>
            </div>
          </div>
        `).join("") || '<p class="empty">This record has no items.</p>'}
      </div>
      <div class="card" style="margin-top:1rem">
        <div class="row spread"><h3>Punch list</h3><button class="btn btn-sm no-print" data-act="punch-add">Add punch</button></div>
        ${(rec.punches || []).length ? '<div class="table-wrap"><table class="data"><thead><tr><th>Description</th><th>Priority</th><th>Status</th><th>Raised</th><th>Owner</th><th class="no-print"></th></tr></thead><tbody>' +
          (rec.punches || []).map((p, i) =>
            "<tr><td>" + esc(p.description) + "</td><td>" + esc(p.priority) + "</td><td>" + statusPill(p.status) +
            "</td><td>" + esc(p.raised || "") + "</td><td>" + esc(p.owner || "") +
            "</td><td class='no-print'><button class='btn btn-sm' data-act='punch-edit' data-i='" + i + "'>Edit</button> " +
            "<button class='btn btn-sm btn-danger' data-act='punch-del' data-i='" + i + "'>Delete</button></td></tr>"
          ).join("") + "</tbody></table></div>" : '<p class="empty">No punches.</p>'}
      </div>
      <div class="card" style="margin-top:1rem">
        <h3>Sign-off</h3>
        <div class="form-grid">
          <label class="fld"><span>Name / role</span><input id="rec-sign-name" value="${esc(rec.signoffName || "")}"></label>
          <label class="fld"><span>Date</span><input id="rec-sign-date" type="date" value="${esc(rec.signoffDate || "")}"></label>
        </div>
        <p class="tiny" style="margin-top:.6rem">A record of checks, not a certified design or PE stamp.</p>
      </div>
    `;
  }

  function viewLogbook() {
    const eqId = ui.logEq || (state.equipment[0] && state.equipment[0].id) || "";
    const eq = eqById(eqId);
    const params = eq ? paramsFor(eq) : [];
    const entries = eq ? state.logs.filter((l) => l.equipmentId === eq.id).sort((a, b) => (a.date + a.time).localeCompare(b.date + b.time)).reverse() : [];
    return `
      <div class="row spread">
        <div>
          <h2>Log book</h2>
          <p class="page-lead">Daily or shift readings. Parameters are defined per equipment, or copied from a category preset. Limits are optional.</p>
        </div>
      </div>
      <div class="card">
        <div class="form-grid">
          <label class="fld"><span>Equipment</span>
            <select id="log-eq">${state.equipment.map((e) =>
              '<option value="' + e.id + '"' + (e.id === eqId ? " selected" : "") + ">" + esc(eqTag(e)) + (eqCat(e) ? " — " + esc(eqCat(e)) : "") + "</option>"
            ).join("") || '<option value="">No equipment</option>'}</select>
          </label>
          <div class="row" style="align-items:flex-end">
            <button class="btn" data-act="params-edit" ${eq ? "" : "disabled"}>Edit parameters / limits</button>
            <button class="btn" data-act="goto-trend" ${eq ? "" : "disabled"}>View trends</button>
          </div>
        </div>
      </div>
      ${eq ? `
      <div class="card" style="margin-top:1rem">
        <h3>New entry</h3>
        <div class="form-grid">
          <label class="fld"><span>Date</span><input id="log-date" type="date" value="${todayISO()}"></label>
          <label class="fld"><span>Time</span><input id="log-time" type="time" value="${nowTime()}"></label>
          <label class="fld"><span>Entered by</span><input id="log-by" placeholder="Name or initials"></label>
          <label class="fld span-2"><span>Remarks</span><input id="log-remarks" placeholder="Optional"></label>
        </div>
        <div class="form-grid" style="margin-top:.7rem">
          ${params.map((p) => {
            const lim = [];
            if (p.min != null && p.min !== "") lim.push("min " + p.min);
            if (p.max != null && p.max !== "") lim.push("max " + p.max);
            return '<label class="fld"><span>' + esc(p.label) + (p.unit ? " (" + esc(p.unit) + ")" : "") +
              (lim.length ? ' <span class="tiny">' + esc(lim.join(", ")) + "</span>" : "") +
              '</span><input type="number" step="any" data-param="' + esc(p.key) + '"></label>';
          }).join("") || '<p class="tiny">No parameters yet. Click “Edit parameters / limits” to add some.</p>'}
        </div>
        <div class="row" style="margin-top:.8rem"><button class="btn btn-primary" data-act="log-add">Save entry</button></div>
      </div>
      <div class="card" style="margin-top:1rem">
        <h3>History (${entries.length})</h3>
        ${entries.length ? '<div class="table-wrap"><table class="data"><thead><tr><th>Date</th><th>Time</th>' +
          params.map((p) => "<th>" + esc(p.label) + "</th>").join("") +
          "<th>Remarks</th><th class='no-print'></th></tr></thead><tbody>" +
          entries.map((l) => {
            const cells = params.map((p) => {
              const v = l.readings ? l.readings[p.key] : null;
              const bad = outOfRange(num(v), p);
              const noted = annotFilled(annotOf(l, p.key));
              return "<td" + (bad ? " style='color:#b42318;font-weight:700'" : "") + ">" + (v == null || v === "" ? "—" : esc(v)) + (noted ? " *" : "") + "</td>";
            }).join("");
            return "<tr><td>" + esc(l.date) + "</td><td>" + esc(l.time || "") + "</td>" + cells +
              "<td>" + esc(l.remarks || "") + "</td><td class='no-print'><button class='btn btn-sm btn-danger' data-act='log-del' data-id='" + l.id + "'>Delete</button></td></tr>";
          }).join("") + "</tbody></table></div>" : '<p class="empty">No entries for this tag yet.</p>'}
      </div>` : '<p class="empty">Add equipment first.</p>'}
    `;
  }

  function viewTrends() {
    const eqId = ui.logEq || (state.equipment[0] && state.equipment[0].id) || "";
    const eq = eqById(eqId);
    const params = eq ? paramsFor(eq) : [];
    const paramKey = ui.trendParam || (params[0] && params[0].key) || "";
    const param = params.find((p) => p.key === paramKey);
    const from = ui.from || (state.logs.length ? state.logs.map((l) => l.date).sort()[0] : addDays(todayISO(), -30));
    const to = ui.to || todayISO();
    const points = (eq && param) ? trendPoints(eq, param, from, to) : [];
    const st = stats(points.map((p) => p.value));
    const oor = points.filter((p) => p.oor).length;
    const unack = points.filter((p) => p.unack).length;
    const noted = points.filter((p) => p.annotation);
    const tableRows = points.filter((p) => p.annotation || p.unack);
    const rows = tableRows.map((p) => {
      const status = p.unack
        ? '<span class="pill pill-unack">Unacknowledged</span>'
        : (p.oor ? '<span class="pill pill-fail">Out of range · annotated</span>' : '<span class="pill pill-pass">Annotated</span>');
      const an = p.annotation || {};
      return "<tr" + (p.unack ? ' class="unack-row"' : "") + "><td>" + (p.flag || "!") +
        "</td><td>" + esc(p.date) + (p.time ? " " + esc(p.time) : "") +
        "</td><td>" + (p.value == null ? "—" : esc(p.value)) +
        "</td><td>" + status +
        "</td><td>" + esc(an.cause || "—") +
        "</td><td>" + esc(an.action || "—") +
        "</td><td>" + esc(an.by || "—") +
        "</td><td>" + esc(fmtAnnotAt(an.at)) +
        "</td><td class='no-print'><button class='btn btn-sm' data-act='annot-open' data-log-id='" + esc(p.logId) +
        "' data-param='" + esc(param.key) + "'>" + (p.annotation ? "Edit" : "Add note") + "</button></td></tr>";
    }).join("");
    const tableBlock = tableRows.length
      ? '<h3 style="margin-top:1rem">Annotations in this range</h3><div class="table-wrap"><table class="data annot-table"><thead><tr><th>#</th><th>Date</th><th>Value</th><th>Status</th><th>Cause</th><th>Action taken</th><th>By</th><th>Timestamp</th><th class="no-print"></th></tr></thead><tbody>' + rows + "</tbody></table></div>"
      : '<p class="tiny" style="margin-top:.8rem">No annotations and no unacknowledged out-of-range points in this range. Click a chart point to add a note.</p>';
    return `
      <div class="row spread">
        <div>
          <h2>Trends</h2>
          <p class="page-lead">One parameter at a time, with optional limit lines. Click or tap a point to add or edit a note. Out-of-range points without a note are unacknowledged.</p>
        </div>
        <button class="btn no-print" data-act="trend-print">Print / PDF</button>
      </div>
      <div class="card no-print">
        <div class="form-grid">
          <label class="fld"><span>Equipment</span>
            <select id="tr-eq">${state.equipment.map((e) =>
              '<option value="' + e.id + '"' + (e.id === eqId ? " selected" : "") + ">" + esc(eqTag(e)) + "</option>").join("")}</select>
          </label>
          <label class="fld"><span>Parameter</span>
            <select id="tr-param">${params.map((p) =>
              '<option value="' + p.key + '"' + (p.key === paramKey ? " selected" : "") + ">" + esc(p.label) + (p.unit ? " (" + p.unit + ")" : "") + "</option>").join("")}</select>
          </label>
          <label class="fld"><span>From</span><input id="tr-from" type="date" value="${esc(from)}"></label>
          <label class="fld"><span>To</span><input id="tr-to" type="date" value="${esc(to)}"></label>
        </div>
      </div>
      <div class="card" style="margin-top:1rem" id="trend-report">
        <div class="print-only print-head">
          <h1>Trend summary — ${esc(eq ? eqTag(eq) : "")} ${esc(param ? param.label : "")}</h1>
          <p class="tiny">${esc(from)} to ${esc(to)} · Logbook v0.3 – early release</p>
        </div>
        <h3 class="no-print">${esc(eq ? eqTag(eq) : "")} · ${esc(param ? param.label : "—")}${param && param.unit ? " (" + esc(param.unit) + ")" : ""}</h3>
        <div class="chart-wrap" id="chart"></div>
        <div class="legend">
          <span><i style="background:#0f6c8c;height:3px"></i>Reading</span>
          <span><i style="background:#b42318;height:0;border-top:2px dashed #b42318"></i>Limit</span>
          <span><i style="background:#b42318;height:8px;width:8px;border-radius:50%"></i>Out of range</span>
          <span><i style="background:#1b365d;height:10px;width:14px;border-radius:2px"></i>Annotated</span>
          <span><i style="background:#9a6700;height:8px;width:8px;border-radius:50%"></i>Unacknowledged</span>
        </div>
        ${st ? '<div class="stats-row">' +
          '<div class="kv">Count<b>' + st.count + "</b></div>" +
          '<div class="kv">Min<b>' + st.min.toFixed(2) + "</b></div>" +
          '<div class="kv">Max<b>' + st.max.toFixed(2) + "</b></div>" +
          '<div class="kv">Average<b>' + st.avg.toFixed(2) + "</b></div>" +
          '<div class="kv">Out of range<b style="' + (oor ? "color:#b42318" : "") + '">' + oor + "</b></div>" +
          '<div class="kv">Annotated<b>' + noted.length + "</b></div>" +
          '<div class="kv">Unacknowledged<b style="' + (unack ? "color:#9a6700" : "") + '">' + unack + "</b></div>" +
          (param && (param.min != null && param.min !== "" || param.max != null && param.max !== "")
            ? '<div class="kv">Limits<b>' + (param.min != null && param.min !== "" ? param.min : "—") + " to " + (param.max != null && param.max !== "" ? param.max : "—") + "</b></div>"
            : "") + "</div>" : '<p class="empty">No data in this range. Load the demo or add log entries.</p>'}
        ${tableBlock}
        <p class="tiny" style="margin-top:.8rem">Limits are whatever you entered on the equipment parameters. Demo limits and notes are fictional.</p>
      </div>
    `;
  }

  function viewImport() {
    const wiz = ui.import || { step: 1, kind: "equipment", logMode: "long", createMissing: true };
    const steps = ["1. File", "2. Map columns", "3. Done"];
    let body = "";
    if (wiz.step === 1 || !wiz.rows) {
      body = `
        <div class="form-grid">
          <label class="fld"><span>What are you importing?</span>
            <select id="im-kind">
              <option value="equipment"${wiz.kind === "equipment" ? " selected" : ""}>Equipment rows</option>
              <option value="template"${wiz.kind === "template" ? " selected" : ""}>Template items (rows of a record template)</option>
              <option value="logs"${wiz.kind === "logs" ? " selected" : ""}>Log readings</option>
            </select>
          </label>
          ${wiz.kind === "logs" ? '<label class="fld"><span>Log layout</span><select id="im-mode"><option value="long"' + (wiz.logMode !== "wide" ? " selected" : "") + '>Long: one reading per row (tag, date, parameter, value)</option><option value="wide"' + (wiz.logMode === "wide" ? " selected" : "") + ">Wide: one row per timestamp, a column per parameter</option></select></label>" : ""}
        </div>
        <div class="drop" id="im-drop" style="margin-top:1rem">
          <p><b>Drop a CSV or Excel (.xlsx) file here</b>, or</p>
          <p style="margin-top:.5rem"><button class="btn btn-primary" data-act="im-pick">Choose file</button></p>
          <input type="file" id="im-file" accept=".csv,.xlsx,text/csv,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" hidden>
          <p class="tiny" style="margin-top:.7rem">Excel files are read in this browser — nothing is uploaded. Formulas, charts, and merged cells are not fully supported; values on the chosen sheet are.</p>
        </div>
        <div style="margin-top:1rem">
          <h3>Blank import templates</h3>
          <div class="row" style="margin-top:.4rem">
            <button class="btn btn-sm" data-act="blank" data-kind="equipment" data-fmt="csv">Equipment CSV</button>
            <button class="btn btn-sm" data-act="blank" data-kind="equipment" data-fmt="xlsx">Equipment Excel</button>
            <button class="btn btn-sm" data-act="blank" data-kind="template" data-fmt="csv">Template items CSV</button>
            <button class="btn btn-sm" data-act="blank" data-kind="template" data-fmt="xlsx">Template items Excel</button>
            <button class="btn btn-sm" data-act="blank" data-kind="logs" data-fmt="csv">Logs CSV</button>
            <button class="btn btn-sm" data-act="blank" data-kind="logs" data-fmt="xlsx">Logs Excel</button>
          </div>
        </div>`;
    } else if (wiz.step === 2) {
      const headers = (wiz.rows[0] || []).map((h, i) => h || "Column " + (i + 1));
      const targets = importTargets(wiz.kind, wiz.logMode);
      const mapRows = headers.map((h, i) =>
        '<div class="map-row"><div><b>' + esc(h) + "</b><div class='tiny'>e.g. " + esc((wiz.rows[1] && wiz.rows[1][i]) || "") +
        '</div></div><select data-map="' + i + '"><option value="">(skip)</option>' +
        targets.map((t) => {
          const extra = t.id === "pnew:" ? " data-pnew='1'" : "";
          return '<option value="' + esc(t.id) + '"' + extra + (wiz.mapping[i] === t.id ? " selected" : "") + ">" + esc(t.label) + "</option>";
        }).join("") + "</select></div>"
      ).join("");
      const preview = wiz.rows.slice(0, 6).map((r, ri) =>
        "<tr>" + r.map((c) => "<td>" + esc(c) + "</td>").join("") + "</tr>"
      ).join("");
      body = `
        <p class="tiny">${esc(wiz.fileName || "")}${wiz.sheets && wiz.sheets.length > 1 ? "" : ""} · ${wiz.rows.length - 1} data row(s)</p>
        ${wiz.sheets && wiz.sheets.length > 1 ? '<label class="fld" style="max-width:16rem"><span>Sheet</span><select id="im-sheet">' +
          wiz.sheets.map((s) => '<option' + (s.name === wiz.sheetName ? " selected" : "") + ">" + esc(s.name) + "</option>").join("") + "</select></label>" : ""}
        ${wiz.kind === "template" ? '<div class="form-grid" style="margin:.6rem 0"><label class="fld"><span>Add items to</span><select id="im-tpl"><option value="__new__">New template…</option>' +
          state.templates.map((t) => '<option value="' + t.id + '"' + (wiz.templateId === t.id ? " selected" : "") + ">" + esc(t.name) + "</option>").join("") +
          '</select></label><label class="fld"><span>New template name</span><input id="im-tpl-name" value="' + esc(wiz.newTemplateName || "Imported template") + '"></label></div>' : ""}
        ${wiz.kind === "equipment" ? '<label class="fld" style="margin:.6rem 0"><span><input type="checkbox" id="im-create"' + (wiz.createMissing ? " checked" : "") + "> Create new columns from unmapped headers</span></label>" : ""}
        <h3>Column mapping</h3>
        ${mapRows}
        <p class="preview-note">Preview of the first rows (including the header):</p>
        <div class="table-wrap"><table class="data">${preview}</table></div>
        <div class="row" style="margin-top:1rem">
          <button class="btn" data-act="im-back">Back</button>
          <button class="btn btn-primary" data-act="im-run">Import</button>
        </div>`;
    } else {
      body = '<p><b>Import finished.</b> ' + esc(wiz.resultText || "") + '</p><div class="row" style="margin-top:1rem"><button class="btn btn-primary" data-act="im-reset">Import another file</button></div>';
    }
    return `
      <div>
        <h2>Bulk import</h2>
        <p class="page-lead">Bring in a CSV or Excel table of equipment, template items, or log readings. Map each source column, preview, then import. Nothing leaves this computer.</p>
      </div>
      <div class="card">
        <div class="steps">${steps.map((s, i) => '<span class="' + (wiz.step === i + 1 ? "on" : "") + '">' + s + "</span>").join("")}</div>
        ${body}
      </div>
    `;
  }

  function viewBackup() {
    const packs = window.STARTER_PACKS || [];
    return `
      <div>
        <h2>Backup &amp; starter packs</h2>
        <p class="page-lead">Everything is stored in the local database. Export a JSON file and keep it somewhere safe. You can also import a v0.2 JSON backup. Starter packs are optional — they add templates and parameter presets without replacing your rows.</p>
      </div>
      <div class="grid-2">
        <div class="card">
          <h3>Backup</h3>
          <div class="stack">
            <div class="row">
              <button class="btn btn-primary" data-act="export-json">Export JSON backup</button>
              <button class="btn" data-act="import-json">Import JSON…</button>
              <input type="file" id="file-json" accept=".json,application/json" hidden>
            </div>
            <label class="fld" style="max-width:16rem"><span>On JSON import</span>
              <select id="import-mode">
                <option value="replace">Replace all data in this browser</option>
                <option value="merge">Merge into existing data</option>
              </select>
            </label>
            <div class="row">
              <button class="btn" data-act="export-eq-csv">Equipment CSV</button>
              <button class="btn" data-act="export-eq-xlsx">Equipment Excel</button>
              <button class="btn" data-act="export-log-csv">Logs CSV</button>
              <button class="btn" data-act="export-log-xlsx">Logs Excel</button>
            </div>
            <p class="tiny">Last saved: ${esc(state.meta.updatedAt || "never")} · ${state.equipment.length} equipment · ${state.logs.length} log rows · ${state.records.length} records${state.meta.demo ? " · demo data loaded" : ""}${state.meta.migratedFrom ? " · migrated from an earlier version" : ""}</p>
            <div class="row">
              <button class="btn" data-act="demo">Load fictional demo</button>
              <button class="btn btn-danger" data-act="clear">Clear all data</button>
            </div>
          </div>
        </div>
        <div class="card">
          <h3>Optional starter packs</h3>
          ${packs.map((p) =>
            '<div class="pack-card" style="margin-bottom:1rem"><h3>' + esc(p.name) + "</h3><p>" + esc(p.description) +
            "</p><button class='btn' data-act='pack-load' data-id='" + esc(p.id) + "'>Add this pack</button></div>"
          ).join("")}
        </div>
      </div>
      <p class="help" style="margin-top:1rem">The UI expects the local server (python run.py). Interactive API documentation is at /docs. Point Grafana, Node-RED, or an agent at the same origin or host:port.</p>
    `;
  }

  /* ---------- modals ---------- */
  function closeModal() { const m = document.getElementById("modal-root"); if (m) m.remove(); }
  function openModal(html) {
    closeModal();
    const wrap = document.createElement("div");
    wrap.id = "modal-root";
    wrap.className = "modal-back";
    wrap.innerHTML = '<div class="modal" role="dialog" aria-modal="true">' + html + "</div>";
    wrap.addEventListener("click", (e) => { if (e.target === wrap) closeModal(); });
    document.body.appendChild(wrap);
    return wrap;
  }

  function modalColumns() {
    const box = openModal(`
      <h2>Equipment columns</h2>
      <p class="tiny">Add, rename, reorder, or remove fields. Tag is required and cannot be removed. Dropdown fields need options, one per line.</p>
      <div id="col-list"></div>
      <div class="row" style="margin-top:.8rem">
        <button class="btn" id="col-add">Add column</button>
        <span style="flex:1"></span>
        <button class="btn" data-close="1">Done</button>
      </div>
    `);
    function redraw() {
      const host = box.querySelector("#col-list");
      host.innerHTML = '<div class="table-wrap"><table class="data"><thead><tr><th>Label</th><th>Type</th><th>Options</th><th></th></tr></thead><tbody>' +
        state.equipmentFields.map((f, i) =>
          "<tr><td><input data-f='label' data-i='" + i + "' value='" + esc(f.label) + "'" + (f.locked ? " disabled" : "") + "></td>" +
          "<td><select data-f='type' data-i='" + i + "'" + (f.locked ? " disabled" : "") + ">" +
          FIELD_TYPES.map((t) => "<option value='" + t.id + "'" + (f.type === t.id ? " selected" : "") + ">" + t.label + "</option>").join("") +
          "</select></td>" +
          "<td>" + (f.type === "select" ? "<input data-f='options' data-i='" + i + "' value='" + esc((f.options || []).join(", ")) + "' placeholder='A, B, C'>" : "<span class='tiny'>—</span>") + "</td>" +
          "<td>" +
          "<button class='btn btn-sm' data-up='" + i + "'" + (i === 0 || f.locked ? " disabled" : "") + ">↑</button> " +
          "<button class='btn btn-sm' data-dn='" + i + "'" + (i === state.equipmentFields.length - 1 ? " disabled" : "") + ">↓</button> " +
          (f.locked ? "" : "<button class='btn btn-sm btn-danger' data-rm='" + i + "'>×</button>") +
          "</td></tr>"
        ).join("") + "</tbody></table></div>";
    }
    function sync() {
      box.querySelectorAll("input[data-f],select[data-f]").forEach((el) => {
        const i = Number(el.dataset.i), f = el.dataset.f, field = state.equipmentFields[i];
        if (!field) return;
        if (f === "options") field.options = el.value.split(",").map((s) => s.trim()).filter(Boolean);
        else if (f === "label") {
          field.label = el.value.trim() || field.label;
          if (!field.locked) field.key = slug(field.label, field.key);
        } else field[f] = el.value;
      });
    }
    box.addEventListener("click", (e) => {
      if (e.target.dataset.up != null) {
        sync();
        const i = Number(e.target.dataset.up);
        if (i > 0 && !state.equipmentFields[i].locked && !state.equipmentFields[i - 1].locked) {
          const a = state.equipmentFields[i - 1]; state.equipmentFields[i - 1] = state.equipmentFields[i]; state.equipmentFields[i] = a;
        } else if (i > 0 && !state.equipmentFields[i].locked) {
          const a = state.equipmentFields[i - 1];
          if (!a.locked) { state.equipmentFields[i - 1] = state.equipmentFields[i]; state.equipmentFields[i] = a; }
        }
        persist(); redraw();
      }
      if (e.target.dataset.dn != null) {
        sync();
        const i = Number(e.target.dataset.dn);
        if (i < state.equipmentFields.length - 1 && !state.equipmentFields[i].locked) {
          const a = state.equipmentFields[i + 1]; state.equipmentFields[i + 1] = state.equipmentFields[i]; state.equipmentFields[i] = a;
        }
        persist(); redraw();
      }
      if (e.target.dataset.rm != null) {
        sync();
        const i = Number(e.target.dataset.rm);
        const f = state.equipmentFields[i];
        if (f && !f.locked && confirm("Remove column “" + f.label + "”? Values already entered stay hidden unless you add it back.")) {
          state.equipmentFields.splice(i, 1);
          persist(); redraw();
        }
      }
    });
    box.addEventListener("change", () => { sync(); persist(); setTimeout(redraw, 0); });
    box.querySelector("#col-add").addEventListener("click", () => {
      sync();
      state.equipmentFields.push({ id: uid("fld"), key: "field_" + (state.equipmentFields.length + 1), label: "New column", type: "text" });
      persist(); redraw();
    });
    box.querySelector("[data-close]").addEventListener("click", () => { sync(); persist(); closeModal(); render(); });
    redraw();
  }

  function modalTplColumn(tpl, index) {
    const existing = index != null ? tpl.columns[index] : { label: "", type: "text", options: [] };
    const box = openModal(`
      <h2>${index != null ? "Edit column" : "Add column"}</h2>
      <div class="stack">
        <label class="fld"><span>Label</span><input id="c-label" value="${esc(existing.label)}"></label>
        <label class="fld"><span>Type</span><select id="c-type">${COL_TYPES.map((t) =>
          '<option value="' + t.id + '"' + (existing.type === t.id ? " selected" : "") + ">" + esc(t.label) + "</option>").join("")}</select></label>
        <label class="fld"><span>Dropdown options (comma separated)</span><input id="c-opts" value="${esc((existing.options || []).join(", "))}"></label>
        <div class="row spread"><button class="btn" data-close="1">Cancel</button><button class="btn btn-primary" id="c-save">Save</button></div>
      </div>
    `);
    box.querySelector("[data-close]").addEventListener("click", closeModal);
    box.querySelector("#c-save").addEventListener("click", () => {
      const label = document.getElementById("c-label").value.trim();
      if (!label) { toast("Label is required."); return; }
      const rec = {
        id: existing.id || uid("col"),
        key: existing.key || slug(label, "col"),
        label,
        type: document.getElementById("c-type").value,
        options: document.getElementById("c-opts").value.split(",").map((s) => s.trim()).filter(Boolean)
      };
      if (index != null) tpl.columns[index] = rec;
      else tpl.columns.push(rec);
      persist(); closeModal(); render();
    });
  }

  function collectTplMeta() {
    const tpl = state.templates.find((t) => t.id === ui.templateId);
    if (!tpl) return;
    const n = document.getElementById("tpl-name");
    const k = document.getElementById("tpl-kind");
    const c = document.getElementById("tpl-cat");
    if (n) tpl.name = n.value.trim() || tpl.name;
    if (k) tpl.kind = k.value;
    if (c) tpl.category = c.value.trim();
    document.querySelectorAll("[data-tpl-row]").forEach((inp) => {
      const i = Number(inp.dataset.tplRow);
      if (tpl.rows[i]) tpl.rows[i][inp.dataset.k] = inp.value;
    });
  }

  function modalStartRecord() {
    if (!state.templates.length || !state.equipment.length) { toast("You need at least one template and one equipment row."); return; }
    const box = openModal(`
      <h2>Start a record</h2>
      <div class="stack">
        <label class="fld"><span>Template</span>
          <select id="m-tpl">${state.templates.map((t) =>
            '<option value="' + t.id + '"' + (t.id === ui.templateId ? " selected" : "") + ">" + esc(t.name) + "</option>").join("")}</select>
        </label>
        <label class="fld"><span>Equipment</span>
          <select id="m-eq">${state.equipment.map((e) =>
            '<option value="' + e.id + '">' + esc(eqTag(e)) + (eqCat(e) ? " — " + esc(eqCat(e)) : "") + "</option>").join("")}</select>
        </label>
        <div class="row spread"><button class="btn" data-close="1">Cancel</button><button class="btn btn-primary" id="m-go">Create</button></div>
      </div>
    `);
    box.querySelector("[data-close]").addEventListener("click", closeModal);
    box.querySelector("#m-go").addEventListener("click", () => {
      const tpl = state.templates.find((t) => t.id === document.getElementById("m-tpl").value);
      const eq = eqById(document.getElementById("m-eq").value);
      const cols = clone(tpl.columns || []).map((c) => Object.assign({}, c, { id: c.id || uid("col") }));
      const rec = {
        id: uid("rec"),
        templateId: tpl.id,
        templateName: tpl.name,
        kind: tpl.kind,
        equipmentId: eq.id,
        createdAt: todayISO(),
        columns: cols,
        items: (tpl.rows || []).map((r) => ({ id: uid("it"), label: r.label, hint: r.hint || "", values: {} })),
        punches: [],
        signoffName: "",
        signoffDate: "",
        notes: ""
      };
      state.records.push(rec);
      persist();
      closeModal();
      ui.view = "recordEdit";
      ui.recordId = rec.id;
      render();
    });
  }

  function modalPunch(rec, index) {
    const p = index != null ? rec.punches[index] : { description: "", priority: "B - Before handover", status: "Open", raised: todayISO(), owner: "", closed: "" };
    const box = openModal(`
      <h2>${index != null ? "Edit punch" : "Add punch"}</h2>
      <div class="stack">
        <label class="fld"><span>Description</span><textarea id="m-desc">${esc(p.description)}</textarea></label>
        <div class="form-grid">
          <label class="fld"><span>Priority</span>
            <select id="m-pri">
              <option${String(p.priority).startsWith("A") ? " selected" : ""}>A - Blocker / safety</option>
              <option${String(p.priority).startsWith("B") ? " selected" : ""}>B - Before handover</option>
              <option${String(p.priority).startsWith("C") ? " selected" : ""}>C - Deferred / minor</option>
            </select>
          </label>
          <label class="fld"><span>Status</span>
            <select id="m-st">${["Open", "In progress", "Closed", "Deferred"].map((s) =>
              "<option" + (p.status === s ? " selected" : "") + ">" + s + "</option>").join("")}</select>
          </label>
          <label class="fld"><span>Raised</span><input id="m-raised" type="date" value="${esc(p.raised || todayISO())}"></label>
          <label class="fld"><span>Owner</span><input id="m-owner" value="${esc(p.owner || "")}"></label>
        </div>
        <div class="row spread"><button class="btn" data-close="1">Cancel</button><button class="btn btn-primary" id="m-save">Save</button></div>
      </div>
    `);
    box.querySelector("[data-close]").addEventListener("click", closeModal);
    box.querySelector("#m-save").addEventListener("click", () => {
      const rec2 = {
        id: p.id || uid("pn"),
        description: document.getElementById("m-desc").value.trim(),
        priority: document.getElementById("m-pri").value,
        status: document.getElementById("m-st").value,
        raised: document.getElementById("m-raised").value,
        owner: document.getElementById("m-owner").value.trim(),
        closed: document.getElementById("m-st").value === "Closed" ? todayISO() : ""
      };
      if (!rec2.description) { toast("Description is required."); return; }
      if (!rec.punches) rec.punches = [];
      if (index != null) rec.punches[index] = rec2;
      else rec.punches.push(rec2);
      persist(); closeModal(); render();
    });
  }

  function modalParams(eq) {
    const params = clone(paramsFor(eq));
    const cat = eqCat(eq);
    const box = openModal(`
      <h2>Log parameters for ${esc(eqTag(eq))}</h2>
      <p class="tiny">These apply to this equipment’s log book and trends. Leave min/max blank if you do not want limit lines. You can also save them as the default for the category ${cat ? "“" + esc(cat) + "”" : "(set a category first)"}.</p>
      <div class="table-wrap" style="margin:.7rem 0"><table class="data" id="p-table">
        <thead><tr><th>Key</th><th>Label</th><th>Unit</th><th>Min</th><th>Max</th><th></th></tr></thead>
        <tbody></tbody></table></div>
      <div class="row spread">
        <div class="row">
          <button class="btn" id="p-add">Add parameter</button>
          ${cat ? '<button class="btn" id="p-save-cat">Save as defaults for ' + esc(cat) + "</button>" : ""}
          ${cat && state.paramPresets[cat] ? '<button class="btn" id="p-load-cat">Load ' + esc(cat) + " defaults</button>" : ""}
        </div>
        <div class="row">
          <button class="btn" data-close="1">Cancel</button>
          <button class="btn btn-primary" id="m-save">Save</button>
        </div>
      </div>
    `);
    function rowsHtml() {
      return params.map((p, i) =>
        '<tr><td><input value="' + esc(p.key) + '" data-f="key" data-i="' + i + '" style="width:7rem"></td>' +
        '<td><input value="' + esc(p.label) + '" data-f="label" data-i="' + i + '"></td>' +
        '<td><input value="' + esc(p.unit || "") + '" data-f="unit" data-i="' + i + '" style="width:6rem"></td>' +
        '<td><input type="number" step="any" value="' + (p.min != null && p.min !== "" ? esc(p.min) : "") + '" data-f="min" data-i="' + i + '" style="width:5.5rem"></td>' +
        '<td><input type="number" step="any" value="' + (p.max != null && p.max !== "" ? esc(p.max) : "") + '" data-f="max" data-i="' + i + '" style="width:5.5rem"></td>' +
        '<td><button class="btn btn-sm btn-danger" data-rm="' + i + '">Remove</button></td></tr>'
      ).join("");
    }
    function syncInputs() {
      box.querySelectorAll("input[data-f]").forEach((inp) => {
        const i = Number(inp.dataset.i);
        const f = inp.dataset.f;
        let v = inp.value;
        if ((f === "min" || f === "max") && v !== "") v = Number(v);
        if (params[i]) params[i][f] = v;
      });
    }
    function redraw() { box.querySelector("#p-table tbody").innerHTML = rowsHtml(); }
    box.addEventListener("click", (e) => {
      const rm = e.target.closest("[data-rm]");
      if (rm) { syncInputs(); params.splice(Number(rm.dataset.rm), 1); redraw(); }
    });
    box.querySelector("#p-add").addEventListener("click", () => {
      syncInputs();
      params.push({ key: "param_" + (params.length + 1), label: "New parameter", unit: "", min: "", max: "" });
      redraw();
    });
    const sc = box.querySelector("#p-save-cat");
    if (sc) sc.addEventListener("click", () => {
      syncInputs();
      state.paramPresets[cat] = clone(params).map((p) => ({ key: p.key, label: p.label, unit: p.unit, min: "", max: "" }));
      persist();
      toast("Saved as defaults for " + cat + ".");
    });
    const lc = box.querySelector("#p-load-cat");
    if (lc) lc.addEventListener("click", () => {
      state.paramPresets[cat].forEach((p) => {
        if (!params.some((x) => x.key === p.key)) params.push(clone(p));
      });
      redraw();
    });
    box.querySelector("[data-close]").addEventListener("click", closeModal);
    box.querySelector("#m-save").addEventListener("click", () => {
      syncInputs();
      eq.params = params.filter((p) => p.key && p.label);
      persist(); closeModal(); render(); toast("Parameters saved.");
    });
    redraw();
  }

  function collectRecordEdits() {
    const rec = state.records.find((r) => r.id === ui.recordId);
    if (!rec) return;
    const notes = document.getElementById("rec-notes");
    if (notes) rec.notes = notes.value;
    const sn = document.getElementById("rec-sign-name");
    const sd = document.getElementById("rec-sign-date");
    if (sn) rec.signoffName = sn.value.trim();
    if (sd) rec.signoffDate = sd.value;
    document.querySelectorAll(".check-item").forEach((row) => {
      const i = Number(row.dataset.idx);
      if (!rec.items[i]) return;
      if (!rec.items[i].values) rec.items[i].values = {};
      (rec.columns || []).forEach((c) => {
        if (c.type === "passfail") {
          const picked = row.querySelector('input[name="res-' + i + "-" + c.id + '"]:checked');
          rec.items[i].values[c.id] = picked ? picked.value : (rec.items[i].values[c.id] || "");
        } else {
          const inp = row.querySelector('[data-col="' + c.id + '"]');
          if (inp) rec.items[i].values[c.id] = inp.value;
        }
      });
    });
  }

  function modalAnnot(logId, paramKey) {
    const log = state.logs.find((l) => l.id === logId);
    if (!log) { toast("Log entry not found."); return; }
    const eq = eqById(log.equipmentId);
    const param = (eq ? paramsFor(eq) : []).find((p) => p.key === paramKey) || { key: paramKey, label: paramKey, unit: "" };
    const existing = annotOf(log, paramKey) || {};
    const val = log.readings ? log.readings[paramKey] : "";
    const bad = outOfRange(num(val), param);
    const when = existing.at || nowDateTime();
    const box = openModal(`
      <h2>${annotFilled(existing) ? "Edit annotation" : "Add annotation"}</h2>
      <p class="tiny">${esc(eq ? eqTag(eq) : "")} · ${esc(param.label)}${param.unit ? " (" + esc(param.unit) + ")" : ""} · ${esc(log.date)} ${esc(log.time || "")} · value ${esc(val)}${bad ? ' <span class="pill pill-fail">Out of range</span>' : ""}</p>
      <div class="stack" style="margin-top:.7rem">
        <label class="fld"><span>Cause</span><textarea id="a-cause" placeholder="What happened?">${esc(existing.cause || "")}</textarea></label>
        <label class="fld"><span>Action taken</span><textarea id="a-action" placeholder="What was done?">${esc(existing.action || "")}</textarea></label>
        <div class="form-grid">
          <label class="fld"><span>Initials / name</span><input id="a-by" value="${esc(existing.by || "")}"></label>
          <label class="fld"><span>Timestamp</span><input id="a-at" type="datetime-local" value="${esc(when)}"></label>
        </div>
        <div class="row spread">
          ${annotFilled(existing) ? '<button class="btn btn-danger" id="a-del">Delete note</button>' : '<button class="btn" data-close="1">Cancel</button>'}
          <div class="row">
            ${annotFilled(existing) ? '<button class="btn" data-close="1">Cancel</button>' : ""}
            <button class="btn btn-primary" id="a-save">Save</button>
          </div>
        </div>
      </div>
    `);
    box.querySelectorAll("[data-close]").forEach((b) => b.addEventListener("click", closeModal));
    box.querySelector("#a-save").addEventListener("click", () => {
      const rec = {
        cause: document.getElementById("a-cause").value.trim(),
        action: document.getElementById("a-action").value.trim(),
        by: document.getElementById("a-by").value.trim(),
        at: document.getElementById("a-at").value || nowDateTime()
      };
      if (!annotFilled(rec)) { toast("Enter a cause, action, or name."); return; }
      if (!log.annotations) log.annotations = {};
      log.annotations[paramKey] = rec;
      const done = () => { closeModal(); render(); toast("Annotation saved."); };
      if (window.LogbookAPI) {
        window.LogbookAPI.putAnnotation(log.id, paramKey, rec).then(done).catch((err) => toast("Save failed: " + err.message));
      } else { persist(); done(); }
    });
    const del = box.querySelector("#a-del");
    if (del) del.addEventListener("click", () => {
      if (!confirm("Delete this annotation?")) return;
      if (log.annotations) delete log.annotations[paramKey];
      const done = () => { closeModal(); render(); toast("Annotation deleted."); };
      if (window.LogbookAPI) {
        window.LogbookAPI.deleteAnnotation(log.id, paramKey).then(done).catch((err) => toast("Delete failed: " + err.message));
      } else { persist(); done(); }
    });
  }

  /* ---------- bind + events ---------- */
  function bindView() {
    if (ui.view === "trends") {
      const el = document.getElementById("chart");
      const eq = eqById(ui.logEq || (state.equipment[0] && state.equipment[0].id));
      const params = eq ? paramsFor(eq) : [];
      const paramKey = ui.trendParam || (params[0] && params[0].key);
      const param = params.find((p) => p.key === paramKey);
      const from = (document.getElementById("tr-from") || {}).value || ui.from;
      const to = (document.getElementById("tr-to") || {}).value || ui.to;
      if (el && eq && param) {
        drawTrend(el, trendPoints(eq, param, from, to), param);
        el.querySelectorAll("[data-log-id]").forEach((g) => {
          g.addEventListener("click", () => modalAnnot(g.dataset.logId, g.dataset.param));
          g.addEventListener("keydown", (ev) => {
            if (ev.key === "Enter" || ev.key === " ") { ev.preventDefault(); modalAnnot(g.dataset.logId, g.dataset.param); }
          });
        });
      }
    }
    if (ui.view === "import") {
      const drop = document.getElementById("im-drop");
      const file = document.getElementById("im-file");
      if (drop && file) {
        ["dragenter", "dragover"].forEach((ev) => drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.add("over"); }));
        ["dragleave", "drop"].forEach((ev) => drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.remove("over"); }));
        drop.addEventListener("drop", (e) => { const f = e.dataTransfer.files[0]; if (f) handleImportFile(f); });
      }
    }
  }

  async function handleImportFile(file) {
    try {
      const kind = (document.getElementById("im-kind") || {}).value || (ui.import && ui.import.kind) || "equipment";
      const logMode = (document.getElementById("im-mode") || {}).value || "long";
      const parsed = await tableFromFile(file);
      const sheet = parsed.sheets[0];
      const mapping = autoMap(sheet.rows[0] || [], importTargets(kind, logMode));
      // wide mode: auto-map leftover numeric-looking headers as new params if no match
      if (kind === "logs" && logMode === "wide") {
        (sheet.rows[0] || []).forEach((h, i) => {
          if (!mapping[i] && h) mapping[i] = "pnew:";
        });
      }
      ui.import = {
        step: 2, kind, logMode, fileName: parsed.fileName, sheets: parsed.sheets,
        sheetName: sheet.name, rows: sheet.rows, mapping, createMissing: true,
        templateId: ui.templateId || "__new__", newTemplateName: "Imported template"
      };
      render();
    } catch (err) {
      toast("Could not read file: " + err.message);
    }
  }

  function confirmDemo() {
    if (state.equipment.length && !state.meta.demo) {
      return confirm("Load fictional demo data? This replaces what is currently stored in the database. Export a JSON backup first if you need it.");
    }
    if (state.equipment.length) return confirm("Reload the fictional demo? Current database data will be replaced.");
    return true;
  }

  document.body.addEventListener("click", (e) => {
    const btn = e.target.closest("[data-act]");
    if (!btn) return;
    const act = btn.dataset.act;
    if (act === "goto") { if (ui.view === "templates") collectTplMeta(), persist(); if (ui.view === "recordEdit") collectRecordEdits(), persist(); ui.view = btn.dataset.view; render(); return; }
    if (act === "demo") {
      if (!confirmDemo()) return;
      loadDemo().then(() => {
        ui.view = "dashboard";
        ui.logEq = "eq_p101";
        ui.trendParam = "vibration";
        toast("Fictional demo data loaded.");
        render();
      }).catch((err) => toast("Could not load demo: " + err.message));
      return;
    }
    if (act === "eq-add") {
      state.equipment.push({ id: uid("eq"), values: {}, params: [], createdAt: todayISO() });
      persist(); render();
      const inputs = document.querySelectorAll("#eq-grid tbody tr:last-child input");
      if (inputs[0]) inputs[0].focus();
      return;
    }
    if (act === "eq-del") {
      const eq = eqById(btn.dataset.id);
      if (!eq || !confirm("Delete " + (eqTag(eq) || "this row") + " and its logs and records?")) return;
      state.logs = state.logs.filter((l) => l.equipmentId !== eq.id);
      state.records = state.records.filter((r) => r.equipmentId !== eq.id);
      state.equipment = state.equipment.filter((x) => x.id !== eq.id);
      persist(); render(); toast("Deleted.");
      return;
    }
    if (act === "col-manage") { modalColumns(); return; }
    if (act === "params-edit") {
      const eq = eqById(btn.dataset.id || ui.logEq || (state.equipment[0] && state.equipment[0].id));
      if (eq) modalParams(eq);
      return;
    }
    if (act === "tpl-new") {
      const t = {
        id: uid("tpl"), name: "New template", kind: "custom", category: "",
        columns: [
          { id: uid("col"), key: "result", label: "Result", type: "passfail" },
          { id: uid("col"), key: "remarks", label: "Remarks", type: "text" }
        ],
        rows: [{ id: uid("ti"), label: "New item", hint: "" }]
      };
      state.templates.push(t); ui.templateId = t.id; persist(); render(); return;
    }
    if (act === "tpl-select") { collectTplMeta(); persist(); ui.templateId = btn.dataset.id; render(); return; }
    if (act === "tpl-col-add") { collectTplMeta(); const tpl = state.templates.find((t) => t.id === ui.templateId); if (tpl) modalTplColumn(tpl, null); return; }
    if (act === "tpl-col-edit") { collectTplMeta(); const tpl = state.templates.find((t) => t.id === ui.templateId); if (tpl) modalTplColumn(tpl, Number(btn.dataset.i)); return; }
    if (act === "tpl-col-up" || act === "tpl-col-dn") {
      collectTplMeta();
      const tpl = state.templates.find((t) => t.id === ui.templateId);
      const i = Number(btn.dataset.i);
      const j = act === "tpl-col-up" ? i - 1 : i + 1;
      if (tpl && tpl.columns[j]) { const a = tpl.columns[i]; tpl.columns[i] = tpl.columns[j]; tpl.columns[j] = a; persist(); render(); }
      return;
    }
    if (act === "tpl-col-del") {
      collectTplMeta();
      const tpl = state.templates.find((t) => t.id === ui.templateId);
      if (tpl && confirm("Remove this column from the template?")) { tpl.columns.splice(Number(btn.dataset.i), 1); persist(); render(); }
      return;
    }
    if (act === "tpl-row-add") { collectTplMeta(); const tpl = state.templates.find((t) => t.id === ui.templateId); if (tpl) { tpl.rows.push({ id: uid("ti"), label: "New item", hint: "" }); persist(); render(); } return; }
    if (act === "tpl-row-del") { collectTplMeta(); const tpl = state.templates.find((t) => t.id === ui.templateId); if (tpl) { tpl.rows.splice(Number(btn.dataset.i), 1); persist(); render(); } return; }
    if (act === "tpl-row-up" || act === "tpl-row-dn") {
      collectTplMeta();
      const tpl = state.templates.find((t) => t.id === ui.templateId);
      const i = Number(btn.dataset.i);
      const j = act === "tpl-row-up" ? i - 1 : i + 1;
      if (tpl && tpl.rows[j]) { const a = tpl.rows[i]; tpl.rows[i] = tpl.rows[j]; tpl.rows[j] = a; persist(); render(); }
      return;
    }
    if (act === "tpl-dup") {
      collectTplMeta();
      const tpl = state.templates.find((t) => t.id === ui.templateId);
      if (!tpl) return;
      const copy = clone(tpl); copy.id = uid("tpl"); copy.name = tpl.name + " (copy)";
      state.templates.push(copy); ui.templateId = copy.id; persist(); render(); return;
    }
    if (act === "tpl-del") {
      const tpl = state.templates.find((t) => t.id === ui.templateId);
      if (tpl && confirm("Delete template “" + tpl.name + "”? Existing records are kept.")) {
        state.templates = state.templates.filter((t) => t.id !== tpl.id);
        ui.templateId = (state.templates[0] && state.templates[0].id) || "";
        persist(); render();
      }
      return;
    }
    if (act === "tpl-import-items") {
      collectTplMeta(); persist();
      ui.import = { step: 1, kind: "template", logMode: "long", createMissing: false, templateId: ui.templateId };
      ui.view = "import"; render(); return;
    }
    if (act === "rec-start") { if (ui.view === "templates") collectTplMeta(), persist(); modalStartRecord(); return; }
    if (act === "rec-open") { ui.recordId = btn.dataset.id; ui.view = "recordEdit"; render(); return; }
    if (act === "rec-del") {
      if (!confirm("Delete this record?")) return;
      state.records = state.records.filter((r) => r.id !== btn.dataset.id);
      persist(); render(); return;
    }
    if (act === "rec-save") { collectRecordEdits(); persist(); render(); toast("Record saved."); return; }
    if (act === "rec-print") { collectRecordEdits(); persist(); window.print(); return; }
    if (act === "punch-add") { const rec = state.records.find((r) => r.id === ui.recordId); if (rec) { collectRecordEdits(); modalPunch(rec, null); } return; }
    if (act === "punch-edit") { const rec = state.records.find((r) => r.id === ui.recordId); if (rec) { collectRecordEdits(); modalPunch(rec, Number(btn.dataset.i)); } return; }
    if (act === "punch-del") {
      const rec = state.records.find((r) => r.id === ui.recordId);
      if (!rec || !confirm("Delete this punch?")) return;
      collectRecordEdits(); rec.punches.splice(Number(btn.dataset.i), 1); persist(); render(); return;
    }
    if (act === "goto-trend") { ui.view = "trends"; render(); return; }
    if (act === "log-add") {
      const eq = eqById(document.getElementById("log-eq").value);
      if (!eq) return;
      const readings = {};
      document.querySelectorAll("input[data-param]").forEach((inp) => {
        const v = num(inp.value); if (v !== null) readings[inp.dataset.param] = v;
      });
      if (!Object.keys(readings).length) { toast("Enter at least one reading."); return; }
      const entry = {
        id: uid("log"), equipmentId: eq.id,
        date: document.getElementById("log-date").value || todayISO(),
        time: document.getElementById("log-time").value || nowTime(),
        readings, remarks: document.getElementById("log-remarks").value.trim(),
        enteredBy: document.getElementById("log-by").value.trim(), annotations: {}
      };
      state.logs.push(entry);
      const after = () => { render(); toast("Log entry saved."); };
      if (window.LogbookAPI) {
        window.LogbookAPI.postLog(entry).then(after).catch((err) => toast("Save failed: " + err.message));
      } else { persist(); after(); }
      return;
    }
    if (act === "log-del") {
      if (!confirm("Delete this log entry?")) return;
      const delId = btn.dataset.id;
      state.logs = state.logs.filter((l) => l.id !== delId);
      if (window.LogbookAPI) {
        window.LogbookAPI.deleteLog(delId).then(() => render()).catch((err) => toast("Delete failed: " + err.message));
      } else { persist(); render(); }
      return;
    }
    if (act === "annot-open") { modalAnnot(btn.dataset.logId, btn.dataset.param); return; }
    if (act === "trend-print") { window.print(); return; }
    if (act === "export-json") { exportJSON(); return; }
    if (act === "export-eq-csv") { exportEquipmentTable("csv"); return; }
    if (act === "export-eq-xlsx") { exportEquipmentTable("xlsx"); return; }
    if (act === "export-log-csv") { exportLogsTable("csv"); return; }
    if (act === "export-log-xlsx") { exportLogsTable("xlsx"); return; }
    if (act === "import-json") { document.getElementById("file-json").click(); return; }
    if (act === "blank") { downloadBlank(btn.dataset.kind, btn.dataset.fmt); return; }
    if (act === "im-pick") { document.getElementById("im-file").click(); return; }
    if (act === "im-back") { ui.import.step = 1; ui.import.rows = null; render(); return; }
    if (act === "im-reset") { ui.import = { step: 1, kind: (ui.import && ui.import.kind) || "equipment", logMode: "long", createMissing: true }; render(); return; }
    if (act === "im-run") { runImport(); return; }
    if (act === "pack-load") {
      const pack = (window.STARTER_PACKS || []).find((p) => p.id === btn.dataset.id);
      if (!pack) return;
      if (!confirm("Add templates and parameter presets from “" + pack.name + "”? Your existing rows are not replaced.")) return;
      applyPack(pack);
      toast("Starter pack added. Open Templates to review.");
      ui.view = "templates";
      render();
      return;
    }
    if (act === "clear") {
      if (!confirm("Clear all data in the database? This cannot be undone unless you have a backup.")) return;
      const after = () => { state = emptyState(); render(); toast("All data cleared."); };
      if (window.LogbookAPI) {
        window.LogbookAPI.clear().then(async () => {
          state = normalize(await window.LogbookAPI.bootstrap());
          render(); toast("All data cleared.");
        }).catch((err) => toast("Clear failed: " + err.message));
      } else after();
    }
  });

  function runImport() {
    const wiz = ui.import;
    if (!wiz || !wiz.rows) return;
    document.querySelectorAll("select[data-map]").forEach((s) => { wiz.mapping[s.dataset.map] = s.value; });
    if (document.getElementById("im-create")) wiz.createMissing = document.getElementById("im-create").checked;
    if (document.getElementById("im-tpl")) wiz.templateId = document.getElementById("im-tpl").value;
    if (document.getElementById("im-tpl-name")) wiz.newTemplateName = document.getElementById("im-tpl-name").value;
    try {
      let text = "";
      if (wiz.kind === "equipment") {
        const r = applyEquipmentImport(wiz.rows, wiz.mapping, wiz.createMissing);
        text = "Added " + r.added + ", updated " + r.updated + ", skipped " + r.skipped + " (missing tag).";
      } else if (wiz.kind === "template") {
        const r = applyTemplateImport(wiz.rows, wiz.mapping, wiz.templateId, wiz.newTemplateName);
        text = "Added " + r.added + " items to “" + r.templateName + "”.";
      } else {
        const r = applyLogsImport(wiz.rows, wiz.mapping, wiz.logMode);
        text = "Imported " + r.added + " log group(s). Skipped " + r.skipped + " (unknown tag or empty).";
      }
      const finish = () => { wiz.step = 3; wiz.resultText = text; render(); toast(text); };
      if (window.LogbookAPI && wiz.kind === "logs") {
        // Re-read recent imported rows from state: last `added` groups
        const rows = (wiz._importedLogs || []);
        if (rows.length) {
          const payload = [];
          rows.forEach((entry) => { payload.push.apply(payload, window.LogbookAPI.logToReadings(entry)); });
          window.LogbookAPI.postReadings(payload, true).then(finish).catch((err) => toast("Import failed: " + err.message));
          return;
        }
      }
      persist();
      finish();
    } catch (err) { toast("Import failed: " + err.message); }
  }

  document.body.addEventListener("change", (e) => {
    if (e.target.id === "eq-q") { ui.q = e.target.value; renderSoon(); return; }
    if (e.target.id === "eq-cat") { ui.filterCat = e.target.value; renderSoon(); return; }
    if (e.target.id === "log-eq") { ui.logEq = e.target.value; renderSoon(); return; }
    if (e.target.id === "tr-eq") { ui.logEq = e.target.value; ui.trendParam = ""; renderSoon(); return; }
    if (e.target.id === "tr-param") { ui.trendParam = e.target.value; renderSoon(); return; }
    if (e.target.id === "tr-from") { ui.from = e.target.value; renderSoon(); return; }
    if (e.target.id === "tr-to") { ui.to = e.target.value; renderSoon(); return; }
    if (e.target.id === "im-kind") { if (!ui.import) ui.import = { step: 1 }; ui.import.kind = e.target.value; renderSoon(); return; }
    if (e.target.id === "im-mode") { if (!ui.import) ui.import = { step: 1 }; ui.import.logMode = e.target.value; return; }
    if (e.target.id === "im-sheet" && ui.import) {
      const sh = ui.import.sheets.find((s) => s.name === e.target.value);
      if (sh) {
        ui.import.sheetName = sh.name;
        ui.import.rows = sh.rows;
        ui.import.mapping = autoMap(sh.rows[0] || [], importTargets(ui.import.kind, ui.import.logMode));
        renderSoon();
      }
      return;
    }
    if (e.target.id === "im-file" && e.target.files[0]) { handleImportFile(e.target.files[0]); e.target.value = ""; return; }
    if (e.target.id === "file-json" && e.target.files[0]) {
      const f = e.target.files[0];
      const mode = (document.getElementById("import-mode") || {}).value || "replace";
      const reader = new FileReader();
      reader.onload = () => {
        try {
          const data = JSON.parse(String(reader.result));
          if (window.LogbookAPI) {
            const replace = mode === "replace";
            window.LogbookAPI.migrateV2(data).then(async () => {
              if (!replace) { /* server default is replace; catalog already in DB */ }
              state = normalize(await window.LogbookAPI.bootstrap());
              renderSoon(); toast("JSON import complete.");
            }).catch((err) => toast("Import failed: " + err.message));
          } else {
            importJSONText(String(reader.result), mode); renderSoon(); toast("JSON import complete.");
          }
        } catch (err) { toast("Import failed: " + err.message); }
      };
      reader.readAsText(f);
      e.target.value = "";
      return;
    }
    if (e.target.matches("#eq-grid [data-eq]")) {
      const eq = eqById(e.target.dataset.eq);
      if (eq) { setEqVal(eq, e.target.dataset.fld, e.target.value); persist(); }
    }
    if (ui.view === "templates" && (e.target.id === "tpl-name" || e.target.id === "tpl-kind" || e.target.id === "tpl-cat" || e.target.dataset.tplRow != null)) {
      collectTplMeta(); persist();
    }
    if (ui.view === "recordEdit" && (e.target.type === "radio" || e.target.id === "rec-sign-name" || e.target.id === "rec-sign-date")) {
      collectRecordEdits(); persist();
    }
  });
  document.body.addEventListener("input", (e) => {
    if (e.target.matches("#eq-grid [data-eq]")) {
      const eq = eqById(e.target.dataset.eq);
      if (eq) setEqVal(eq, e.target.dataset.fld, e.target.value);
    }
    if (ui.view === "recordEdit" && (e.target.matches("[data-col]") || e.target.id === "rec-notes")) collectRecordEdits();
  });
  document.body.addEventListener("focusout", (e) => {
    if (e.target.matches("#eq-grid [data-eq]")) persist();
    if (ui.view === "templates") { collectTplMeta(); persist(); }
    if (ui.view === "recordEdit") { collectRecordEdits(); persist(); }
  });

  document.querySelectorAll("nav.tabs button").forEach((b) => {
    b.addEventListener("click", () => {
      if (ui.view === "templates") collectTplMeta(), persist();
      if (ui.view === "recordEdit") collectRecordEdits(), persist();
      ui.view = b.dataset.view;
      render();
    });
  });

  async function init() {
    state = await loadStore();
    render();
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", () => { init(); });
  else init();

  window.Logbook = {
    version: APP_VERSION,
    getState: () => state,
    loadDemo, persist, render, loadStore,
    goto: (v, extra) => { Object.assign(ui, extra || {}, { view: v }); render(); },
    applyPack, handleImportFile, tableFromFile,
    // test hook: set import wizard and run
    importTable: (kind, rows, mapping, extra) => {
      ui.import = Object.assign({ step: 2, kind, logMode: "long", rows, mapping, createMissing: true }, extra || {});
      runImport();
    }
  };
})();
