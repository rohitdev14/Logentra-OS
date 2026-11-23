/* Minimal XLSX read/write for simple tables. No CDN. Uses DecompressionStream for deflate. */
(function (global) {
  "use strict";

  function crc32(data) {
    let table = crc32._t;
    if (!table) {
      table = crc32._t = new Uint32Array(256);
      for (let i = 0; i < 256; i++) {
        let c = i;
        for (let k = 0; k < 8; k++) c = (c & 1) ? (0xedb88320 ^ (c >>> 1)) : (c >>> 1);
        table[i] = c >>> 0;
      }
    }
    let crc = 0xffffffff;
    for (let i = 0; i < data.length; i++) crc = table[(crc ^ data[i]) & 0xff] ^ (crc >>> 8);
    return (crc ^ 0xffffffff) >>> 0;
  }

  function u8(str) {
    return new TextEncoder().encode(str);
  }
  function dec(bytes) {
    return new TextDecoder("utf-8").decode(bytes);
  }
  function concat(arrs) {
    const n = arrs.reduce((a, b) => a + b.length, 0);
    const out = new Uint8Array(n);
    let o = 0;
    arrs.forEach((a) => { out.set(a, o); o += a.length; });
    return out;
  }

  function buildZip(files) {
    // files: [{name, data: Uint8Array}]
    const locals = [];
    const centrals = [];
    let offset = 0;
    files.forEach((f) => {
      const name = u8(f.name);
      const data = f.data;
      const crc = crc32(data);
      const local = new Uint8Array(30 + name.length + data.length);
      const v = new DataView(local.buffer);
      v.setUint32(0, 0x04034b50, true);
      v.setUint16(4, 20, true);
      v.setUint16(8, 0, true); // store
      v.setUint32(14, crc, true);
      v.setUint32(18, data.length, true);
      v.setUint32(22, data.length, true);
      v.setUint16(26, name.length, true);
      local.set(name, 30);
      local.set(data, 30 + name.length);
      locals.push(local);
      const central = new Uint8Array(46 + name.length);
      const cv = new DataView(central.buffer);
      cv.setUint32(0, 0x02014b50, true);
      cv.setUint16(4, 20, true);
      cv.setUint16(6, 20, true);
      cv.setUint32(16, crc, true);
      cv.setUint32(20, data.length, true);
      cv.setUint32(24, data.length, true);
      cv.setUint16(28, name.length, true);
      cv.setUint32(42, offset, true);
      central.set(name, 46);
      centrals.push(central);
      offset += local.length;
    });
    const cd = concat(centrals);
    const eocd = new Uint8Array(22);
    const ev = new DataView(eocd.buffer);
    ev.setUint32(0, 0x06054b50, true);
    ev.setUint16(8, files.length, true);
    ev.setUint16(10, files.length, true);
    ev.setUint32(12, cd.length, true);
    ev.setUint32(16, offset, true);
    return concat(locals.concat([cd, eocd]));
  }

  async function inflateRaw(bytes) {
    if (typeof DecompressionStream !== "function") {
      throw new Error("This browser cannot decompress Excel files (no DecompressionStream). Save as CSV instead.");
    }
    const stream = new Blob([bytes]).stream().pipeThrough(new DecompressionStream("deflate-raw"));
    return new Uint8Array(await new Response(stream).arrayBuffer());
  }

  async function readZip(buf) {
    const u = buf instanceof Uint8Array ? buf : new Uint8Array(buf);
    const v = new DataView(u.buffer, u.byteOffset, u.byteLength);
    const files = {};
    let i = 0;
    while (i + 4 <= u.length) {
      const sig = v.getUint32(i, true);
      if (sig === 0x04034b50) {
        const method = v.getUint16(i + 8, true);
        let comp = v.getUint32(i + 18, true);
        let size = v.getUint32(i + 22, true);
        const nlen = v.getUint16(i + 26, true);
        const elen = v.getUint16(i + 28, true);
        const name = dec(u.subarray(i + 30, i + 30 + nlen)).replace(/\\/g, "/");
        let dataStart = i + 30 + nlen + elen;
        // Data descriptor if bit 3 set and sizes are 0
        const flags = v.getUint16(i + 6, true);
        if ((flags & 8) && comp === 0 && size === 0) {
          // search for descriptor — uncommon in xlsx; fall through
        }
        const compData = u.subarray(dataStart, dataStart + (method === 0 ? size : comp));
        let data;
        if (method === 0) data = new Uint8Array(compData);
        else if (method === 8) data = await inflateRaw(compData);
        else throw new Error("Unsupported ZIP compression in the Excel file.");
        files[name] = data;
        i = dataStart + (method === 0 ? size : comp);
      } else if (sig === 0x02014b50 || sig === 0x06054b50) {
        break;
      } else {
        i += 1;
      }
    }
    return files;
  }

  function xmlText(s) {
    return String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
  }

  function colLetters(n) {
    let s = "";
    n += 1;
    while (n > 0) {
      const r = (n - 1) % 26;
      s = String.fromCharCode(65 + r) + s;
      n = Math.floor((n - 1) / 26);
    }
    return s;
  }
  function colIndex(letters) {
    let n = 0;
    const s = letters.replace(/[0-9]/g, "");
    for (let i = 0; i < s.length; i++) n = n * 26 + (s.charCodeAt(i) - 64);
    return n - 1;
  }

  function parseSharedStrings(xml) {
    const out = [];
    const re = /<si[\s\S]*?<\/si>/g;
    const bits = xml.match(re) || [];
    bits.forEach((si) => {
      const texts = [];
      const reT = /<t[^>]*>([\s\S]*?)<\/t>/g;
      let m;
      while ((m = reT.exec(si))) texts.push(decodeEntities(m[1]));
      out.push(texts.join(""));
    });
    return out;
  }
  function decodeEntities(s) {
    return s.replace(/&lt;/g, "<").replace(/&gt;/g, ">").replace(/&quot;/g, '"').replace(/&apos;/g, "'").replace(/&amp;/g, "&");
  }

  function parseSheet(xml, sst) {
    const rows = [];
    const rowRe = /<row[^>]*>([\s\S]*?)<\/row>/g;
    let rm;
    while ((rm = rowRe.exec(xml))) {
      const block = rm[1];
      const cells = [];
      const cRe = /<c([^>]*)>([\s\S]*?)<\/c>|<c([^>]*)\/>/g;
      let cm;
      while ((cm = cRe.exec(block))) {
        const attrs = cm[1] || cm[3] || "";
        const inner = cm[2] || "";
        const r = (attrs.match(/\br="([A-Z]+[0-9]+)"/) || [])[1];
        const t = (attrs.match(/\bt="([^"]+)"/) || [])[1] || "";
        const ci = r ? colIndex(r) : cells.length;
        let val = "";
        if (t === "s") {
          const v = (inner.match(/<v[^>]*>([\s\S]*?)<\/v>/) || [])[1];
          val = sst[Number(v)] != null ? sst[Number(v)] : "";
        } else if (t === "inlineStr") {
          const ts = [];
          const reT = /<t[^>]*>([\s\S]*?)<\/t>/g;
          let m;
          while ((m = reT.exec(inner))) ts.push(decodeEntities(m[1]));
          val = ts.join("");
        } else if (t === "b") {
          const v = (inner.match(/<v[^>]*>([\s\S]*?)<\/v>/) || [])[1];
          val = v === "1" ? "TRUE" : "FALSE";
        } else {
          const v = (inner.match(/<v[^>]*>([\s\S]*?)<\/v>/) || [])[1];
          val = v != null ? decodeEntities(v) : "";
        }
        cells[ci] = val;
      }
      // fill holes
      const width = cells.length;
      for (let i = 0; i < width; i++) if (cells[i] == null) cells[i] = "";
      rows.push(cells);
    }
    // normalize width
    let max = 0;
    rows.forEach((r) => { if (r.length > max) max = r.length; });
    rows.forEach((r) => { while (r.length < max) r.push(""); });
    return rows;
  }

  function sheetNames(workbookXml, relsXml) {
    const names = [];
    const idToTarget = {};
    const relRe = /<Relationship([^>]*)>/g;
    let m;
    while ((m = relRe.exec(relsXml || ""))) {
      const a = m[1];
      const id = (a.match(/\bId="([^"]+)"/) || [])[1];
      const tgt = (a.match(/\bTarget="([^"]+)"/) || [])[1];
      if (id && tgt) idToTarget[id] = tgt.replace(/^\/?xl\//, "").replace(/^\.\//, "");
    }
    const shRe = /<sheet([^>]*)\/>/g;
    while ((m = shRe.exec(workbookXml || ""))) {
      const a = m[1];
      const name = (a.match(/\bname="([^"]+)"/) || [])[1] || "Sheet";
      const rid = (a.match(/\br:id="([^"]+)"/) || [])[1];
      let path = rid && idToTarget[rid] ? idToTarget[rid] : "";
      if (path && !path.startsWith("xl/")) path = "xl/" + path.replace(/^xl\//, "");
      names.push({ name, path });
    }
    return names;
  }

  async function parse(arrayBuffer) {
    const files = await readZip(arrayBuffer);
    const wb = dec(files["xl/workbook.xml"] || new Uint8Array());
    const rels = dec(files["xl/_rels/workbook.xml.rels"] || new Uint8Array());
    const sheetsMeta = sheetNames(wb, rels);
    const sstXml = files["xl/sharedStrings.xml"] ? dec(files["xl/sharedStrings.xml"]) : "";
    const sst = sstXml ? parseSharedStrings(sstXml) : [];
    const sheets = [];
    for (const s of sheetsMeta) {
      const key = Object.keys(files).find((k) => k.replace(/\\/g, "/") === s.path || k.endsWith("/" + s.path.replace(/^xl\//, "")) || k === s.path);
      const xml = key ? dec(files[key]) : "";
      sheets.push({ name: s.name, rows: xml ? parseSheet(xml, sst) : [] });
    }
    if (!sheets.length) {
      // fallback first worksheet file
      const k = Object.keys(files).find((n) => /xl\/worksheets\/sheet\d+\.xml$/.test(n));
      if (k) sheets.push({ name: "Sheet1", rows: parseSheet(dec(files[k]), sst) });
    }
    return { sheets };
  }

  function build(sheetName, rows) {
    const name = sheetName || "Sheet1";
    const maxCols = Math.max(1, ...rows.map((r) => r.length));
    const maxRows = Math.max(1, rows.length);
    let sheetBody = "";
    rows.forEach((row, ri) => {
      let cells = "";
      for (let ci = 0; ci < maxCols; ci++) {
        const val = row[ci] == null ? "" : String(row[ci]);
        const ref = colLetters(ci) + (ri + 1);
        if (val === "") continue;
        const asNum = val !== "" && isFinite(Number(val)) && val.trim() !== "" && !/^0\d+/.test(val.trim());
        if (asNum) cells += '<c r="' + ref + '"><v>' + xmlText(String(Number(val))) + "</v></c>";
        else cells += '<c r="' + ref + '" t="inlineStr"><is><t>' + xmlText(val) + "</t></is></c>";
      }
      sheetBody += '<row r="' + (ri + 1) + '">' + cells + "</row>";
    });
    const sheetXml = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>' +
      '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">' +
      '<dimension ref="A1:' + colLetters(maxCols - 1) + maxRows + '"/>' +
      "<sheetData>" + sheetBody + "</sheetData></worksheet>";
    const wbXml = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>' +
      '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">' +
      '<sheets><sheet name="' + xmlText(name) + '" sheetId="1" r:id="rId1"/></sheets></workbook>';
    const rels = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>' +
      '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">' +
      '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>' +
      "</Relationships>";
    const wbRels = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>' +
      '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">' +
      '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>' +
      "</Relationships>";
    const ctypes = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>' +
      '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">' +
      '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>' +
      '<Default Extension="xml" ContentType="application/xml"/>' +
      '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>' +
      '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>' +
      "</Types>";
    const zip = buildZip([
      { name: "[Content_Types].xml", data: u8(ctypes) },
      { name: "_rels/.rels", data: u8(rels) },
      { name: "xl/workbook.xml", data: u8(wbXml) },
      { name: "xl/_rels/workbook.xml.rels", data: u8(wbRels) },
      { name: "xl/worksheets/sheet1.xml", data: u8(sheetXml) }
    ]);
    return new Blob([zip], { type: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" });
  }

  global.XlsxUtil = { parse, build, colLetters };
})(typeof window !== "undefined" ? window : globalThis);
