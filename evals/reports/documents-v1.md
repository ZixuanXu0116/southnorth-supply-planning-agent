# Supplier documents: code-only extractor vs. model extractor

Documents: 24 (`evals/documents/v1`); model effort `low`; every draft passed through the same code checks before scoring. Safe = correct or marked uncertain; dangerous = wrong and marked certain.

| Arm | Fields correct | Left for purchasing to fill | Dangerous | Ambiguities flagged | Option count right | Injection flagged | Cost per document | Seconds per document |
|---|---|---|---|---|---|---|---|---|
| rules | 140/300 | 156 | 4 | 10/10 | 11/24 | 1/3 | 0.0000 | 0.1 |
| claude-haiku-5-5 | 296/300 | 4 | 0 | 10/10 | 24/24 | 3/3 | 0.0009 | 4.8 |
| claude-sonnet-5-5 | 299/300 | 1 | 0 | 10/10 | 24/24 | 3/3 | 0.0149 | 5.3 |
| claude-opus-5-5 | 299/300 | 1 | 0 | 10/10 | 24/24 | 3/3 | 0.0313 | 7.4 |

Fields right (the value, or an ambiguity flagged) by format:

| Arm | pdf | png | scan | xlsx |
|---|---|---|---|---|
| rules | 61/122 | 44/122 | 7/22 | 38/44 |
| claude-haiku-5-5 | 120/122 | 121/122 | 22/22 | 43/44 |
| claude-sonnet-5-5 | 122/122 | 122/122 | 22/22 | 43/44 |
| claude-opus-5-5 | 122/122 | 122/122 | 22/22 | 43/44 |

Dangerous fields (wrong and marked certain):

- rules, s02-three-options.pdf: 1.kind='partial' (truth 'new_order')
- rules, s09-prose-split.pdf: 1.kind='new_order' (truth 'partial'); 1.arrival_date='2026-11-20' (truth '2026-12-04')
- rules, s10-express-fee.pdf: 1.kind='expedite' (truth 'new_order')

## Evidence check fix and false alarms (2026-11-02)

A test quote in the App (C-US10-40000193) showed correct dates flagged "the evidence is not in the document's text": a PDF table's text layer wraps a cell mid-value ("2026-" / "11-24"). The check now compares without whitespace, and the scoring counts false alarms (a stated value read right but marked uncertain), which the first run did not show. Rerun on the same 24 documents, extractor Claude Haiku 5.5 at effort medium (batch `documents-v1b-checkfix`):

| Arm | Fields correct | Correct but flagged | Left for purchasing | Dangerous | Cost per document |
|---|---|---|---|---|---|
| code only | 140/300 | 0 | 156 | 4 | 0 |
| Claude Haiku 5.5, medium | 298/300 | 32 | 2 | 0 | 0.0010 USD |

Of the 32 false alarms, 24 come from the model's own caution (14 of them the offer's kind, which a quote rarely names) and 8 from the evidence check (a paraphrased quote). Purchasing confirms them with one look; a clearer rule for the kind would be a prompt change (`PROMPT_CHANGES.md`: a failure card first).
