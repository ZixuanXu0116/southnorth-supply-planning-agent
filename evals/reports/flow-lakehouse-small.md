# Case flow simulation: flow-lakehouse-small

7 cases played end to end on `lakehouse case store`: 5 order cases (5 decided, 3 with a proposal inside the mandate) and 2 transfer cases (published by the system); 0 errors, **0 invariant violations**. Every order decision was recorded by: human.

Invariants checked after each case: sequence numbers 1..n and earlier events unchanged; every event's actor allowed and its transition legal; every decision by a person; outbound text within the outward fields; nothing internal on the supplier view; every file stored with its hash and size and every inbound sheet's options recorded; every proposal under a mandate flagged, with flags that reproduce.

| Case | Kind | Supplier behavior | Proposals (in mandate) | Recommended | Decision | By | Final state | Events | Files | Seconds | Problems |
|---|---|---|---|---|---|---|---|---|---|---|---|
| C-US10-40000018-SH-20261023 | order | inside | 1 (1) | P-1-9-C-US10-40000018-SH-20261023 | P-1-9-C-US10-40000018-SH-20261023 | human | closed | 12 | 2 | 59.68 |  |
| C-US10-40000030-SH-20261109 | order | late | 1 (0) |  | new_order | human | closed | 14 | 2 | 53.5 |  |
| C-US10-40000047-SH-20261026 | order | two_options | 2 (1) | P-1-9-C-US10-40000047-SH-20261026 | P-1-9-C-US10-40000047-SH-20261026 | human | closed | 12 | 2 | 57.27 |  |
| C-US10-40000048-WA-20260921 | order | decline | 1 (0) |  | new_order | human | closed | 15 | 2 | 52.29 |  |
| C-US10-40000054-SH-20261028 | order | message_first | 1 (1) | P-1-10-C-US10-40000054-SH-20261028 | P-1-10-C-US10-40000054-SH-20261028 | human | closed | 13 | 2 | 60.53 |  |
| C-US10-40000034-PU-20261026 | transfer | publish | 0 (0) |  | transfer | system | closed | 4 | 0 | 9.83 |  |
| C-US10-40000041-PU-20261207 | transfer | publish | 0 (0) |  | transfer | system | closed | 4 | 0 | 10.88 |  |
