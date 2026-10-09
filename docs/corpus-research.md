# Legal / Procurement Text Corpora for Compliance-Aware RAG — Verified Findings

**Verification date:** 2026-10-09 · **Method:** HuggingFace API (`/api/datasets`, `/tree?recursive=true`, `datasets-server`), live HTTP probes, and actual file downloads + parsing. All sizes are **measured sum of real file bytes**, not card claims. All licenses read from card metadata / raw license files.

**Use case:** clause-level contract TEXT for RAG, jurisdiction-tagged, feeding B2B SOW drafting + audit against GDPR / EU AI Act / SEC / Delaware / CCPA.

---

## 1. Verdict table

Legend — **Clause text?** = does it contain retrievable contract/clause prose (vs. metadata only). **Verdict** = useful for *this* use case.

| Dataset | Exact size (measured) | Records | License | Format | Clause text? | Auth | Last update | Verdict |
|---|---|---|---|---|---|---|---|---|
| **CUAD** (`theatticusproject/cuad`) | **159.15 MB** (745 files); `CUAD_v1.json` 40.13 MB | **510 contracts**, 20,910 QA, 41 categories | **CC BY 4.0** ✅ | JSON, CSV, XLSX, PDF, TXT | **YES** — 26.8M chars full text; 3.62M chars clause spans | none | 2023-01-02 (frozen) | ✅ **PRIMARY** |
| **MAUD** (`theatticusproject/maud`) | **158.45 MB**; 3 CSVs 123.0 MB + 100 `.txt` 35.34 MB | **39,231 rows**, 100 contracts | **CC BY 4.0** ✅ | CSV + TXT | **YES** — clause text + labels | none | 2023-01-02 (frozen) | ✅ **USEFUL** |
| **LEDGAR** (`coastalcph/lex_glue`, cfg `ledgar`) | **27.65 MB** (subset); lex_glue 564.0 MB total | **80,000 rows**, 100 labels | **CC BY 4.0** ✅ | Parquet | **YES** — provision text + 100 categories | none | 2024-01-04 | ✅ **USEFUL** |
| **UmaiTech redlining** (`UmaiTech/legal-contract-gpt41-redlining-10k`) | **31.11 MB** (14 files) | **59,862 rows** (9,977 unique × 6 configs) | **CC BY 4.0** ✅ | Parquet | **YES** — + **`Jurisdiction:` label** | none | 2025-11-07 | ⚠️ **USEFUL but SYNTHETIC** |
| **TED bulk XML** (`ted.europa.eu/packages/…`) | **9.38 MB/day → 91 MB extracted**; **291.6–334.1 MB/month** | **1,763 notices/day** | EU reuse (CC BY 4.0 / COM_REUSE) | XML (UBL eForms) | ⚠️ Procedure text, **not clauses** | none | daily | ⚠️ **MARGINAL** |
| **TED Search API** (`api.ted.europa.eu/v3`) | JSON responses | 1,478,671 DEU notices all-history | EU reuse | JSON | only metadata + descriptions | none | live | ⚠️ **for discovery only** |
| **`laredoyin/eu-ai-act`** | **0.87 MB** | **112 articles** + annexes | Apache-2.0 ✅ | CSV | No — **regulation text** | none | 2024-11-08 | ✅ **obligations side** |
| **`AndreaSimeri/GDPR`** | **0.45 MB** | articles + recitals | Apache-2.0 ✅ | CSV | No — **regulation text** | none | 2024-07-05 | ✅ **obligations side** |
| **`Sebastyijan/gdpr-enforcement-sample`** | **0.41 MB** | **682 rows** | **CC0-1.0** ✅✅ | CSV | No — enforcement actions | none | 2026-02-17 | ✅ obligations side |
| **`dennlinger/eur-lex-sum`** | **3.65 GB** (76 files) | EU law + summaries | **CC BY 4.0** ✅ | JSON (per language, **incl. German 102 MB**) | No — **EU legislation** | none | 2024-09-11 | ✅ obligations side |
| **`coastalcph/multi_eurlex`** | **2.77 GB** (1 tar.gz) | EU legislation, multilingual | **CC BY-SA 4.0** ⚠️ | tar.gz | No — legislation | none | 2024-02-29 | ⚠️ ShareAlike |
| **SEC EDGAR EFTS** | JSON, 100 hits/page; **10,000-result cap** | "statement of work"+10-K → 3,736 hits | US public domain ✅ | JSON | **YES — via exhibit files** | none (UA only) | live | ✅ **KEY SOURCE** |
| **SEC EDGAR full-index** | `form.idx` **53.1 MB** (2025 Q1), 340,112 rows | **0 EX-10 rows** | US public domain ✅ | fixed-width text | **NO** | none (UA only) | quarterly | ❌ **cannot find contracts** |
| **`nguyenminh871/software_requirements`** | 0.04 MB | 61 rows | MIT | CSV | user stories | none | 2024-06-25 | ❌ too small |
| **`shinoo17/simple_user_stories`** | 0.03 MB | 693 rows | Apache-2.0 | TXT | user stories | none | 2024-10-18 | ❌ too small |
| **`udaykiran19491/…-tawos…`** | 14.18 MB | 22,459 rows | Apache-2.0 | JSON | **not requirements** | none | 2024-07-18 | ❌ regression set |
| **`weio-ops/sam-contract-opportunities`** | **117.0 MB** | **36,448 notices** | CC BY 4.0 ✅ | Parquet + CSV | **NO — notices only** | none | 2026-10-09 (daily) | ❌ **USELESS** |
| **`tenderguru/…-data-catalog`** | **37,928 bytes total** | **0 records** | "other"/commercial; LICENSE empty | JSON (catalog only) | **NO** | n/a | 2026-07-03 | ❌ **USELESS (lead-gen)** |
| **`joelparkerhenderson/statement-of-work`** | **34,815 bytes**, 4 files | **1 template** | **NO LICENSE** ❌ | Markdown | 1 doc, no clauses | none | pushed 2025-04-14 | ❌ **USELESS** |
| **Pile of Law** (`pile-of-law/pile-of-law`) | **44.14 GB** (122 files) | 10M–100M | **CC BY-NC-SA 4.0** ❌ | JSONL.xz | YES but **non-commercial** | none | 2026-07-18 | ❌ **license blocks** |
| **ContractNLI** (`kiddothe2b/contract-nli`) | **2.60 MB** | 20,107 rows | **CC BY-NC-SA 4.0** ❌ | JSONL (zip) | NDA clause excerpts | none | 2022-07-27 | ❌ **license blocks** |
| **`joelniklaus/online_terms_of_service`** | **9.71 MB** | 25,929 rows (~9.7% labeled) | **cc-by-nc-2.5** ❌ | JSONL | YES — ToS clauses | none | 2022-09-22 | ❌ **license blocks** |
| **`isaacus/gdpr-holdings-retrieval`** | **1.67 MB** | 500 corpus docs | **CC BY-NC-SA 4.0** ❌ | JSONL | No — decision summaries | none | 2025-10-23 | ❌ **not clauses + NC** |
| **ACORD** | — | — | — | — | — | — | — | ⚠️ **UNVERIFIED** |

