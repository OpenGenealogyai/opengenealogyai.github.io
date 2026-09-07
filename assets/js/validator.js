/* Browser-side MAXGEN validator. Uses Ajv (JSON Schema 2020-12) loaded from /assets/js/ajv2020.min.js.
   Nothing you paste leaves your browser. */
(function () {
  var sel = document.getElementById("schema-select");
  var ta = document.getElementById("record");
  var out = document.getElementById("result");
  var btn = document.getElementById("validate");
  var sample = document.getElementById("sample");
  if (!sel || !ta || !out || !btn) return;

  var cache = {};
  function show(cls, html) { out.className = cls; out.innerHTML = html; out.hidden = false; }

  function loadSchema(name) {
    if (cache[name]) return Promise.resolve(cache[name]);
    return fetch("/schemas/maxgen/v1/" + name + ".schema.json").then(function (r) {
      if (!r.ok) throw new Error("Could not load schema (" + r.status + ")");
      return r.json();
    }).then(function (s) { cache[name] = s; return s; });
  }

  btn.addEventListener("click", function () {
    var name = sel.value;
    var data;
    try { data = JSON.parse(ta.value); }
    catch (e) { show("bad", "<strong>That is not valid JSON.</strong> " + e.message); return; }
    if (typeof window.ajv2020 !== "function") { show("bad", "The validator library did not load. Check your connection and reload."); return; }
    loadSchema(name).then(function (schema) {
      var Ajv = window.ajv2020;
      var ajv = new Ajv({ allErrors: true, strict: false, allowUnionTypes: true });
      // The formats MAXGEN schemas use, implemented inline so no extra library is needed.
      var UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
      var DATE = /^\d{4}-\d{2}-\d{2}$/;
      var DATETIME = /^\d{4}-\d{2}-\d{2}[Tt ]\d{2}:\d{2}:\d{2}(\.\d+)?([Zz]|[+-]\d{2}:\d{2})$/;
      var URI = /^[a-z][a-z0-9+.-]*:[^\s]+$/i;
      var EMAIL = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
      ajv.addFormat("uuid", UUID);
      ajv.addFormat("date", DATE);
      ajv.addFormat("date-time", DATETIME);
      ajv.addFormat("uri", URI);
      ajv.addFormat("uri-reference", /^\S*$/);
      ajv.addFormat("email", EMAIL);
      ajv.addFormat("hostname", /^[a-z0-9.-]+$/i);
      ajv.addFormat("ipv4", /^(\d{1,3}\.){3}\d{1,3}$/);
      ajv.addFormat("iri", URI);
      ajv.addFormat("regex", function () { return true; });
      var validate;
      try { validate = ajv.compile(schema); }
      catch (e) { show("bad", "Schema could not be compiled: " + e.message); return; }
      var ok = validate(data);
      if (ok) {
        show("ok", "<strong>Valid.</strong> This record conforms to " + (schema.title || name) + " v" + (schema.properties && schema.properties.schema_version && schema.properties.schema_version.const || "") + ".");
      } else {
        var items = validate.errors.map(function (e) {
          var where = e.instancePath || "(top level)";
          var extra = e.params && e.params.additionalProperty ? " — unexpected field <code>" + e.params.additionalProperty + "</code>" : "";
          if (e.params && e.params.allowedValues) extra = " — allowed: <code>" + e.params.allowedValues.join("</code>, <code>") + "</code>";
          return "<li><code>" + where + "</code> " + e.message + extra + "</li>";
        });
        show("bad", "<strong>" + validate.errors.length + " problem" + (validate.errors.length === 1 ? "" : "s") + " found.</strong><ul>" + items.join("") + "</ul>");
      }
    }).catch(function (e) { show("bad", e.message); });
  });

  if (sample) sample.addEventListener("click", function () {
    var samples = {
      "raw-record": { record_id: "a1b2c3d4-0003-4000-8000-000000000003", schema_version: "1.13", record_type: "marriage_certificate", redistribution_license: "CC0", is_living_flag: false, source_url: "https://archive.org/details/illinois-marriage-1842-sangamon", extraction_confidence: 0.97, persons_mentioned: [{ name_as_written: "Abraham Lincoln", role: "subject" }, { name_as_written: "Mary Todd", role: "spouse" }], record_date: { year_min: 1842, year_max: 1842, month: 11, day: 4, date_type: "exact" } },
      "person": { person_id: "b1000000-0006-4000-8000-000000000006", schema_version: "1.13", is_living: false, name_assertions: [{ name_as_written: "Mary Todd", confidence: 0.95, source_record_id: "a1b2c3d4-0003-4000-8000-000000000003", asserted_by: "you", asserted_at: "2026-05-01T14:00:00Z" }], asserted_by: "you", asserted_at: "2026-05-01T14:00:00Z" },
      "task-queue": { task_id: "c1000000-0097-4000-8000-000000000097", schema_version: "1.13", task_type: "validate_record", status: "pending", created_at: "2026-05-01T00:00:00Z", created_by: "you" },
      "source": { source_id: "f1700000-0001-4000-8000-000000000001", schema_version: "1.13", name: "FamilySearch Research Wiki — Bavaria Church Records", source_url: "https://www.familysearch.org/en/wiki/Bavaria_Church_Records", source_type: "research_wiki", redistribution_license: "CC-BY-SA" },
      "recognition": { recognition_id: "c1900000-0002-4000-8000-000000000001", schema_version: "1.13", recognition_type: "htr", image_url: "https://example.org/register-page-12.jpg", output_text: "Johann Maier, geb. 14 März 1842", processing_date: "2026-06-20T14:00:00Z", contributor: { contributor_type: "human", contributor_name: "A. Reader" } },
      "name": { name_id: "e1f2a3b4-c5d6-4789-9abc-de0123456789", schema_version: "1.13", canonical_form: "Maxwell", name_type: "surname" },
      "dna": { dna_id: "d1000000-0001-4000-8000-000000000001", schema_version: "1.13", person_id: "b1000000-0006-4000-8000-000000000006", test_type: "autosomal", kit_id_hash: "0000000000000000000000000000000000000000000000000000000000000000", kit_source: "other", is_living_flag: true, redistribution_license: "tier2-private", raw_genotype_stored: false, consent_status: "subject_explicit_opt_in", asserted_by: "you", asserted_at: "2026-05-01T00:00:00Z" }
    };
    ta.value = JSON.stringify(samples[sel.value] || samples.person, null, 2);
    out.hidden = true;
  });
})();
