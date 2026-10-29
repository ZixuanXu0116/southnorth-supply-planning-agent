---
id: policy.people_analytics
title: People analytics
type: policy
aliases: [operator performance, productivity by person, picker ranking, who is slowest]
status: reviewed
last_reviewed: 2026-09-24
---

# People analytics (mandatory)

Operator data is pseudonymized (`OP-nnnnn`, SAP user names) and still sensitive.

Rules:
1. Default granularity is **team and shift**. Report individual operators only when the active role is a site manager **and** the operator belongs to that manager's site.
2. Never produce rankings, "worst performer" lists, or disciplinary recommendations about individuals. Offer team/shift-level analysis instead.
3. Exclude system users (`WF-BATCH`, `AS-INTERFACE`, `RFC-EWM`) from any per-person or productivity measure; they are not people.
4. Do not attempt to re-identify pseudonymized operators.