---

## 2. Per-dataset notes

### CUAD — the only large, clean, commercially-licensed clause corpus found
- **URL:** `https://huggingface.co/datasets/theatticusproject/cuad` → `https://huggingface.co/datasets/theatticusproject/cuad/resolve/main/CUAD_v1/CUAD_v1.json`
- **Measured:** 159,150,185 bytes / 745 files. `CUAD_v1.json` = 40,128,638 bytes. `full_contract_pdf/` = 101,174,657 bytes / 510 files. `full_contract_txt/` = 11,125,239 bytes / **200 files only**. `master_clauses.csv` = 3,955,428 bytes. `label_group_xlsx/` = 1,550,235 bytes / 28 files.
- **Structure (parsed directly):** `{version: "aok_v1.0", data: [510 docs]}`. Each doc = `{title, paragraphs:[{context, qas}]}`. **1 paragraph per contract**, i.e. `context` is the **full contract text as one string**. 20,910 qas = 510 × 41. **6,702 answered, 14,208 `is_impossible`**, 13,823 answer spans. `answer_start` offsets: **13,823/13,823 verified correct (0 bad)**.
- **Volume:** full text **26,807,133 chars (~6.7M tokens)**; clause-level spans **3,621,887 chars (~905k tokens)**.
- **41 categories usable as metadata filters — YES**, verified: all 510 qas each. Counts (present/docs): License Grant 255, Cap On Liability 275, Audit Rights 214, Anti-Assignment 374, Governing Law 437, Insurance 166, Termination For Convenience 183, etc.
- **`master_clauses.csv` is the best RAG artifact:** 511 rows × 83 cols = `Filename` + 41 × (label col + `-Answer` col). One tidy row per contract → contract × clause-category × answer-text.
- **⚠️ Caveats:** (a) **US-only, English** — SEC Exhibit 10 filings; contains **zero GDPR / AI Act / CCPA text**. (b) Jurisdiction tagging is *not* ready-made: I found only **437/510** contracts with a Governing Law span, and **24 spans truncate before the state name** (e.g. `"…in accordance with the laws of the State"`). Keyword tally inside spans: New York 19, California 12, **Delaware 11**, Florida 9, Texas 9 — these are **undercounts**. (c) Only 200/510 contracts have `.txt`; the rest are PDF-only. (d) Last modified 2023-01-02 — frozen, but that is fine for a static corpus.

