/* Talk to the local Logbook API instead of localStorage. */
(function (global) {
  "use strict";

  const API = "/api";

  async function req(method, path, body, opts) {
    const headers = Object.assign({ "Accept": "application/json" }, (opts && opts.headers) || {});
    if (global.LOGBOOK_API_KEY) {
      headers["X-API-Key"] = global.LOGBOOK_API_KEY;
    }
    const init = { method, headers };
    if (body !== undefined) {
      headers["Content-Type"] = "application/json";
      init.body = JSON.stringify(body);
    }
    const res = await fetch(API + path, init);
    if (!res.ok) {
      let detail = res.status + " " + res.statusText;
      try {
        const j = await res.json();
        if (j.detail) detail = typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail);
      } catch (e) { /* ignore */ }
      const err = new Error(detail);
      err.status = res.status;
      throw err;
    }
    if (res.status === 204) return null;
    const ct = res.headers.get("content-type") || "";
    if (ct.includes("application/json")) return res.json();
    return res;
  }

  function catalogPayload(state) {
    return {
      equipmentFields: state.equipmentFields,
      equipment: state.equipment,
      templates: state.templates,
      records: state.records,
      paramPresets: state.paramPresets
    };
  }

  function logToReadings(entry) {
    const readings = entry.readings || {};
    const anns = entry.annotations || {};
    return Object.keys(readings).map((key) => {
      const rec = {
        equipment_id: entry.equipmentId,
        parameter: key,
        value: readings[key],
        date: entry.date,
        time: entry.time || "00:00",
        remarks: entry.remarks || "",
        entered_by: entry.enteredBy || "",
        entry_id: entry.id,
        source: "manual",
        id: undefined
      };
      const an = anns[key];
      if (an && (an.cause || an.action || an.by)) {
        rec.annot_cause = an.cause;
        rec.annot_action = an.action;
        rec.annot_by = an.by;
        rec.annot_at = an.at;
      }
      return rec;
    });
  }

  const api = {
    health: () => req("GET", "/health"),
    bootstrap: () => req("GET", "/bootstrap"),
    sync: (state) => req("PUT", "/sync", catalogPayload(state)),
    loadDemo: () => req("POST", "/demo"),
    clear: () => req("DELETE", "/data"),
    migrateV2: (data) => req("POST", "/migrate/v2", data),
    postReadings: (readings, createMissing) =>
      req("POST", "/readings/bulk", { readings, create_missing_equipment: !!createMissing }),
    postLog: (entry) => {
      const rows = logToReadings(entry);
      if (!rows.length) return Promise.resolve({ added: 0 });
      return req("POST", "/readings/bulk", { readings: rows });
    },
    deleteLog: (entryId) => req("DELETE", "/readings/" + encodeURIComponent(entryId)),
    putAnnotation: (entryId, param, rec) =>
      req("PUT", "/logs/" + encodeURIComponent(entryId) + "/annotations/" + encodeURIComponent(param), rec),
    deleteAnnotation: (entryId, param) =>
      req("DELETE", "/logs/" + encodeURIComponent(entryId) + "/annotations/" + encodeURIComponent(param)),
    ingestReadings: (payload) => req("POST", "/ingest/readings", payload),
    exportUrl: (name) => API + "/export/" + name,
    docsUrl: "/docs",
    logToReadings
  };

  global.LogbookAPI = api;
})(typeof window !== "undefined" ? window : globalThis);
