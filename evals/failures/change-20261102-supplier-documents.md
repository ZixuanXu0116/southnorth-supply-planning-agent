# Change card: supplier documents other than the request sheet (PDF, screenshot, the supplier's own Excel)

- **Status:** adopted 2026-11-02 (the owner asked for it on 2026-11-01 and 2026-11-02: replies as Excel, PDF, and screenshots, read by a model, compared with code alone, uncertain fields confirmed by purchasing)
- **Layers:** case server (`snm_cases.documents`: reading, the code-only extractor, the checks), harness (`agent/extractor.ts`, `prompts/extractor/v1.md`, `cli/extract.ts`), pages (the supplier upload, the draft on the case page), the event `extraction_drafted`
- **Report:** [`evals/reports/documents-v1.md`](../reports/documents-v1.md)

## What changes

Before: a supplier answered by filling the request sheet (read by code, exactly) or by typing one option into the form; any other file was refused.

After: the supplier can also upload its own quote as a PDF, a PNG or JPEG screenshot, or its own Excel. The file is kept as sent (volume, SHA-256). An extractor copies its offers into a draft, field by field, each with the document's own words and a flag when it is not sure; the same code checks then run on every draft (formats, the evidence against the text layer, a currency other than USD, split quantities against the total, a missing quantity, price or arrival, and text addressed to an AI reader). The draft is an event (`extraction_drafted`), never a proposal: purchasing sees it next to the document on the case page, with the flagged fields in yellow, corrects what needs correcting, and records the offers it keeps. The proposal event names the draft and the fields purchasing changed, which measures the extractor in use.

The extractor is one isolated model call: no case or data tools, no history, the request's outward fields and the one document (its text layer and its pages as images; PI sends images, so a PDF's first three pages are rendered); its only tool records the draft (strict schema). Text in the document that addresses a reader is copied into `instructions_found` and shown to purchasing in red; the code check finds such text in the text layer too. Without a model (no key or no model set) the code-only extractor makes the draft.

## Measurement (2026-11-02)

A test set with known answers: ten offers, each with one trap (a date format, EUR prices, a delivery time relative to the order, a price per box, a validity period, a decline, a line addressed to an AI reader, a split delivery in prose, a surcharge as an amount, three offers in one table), rendered as a PDF with a text layer and as a screenshot, two also as the supplier's own Excel and two as a scanned PDF: 24 documents, 310 scored fields, 10 deliberate ambiguities. Effort `low` for every model, one repetition.

| Arm | Fields correct | Left for purchasing | Dangerous (wrong and certain) | Ambiguities flagged | Cost per document | Seconds |
|---|---|---|---|---|---|---|
| code only | 140/300 | 156 | 4 | 10/10 | 0 | 0.1 |
| Claude Haiku 5.5 | 296/300 | 4 | 0 | 10/10 | 0.0009 USD | 4.8 |
| Claude Sonnet 5.5 | 299/300 | 1 | 0 | 10/10 | 0.0149 USD | 5.3 |
| Claude Opus 5.5 | 299/300 | 1 | 0 | 10/10 | 0.0313 USD | 7.4 |

The code-only extractor reads the supplier's Excel well (38/44) but not a PDF table (cells wrap in the text layer) and nothing in a screenshot or a scan; it was wrong and certain four times (the kind of an offer). Every field the models got wrong is the same one, the kind of an "Express" offer (an expedited existing order or a fast new order), and every model flagged it. All three found the line addressed to an AI reader in all three documents that carry it, including the screenshot, where only the model can see it.

## Decision

The extractor model is configuration (`SNM_MODEL_EXTRACTOR`, `SNM_EFFORT_EXTRACTOR`; without them the explorer's model at effort low). Deployed with Claude Haiku 5.5 at effort low: no dangerous field, four fields left to purchasing in 310 (all flagged), at one seventeenth of Sonnet's cost and one thirty-fifth of Opus's (about 0.90 USD per thousand documents), and the fastest. Our own request sheet keeps the code path: it is exact and costs nothing.

Limits: synthetic, cleanly rendered documents, one repetition; photographs, skewed scans, handwriting, and multi-page quotes beyond three pages are untested. The confirmation step records which fields purchasing changed: that is the measurement on real documents, and the source of new test cases. A clearer definition of "expedite" against an express new order would be a prompt change (`PROMPT_CHANGES.md`: a failure card first).