### MAUD — merger-agreement clause text
- **URL:** `https://huggingface.co/datasets/theatticusproject/maud`
- **Measured:** 158,445,226 bytes / 106 files. `MAUD_train.csv` 81,299,409 · `MAUD_dev.csv` 21,189,496 · `MAUD_test.csv` 20,550,295 · `MAUD_v1/contracts/` = **100 `.txt`, 35,338,407 bytes**.
- **Records:** **39,231** (datasets-server `num_rows`).
- **Fields (verified via first-rows):** `data_type, contract_name, text, answer, label, question, subquestion, text_type, id, category`.
- **Verdict:** genuinely clause-level with labels and 100 full contract texts. **But it is M&A merger agreements**, not services/SOW — domain mismatch for B2B SOW drafting.

### LEDGAR — 80,000 labeled provisions, CC BY 4.0
- **URL:** `https://huggingface.co/datasets/coastalcph/lex_glue` (config `ledgar`)
- **Measured:** LEDGAR subset **27,650,585 bytes** (train 20,897,973 / test 3,313,508 / validation 3,439,104). lex_glue total 563,964,254 bytes, 236,714 rows across all configs.
- **Verified content:** `text` = real provision prose (e.g. *"Except as otherwise set forth in this Debenture…"*), `label` = integer into a **100-name** list (Adjustments, Anti-Corruption Laws, Assignments, Confidentiality, Governing Laws, Indemnification, …).
- **Verdict:** solid clause *classification* corpus; labels are broad provision types, less granular than the 41 CUAD categories for compliance mapping. Source is also SEC exhibits → US/English.

### UmaiTech redlining — the only **jurisdiction-tagged** commercial-licensed clause set, but synthetic
- **URL:** `https://huggingface.co/datasets/UmaiTech/legal-contract-gpt41-redlining-10k`
- **Measured:** 31,106,825 bytes / 14 files. **59,862 rows** = 6 format configs × ~9,977 (alpaca, fim, harmony, llama_chat, openai_chat, qa).
- **Structure:** `instruction / input / output / metadata`. `input` = `Clause Category: … / Contract Type: … / Jurisdiction: … / Original Clause: …`; `output` = `Redlined Clause: … / Rationale: … / Specific Changes: …`.
- **Sampled 900 rows** (avg input 444 chars, avg output 1,124 chars):
  - **10 US jurisdictions:** New York 103, Illinois 101, Massachusetts 100, Nevada 95, **Delaware 95**, California 87, Georgia 86, Colorado 81, Texas 78, Florida 74
  - **4 clause categories:** liability 358, termination 278, governing law 179, warranty 85
  - **10 contract types:** license, employment, nda, development, service, supply, manufacturing, strategic alliance, distributor, consulting
- **⚠️ CRITICAL:** this is a **perfectly balanced 10×10×4 cross-product ≈ 9,977 rows → strong evidence of synthetic construction**, confirmed by the dataset name (`gpt41`) and its `synthetic-data` tag. The "original clauses" carry SEC-style redaction markers (`[* * *]`) suggesting derivation from real filings, but **provenance is unverifiable**. Treat as instruction-tuning / eval / jurisdiction-filter scaffolding, **not** as authentic clause language to retrieve from.
- **Why it matters anyway:** it is the only dataset found that gives you **Delaware-tagged clause text under CC BY 4.0** — directly relevant to the Delaware obligation in scope.

