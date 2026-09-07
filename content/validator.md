---
title: Validator
description: Paste a JSON record and check it against the current MAXGEN schema, entirely in your browser.
---
<p class="eyebrow">Tools</p>
# Check a record

Paste a JSON record, choose the schema, and press **Validate**. The check runs in your browser using the canonical schema files; nothing you paste is sent anywhere.

<div class="validator">
<label for="schema-select"><strong>Schema</strong></label><br>
<select id="schema-select">
<option value="person">MaxPerson (person.schema.json)</option>
<option value="raw-record">MaxRecord (raw-record.schema.json)</option>
<option value="task-queue">MaxTask (task-queue.schema.json)</option>
<option value="dna">MaxDNA (dna.schema.json)</option>
<option value="source">MaxSource (source.schema.json)</option>
<option value="recognition">MaxRecognition (recognition.schema.json)</option>
<option value="name">MaxName (name.schema.json)</option>
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
