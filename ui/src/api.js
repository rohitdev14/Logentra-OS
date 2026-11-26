/* Talk to the local Logentra OS API instead of localStorage. */
(function (global) {
  "use strict";

  const API = "/api";
  const TOKEN_KEY = "logbook.token";

  function getToken() {
    try { return global.sessionStorage && sessionStorage.getItem(TOKEN_KEY); } catch (e) { return global.LOGBOOK_TOKEN || ""; }
  }
  function setToken(token) {
    try {
      if (token) sessionStorage.setItem(TOKEN_KEY, token);
      else sessionStorage.removeItem(TOKEN_KEY);
    } catch (e) { global.LOGBOOK_TOKEN = token || ""; }
  }

  async function req(method, path, body, opts) {
    const headers = Object.assign({ "Accept": "application/json" }, (opts && opts.headers) || {});
    const token = getToken();
    if (token) headers["Authorization"] = "Bearer " + token;
    if (global.LOGBOOK_API_KEY) {
      headers["X-API-Key"] = global.LOGBOOK_API_KEY;
    }
    const init = { method, headers };
    if (body instanceof FormData) {
      init.body = body;
    } else if (body !== undefined) {
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
      if (res.status === 401 && !(opts && opts.skipAuthHandler)) {
        try { global.dispatchEvent && dispatchEvent(new CustomEvent("logbook:auth-required")); } catch (e2) {}
      }
      const err = new Error(detail);
      err.status = res.status;
      const retry = parseInt(res.headers.get("Retry-After") || "", 10);
      if (retry > 0) err.retryAfter = retry;
      throw err;
    }
    if (res.status === 204) return null;
    const ct = res.headers.get("content-type") || "";
    if (ct.includes("application/json")) return res.json();
    return res;
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
    shiftRequest: (method, path, body) => req(method, path, body),
    listQRLabels: params => req("GET", "/equipment/qr-labels" + qs(params)),
    issueQRCodes: (ids, reprint) => req("POST", "/equipment/qr-codes", {ids, reprint}),
    getPublicURL: () => req("GET", "/settings/public-url"),
    setPublicURL: (public_base_url) => req("PUT", "/settings/public-url", {public_base_url}),
    storage: () => req("GET", "/storage"),
    phoneSessions: (user) => req("GET", user ? "/users/" + encodeURIComponent(user) + "/sessions" : "/auth/sessions"),
    revokePhone: (user, session) => req("DELETE",
      (user ? "/users/" + encodeURIComponent(user) + "/sessions/" : "/auth/sessions/") + encodeURIComponent(session)),
    authenticatedFetch: (path) => {
      const headers = {};
      if (getToken()) headers.Authorization = "Bearer " + getToken();
      if (global.LOGBOOK_API_KEY) headers["X-API-Key"] = global.LOGBOOK_API_KEY;
      return fetch(path, {headers}).then(response => {
        if (!response.ok) throw new Error("Request failed (" + response.status + ")");
        return response;
      });
    },
    health: () => req("GET", "/health"),
    bootstrap: () => req("GET", "/bootstrap"),
    sync: (payload, revision) => req("PUT", "/sync", payload, { headers: revision ? { "If-Match": revision } : {} }),
    deleteCatalog: (kind, id, revision) => req("DELETE", "/" + kind + "/" + encodeURIComponent(id), undefined,
      { headers: revision ? { "If-Match": revision } : {} }),
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
    logToReadings,
    getToken, setToken,
    login: (username, password) => req("POST", "/auth/login", { username, password }, { skipAuthHandler: true }).then((r) => {
      if (r && r.token) setToken(r.token);
      return r;
    }),
    logout: () => req("POST", "/auth/logout").catch(() => null).finally(() => setToken("")),
    me: () => req("GET", "/auth/me"),
    listEquipment: (params) => req("GET", "/equipment" + qs(params)),
    listWorkOrders: (params) => req("GET", "/work-orders" + qs(params)),
    getWorkOrder: (id) => req("GET", "/work-orders/" + encodeURIComponent(id)),
    createWorkOrder: (body) => req("POST", "/work-orders", body),
    patchWorkOrder: (id, body) => req("PATCH", "/work-orders/" + encodeURIComponent(id), body),
    transitionWorkOrder: (id, status) => req("POST", "/work-orders/" + encodeURIComponent(id) + "/transition", { status }),
    assignWorkOrder: (id, assignee_id) => req("POST", "/work-orders/" + encodeURIComponent(id) + "/assign", { assignee_id }),
    commentWorkOrder: (id, body) => req("POST", "/work-orders/" + encodeURIComponent(id) + "/comments", { body }),
    labourWorkOrder: (id, payload) => req("POST", "/work-orders/" + encodeURIComponent(id) + "/labour", payload),
    attachWorkOrderMeta: (id, payload) => req("POST", "/work-orders/" + encodeURIComponent(id) + "/attachments", payload),
    listRules: () => req("GET", "/work-order-rules"),
    createRule: (body) => req("POST", "/work-order-rules", body),
    patchRule: (id, body) => req("PATCH", "/work-order-rules/" + encodeURIComponent(id), body),
    deleteRule: (id) => req("DELETE", "/work-order-rules/" + encodeURIComponent(id)),
    listUsers: (params) => req("GET", "/users" + qs(params)),
    createUser: (body) => req("POST", "/users", body),
    patchUser: (id, body) => req("PATCH", "/users/" + encodeURIComponent(id), body),
    deleteUser: (id) => req("DELETE", "/users/" + encodeURIComponent(id)),
    listRoles: () => req("GET", "/roles"),
    createRole: (body) => req("POST", "/roles", body),
    patchRole: (id, body) => req("PATCH", "/roles/" + encodeURIComponent(id), body),
    deleteRole: (id) => req("DELETE", "/roles/" + encodeURIComponent(id)),
    listPermissions: () => req("GET", "/auth/permissions"),
    createApiKey: (uid, body) => req("POST", "/users/" + encodeURIComponent(uid) + "/api-keys", body || {}),
    listApiKeys: (uid) => req("GET", "/users/" + encodeURIComponent(uid) + "/api-keys"),
    revokeApiKey: (uid, kid) => req("DELETE", "/users/" + encodeURIComponent(uid) + "/api-keys/" + encodeURIComponent(kid)),
    listSites: () => req("GET", "/sites"),
    createSite: (body) => req("POST", "/sites", body),
    updateSite: (id, body) => req("PATCH", "/sites/" + encodeURIComponent(id), body),
    getRecord: (id) => req("GET", "/records/" + encodeURIComponent(id)),
    submitRecord: (id, body) => req("POST", "/records/" + encodeURIComponent(id) + "/submit", body),
    approveRecord: (id, body) => req("POST", "/records/" + encodeURIComponent(id) + "/approve", body),
    returnRecord: (id, body) => req("POST", "/records/" + encodeURIComponent(id) + "/return", body),
    getRecordAudit: (id) => req("GET", "/records/" + encodeURIComponent(id) + "/audit"),
    getEquipment: (id) => req("GET", "/equipment/" + encodeURIComponent(id)),
    listLogs: (params) => req("GET", "/logs" + qs(params)),
    dashboard: () => req("GET", "/dashboard"),
    listDocuments: (params) => req("GET", "/documents" + qs(params)),
    getDocument: (id) => req("GET", "/documents/" + encodeURIComponent(id)),
    documentFileUrl: (id, versionId) => API + "/documents/" + encodeURIComponent(id) + "/file" + (versionId ? "?version_id=" + encodeURIComponent(versionId) : ""),
    uploadDocument: (form) => req("POST", "/documents", form),
    addDocumentVersion: (id, form) => req("POST", "/documents/" + encodeURIComponent(id) + "/versions", form),
    patchDocument: (id, body) => req("PATCH", "/documents/" + encodeURIComponent(id), body),
    submitDocument: (id, body) => req("POST", "/documents/" + encodeURIComponent(id) + "/submit", body || {}),
    approveDocument: (id, comment) => req("POST", "/documents/" + encodeURIComponent(id) + "/approve", { comment: comment || "" }),
    rejectDocument: (id, comment) => req("POST", "/documents/" + encodeURIComponent(id) + "/reject", { comment: comment }),
    returnDocument: (id, comment) => req("POST", "/documents/" + encodeURIComponent(id) + "/return", { comment: comment }),
    commentDocument: (id, body) => req("POST", "/documents/" + encodeURIComponent(id) + "/comments", { body: body }),
    documentsQueue: (params) => req("GET", "/documents/queue" + qs(params)),
    listRoutes: (params) => req("GET", "/approval-routes" + qs(params)),
    getRoute: (id) => req("GET", "/approval-routes/" + encodeURIComponent(id)),
    createRoute: (body) => req("POST", "/approval-routes", body),
    patchRoute: (id, body) => req("PATCH", "/approval-routes/" + encodeURIComponent(id), body),
    deleteRoute: (id) => req("DELETE", "/approval-routes/" + encodeURIComponent(id))
  };

  function qs(params) {
    if (!params) return "";
    const u = new URLSearchParams();
    Object.keys(params).forEach((k) => {
      if (params[k] !== undefined && params[k] !== null && params[k] !== "") u.set(k, params[k]);
    });
    const s = u.toString();
    return s ? "?" + s : "";
  }

  global.LogbookAPI = api;
})(typeof window !== "undefined" ? window : globalThis);