### TED — real text, wrong kind of text
- **Bulk (verified by HEAD, HTTP 200, no auth):**
  - Daily: `https://ted.europa.eu/packages/daily/{YYYYNNN}` → `202500001` = **9,375,807 bytes (9.38 MB)** → **91 MB extracted, 1,763 XML files**; `202400001` = 9,083,427 bytes
  - Monthly: `https://ted.europa.eu/packages/monthly/{YYYY-M}` → `2025-1` = **334,137,179 bytes (334.1 MB)**; `2024-1` = **291,615,691 bytes (291.6 MB)**
  - Direct notice: `https://ted.europa.eu/{lang}/notice/{pubnum}/{html|pdf|pdfs}` and `…/notice/{pubnum}/xml`
- **API (authoritative):** OpenAPI 3.1.0 "TED Search API" at `https://ted.europa.eu/docs/v3`, single path `POST /v3/notices/search`. **`securitySchemes: null` and global `security: null` → no authentication.** Confirmed live without any key.
- **Live cross-validation:** `publication-date=20250102` → **1,763** (exactly matches the 1,763 XMLs); `… AND official-language=DEU` → **103** (exactly matches my XML language count); `official-language=DEU` → **1,478,671** all-history; `classification-cpv=48000000` → 252,853 (hierarchical/approximate — returned notices without that exact code).
- **Filtering:** `official-language` (alias `OL`, BT-702-notice) and `classification-cpv` (alias `PC`) are **API filter fields — no parsing required**. 1,340 searchable fields published as CSV at `https://docs.ted.europa.eu/ODS/latest/reuse/_attachments/List_of_search_fields.csv` (90,369 bytes).
- **What the text is:** 27,724,276 chars/day (~6.9M tokens), avg 15,726 chars/notice. `<Description>` = 18.1M chars across 38,537 instances, whose parents are `SubordinateAwardingCriterion` (2,118), `SpecificTendererRequirement` (1,855), `ProcurementProject` (1,710), `SelectionCriteria` (808), `ContractExecutionRequirement` (261). `<Note>` = 3.3M chars, **hard-truncated at 10,000 chars**. This is **tender procedure text** — award criteria, exclusion grounds, evidence documents — **not commercial contract clauses**. No liability caps, no IP ownership, no DPA/GDPR clauses.
- **Language reality check:** of 1,763 notices — POL 510, FRA 295, **DEU 103**, RON 92, ITA 86, SPA 77, …, **ENG only 29**. **ENG+DEU = 132 notices = 1,310,132 chars (7.5% of the text).** Each notice exists in **one** language. The bulk XML is NOT multilingual; use the API's `official-language` filter or parse `NoticeLanguageCode`.
- **License:** data.europa.eu distributions are tagged **CC_BY_4_0** (2020–2022) and **COM_REUSE** (European Commission reuse notice, Decision 2011/833/EU) for older years. TED also publishes a Copyright notice.
- **⚠️ Broken route:** the data.europa.eu TED **CSV subset zips now return HTTP 401** (e.g. `https://data.europa.eu/api/hub/store/data/ted-contract-notices-2022.zip` → redirects to `…/data-management/store/api/legacy/…` → **401**). The convenient CSV path appears closed; **XML packages are the working route**.

### SEC EDGAR — EFTS is the real door, bulk indexes are not
- **✅ EFTS full-text search works:** `https://efts.sec.gov/LATEST/search-index?q=%22statement+of+work%22&forms=10-K` → HTTP 200, 60,273 bytes, **3,736 hits**, 100 per page, `from=` pagination works. Requires a descriptive `User-Agent`; no other auth. **Result cap = 10,000** (verified: `"limitation of liability"` returns `total.value = 10000`).
- **✅ EFTS returns exhibit documents directly.** `_id` values are `<accession>:<exhibit filename>`, e.g. `0001144204-13-042758:v351020_ex10-2.htm`, `0001193125-04-053844:dex1034.htm`, `0001171520-11-000234:ex10-32.htm`, `0000892569-03-001660:a91437exv10w67.txt`. **These are the Exhibit 10.* contracts.** You can filter by filename pattern client-side.
- **❌ The bulk index does NOT contain contracts.** `https://www.sec.gov/Archives/edgar/full-index/2025/QTR1/form.idx` = **53,059,021 bytes, 340,112 rows, and ZERO `EX-10` rows** (2024 QTR1 = 57,770,533 bytes). `form.idx` lists *filings by form type*; exhibits are documents **inside** filings, not separate filings. So **there is no bulk archive of Exhibit 10.* text** — you must walk index → filing → exhibit.
- **✅ Filing-level index works:** `https://www.sec.gov/Archives/edgar/data/839470/000155837021000999/index.json` → HTTP 200, 10,927 bytes, `directory.item[]` with 110 entries. Exhibit names present with sizes: `wwr-20201231ex1016d2625.htm` (**192,817 bytes** — an EX-10.16 contract), `ex2115…`, `ex311…`. **⚠️ The `type` field is just an icon name (`text.gif`), NOT the exhibit type — exhibit identity is only in the filename and must be regex-parsed (`ex10`, `ex-10`, `exv10w`, `dex10`).**
- **Financial Statement Data Sets:** `https://www.sec.gov/files/dera/data/financial-statement-data-sets/2024q1.zip` → HTTP 200, **124,336,804 bytes**. These are DERA XBRL numeric datasets — **no exhibit/contract text** (verified existence and size; contents not enumerated).
- **Verdict:** EFTS is the single best *authentic US contract* pipeline for this use case, and it is free, unauthenticated, and public-domain. Cost is per-document fetching, not bulk.

