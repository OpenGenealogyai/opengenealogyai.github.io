---
title: Validator
description: Paste a JSON record and check it against the MAXGEN 2.0 schemas, entirely in your browser.
---
<p class="eyebrow">Tools</p>
# Check a record

Paste a JSON record, choose the schema (or let the page detect the kind from the record), and press **Validate**. The check runs in your browser using the canonical schema files: all twelve MAXGEN 2.0 files are loaded together, the shared building-blocks file first. Nothing you paste is sent anywhere. The archived 1.13 schemas are in the list too.

<div class="validator">
<label for="schema-select"><strong>Schema</strong></label><br>
<select id="schema-select">
<option value="auto">Detect the kind from the record (MAXGEN 2.0)</option>
<optgroup label="MAXGEN 2.0">
<option value="raw-record">MaxRecord (raw-record.schema.json)</option>
<option value="person">MaxPerson (person.schema.json)</option>
<option value="relationship">MaxRelationship (relationship.schema.json)</option>
<option value="task-queue">MaxTask (task-queue.schema.json)</option>
<option value="source">MaxSource (source.schema.json)</option>
<option value="place">MaxPlace (place.schema.json)</option>
<option value="group">MaxGroup (group.schema.json)</option>
<option value="event">MaxEvent (event.schema.json)</option>
<option value="name">MaxName (name.schema.json)</option>
<option value="recognition">MaxRecognition (recognition.schema.json)</option>
<option value="dna">MaxDNA (dna.schema.json)</option>
</optgroup>
<optgroup label="MAXGEN 1.13 (archived)">
<option value="v1:person">MaxPerson 1.13 (person.schema.json)</option>
<option value="v1:raw-record">MaxRecord 1.13 (raw-record.schema.json)</option>
<option value="v1:task-queue">MaxTask 1.13 (task-queue.schema.json)</option>
<option value="v1:dna">MaxDNA 1.13 (dna.schema.json)</option>
<option value="v1:source">MaxSource 1.13 (source.schema.json)</option>
<option value="v1:recognition">MaxRecognition 1.13 (recognition.schema.json)</option>
<option value="v1:name">MaxName 1.13 (name.schema.json)</option>
</optgroup>
</select>
<button id="sample" type="button">Load a sample</button>
<br>
<textarea id="record" spellcheck="false" placeholder='{ "person_id": "...", ... }'></textarea>
<br>
<button id="validate" class="primary" type="button">Validate</button>
<div id="result" hidden></div>
</div>

<p class="muted">Powered by Ajv, an open-source JSON Schema validator, loaded from a public content network. If your browser blocks third-party scripts the button will report that the library did not load.</p>

<script src="https://cdnjs.cloudflare.com/ajax/libs/ajv/8.17.1/ajv2020.min.js"></script>
<script src="/assets/js/validator.js"></script>
