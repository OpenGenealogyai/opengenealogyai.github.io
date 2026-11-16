/* Browser-side MAXGEN validator. Uses Ajv (JSON Schema 2020-12) loaded from a public content network.
   MAXGEN 2.0: all twelve files are loaded into one Ajv registry (the shared building-blocks file first), as the
   schema repo's own validators do. Nothing you paste leaves your browser. */
(function () {
  var sel = document.getElementById("schema-select");
  var ta = document.getElementById("record");
  var out = document.getElementById("result");
  var btn = document.getElementById("validate");
  var sample = document.getElementById("sample");
  if (!sel || !ta || !out || !btn) return;

  var V2 = "/schemas/maxgen/v2/";
  var DEFS = "maxgen-defs.schema.json";
  // [value, label, file, example] in the order of the schema pages
  var KINDS = [
    ["raw-record", "MaxRecord", "raw-record.schema.json", "record/marriage_register_1880_obadiah_patience.json"],
    ["person", "MaxPerson", "person.schema.json", "person/birth_calculated_from_census_age.json"],
    ["relationship", "MaxRelationship", "relationship.schema.json", "relationship/parent_child_candidate_father_a.json"],
    ["task-queue", "MaxTask", "task-queue.schema.json", "task/extract_record_with_steps.json"],
    ["source", "MaxSource", "source.schema.json", "source/census_collection_with_routing.json"],
    ["place", "MaxPlace", "place.schema.json", "place/town_with_dated_kinds.json"],
    ["group", "MaxGroup", "group.schema.json", "group/emigrant_company_no_members.json"],
    ["event", "MaxEvent", "event.schema.json", "event/marriage_shared_by_three_documents.json"],
    ["name", "MaxName", "name.schema.json", "name/quillfeather_surname_variants_with_status.json"],
    ["recognition", "MaxRecognition", "recognition.schema.json", "recognition/htr_reading_with_region.json"],
    ["dna", "MaxDNA", "dna.schema.json", "dna/autosomal_with_match_ids.json"]
  ];
  // A document's kind is the first of these keys found at its top level (schemas/README-v2.md).
  var KIND_KEYS = [["dna_id", "dna"], ["recognition_id", "recognition"], ["name_id", "name"],
    ["relationship_id", "relationship"], ["occurrence_id", "event"], ["place_id", "place"], ["group_id", "group"],
    ["record_id", "raw-record"], ["task_id", "task-queue"], ["source_id", "source"], ["person_id", "person"]];
  var V1 = [["v1:person", "MaxPerson"], ["v1:raw-record", "MaxRecord"], ["v1:task-queue", "MaxTask"], ["v1:dna", "MaxDNA"],
    ["v1:source", "MaxSource"], ["v1:recognition", "MaxRecognition"], ["v1:name", "MaxName"]];

  // Build the menu: detect, the eleven 2.0 kinds, then the archived 1.13 schemas.
  sel.innerHTML = "";
  function opt(parent, value, text) { var o = document.createElement("option"); o.value = value; o.textContent = text; parent.appendChild(o); }
  opt(sel, "auto", "Detect the kind from the record (MAXGEN 2.0)");
  var g2 = document.createElement("optgroup"); g2.label = "MAXGEN 2.0"; sel.appendChild(g2);
  KINDS.forEach(function (k) { opt(g2, k[0], k[1] + " (" + k[2] + ")"); });
  var g1 = document.createElement("optgroup"); g1.label = "MAXGEN 1.13 (archived)"; sel.appendChild(g1);
  V1.forEach(function (k) { opt(g1, k[0], k[1] + " 1.13 (" + k[0].slice(3) + ".schema.json)"); });

  function show(cls, html) { out.className = cls; out.innerHTML = html; out.hidden = false; }
  function esc(s) { return String(s).replace(/[&<>"]/g, function (c) { return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]; }); }
  function getJSON(url) {
    return fetch(url).then(function (r) { if (!r.ok) throw new Error("Could not load " + url + " (" + r.status + ")"); return r.json(); });
  }
  function newAjv() {
    var ajv = new window.ajv2020({ allErrors: true, strict: false, allowUnionTypes: true });
    // The formats MAXGEN schemas use, implemented inline so no extra library is needed.
    var URI = /^[a-z][a-z0-9+.-]*:[^\s]+$/i;
    ajv.addFormat("uuid", /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i);
    ajv.addFormat("date", /^\d{4}-\d{2}-\d{2}$/);
    ajv.addFormat("date-time", /^\d{4}-\d{2}-\d{2}[Tt ]\d{2}:\d{2}:\d{2}(\.\d+)?([Zz]|[+-]\d{2}:\d{2})$/);
    ajv.addFormat("uri", URI);
    ajv.addFormat("iri", URI);
    ajv.addFormat("uri-reference", /^\S*$/);
    ajv.addFormat("email", /^[^\s@]+@[^\s@]+\.[^\s@]+$/);
    ajv.addFormat("hostname", /^[a-z0-9.-]+$/i);
    ajv.addFormat("ipv4", /^(\d{1,3}\.){3}\d{1,3}$/);
    ajv.addFormat("regex", function () { return true; });
    return ajv;
  }

  var v2ajv = null;   // one registry with all twelve files, built once
  function loadV2() {
    if (v2ajv) return Promise.resolve(v2ajv);
    var files = [DEFS].concat(KINDS.map(function (k) { return k[2]; }));
    return Promise.all(files.map(function (f) { return getJSON(V2 + f); })).then(function (schemas) {
      var ajv = newAjv();
      schemas.forEach(function (s) { ajv.addSchema(s); });   // the shared file is first
      v2ajv = ajv;
      return ajv;
    });
  }
  var v1cache = {};
  function loadV1(name) {
    if (v1cache[name]) return Promise.resolve(v1cache[name]);
    return getJSON("/schemas/maxgen/v1/" + name + ".schema.json").then(function (s) {
      // Some 1.x files name an older draft in $schema; the 2020-12 engine reads their keywords the same way.
      delete s.$schema;
      var ajv = newAjv(); v1cache[name] = ajv.compile(s); v1cache[name].title = s.title; return v1cache[name];
    });
  }
  function kindOf(data) {
    if (!data || typeof data !== "object" || Array.isArray(data)) return null;
    for (var i = 0; i < KIND_KEYS.length; i++) if (Object.prototype.hasOwnProperty.call(data, KIND_KEYS[i][0])) return KIND_KEYS[i][1];
    return null;
  }
  function kindInfo(value) { for (var i = 0; i < KINDS.length; i++) if (KINDS[i][0] === value) return KINDS[i]; return null; }

  function report(validate, label) {
    if (validate.errors === null || validate.errors === undefined || validate.errors.length === 0) {
      show("ok", "<strong>Valid.</strong> This record conforms to " + esc(label) + "."); return;
    }
    var items = validate.errors.slice(0, 40).map(function (e) {
      var where = e.instancePath || "(top level)";
      var extra = "";
      if (e.params && e.params.additionalProperty) extra = " — unexpected field <code>" + esc(e.params.additionalProperty) + "</code>";
      if (e.params && e.params.unevaluatedProperty) extra = " — unexpected field <code>" + esc(e.params.unevaluatedProperty) + "</code>";
      if (e.params && e.params.allowedValues) extra = " — allowed: <code>" + e.params.allowedValues.map(esc).join("</code>, <code>") + "</code>";
      return "<li><code>" + esc(where) + "</code> " + esc(e.message) + extra + "</li>";
    });
    var n = validate.errors.length;
    show("bad", "<strong>" + n + " problem" + (n === 1 ? "" : "s") + " found against " + esc(label) + ".</strong><ul>" + items.join("") + "</ul>");
  }

  btn.addEventListener("click", function () {
    var data;
    try { data = JSON.parse(ta.value); }
    catch (e) { show("bad", "<strong>That is not valid JSON.</strong> " + esc(e.message)); return; }
    if (typeof window.ajv2020 !== "function") { show("bad", "The validator library did not load. Check your connection and reload."); return; }
    var choice = sel.value;
    if (choice.indexOf("v1:") === 0) {
      var name = choice.slice(3);
      loadV1(name).then(function (validate) { validate(data); report(validate, (validate.title || name) + " 1.13 (archived)"); })
        .catch(function (e) { show("bad", esc(e.message)); });
      return;
    }
    var kind = choice === "auto" ? kindOf(data) : choice;
    if (!kind) { show("bad", "<strong>Could not tell which kind of record this is.</strong> A MAXGEN 2.0 document has one of these keys at its top level: <code>" + KIND_KEYS.map(function (k) { return k[0]; }).join("</code>, <code>") + "</code>. Or choose the schema from the list."); return; }
    var info = kindInfo(kind);
    loadV2().then(function (ajv) {
      var validate;
      try { validate = ajv.getSchema("https://opengenealogyai.org" + V2 + info[2]); }   // each file's $id
      catch (e) { show("bad", "Schema could not be compiled: " + esc(e.message)); return; }
      if (!validate) { show("bad", "Schema " + esc(info[2]) + " is not loaded."); return; }
      validate(data);
      report(validate, info[1] + " (MAXGEN 2.0)" + (choice === "auto" ? ", detected from the record" : ""));
    }).catch(function (e) { show("bad", esc(e.message)); });
  });

  if (sample) sample.addEventListener("click", function () {
    var info = kindInfo(sel.value);
    if (!info) {
      if (sel.value.indexOf("v1:") === 0) { show("bad", "Samples are offered for MAXGEN 2.0 only."); return; }
      info = kindInfo("person");
    }
    getJSON("/content/v2/examples/" + info[3]).then(function (d) {
      ta.value = JSON.stringify(d, null, 2);
      out.hidden = true;
    }).catch(function (e) { show("bad", esc(e.message)); });
  });
})();