### Backlog side (software requirements / user stories) — effectively absent
- `nguyenminh871/software_requirements`: MIT, **36,505 bytes**, **61 rows** (one CSV).
- `shinoo17/simple_user_stories`: Apache-2.0, **32,496 bytes**, **693 rows**, 6 TXT files for sample systems.
- `udaykiran19491/user-stories-from-tawos-for-llm-fine-tuning`: Apache-2.0, 14,180,612 bytes, **22,459 rows** — but rows are `{instruction, input, output}` where `output` is a **resolution time in minutes** (e.g. `"11833.0"`). Only **538 rows (2.4%)** match typical `"As a … I'd like …"` phrasing. It is a **Jira resolution-time regression set**, not a requirements corpus. Total `input` text 9,491,754 chars is entangled with that task.
- **Conclusion: no substantial, well-licensed software-requirements / user-story corpus exists on HuggingFace.** The backlog side will need synthetic generation or your own authoring.

---

## 3. Explicitly useless — and why

| Dataset | Why it is useless here |
|---|---|
| `weio-ops/sam-contract-opportunities` | 117 MB and 36,448 records looks promising but every record is a **notice**: notice ID, agency, NAICS/PSC, deadline, set-aside, award fields, plus a short `Description` blurb. **No contract or clause text.** Metadata-only → cannot support clause retrieval. |
| `tenderguru/tenderguru-procurement-data-catalog` | **Not a dataset.** 37,928 bytes total, and `LICENSE` is a **0-byte file**. Card states verbatim: *"No procurement records or sample data are hosted in this repository."* Access is commercial via a contact form, and the license summary **prohibits** *"Publishing OCR text, document fragments or an RAG corpus in open access."* Lead-gen catalog. |
| `joelparkerhenderson/statement-of-work` | 4 files / 34,815 bytes; the whole corpus is **one 28 KB README** with ~25 SOW section headings, and it says *"Examples are by ChatGPT."* GitHub's license API returns **"Not Found"** → **no license, so all rights reserved and not redistributable**. Only conceivable use: section taxonomy for prompt structure. |
| `pile-of-law/pile-of-law` | 44.14 GB but **CC BY-NC-SA 4.0 → non-commercial**, which blocks a commercial B2B product. Its `atticus_contracts` shards (~5.4 GB across 4 train + 1 validation shards) are just **CUAD redistributed** — get CUAD directly under CC BY 4.0 instead. |
| `kiddothe2b/contract-nli` | **CC BY-NC-SA 4.0 → non-commercial.** Also only clause *excerpts* (premises), not full NDAs — the upstream authors did not release the contract texts. Note the canonical id `stanfordnlp/contract_nli` **does not exist** (401/404). |
| `joelniklaus/online_terms_of_service` | **cc-by-nc-2.5 → non-commercial** (stated in the README Licensing Information section; card just says "other"). Also only **417/4,297 (9.7%)** of the test split carries any clause label (~2,500 labeled of 25,929 total), avg tagged clause 231 chars, and it covers consumer ToS with 4 languages — not B2B SOWs. |
| `isaacus/gdpr-holdings-retrieval` | 1.67 MB / 500 corpus docs of **GDPR enforcement decision summaries** (~1,712 chars each), **CC BY-NC-SA 4.0**. Not clause text, non-commercial, and explicitly a retrieval *benchmark*. |
| `nguyenminh871/…`, `shinoo17/…`, `udaykiran19491/…` | Sizes 0.03–14 MB; either 61–693 rows (too small to matter) or a resolution-time regression set mislabeled as user stories. |
| SEC `form.idx` as a contract source | 340,112 rows and **zero EX-10** entries — it indexes filings, not their exhibits. |
| data.europa.eu TED CSV zips | **HTTP 401** as of verification; the previously convenient bulk CSV route is no longer anonymously downloadable. |

