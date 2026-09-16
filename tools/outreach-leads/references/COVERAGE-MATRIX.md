---
type: source
name: "Coverage matrix"
---

# Vertical coverage matrix

Machine thresholds: `references/coverage.json` (pack) and mirrored under vault `Sources/COVERAGE-MATRIX.md` when you copy it. Blind = dumps alone under-count; run specialty-directory.

| Category | Primary dumps | Specialty / rescue | Status | min_notes | min_site_fill | specialty_required |
|---|---|---|---|---|---|---|
| Salon | TDLR | — | strong | 50 | 0.10 | no |
| Logistics | FMCSA/USDOT | — | strong | 50 | 0.10 | no |
| GC / trades | TDLR, OSM craft=* | — | medium–strong | — | — | no |
| Restaurant / Retail / Auto | OSM, Comptroller, TABC | Chamber/BBB | medium | — | — | no |
| Childcare | HHSC | — | medium | — | — | no |
| Church / Nonprofit | OSM worship, IRS EO, Comptroller 813 | — | medium | 20 | 0.10 | no |
| Fuel / Oilfield / Ag / Warehouse | Vertical dumps, FMCSA | Archive rescue | medium | — | — | no |
| **Law / PI / trial** | Comptroller 541110 (sparse), OSM office=lawyer (thin) | **Justia, FindLaw, Archive** | specialty required | 80 (PI: 50) | 0.25 | **yes** |
| **Law / family** | — | **Justia family-law Texas** | specialty | — | — | soft |
| **Law / immigration** | — | **Justia immigration-law Texas** | specialty | — | — | soft |
| **Medical** | OSM doctors/clinic | **OSM + NPPES NPI-2** | specialty required | 30 | 0.15 | **yes** |
| **Dentist** | OSM amenity=dentist | **OSM + TSBDE CSV** (TSBDE TLS may fail) | specialty required | 40 | 0.10 | **yes** |
| **Accounting** | Comptroller 541211/213/214 + OSM office=accountant|tax_advisor | **specialty-directory** | specialty required | 40 | 0.10 | **yes** |
| **Insurance** | OSM office=insurance + Comptroller 5242xx (thin) | **specialty-directory** | thin → specialty | 25 | 0.10 | **yes** |
| **Real estate** | OSM estate_agent/realtor + TREC Broker Company SODA + Comptroller 5312xx | **specialty-directory** | specialty required | 20 | 0.10 | **yes** |
| **HVAC** | OSM craft/shop=hvac + TDLR A/C Contractor + Comptroller 23822x | **specialty-directory** | specialty required | 25 | 0.10 | **yes** |
| **Roofing** | OSM craft=roofer + Comptroller 23816x | **specialty-directory** | thin → specialty | 15 | 0.10 | **yes** |
| **Engineering** | OSM office=engineer|architect + Comptroller 5413xx | **specialty-directory** (TBPELS roster ZIP = no city filter) | thin → specialty | 15 | 0.10 | **yes** |

## Blind-spot gate

1. Count notes in `category: "[[Law]]"` (wikilink) and `practice:` when used.
2. If specialty_required and you only ran bulk-dump → NOT COVERED.
3. Empty `website: ""` counts as missing for site fill %.
4. Write the gap into the prove log.

CLI: `python3 scripts/outreach_leads.py coverage-check --category Law [--practice pi|family|immigration]`
CLI: `python3 scripts/outreach_leads.py specialty-directory --vertical accounting|dentist|insurance|pi|real-estate|hvac|roofing|medical|engineering|family|immigration --geo texas`
