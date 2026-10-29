# Case flow simulation: flow-local-small

20 cases played end to end on `local store out/sim/flow`: 15 order cases (15 decided, 9 with a proposal inside the mandate) and 5 transfer cases (published by the system); 0 errors, **0 invariant violations**. Every order decision was recorded by: human.

Invariants checked after each case: sequence numbers 1..n and earlier events unchanged; every event's actor allowed and its transition legal; every decision by a person; outbound text within the outward fields; nothing internal on the supplier view; every file stored with its hash and size and every inbound sheet's options recorded; every proposal under a mandate flagged, with flags that reproduce.

| Case | Kind | Supplier behavior | Proposals (in mandate) | Recommended | Decision | By | Final state | Events | Files | Seconds | Problems |
|---|---|---|---|---|---|---|---|---|---|---|---|
| C-US10-40000005-SH-20261214 | order | inside | 1 (1) | P-1-9-C-US10-40000005-SH-20261214 | P-1-9-C-US10-40000005-SH-20261214 | human | closed | 12 | 2 | 0.46 |  |
| C-US10-40000014-WA-20261221 | order | late | 1 (0) |  | new_order | human | closed | 12 | 2 | 0.41 |  |
| C-US10-40000018-SH-20261023 | order | two_options | 2 (1) | P-1-9-C-US10-40000018-SH-20261023 | P-1-9-C-US10-40000018-SH-20261023 | human | closed | 12 | 2 | 0.54 |  |
| C-US10-40000030-SH-20261110 | order | decline | 1 (0) |  | new_order | human | closed | 12 | 2 | 0.43 |  |
| C-US10-40000047-SH-20261026 | order | message_first | 1 (1) | P-1-10-C-US10-40000047-SH-20261026 | P-1-10-C-US10-40000047-SH-20261026 | human | closed | 13 | 2 | 0.55 |  |
| C-US10-40000048-WA-20261116 | order | inside | 1 (1) | P-1-9-C-US10-40000048-WA-20261116 | P-1-9-C-US10-40000048-WA-20261116 | human | closed | 12 | 2 | 0.49 |  |
| C-US10-40000054-SH-20261028 | order | late | 1 (0) |  | expedited_new_order | human | closed | 12 | 2 | 0.59 |  |
| C-US10-40000055-WA-20261214 | order | two_options | 2 (1) | P-1-9-C-US10-40000055-WA-20261214 | P-1-9-C-US10-40000055-WA-20261214 | human | closed | 12 | 2 | 0.47 |  |
| C-US10-40000061-WA-20261026 | order | decline | 1 (0) |  | new_order | human | closed | 12 | 2 | 0.54 |  |
| C-US10-40000075-WA-20261130 | order | message_first | 1 (1) | P-1-10-C-US10-40000075-WA-20261130 | P-1-10-C-US10-40000075-WA-20261130 | human | closed | 13 | 2 | 0.59 |  |
| C-US10-40000080-SH-20261215 | order | inside | 1 (1) | P-1-9-C-US10-40000080-SH-20261215 | P-1-9-C-US10-40000080-SH-20261215 | human | closed | 12 | 2 | 0.65 |  |
| C-US10-40000095-SH-20261127 | order | late | 1 (0) |  | new_order | human | closed | 12 | 2 | 0.55 |  |
| C-US10-40000100-WA-20261109 | order | two_options | 2 (1) | P-1-9-C-US10-40000100-WA-20261109 | P-1-9-C-US10-40000100-WA-20261109 | human | closed | 12 | 2 | 0.59 |  |
| C-US10-40000105-SH-20261127 | order | decline | 1 (0) |  | new_order | human | closed | 12 | 2 | 0.57 |  |
| C-US10-40000107-SH-20261124 | order | message_first | 1 (1) | P-1-10-C-US10-40000107-SH-20261124 | P-1-10-C-US10-40000107-SH-20261124 | human | closed | 13 | 2 | 0.64 |  |
| C-US10-40000034-PU-20261026 | transfer | publish | 0 (0) |  | transfer | system | closed | 4 | 0 | 0.2 |  |
| C-US10-40000041-PU-20261207 | transfer | publish | 0 (0) |  | transfer | system | closed | 4 | 0 | 0.21 |  |
| C-US10-40000113-PU-20261130 | transfer | publish | 0 (0) |  | transfer | system | closed | 4 | 0 | 0.13 |  |
| C-US10-40000123-PU-20261026 | transfer | publish | 0 (0) |  | transfer | system | closed | 4 | 0 | 0.21 |  |
| C-US10-40000132-PU-20261026 | transfer | publish | 0 (0) |  | transfer | system | closed | 4 | 0 | 0.14 |  |