---

## 4. Bottom line for this system

1. **Clause-level B2B contract text that is commercially licensed and jurisdiction-taggable: CUAD + MAUD + LEDGAR**, all CC BY 4.0, ~345 MB combined, ~7M+ tokens. This is a **>250× upgrade** over 26k tokens. CUAD's `master_clauses.csv` is the fastest integration (510 rows × 41 labeled clause slots).
2. **Jurisdiction tags must be built, not downloaded.** CUAD has no reliable jurisdiction field (437 Governing Law spans, 24 truncated before the state name). The only Delaware-tagged clause data found is `UmaiTech/…-redlining-10k` (CC BY 4.0) — **but it is synthetic**, so use it for eval/filter scaffolding, never as authoritative clause language.
3. **For authentic, jurisdiction-rich US contracts, EDGAR EFTS is the best free pipeline** — unauthenticated, public domain, and it returns Exhibit 10.* filenames directly. Budget for per-document fetches; there is no bulk exhibit archive.
4. **Nothing publicly downloadable provides GDPR / EU AI Act / CCPA *clause* text.** The EU-side datasets that exist are **regulation text** (AI Act articles, GDPR articles + recitals, EUR-Lex) — perfect for the *obligation* side of the audit, not for retrieving precedent clause language. Searches for "SCC"/"data processing agreement" returned only bioinformatics and ML-pipeline noise.
5. **TED is the wrong tool for clause retrieval.** It yields ~6.9M tokens/day of *tender procedure* text, only ~7.5% of it in English or German, with no commercial clauses. Use the API for procurement-context/discovery, not as a clause corpus.
6. **The backlog side has no off-the-shelf corpus** — plan to author or synthesize user stories.

---

## 5. Could not verify / open items

- **ACORD** — no HF dataset found at `ColumbiaNLP/ACORD` or `acord/lex_glue` (both 401/404). Existence, size, license **UNVERIFIED**. (ACORD's labelled data may not be publicly redistributable.)
- **`stanfordnlp/contract_nli`** — id returns 401/404; treated as non-existent. License/reason for withheld NDA text not confirmed from a primary source.
- **UmaiTech redlining provenance** — whether the "original clauses" derive from real filings is **not verifiable** from the card or files.
- **`suhas-km/EU-AI-Act-Flagged`** (MIT, 0.21 MB, 206 train rows; fields text/violation/category/severity/articles/explanation/context) and **`AYI-NEDJIMI/ai-act-en`** (Apache-2.0, 0.35 MB, incl. `high_risk_requirements.json`, `compliance_checklist.json`) — sizes/licenses verified, but **content quality and whether synthetic is unverified**. Potentially useful for audit-side eval; both tiny.
- **DERA Financial Statement Data Sets** — verified HTTP 200 and 124,336,804 bytes; did **not** enumerate contents to confirm absence of exhibits (inferred from DERA's documented scope).
- **EDGAR redistribution terms** — the exact SEC statement URL on reuse/rate limits was not captured verbatim; public-domain status of US government works applies, but filings are submitted by companies. Recommend confirming before redistribution.
- **Full-history EX-10.* volume / cost estimate** — not enumerated. Only one quarter's `form.idx` was parsed (340,112 rows, 0 EX-10) plus live EFTS hit counts.
- **TED full-history size** — measured 1 daily package and HEAD'd 2 monthly packages; per-month totals vary (291.6–334.1 MB observed). Total archive size not computed.
- **data.europa.eu TED CSVs** — verified 401; did **not** determine whether an authenticated or alternate route exists.
- **`unfair_tos` license chain** — `coastalcph/lex_glue` is CC BY 4.0 and contains the `unfair_tos` subset (9,414 rows, 865,604 bytes; `text` + up to 8 labels: Limitation of liability, Unilateral termination, Unilateral change, Content removal, Contract by using, Choice of law, Jurisdiction, Arbitration). But only **12%** of sampled rows carry a label, and the underlying ToS data originates from Lippi/Drawzeski et al. whose own release is NC — the CC BY 4.0 chain is **ambiguous; confirm before commercial use**.
