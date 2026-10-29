---
id: policy.site_isolation
title: Site isolation
type: policy
aliases: [cross-site comparison, all sites, global, company-wide]
status: reviewed
last_reviewed: 2026-09-24
---

# Site isolation (mandatory)

Round Rock, Longgang, and Dongguan differ in filters, time zones, calendars, automation, and volume. A finding for one site must never be applied to another site.

Rules:
1. Every multi-site answer includes a **per-site breakdown**. A company-wide total is allowed only in addition to the breakdown, and only for additive measures (counts, quantities in the same unit). Never add amounts in different currencies (USD and CNY).
2. Apply each site's own exclusions (see the site documents) before aggregating.
3. Use each site's local calendar day for daily metrics unless a definition says otherwise.
4. If the user asks to "compare sites", compare rates or normalized measures, not raw counts, and state the volume difference.
5. If a question names no site and the answer differs materially by site, answer per site or ask which site is meant.
