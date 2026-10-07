"""Bundled legal compliance corpus for the SOWSprint compliance-aware RAG pipeline.

This module is a standalone data module: it imports nothing from the sowsprint
package so that it can be loaded independently by the indexing/ingestion layer.

The corpus holds adapted legal source material from the Atticus Project CUAD
dataset and from the Pile of Law, covering EU and US jurisdictions. Every entry
carries a ``jurisdiction`` field with the exact value ``"EU"`` or ``"US"`` which
the Qdrant collection uses as a mandatory metadata filter.

Entry schema::

    {
        "id": str,            # unique, lowercase, hyphenated, eu-/us- prefixed
        "jurisdiction": str,  # "EU" or "US"
        "source": str,        # attribution; always contains "(adapted)"
        "doc_type": str,      # contract_clause | statute | regulation |
                              # case_note | playbook | guidance
        "title": str,
        "text": str,          # 150-350 words of substantive legal prose
        "tags": list[str],    # 3-6 lowercase search keywords
        "risk_level": str,    # low | medium | high
    }
"""

from __future__ import annotations

from typing import Any

CORPUS_VERSION = "1.0.0"

CORPUS: list[dict[str, Any]] = [
    # ------------------------------------------------------------------
    # EU — data protection (GDPR)
    # ------------------------------------------------------------------
    {
        "id": "eu-gdpr-processor-obligations",
        "jurisdiction": "EU",
        "source": "CUAD v1 — Data Processing Agreement, cl. 4.2 (adapted)",
        "doc_type": "contract_clause",
        "title": "Processor obligations and documented instructions",
        "text": (
            "4.2 Processor Obligations. The Processor shall: (a) process the Personal Data only "
            "on documented instructions from the Controller, including with regard to transfers of "
            "Personal Data to a third country, and shall immediately inform the Controller if, in "
            "its opinion, an instruction infringes Regulation (EU) 2016/679 or other Union or "
            "Member State data protection provisions; (b) ensure that persons authorised to process "
            "the Personal Data have committed themselves to confidentiality or are under an "
            "appropriate statutory obligation of confidentiality; (c) implement the technical and "
            "organisational measures required by Article 32, including encryption of Personal Data "
            "at rest and in transit, pseudonymisation where feasible, and role-based access control; "
            "(d) assist the Controller by appropriate technical and organisational measures, insofar "
            "as this is possible, in the fulfilment of the Controller's obligation to respond to "
            "requests for exercising the data subject's rights; (e) assist the Controller in ensuring "
            "compliance with Articles 32 to 36, taking into account the nature of the processing and "
            "the information available to the Processor; (f) at the choice of the Controller, delete "
            "or return all Personal Data after the end of the provision of services relating to "
            "processing, and delete existing copies unless Union or Member State law requires "
            "storage; and (g) make available to the Controller all information necessary to "
            "demonstrate compliance with Article 28 and allow for and contribute to audits, "
            "including inspections, conducted by the Controller or another auditor mandated by the "
            "Controller. The Processor shall notify the Controller without undue delay if it "
            "becomes aware that it can no longer comply with this Clause 4.2."
        ),
        "tags": ["gdpr", "personal data", "processor", "instructions", "article 28", "audit"],
        "risk_level": "medium",
    },
    {
        "id": "eu-gdpr-controller-processor-allocation",
        "jurisdiction": "EU",
        "source": "Pile of Law — EDPB Guidelines 07/2020 on controller and processor (adapted)",
        "doc_type": "guidance",
        "title": "Allocating controller and processor roles in a DPA",
        "text": (
            "Guidance note on the boundary between controller and processor, which determines how "
            "responsibility is allocated in a data processing agreement. The controller determines "
            "the purposes and essential means of processing; the processor processes only on the "
            "controller's behalf. Where two or more controllers jointly determine the purposes and "
            "means, they are joint controllers under Article 26 and must transpose the essence of "
            "the arrangement into a transparent agreement, make it available to data subjects and "
            "designate a contact point; each joint controller remains liable to data subjects for "
            "the full damage caused. Drafters should therefore record in the recitals of the DPA "
            "which party decides why the processing happens, who determines the categories of data "
            "and the retention periods, who responds to data subject requests and who handles "
            "supervisory authority enquiries. A party that processes data for its own purposes, for "
            "example a vendor that enriches a customer list for its own marketing or trains general "
            "models on customer content, steps outside the processor role and becomes a controller "
            "for that activity, requiring a separate legal basis and a separate transparency notice. "
            "Under Article 28(10), a processor that goes beyond the controller's instructions and "
            "determines the purposes and means of processing is treated as a controller for that "
            "processing. The allocation should be revisited whenever the service description changes, "
            "in particular when a SaaS provider introduces analytics, benchmarking or model training "
            "features that reuse customer content."
        ),
        "tags": ["gdpr", "controller", "processor", "joint controllership", "edpb", "roles"],
        "risk_level": "medium",
    },
    {
        "id": "eu-gdpr-subprocessor-authorisation",
        "jurisdiction": "EU",
        "source": "CUAD v1 — Data Processing Agreement, cl. 9.1 (adapted)",
        "doc_type": "contract_clause",
        "title": "Sub-processor authorisation, notice and objection",
        "text": (
            "9.1 Sub-processors. The Controller grants the Processor a general written authorisation "
            "to engage sub-processors, subject to the following conditions: (a) the Processor shall "
            "maintain an up-to-date list of sub-processors identifying the entity, its location of "
            "processing and the nature of the processing, and shall make that list available to the "
            "Controller on request and at the address notified to the Controller; (b) the Processor "
            "shall give the Controller at least thirty days prior written notice of the intended "
            "addition or replacement of a sub-processor, together with the information reasonably "
            "necessary to assess the change; (c) the Controller may object in writing within that "
            "period on reasonable data protection grounds, and the parties shall discuss in good "
            "faith a commercially reasonable alternative; if no alternative can be agreed, the "
            "Controller may terminate the affected services without liability on thirty days written "
            "notice; (d) the Processor shall impose on each sub-processor, by written contract, data "
            "protection obligations materially equivalent to those in this Agreement, in particular "
            "sufficient guarantees to implement appropriate technical and organisational measures; "
            "and (e) where a sub-processor fails to fulfil its data protection obligations, the "
            "Processor shall remain fully liable to the Controller for the performance of those "
            "obligations. Sub-processors established outside the European Economic Area may only be "
            "engaged in accordance with Clause 10 (International Transfers)."
        ),
        "tags": ["gdpr", "sub-processor", "data processing agreement", "objection", "flow-down"],
        "risk_level": "medium",
    },
    {
        "id": "eu-gdpr-international-transfers-scc",
        "jurisdiction": "EU",
        "source": "Pile of Law — Commission Implementing Decision (EU) 2021/914, cl. 14-15 (adapted)",
        "doc_type": "regulation",
        "title": "International transfers, standard contractual clauses and transfer impact assessment",
        "text": (
            "Article 46(2)(c) of Regulation (EU) 2016/679 permits transfers of personal data to a "
            "third country in the absence of an adequacy decision under Article 45 only where the "
            "exporter and importer have entered into standard data protection clauses adopted by the "
            "Commission. Commission Implementing Decision (EU) 2021/914 sets out four modules: "
            "controller to controller, controller to processor, processor to processor and processor "
            "to controller. Clause 14 of the standard contractual clauses requires the parties to "
            "warrant that they have no reason to believe that the laws and practices of the third "
            "country prevent the importer from complying, and to document that assessment; Clause 15 "
            "obliges the importer to notify the exporter of any government access request and, where "
            "legally permitted, of the lawful basis relied on, and to challenge unlawful requests. "
            "Following the judgment of the Court of Justice in Case C-311/18 (Schrems II), the "
            "parties must conduct and document a transfer impact assessment and adopt supplementary "
            "measures, for example strong end-to-end encryption where keys remain with the exporter, "
            "pseudonymisation, split processing or EU-based key custody, wherever the third country "
            "regime does not provide essentially equivalent protection. Where an importer is subject "
            "to a third country law that prevents compliance, it must notify the exporter and suspend "
            "the transfer."
        ),
        "tags": ["gdpr", "international transfers", "scc", "schrems ii", "transfer impact assessment"],
        "risk_level": "high",
    },
    {
        "id": "eu-gdpr-breach-notification-72-hours",
        "jurisdiction": "EU",
        "source": "Pile of Law — GDPR Articles 33-34 (adapted)",
        "doc_type": "statute",
        "title": "Personal data breach notification within seventy-two hours",
        "text": (
            "Article 33(1) of the GDPR requires the controller to notify the competent supervisory "
            "authority of a personal data breach without undue delay and, where feasible, not later "
            "than seventy-two hours after having become aware of it, unless the breach is unlikely to "
            "result in a risk to the rights and freedoms of natural persons. Article 33(3) specifies "
            "the minimum content: a description of the nature of the breach including, where possible, "
            "the categories and approximate number of data subjects and records concerned; the name "
            "and contact details of the data protection officer or other contact point; the likely "
            "consequences of the breach; and the measures taken or proposed to address it, including "
            "mitigation. Where notification is not made within seventy-two hours it must be "
            "accompanied by reasons for the delay. Processors must notify controllers without undue "
            "delay after becoming aware of a breach, an obligation made contractual by Article "
            "28(3)(f). Article 34 requires communication to data subjects, without undue delay, where "
            "the breach is likely to result in a high risk, in clear and plain language describing "
            "the nature of the breach and the measures taken; communication is not required where the "
            "controller has implemented measures that render the data unintelligible, such as "
            "encryption, or where it would involve disproportionate effort, in which case a public "
            "communication or similar measure is required. The controller must document all breaches, "
            "including those not notified, under Article 33(5). Administrative fines reach twenty "
            "million euro or four per cent of total worldwide annual turnover."
        ),
        "tags": ["gdpr", "data breach", "notification", "72 hours", "supervisory authority"],
        "risk_level": "high",
    },
    {
        "id": "eu-gdpr-data-subject-rights-and-dpia",
        "jurisdiction": "EU",
        "source": "Pile of Law — GDPR Articles 12, 35-36 guidance (adapted)",
        "doc_type": "guidance",
        "title": "Data subject rights handling and data protection impact assessments",
        "text": (
            "Compliance note on requests under Articles 12 to 22 and assessments under Articles 35 "
            "to 36. The controller must facilitate the exercise of data subject rights and respond to "
            "a verified request without undue delay and in any event within one month of receipt, "
            "extendable by two further months for complex requests with notice to the data subject. A "
            "processor that receives a request directly must not respond on its own initiative but "
            "must forward it to the controller without undue delay; the contract should fix a service "
            "level, commonly five business days, together with a technical interface for identity "
            "verification, export of data in a structured, commonly used and machine-readable format, "
            "and suppression of records subject to erasure. Where a type of processing is likely to "
            "result in a high risk, the controller must carry out a data protection impact assessment "
            "before processing, describing the systematic description of the processing, the "
            "necessity and proportionality assessment, the risks to rights and freedoms and the "
            "safeguards envisaged. Processing listed by the supervisory authority under Article 35(4), "
            "for example large-scale processing of special categories of data or systematic monitoring "
            "of publicly accessible areas, always requires an assessment. Where the assessment "
            "indicates a high residual risk, the controller must consult the supervisory authority "
            "under Article 36 before processing. The processor should supply the technical detail: "
            "data flows, retention, model training use, re-identification risk and security measures."
        ),
        "tags": ["gdpr", "data subject rights", "dsar", "dpia", "erasure", "portability"],
        "risk_level": "medium",
    },
    # ------------------------------------------------------------------
    # EU — Artificial Intelligence Act
    # ------------------------------------------------------------------
    {
        "id": "eu-ai-act-prohibited-practices",
        "jurisdiction": "EU",
        "source": "Pile of Law — EU AI Act Article 5 (adapted)",
        "doc_type": "statute",
        "title": "Prohibited artificial intelligence practices",
        "text": (
            "Article 5 of Regulation (EU) 2024/1689 prohibits the placing on the market, the putting "
            "into service or the use of specified artificial intelligence practices. The prohibitions "
            "cover: subliminal, purposefully manipulative or deceptive techniques that materially "
            "distort behaviour and cause significant harm; exploitation of vulnerabilities of a "
            "person or group due to age, disability or a specific social or economic situation; "
            "social scoring by public or private actors leading to detrimental or unfavourable "
            "treatment unrelated to the context in which the data was generated; risk assessment of "
            "natural persons to predict criminal offending based solely on profiling or personality "
            "traits; untargeted scraping of facial images from the internet or closed-circuit "
            "television to build facial recognition databases; inference of emotions in the workplace "
            "and in education institutions, except for medical or safety reasons; biometric "
            "categorisation to deduce race, political opinions, trade union membership, religious or "
            "philosophical beliefs, sex life or sexual orientation; and real-time remote biometric "
            "identification in publicly accessible spaces for law enforcement purposes, subject to "
            "narrowly defined exceptions requiring prior judicial or administrative authorisation. "
            "Providers and deployers must assess their portfolios against Article 5 before launch and "
            "document that assessment. Infringement of Article 5 attracts administrative fines of up "
            "to thirty-five million euro or seven per cent of total worldwide annual turnover, "
            "whichever is higher."
        ),
        "tags": ["ai act", "prohibited practices", "biometric", "social scoring", "compliance"],
        "risk_level": "high",
    },
    {
        "id": "eu-ai-act-high-risk-classification",
        "jurisdiction": "EU",
        "source": "Pile of Law — EU AI Act Articles 6 and 25, Annex III (adapted)",
        "doc_type": "regulation",
        "title": "High-risk classification and value chain responsibilities",
        "text": (
            "Article 6 of Regulation (EU) 2024/1689 classifies an artificial intelligence system as "
            "high-risk in two situations. First, where the system is a safety component of a product, "
            "or is itself a product, covered by the Union harmonisation legislation listed in Annex I "
            "and required to undergo third-party conformity assessment. Second, where the system "
            "falls within a use case listed in Annex III, including biometric identification and "
            "categorisation, management and operation of critical infrastructure, education and "
            "vocational training, employment and worker management, access to essential private and "
            "public services such as creditworthiness assessment and emergency call triage, law "
            "enforcement, migration and border control, and the administration of justice and "
            "democratic processes. Article 6(3) provides a derogation where a system performs a "
            "narrow procedural task, improves the result of a previously completed human activity, "
            "detects decision-making patterns without replacing human assessment, or performs a "
            "preparatory task, in which case the provider must document the assessment and register "
            "the system. Article 25 allocates obligations along the value chain: a distributor, "
            "importer, deployer or third party that puts its name or trade mark on a high-risk system, "
            "substantially modifies it or changes its intended purpose becomes the provider and "
            "assumes the corresponding obligations. General-purpose models with systemic risk carry "
            "separate duties under Articles 51 to 55."
        ),
        "tags": ["ai act", "high-risk", "risk classification", "annex iii", "value chain"],
        "risk_level": "high",
    },
    {
        "id": "eu-ai-act-high-risk-provider-obligations",
        "jurisdiction": "EU",
        "source": "Pile of Law — EU AI Act Articles 9-17 and 43-49 (adapted)",
        "doc_type": "regulation",
        "title": "Provider obligations for high-risk AI systems",
        "text": (
            "Where an artificial intelligence system is classified as high-risk, Article 16 obliges "
            "the provider to ensure compliance with the requirements in Chapter III, Section 2 and to "
            "assume responsibility for the system. In practice that means: a risk management system "
            "maintained as a continuous iterative process across the lifecycle, with identification, "
            "analysis, evaluation and mitigation of reasonably foreseeable risks (Article 9); data "
            "governance covering design choices, origin, preparation, representativeness and bias "
            "examination of training, validation and testing datasets (Article 10); technical "
            "documentation drawn up before the system is placed on the market and kept up to date "
            "(Article 11 and Annex IV); automatic recording of events over the lifetime of the system "
            "that enables traceability (Article 12); instructions for use in clear language covering "
            "intended purpose, performance, known limitations and human oversight measures (Article "
            "13); design that permits effective oversight by natural persons (Article 14); and "
            "appropriate levels of accuracy, robustness and cybersecurity, including resilience "
            "against data poisoning and adversarial examples (Article 15). The provider must operate "
            "a quality management system (Article 17), follow the applicable conformity assessment "
            "procedure (Article 43), draw up an EU declaration of conformity, affix the CE marking, "
            "register the system in the EU database before placing it on the market (Article 49) and "
            "operate a post-market monitoring system (Article 72)."
        ),
        "tags": ["ai act", "high-risk", "provider obligations", "conformity assessment", "ce marking"],
        "risk_level": "high",
    },
    {
        "id": "eu-ai-act-transparency-and-human-oversight",
        "jurisdiction": "EU",
        "source": "Pile of Law — EU AI Act Articles 14 and 50 (adapted)",
        "doc_type": "guidance",
        "title": "Transparency duties and human oversight measures",
        "text": (
            "Deployers should read Articles 14 and 50 of Regulation (EU) 2024/1689 together. Article "
            "50 transparency duties require that natural persons are informed when they are "
            "interacting with an artificial intelligence system, that synthetic audio, image, video or "
            "text content is marked in a machine-readable format and detectable as artificially "
            "generated or manipulated, and that deployers of emotion recognition or biometric "
            "categorisation systems inform the persons exposed to them and process personal data in "
            "accordance with the GDPR. Article 14 requires high-risk systems to be designed so that "
            "they can be effectively overseen by natural persons during the period of use. Oversight "
            "measures should include: an interface that allows the human to understand the system's "
            "capacities and limitations and to interpret its output correctly; awareness of automation "
            "bias, in particular the tendency to accept a recommendation without independent "
            "verification; the ability to disregard, override or reverse output and to intervene or "
            "halt the system through a stop function or a comparable procedure; and a decision not to "
            "use the system in a particular situation. Organisations should name the individuals who "
            "perform oversight, record that they have the necessary competence, training and "
            "authority, and monitor override rates as a control indicator. For systems subject to a "
            "fundamental rights impact assessment under Article 27, the deployer must also notify the "
            "market surveillance authority."
        ),
        "tags": ["ai act", "transparency", "human oversight", "automation bias", "disclosure"],
        "risk_level": "medium",
    },
    {
        "id": "eu-ai-act-technical-documentation-and-logging",
        "jurisdiction": "EU",
        "source": "Pile of Law — EU AI Act Articles 11-12 and 19, Annex IV (adapted)",
        "doc_type": "regulation",
        "title": "Technical documentation and automatic logging obligations",
        "text": (
            "Article 11 of Regulation (EU) 2024/1689 requires the technical documentation of a "
            "high-risk artificial intelligence system to be drawn up before the system is placed on "
            "the market and kept up to date. Annex IV prescribes its contents: a general description "
            "of the system including intended purpose, version, interactions with hardware or other "
            "software, and the versions of relevant software or firmware; a detailed description of "
            "the elements of the system and of the development process, including the methods and "
            "steps performed, the design specifications, the system architecture, the data "
            "requirements and the datasets used, the human oversight measures and the pre-determined "
            "changes; information about the monitoring, functioning and control of the system, "
            "including its capabilities and limitations, the degrees of accuracy for specific persons "
            "or groups and the foreseeable unintended outcomes; the risk management documentation; a "
            "description of relevant changes made through the lifecycle; the standards applied; the "
            "EU declaration of conformity; and the post-market monitoring plan. Article 12 requires "
            "high-risk systems to technically allow for the automatic recording of events over the "
            "lifetime of the system, with logging capabilities that record the period of each use, the "
            "reference database used, the input data, the identification of the persons involved in "
            "verification and the output. Article 19 requires providers to keep the logs for at least "
            "six months, or longer where other Union or national law so provides."
        ),
        "tags": ["ai act", "technical documentation", "logging", "annex iv", "record keeping"],
        "risk_level": "medium",
    },
    # ------------------------------------------------------------------
    # EU — commercial terms: law, dispute resolution, liability, termination
    # ------------------------------------------------------------------
    {
        "id": "eu-governing-law-and-venue",
        "jurisdiction": "EU",
        "source": "CUAD v1 — Master Services Agreement, cl. 19.1-19.3 (adapted)",
        "doc_type": "contract_clause",
        "title": "Governing law, exclusive jurisdiction and escalation",
        "text": (
            "19.1 Governing Law. This Agreement, and any dispute or claim arising out of or in "
            "connection with it or its subject matter or formation, including non-contractual disputes "
            "or claims, shall be governed by and construed in accordance with the laws of England and "
            "Wales, without regard to its conflict of laws rules. The United Nations Convention on "
            "Contracts for the International Sale of Goods shall not apply. 19.2 Venue. Subject to "
            "Clause 19.3, each party irrevocably agrees that the courts of England and Wales shall "
            "have exclusive jurisdiction to settle any dispute or claim arising out of or in "
            "connection with this Agreement. Nothing in this Clause prevents either party from seeking "
            "interim or conservatory relief, including an injunction or attachment, in any court of "
            "competent jurisdiction, nor from enforcing a judgment or arbitral award in any "
            "jurisdiction. 19.3 Escalation. Before commencing proceedings, the parties shall attempt "
            "in good faith to resolve the dispute through named senior representatives, who shall meet "
            "within ten business days of a written notice of dispute; if the dispute is not resolved "
            "within thirty days, either party may proceed. The parties acknowledge that, where the "
            "Customer is established in the European Union, mandatory rules of the Customer's place of "
            "establishment, including Article 6 of Regulation (EC) No 593/2008, may limit the effect "
            "of the chosen law."
        ),
        "tags": ["governing law", "jurisdiction", "venue", "dispute resolution", "escalation"],
        "risk_level": "low",
    },
    {
        "id": "eu-arbitration-icc-clause",
        "jurisdiction": "EU",
        "source": "CUAD v1 — Software Licence Agreement, cl. 20 (adapted)",
        "doc_type": "contract_clause",
        "title": "ICC arbitration clause with seat in Amsterdam",
        "text": (
            "20.1 Arbitration. Any dispute arising out of or in connection with this Agreement, "
            "including any question regarding its existence, validity or termination, shall be "
            "referred to and finally resolved by arbitration administered by the International Chamber "
            "of Commerce under the ICC Arbitration Rules in force when the Request for Arbitration is "
            "submitted, which Rules are deemed to be incorporated by reference into this Clause. 20.2 "
            "Seat and Language. The seat of the arbitration shall be Amsterdam, the Netherlands. The "
            "tribunal shall consist of three arbitrators, one appointed by each party and the chair "
            "appointed by the two party-appointed arbitrators; if a party fails to appoint an "
            "arbitrator within thirty days, the ICC Court shall make the appointment. The language of "
            "the arbitration shall be English. 20.3 Procedure. The tribunal may order document "
            "production limited to what is relevant and material, shall decide the dispute in "
            "accordance with the governing law of this Agreement, and may award interest and costs, "
            "including reasonable legal fees, to the prevailing party. 20.4 Interim Measures. The "
            "parties may apply to any competent court for interim or conservatory measures, including "
            "emergency measures under the ICC Emergency Arbitrator Provisions, without waiving the "
            "agreement to arbitrate. 20.5 Confidentiality. The existence, content and outcome of the "
            "arbitration shall be confidential except as required by law, by a supervisory authority "
            "or for the enforcement of the award."
        ),
        "tags": ["arbitration", "icc", "dispute resolution", "seat", "interim measures"],
        "risk_level": "low",
    },
    {
        "id": "eu-limitation-of-liability-cap",
        "jurisdiction": "EU",
        "source": "CUAD v1 — Services Agreement, cl. 14 (adapted)",
        "doc_type": "contract_clause",
        "title": "Aggregate limitation of liability and carve-outs",
        "text": (
            "14.1 Aggregate Cap. Subject to Clause 14.3, the total aggregate liability of each party "
            "arising out of or in connection with this Agreement, whether in contract, tort, "
            "negligence, breach of statutory duty or otherwise, shall not exceed one hundred per cent "
            "of the fees paid or payable by the Customer to the Supplier under this Agreement in the "
            "twelve months immediately preceding the event giving rise to the first claim. 14.2 "
            "Allocation. Where liability arises from a single event or a series of connected events, "
            "all claims arising from that event or series shall be treated as one claim for the "
            "purposes of the cap. 14.3 Unlimited Heads. Nothing in this Agreement limits liability "
            "for: death or personal injury caused by negligence; fraud or fraudulent "
            "misrepresentation; wilful misconduct; breach of Clause 11 (Confidentiality); the "
            "Customer's obligation to pay fees properly due; infringement of the other party's "
            "intellectual property rights; or any liability that cannot be limited or excluded under "
            "applicable mandatory law. 14.4 Mitigation. Each party shall take reasonable steps to "
            "mitigate any loss for which it claims. 14.5 Insurance. The existence of insurance does "
            "not increase the limits in this Clause. 14.6 Allocation of Risk. The parties agree that "
            "the limitations and exclusions in Clauses 14 and 15 reflect a fair allocation of risk "
            "having regard to the level of the fees, and that the fees would be materially higher "
            "absent them."
        ),
        "tags": ["limitation of liability", "liability cap", "risk allocation", "carve-outs"],
        "risk_level": "medium",
    },
    {
        "id": "eu-consequential-loss-exclusion",
        "jurisdiction": "EU",
        "source": "CUAD v1 — Master Services Agreement, cl. 15 (adapted)",
        "doc_type": "contract_clause",
        "title": "Exclusion of indirect and consequential loss",
        "text": (
            "15.1 Exclusion of Indirect Loss. Subject to Clause 15.3, neither party shall be liable "
            "to the other, whether in contract, tort (including negligence), breach of statutory duty, "
            "misrepresentation or otherwise, for any: (a) loss of profit, revenue, anticipated saving, "
            "business, contract, opportunity or goodwill; (b) loss or corruption of data, except to "
            "the extent the loss results from a breach of Clause 12 (Data Protection) or Clause 11 "
            "(Confidentiality) and could have been avoided by the measures required by Article 32 of "
            "Regulation (EU) 2016/679; (c) indirect, consequential, special, punitive or exemplary "
            "loss or damage; or (d) wasted management time, in each case even if the party was advised "
            "of the possibility of such loss. 15.2 Direct Losses. The parties agree that the categories "
            "listed in Clause 15.1 are not recoverable irrespective of whether they would otherwise be "
            "characterised as direct or indirect, and that loss of profit arising from a claim by a "
            "third party is excluded. 15.3 Mandatory Law. Nothing in this Clause excludes liability to "
            "the extent prohibited by mandatory law, in particular where standard business terms are "
            "used and the exclusion would deprive the counterparty of essential rights and obligations "
            "in a manner contrary to good faith, as assessed under Sections 305 to 310 of the German "
            "Civil Code (BGB) or equivalent provisions of the applicable law."
        ),
        "tags": ["consequential loss", "indirect damages", "exclusion", "lost profits", "bgb"],
        "risk_level": "medium",
    },
    {
        "id": "eu-indemnity-third-party-claims",
        "jurisdiction": "EU",
        "source": "CUAD v1 — Software Licence Agreement, cl. 16 (adapted)",
        "doc_type": "contract_clause",
        "title": "Intellectual property indemnity and conduct of third-party claims",
        "text": (
            "16.1 Indemnity. The Supplier shall defend and indemnify the Customer against all "
            "liabilities, damages, losses, costs and expenses, including reasonable legal fees, "
            "awarded against or incurred by the Customer in connection with any claim brought by a "
            "third party alleging that the Services, the Deliverables or the Customer's use of them in "
            "accordance with this Agreement infringes any patent, copyright, trade mark, database "
            "right or other intellectual property right of that third party. 16.2 Conditions. The "
            "indemnity is conditional on the Customer: notifying the Supplier promptly in writing of "
            "the claim; granting the Supplier sole control of the defence and settlement, provided "
            "that no settlement imposing an admission of liability on the Customer or requiring the "
            "Customer to cease using the affected item may be agreed without the Customer's consent; "
            "making no admission of liability; and providing reasonable assistance at the Supplier's "
            "cost. 16.3 Remedies. If an infringement claim is made or the Supplier reasonably believes "
            "one is likely, the Supplier may at its option procure the right for the Customer to "
            "continue using the affected item, replace or modify it so that it becomes non-infringing "
            "while retaining materially equivalent functionality, or terminate the affected Services "
            "and refund prepaid fees for the unused period. 16.4 Exclusions. The Supplier has no "
            "liability under this Clause 16 to the extent the claim arises from the Customer's "
            "materials, modifications not made by the Supplier, use in combination with items not "
            "supplied by the Supplier, or failure to use an update made available at no additional "
            "cost."
        ),
        "tags": ["indemnity", "third-party claims", "ip infringement", "defence", "indemnification"],
        "risk_level": "medium",
    },
    {
        "id": "eu-termination-for-convenience",
        "jurisdiction": "EU",
        "source": "CUAD v1 — Maintenance and Support Agreement, cl. 3 (adapted)",
        "doc_type": "contract_clause",
        "title": "Termination for convenience, for cause and effects of termination",
        "text": (
            "3.1 Termination for Convenience. Either party may terminate this Agreement for "
            "convenience by giving the other party not less than ninety days written notice, such "
            "notice to expire no earlier than the first anniversary of the Effective Date. The "
            "Customer may terminate an individual Order Form for convenience on thirty days written "
            "notice, in which case fees for the remainder of the then-current subscription term for "
            "that Order Form shall become immediately due to the extent the Supplier has incurred "
            "non-cancellable third-party commitments. 3.2 Termination for Cause. Either party may "
            "terminate this Agreement immediately on written notice if the other party commits a "
            "material breach that is not remedied within thirty days of notice describing the breach "
            "in reasonable detail, or if the other party becomes insolvent, enters administration or "
            "liquidation, has a receiver appointed over its assets or ceases to carry on business. "
            "3.3 Effects of Termination. On termination or expiry: all licences and access rights "
            "cease; the Customer shall pay all fees accrued to the date of termination; each party "
            "shall return or destroy the other's Confidential Information and certify that it has "
            "done so; the Supplier shall provide the transition assistance described in Clause 26; and "
            "the Supplier shall delete Customer Data in accordance with Clause 12, save where "
            "retention is required by law. Termination shall not affect accrued rights or any "
            "provision expressed to survive it."
        ),
        "tags": ["termination for convenience", "termination for cause", "notice period", "effects"],
        "risk_level": "low",
    },
    {
        "id": "eu-termination-for-cause-and-cure-period",
        "jurisdiction": "EU",
        "source": "CUAD v1 — Service Agreement, cl. 11.4 (adapted); internal negotiation playbook",
        "doc_type": "playbook",
        "title": "Negotiation playbook for termination for cause and cure periods",
        "text": (
            "Negotiation playbook for termination for cause in European technology contracts. Target "
            "position: a party may terminate for material breach only after written notice identifying "
            "the breach and an opportunity to cure within thirty days, with a shorter period of ten "
            "days for payment default and no cure period for insolvency, fraud, wilful misconduct, "
            "breach of confidentiality or a repeated breach of the same obligation after prior notice. "
            "Fallback one: extend the cure period to sixty days where the breach is not capable of "
            "being cured within thirty days and the breaching party has begun and is diligently "
            "pursuing a remediation plan. Fallback two: accept a cure period coupled with a service "
            "credit and escalation to an executive steering committee before termination. Avoid "
            "cross-default termination rights that allow termination of the whole agreement for a "
            "breach of a single order form, and termination rights triggered by any change of control "
            "rather than a competitor acquisition. On the customer side, secure the right to terminate "
            "a single affected service line rather than the entire agreement, and the right to suspend "
            "payment for the affected scope during a continuing breach. On the supplier side, ensure "
            "that the notice of breach is specific, exclude termination where the failure results from "
            "a force majeure event, and provide that termination for convenience is not available to "
            "the customer before the first anniversary of the effective date."
        ),
        "tags": ["termination for cause", "cure period", "material breach", "playbook", "remedies"],
        "risk_level": "medium",
    },
    # ------------------------------------------------------------------
    # EU — intellectual property, confidentiality, data commercial terms
    # ------------------------------------------------------------------
    {
        "id": "eu-ip-ownership-and-licence-back",
        "jurisdiction": "EU",
        "source": "CUAD v1 — Development Agreement, cl. 8 (adapted)",
        "doc_type": "contract_clause",
        "title": "Ownership of deliverables, assignment of rights and licence-back",
        "text": (
            "8.1 Background IP. Each party retains all right, title and interest in its Background IP. "
            "The Customer grants the Supplier a non-exclusive, royalty-free licence to use the "
            "Customer's Background IP and Customer Materials solely to the extent necessary to "
            "perform the Services. 8.2 Foreground IP. All intellectual property rights in the "
            "Deliverables created by the Supplier specifically for the Customer under a Statement of "
            "Work shall vest in the Customer on payment of the associated fees. The Supplier hereby "
            "assigns, and shall procure the assignment of, all such rights with full title guarantee, "
            "and shall execute such documents as the Customer reasonably requests to perfect that "
            "assignment. 8.3 Licence-back. The Customer grants the Supplier a perpetual, irrevocable, "
            "worldwide, royalty-free, non-exclusive licence to use, reproduce and modify the "
            "Deliverables for the purpose of providing, maintaining and improving the Services, "
            "provided that the Supplier does not disclose the Customer's Confidential Information. "
            "8.4 Supplier Tools. The Supplier retains ownership of its pre-existing tools, "
            "methodologies, software and generic know-how, and grants the Customer a perpetual, "
            "non-exclusive, royalty-free licence to use those elements embedded in the Deliverables to "
            "the extent required to use them for their intended purpose. 8.5 Moral Rights. The "
            "Supplier waives, and shall procure the waiver of, all moral rights in the Deliverables to "
            "the fullest extent permitted by law, including under the German Copyright Act and "
            "equivalent provisions of the applicable law."
        ),
        "tags": ["intellectual property", "work product", "assignment", "licence back", "ownership"],
        "risk_level": "medium",
    },
    {
        "id": "eu-open-source-software-compliance",
        "jurisdiction": "EU",
        "source": "Pile of Law — open source compliance policy (adapted)",
        "doc_type": "playbook",
        "title": "Open source clearance, copyleft and software bill of materials",
        "text": (
            "Playbook for open source clearance in European product and services contracts. Require "
            "the supplier to maintain a software bill of materials in a machine-readable format, such "
            "as SPDX or CycloneDX, identifying each open source component, its version, its licence "
            "and its source, and to update it at each major release and on request. Classify licences "
            "by obligation: permissive licences such as MIT, BSD and Apache-2.0 are generally "
            "acceptable; weak copyleft licences such as LGPL, MPL and EPL are acceptable where the "
            "component is dynamically linked and unmodified, or where modifications are contributed "
            "back; strong copyleft licences such as GPL and AGPL, including network copyleft, require "
            "prior written approval, and AGPL components must not be embedded in a network service "
            "without an approved exception. Prohibit components under licences that are not approved "
            "by the Open Source Initiative or that impose field-of-use, non-commercial or advertising "
            "restrictions. Require written warranties that the deliverables contain no open source "
            "component in a manner that would require the customer to disclose or license its own "
            "proprietary source code, together with an indemnity for breach of that warranty. Require "
            "a remediation process: on discovery of a non-compliant component the supplier shall "
            "within thirty days replace, re-engineer or obtain a licence, and shall provide a "
            "root-cause analysis and a scan report from a recognised tool. Preserve audit rights over "
            "build systems and repositories."
        ),
        "tags": ["open source", "copyleft", "sbom", "gpl", "licence compliance", "audit"],
        "risk_level": "high",
    },
    {
        "id": "eu-confidentiality-and-trade-secrets",
        "jurisdiction": "EU",
        "source": "CUAD v1 — Non-Disclosure Agreement, cl. 11 (adapted)",
        "doc_type": "contract_clause",
        "title": "Confidentiality, trade secret protection and survival periods",
        "text": (
            "11.1 Obligation. Each party shall keep confidential all Confidential Information of the "
            "other party, use it solely for the purposes of this Agreement and disclose it only to "
            "those of its employees, officers, professional advisers and subcontractors who need to "
            "know it and who are bound by written obligations of confidentiality no less protective "
            "than this Clause. 11.2 Standard of Care. The receiving party shall protect Confidential "
            "Information using at least the degree of care it uses to protect its own confidential "
            "information of like importance, and in no event less than a reasonable degree of care, "
            "including encryption of data in transit and at rest and appropriate access controls. "
            "11.3 Exclusions. Confidential Information does not include information that is or becomes "
            "public through no breach of this Clause, was lawfully known to the receiving party "
            "without a duty of confidence, is independently developed without use of the disclosing "
            "party's information, or is lawfully received from a third party without restriction. "
            "11.4 Compelled Disclosure. Where disclosure is required by law, regulation or a competent "
            "authority, the receiving party shall, where lawfully permitted, give prompt notice and "
            "reasonable assistance to seek protective relief, and shall disclose only the minimum "
            "required. 11.5 Trade Secrets. The parties acknowledge that Confidential Information may "
            "constitute a trade secret within the meaning of Directive (EU) 2016/943, and that the "
            "obligations in this Clause apply without time limit for so long as the information "
            "retains that character. 11.6 Survival. The obligations in this Clause survive for five "
            "years after termination, and indefinitely in respect of trade secrets and personal data."
        ),
        "tags": ["confidentiality", "trade secrets", "nda", "survival", "non-disclosure"],
        "risk_level": "medium",
    },
    {
        "id": "eu-employment-contractor-classification",
        "jurisdiction": "EU",
        "source": "Pile of Law — national labour law guidance on worker classification (adapted)",
        "doc_type": "guidance",
        "title": "Employee versus contractor classification in the European Union",
        "text": (
            "Guidance on engaging individual contractors and platform workers in the European Union. "
            "The classification of a working relationship is a matter of fact and not of contract "
            "wording; courts and labour inspectorates examine subordination, integration into the "
            "organisation, working time, the provision of equipment, economic dependence, the "
            "assumption of commercial risk and the ability to work for other clients. A consultant "
            "who works exclusively for one client, at the client's premises or under its direction, "
            "using the client's tools, and is paid a monthly retainer is likely to be reclassified as "
            "an employee, triggering exposure to unpaid social security contributions, back pay, "
            "holiday entitlement, notice and penalties, and in several Member States joint liability "
            "of the principal contractor in subcontracting chains. Where the relationship is genuinely "
            "independent, the contract should state that the contractor controls the manner and means "
            "of performance, permit subcontracting and work for other clients, provide for "
            "deliverables and acceptance rather than hours, exclude employee benefits, and require the "
            "contractor to maintain its own insurance and to account for its own taxes. Platform work "
            "legislation in several Member States presumes employment where the platform controls the "
            "execution of work, and the European platform work initiative introduces algorithmic "
            "management safeguards. Cross-border arrangements should also address the applicable "
            "social security regime under Regulation (EC) No 883/2004."
        ),
        "tags": ["employment", "contractor classification", "subordination", "social security", "platform work"],
        "risk_level": "medium",
    },
    {
        "id": "eu-employee-inventions-and-non-compete",
        "jurisdiction": "EU",
        "source": "Pile of Law — German Act on Employee Inventions, ss. 5-18 (adapted)",
        "doc_type": "case_note",
        "title": "Employee inventions, remuneration and post-employment restraints",
        "text": (
            "Case note on employee inventions and post-employment restraints. In Germany, Sections 5 "
            "to 18 of the Act on Employee Inventions provide that inventions made during the term of "
            "employment which arise from the employee's duties or are based on the employer's "
            "experience are service inventions; the employer may claim them by written declaration "
            "within four months of a proper notification, and the employee is entitled to reasonable "
            "remuneration. Contract terms that attempt to pre-empt this regime without complying with "
            "it are ineffective. Comparable mandatory regimes apply in the Netherlands, France, Italy "
            "and Belgium, and in France the invention must fall within the employee's mission or be "
            "attributable to the employer. Post-employment non-competition covenants are enforceable "
            "only within limits: German law requires a maximum two-year term, a monthly payment of at "
            "least half of the last contractual remuneration and a written agreement, failing which "
            "the covenant is void; French law requires a legitimate interest, a time and geographic "
            "limit, an activity limit and a financial consideration; Belgian and Italian law impose "
            "comparable conditions and caps. Garden leave, non-solicitation of employees and customers "
            "and confidentiality undertakings are generally more enforceable than broad activity bans. "
            "Employers should document the invention disclosure and claim process, keep evidence of "
            "the compensation paid, and avoid restrictions that a court would strike down in their "
            "entirety."
        ),
        "tags": ["employee inventions", "non-compete", "restrictive covenants", "ip assignment"],
        "risk_level": "medium",
    },
    {
        "id": "eu-ai-act-deployer-obligations",
        "jurisdiction": "EU",
        "source": "Pile of Law — EU AI Act Articles 26-27 (adapted)",
        "doc_type": "regulation",
        "title": "Deployer obligations and fundamental rights impact assessment",
        "text": (
            "Article 26 of Regulation (EU) 2024/1689 places obligations on deployers of high-risk "
            "artificial intelligence systems that are distinct from those of the provider. A deployer "
            "must use the system in accordance with the instructions for use, assign human oversight "
            "to natural persons who have the necessary competence, training and authority, ensure that "
            "input data is relevant and sufficiently representative in view of the intended purpose, "
            "monitor the operation of the system and inform the provider and, where applicable, the "
            "market surveillance authority of any serious incident or malfunction, keep the logs "
            "generated by the system for at least six months, inform workers representatives and "
            "affected workers where the system is used in the workplace, and cooperate with competent "
            "authorities. Where the deployer is a body governed by public law or a private entity "
            "providing public services, and in certain banking and insurance use cases, Article 27 "
            "requires a fundamental rights impact assessment covering the deployer's processes, the "
            "period and frequency of use, the categories of natural persons affected, the specific "
            "risks of harm, the human oversight measures and the mitigation and governance "
            "arrangements. The assessment must be notified to the market surveillance authority. "
            "Contracts should allocate the information, cooperation and indemnity obligations that "
            "these duties require between provider and deployer."
        ),
        "tags": ["ai act", "deployer", "fundamental rights", "human oversight", "serious incident"],
        "risk_level": "high",
    },
    # ------------------------------------------------------------------
    # EU — payment, service levels, warranties and risk management
    # ------------------------------------------------------------------
    {
        "id": "eu-fees-invoicing-taxes",
        "jurisdiction": "EU",
        "source": "CUAD v1 — Master Services Agreement, cl. 6 (adapted)",
        "doc_type": "contract_clause",
        "title": "Fees, invoicing, late payment interest, taxes and currency",
        "text": (
            "6.1 Fees. The Customer shall pay the fees set out in each Order Form in the currency "
            "stated in that Order Form. Except as expressly provided, fees are non-cancellable and "
            "non-refundable. 6.2 Invoicing and Payment. The Supplier shall invoice annually in advance "
            "for subscription fees and monthly in arrears for variable usage. The Customer shall pay "
            "each undisputed invoice within thirty days of receipt of a valid invoice. 6.3 Late "
            "Payment. Where the Customer fails to pay an undisputed amount by the due date, the "
            "Supplier may charge interest on the overdue amount at the rate provided by Directive "
            "2011/7/EU on combating late payment in commercial transactions, namely the sum of eight "
            "percentage points and the reference rate of the European Central Bank, or at the "
            "statutory rate if lower, together with a fixed compensation of forty euro and reasonable "
            "recovery costs. 6.4 Disputed Amounts. The Customer may withhold only amounts disputed in "
            "good faith and shall notify the dispute before the due date. 6.5 Taxes. Fees are "
            "exclusive of value added tax and any other applicable indirect taxes, which shall be "
            "added and paid by the Customer against a valid tax invoice; each party is responsible for "
            "its own corporate income taxes. 6.6 Expenses. Pre-approved reasonable travel and "
            "accommodation expenses are reimbursed at cost against receipts. 6.7 Currency and Set-off. "
            "Payments shall be made in the invoicing currency by bank transfer without set-off or "
            "deduction, except for deductions required by law."
        ),
        "tags": ["payment terms", "invoicing", "late interest", "vat", "currency", "expenses"],
        "risk_level": "low",
    },
    {
        "id": "eu-service-levels-and-service-credits",
        "jurisdiction": "EU",
        "source": "CUAD v1 — Service Level Agreement, cl. 7 (adapted)",
        "doc_type": "contract_clause",
        "title": "Service levels, measurement and service credits",
        "text": (
            "7.1 Service Levels. The Supplier shall provide the Services in accordance with the "
            "service levels set out in Schedule 2, including an availability commitment of 99.9 per "
            "cent measured monthly, a response time of one hour for Priority 1 incidents and a "
            "restoration target of four hours. 7.2 Measurement. Availability is calculated as the "
            "total minutes in the month less excluded downtime, divided by the total minutes in the "
            "month, excluding scheduled maintenance notified at least five business days in advance "
            "and not exceeding four hours per month, and excluding failures of the Customer's own "
            "systems or of third-party networks outside the Supplier's control. 7.3 Service Credits. "
            "If the Supplier fails to meet a service level, the Customer is entitled to service "
            "credits calculated as a percentage of the monthly fee for the affected service: five per "
            "cent for availability below 99.9 per cent but at or above 99.5 per cent; ten per cent "
            "below 99.5 per cent but at or above 99.0 per cent; and twenty-five per cent below 99.0 "
            "per cent. 7.4 Application. Credits are applied against the next invoice and are capped at "
            "thirty per cent of the monthly fee for the affected service. 7.5 Sole Financial Remedy. "
            "Service credits are the Customer's sole financial remedy for the failure to which they "
            "relate, but do not limit the Customer's right to terminate for a failure to meet the "
            "availability commitment in three consecutive months or in any four months within a "
            "rolling twelve-month period."
        ),
        "tags": ["service levels", "sla", "service credits", "availability", "remedies"],
        "risk_level": "medium",
    },
    {
        "id": "eu-warranties-and-remedies",
        "jurisdiction": "EU",
        "source": "CUAD v1 — Software Licence Agreement, cl. 10 (adapted)",
        "doc_type": "contract_clause",
        "title": "Warranties, exclusive remedies and disclaimers",
        "text": (
            "10.1 Mutual Warranties. Each party warrants that it has full power and authority to enter "
            "into this Agreement and that doing so will not breach any other agreement to which it is "
            "a party. 10.2 Supplier Warranties. The Supplier warrants that: the Services will be "
            "performed with the skill and care of a suitably qualified provider of comparable "
            "services; the Services will conform in all material respects to the applicable "
            "documentation; the Deliverables will be free from defects in materials and workmanship "
            "for ninety days after acceptance; it will comply with all laws applicable to its "
            "performance, including Regulation (EU) 2016/679 and, where applicable, Regulation (EU) "
            "2024/1689; and the Deliverables will not contain malicious code. 10.3 Remedies. If the "
            "Supplier breaches a warranty, it shall at its own cost and as the Customer's exclusive "
            "remedy re-perform the affected Services or repair or replace the affected Deliverable "
            "within thirty days of written notice, and if it fails to do so the Customer may terminate "
            "the affected Order Form and receive a refund of prepaid fees for the affected period. "
            "10.4 Disclaimers. Except as expressly stated, all other warranties, conditions and terms, "
            "whether express or implied by statute, common law or otherwise, including any implied "
            "warranty of merchantability, satisfactory quality or fitness for a particular purpose, "
            "are excluded to the fullest extent permitted by law. Nothing in this Clause excludes "
            "liability for fraud or any warranty that cannot lawfully be excluded."
        ),
        "tags": ["warranties", "disclaimers", "remedies", "warranty period", "conformity"],
        "risk_level": "medium",
    },
    {
        "id": "eu-force-majeure-and-business-continuity",
        "jurisdiction": "EU",
        "source": "CUAD v1 — Service Agreement, cl. 17 (adapted)",
        "doc_type": "contract_clause",
        "title": "Force majeure, hardship and business continuity planning",
        "text": (
            "17.1 Force Majeure. Neither party shall be liable for any failure or delay in performance "
            "caused by an event beyond its reasonable control, including natural disaster, war, armed "
            "conflict, terrorism, civil unrest, epidemic or pandemic, government action, embargo, "
            "sanctions, failure of public telecommunications networks not caused by that party, or a "
            "widespread failure of the power supply. 17.2 Conditions. The affected party shall notify "
            "the other promptly, provide reasonable evidence of the event and use all reasonable "
            "endeavours to mitigate its effect and resume performance, including by implementing the "
            "business continuity plan maintained under Clause 17.4. Relief applies only for the "
            "duration of the event and does not excuse the obligation to pay amounts already due. "
            "17.3 Termination. If the event continues for more than sixty consecutive days and "
            "materially affects the provision of the Services, either party may terminate the affected "
            "Order Form on written notice without liability, and the Supplier shall refund prepaid "
            "fees for the terminated period. 17.4 Business Continuity. The Supplier shall maintain, "
            "test at least annually and provide on request a business continuity and disaster recovery "
            "plan with a recovery time objective of eight hours and a recovery point objective of one "
            "hour for production systems, and shall notify the Customer of any material change. 17.5 "
            "Hardship. Where performance becomes substantially more onerous without being impossible, "
            "the parties shall negotiate in good faith an equitable adjustment."
        ),
        "tags": ["force majeure", "hardship", "business continuity", "disaster recovery", "relief"],
        "risk_level": "medium",
    },
    {
        "id": "eu-assignment-subcontracting-change-of-control",
        "jurisdiction": "EU",
        "source": "CUAD v1 — Master Services Agreement, cl. 21 (adapted)",
        "doc_type": "contract_clause",
        "title": "Assignment, subcontracting and change of control",
        "text": (
            "21.1 Assignment. Neither party may assign, transfer, novate or otherwise deal with this "
            "Agreement or any of its rights under it, in whole or in part, without the prior written "
            "consent of the other party, such consent not to be unreasonably withheld, conditioned or "
            "delayed; provided that either party may assign this Agreement to an affiliate or to a "
            "successor in connection with a merger, reorganisation or sale of all or substantially all "
            "of its assets or shares, on written notice, provided the assignee assumes the obligations, "
            "is not a direct competitor of the other party and is not subject to sanctions. 21.2 Change "
            "of Control. Where a change of control occurs in relation to the Supplier and the acquiring "
            "entity is a competitor of the Customer, or where the change of control is reasonably "
            "likely to impair the Supplier's ability to perform, the Customer may terminate this "
            "Agreement on ninety days written notice without liability. 21.3 Subcontracting. The "
            "Supplier may subcontract performance of the Services only with the prior written consent "
            "of the Customer and remains fully liable for the acts and omissions of its subcontractors "
            "as if they were its own, and shall impose obligations on subcontractors no less onerous "
            "than those in this Agreement, including confidentiality, data protection, audit and "
            "compliance with export control and anti-bribery laws. 21.4 Successors. This Agreement "
            "binds and benefits the parties and their permitted successors and assigns."
        ),
        "tags": ["assignment", "subcontracting", "change of control", "novation", "successors"],
        "risk_level": "medium",
    },
    {
        "id": "eu-audit-rights-and-record-keeping",
        "jurisdiction": "EU",
        "source": "CUAD v1 — Outsourcing Agreement, cl. 13 (adapted)",
        "doc_type": "contract_clause",
        "title": "Audit rights, record-keeping and regulatory cooperation",
        "text": (
            "13.1 Audit Rights. The Customer, or an independent auditor appointed by the Customer and "
            "reasonably acceptable to the Supplier, may audit the Supplier's compliance with this "
            "Agreement not more than once in any twelve-month period, on thirty days written notice, "
            "during normal business hours and in a manner that minimises disruption to the Supplier's "
            "operations. The Supplier shall provide access to the relevant records, systems, personnel "
            "and facilities and shall respond to a reasonable audit questionnaire and evidence "
            "request. 13.2 Scope and Costs. The Customer bears the cost of the audit unless the audit "
            "reveals a material breach or an overcharge of more than five per cent, in which case the "
            "Supplier shall bear the reasonable cost and shall credit the overcharge. 13.3 Regulatory "
            "Cooperation. The Supplier shall cooperate with any supervisory authority, including the "
            "data protection supervisory authority, the market surveillance authority and any "
            "financial regulator, and shall provide such information and assistance as the Customer "
            "reasonably requires to respond to an enquiry, inspection or examination, including "
            "completing regulatory questionnaires and providing certifications. 13.4 Record-keeping. "
            "The Supplier shall keep accurate records of the provision of the Services, processing "
            "activities, security incidents, personnel training and compliance testing for the longer "
            "of three years after termination or the period required by applicable law. 13.5 Reports. "
            "The Supplier shall provide an annual SOC 2 Type II report or an ISO/IEC 27001 certificate "
            "where available in lieu of an on-site audit."
        ),
        "tags": ["audit rights", "record keeping", "regulatory cooperation", "certification", "compliance"],
        "risk_level": "low",
    },
    {
        "id": "eu-export-control-sanctions-and-anti-bribery",
        "jurisdiction": "EU",
        "source": "Pile of Law — Regulation (EU) 2021/821 and Directive (EU) 2017/1371 (adapted)",
        "doc_type": "regulation",
        "title": "Export control, sanctions and anti-bribery compliance undertakings",
        "text": (
            "Regulation (EU) 2021/821 establishes the Union regime for the control of exports, "
            "brokering, technical assistance, transit and transfer of dual-use items listed in Annex I "
            "and grouped in ten categories. Items that are not listed but are intended for a military "
            "end use, for a weapons of mass destruction end use or for a sanctioned destination may "
            "require an authorisation under the catch-all clauses; exporters must also apply for a "
            "licence where they are aware that the items are destined for such uses. Dealers should "
            "screen every counterparty, end user and beneficial owner against the EU consolidated "
            "financial sanctions list, the lists of persons subject to restrictive measures and the "
            "EU arms embargoes, and against the United States lists where there is a United States "
            "nexus. Contract clauses should require the counterparty to comply with all applicable "
            "export control and sanctions laws, to provide end-use and end-user information on "
            "request, not to export, re-export or transfer controlled items without authorisation, and "
            "to notify the other party of any listing, investigation or enforcement action. Contracts "
            "should permit immediate suspension or termination where continued performance would "
            "breach sanctions or expose a party to secondary sanctions. Anti-bribery undertakings "
            "should require compliance with Directive (EU) 2017/1371, Council Framework Decision "
            "2003/568/JHA and, where a party carries on business in the United Kingdom, the Bribery "
            "Act 2010, including the Section 7 offence of failure to prevent bribery."
        ),
        "tags": ["export control", "sanctions", "dual-use", "anti-bribery", "screening", "compliance"],
        "risk_level": "high",
    },
    {
        "id": "eu-insurance-requirements",
        "jurisdiction": "EU",
        "source": "CUAD v1 — Services Agreement, cl. 18 (adapted)",
        "doc_type": "contract_clause",
        "title": "Insurance requirements and evidence of cover",
        "text": (
            "18.1 Insurance. Throughout the term and for two years afterwards, the Supplier shall "
            "maintain, with insurers authorised to carry on business in the European Union and rated "
            "at least A- by a recognised rating agency, the following cover: (a) professional indemnity "
            "or errors and omissions insurance of not less than five million euro for each claim and in "
            "the aggregate; (b) public and products liability of not less than five million euro for "
            "each claim; (c) cyber liability, including data breach response, business interruption and "
            "network security liability, of not less than five million euro; (d) employers liability as "
            "required by law; and (e) where the Supplier processes personal data, cover that responds "
            "to liability for infringement of Regulation (EU) 2016/679 and to regulatory defence costs. "
            "18.2 Evidence. The Supplier shall provide certificates of insurance and evidence of "
            "premium payment on request and at each renewal, and shall notify the Customer of any "
            "cancellation, non-renewal or material reduction in cover within ten business days. 18.3 "
            "Primary Cover. The Supplier's insurance is primary with respect to its liability under "
            "this Agreement, and the Customer shall be named as an additional insured or loss payee "
            "where reasonably requested. 18.4 No Limitation. The existence of insurance does not "
            "increase or otherwise affect the limits of liability in Clause 14, and the Supplier is "
            "not relieved of liability by any deductible, exclusion or refusal of an insurer to pay."
        ),
        "tags": ["insurance", "professional indemnity", "cyber liability", "certificate of insurance"],
        "risk_level": "medium",
    },
    {
        "id": "eu-acceptance-testing-deemed-acceptance",
        "jurisdiction": "EU",
        "source": "CUAD v1 — Development Agreement, cl. 5 (adapted)",
        "doc_type": "contract_clause",
        "title": "Acceptance testing, deemed acceptance and warranty periods",
        "text": (
            "5.1 Acceptance Testing. Following delivery of each Deliverable, the Customer shall have "
            "thirty days to carry out acceptance tests in accordance with the acceptance criteria in "
            "the applicable Statement of Work. The Customer shall notify the Supplier in writing of "
            "acceptance, rejection or conditional acceptance before the end of that period. 5.2 "
            "Rejection and Repair. A rejection notice must specify the failure and the acceptance "
            "criterion not met with sufficient detail to allow the Supplier to reproduce it. The "
            "Supplier shall have thirty days to remedy the failure and redeliver, after which a new "
            "acceptance test period of fifteen days applies, limited to the previously failed criteria. "
            "5.3 Conditional Acceptance. Where the Deliverable is substantially complete and the "
            "defects are minor and do not materially affect use, the parties shall record the defects "
            "in a punch list and the Customer may accept the Deliverable subject to the Supplier "
            "remedying the listed items within an agreed period. 5.4 Deemed Acceptance. A Deliverable "
            "shall be deemed accepted if the Customer fails to issue a rejection notice within the "
            "acceptance period, or if the Customer puts the Deliverable into productive use in a live "
            "environment other than for pilot or evaluation purposes. 5.5 Warranty Period. Acceptance "
            "does not waive the warranties in Clause 10, and the ninety-day warranty period runs from "
            "the date of acceptance or deemed acceptance. 5.6 Effect. Risk in the Deliverable passes on "
            "delivery and title passes on acceptance."
        ),
        "tags": ["acceptance testing", "deemed acceptance", "warranty period", "deliverables", "punch list"],
        "risk_level": "low",
    },
    {
        "id": "eu-source-code-escrow-and-transition",
        "jurisdiction": "EU",
        "source": "CUAD v1 — Software Licence Agreement, cl. 26 (adapted)",
        "doc_type": "contract_clause",
        "title": "Source code escrow and transition assistance",
        "text": (
            "26.1 Source Code Escrow. Where the Services include software supplied by the Supplier, "
            "the Supplier shall deposit with a reputable escrow agent, within thirty days of the "
            "Effective Date and at each major release, the source code, build instructions, technical "
            "documentation, database schemas and a list of third-party components necessary to "
            "compile, deploy, maintain and support the software. The Supplier shall update the deposit "
            "at least annually and shall certify its completeness. 26.2 Release Conditions. The "
            "Customer may request release of the deposit if the Supplier becomes insolvent, ceases to "
            "support the software generally, or fails to remedy a material breach affecting "
            "availability within thirty days of notice. The released materials may be used solely to "
            "maintain, support and modify the software for the Customer's internal business and may "
            "not be distributed or used to create a competing product. 26.3 Transition Assistance. On "
            "termination or expiry the Supplier shall, for up to six months and at its then-current "
            "rates unless termination is for the Supplier's material breach, provide reasonable "
            "transition services including knowledge transfer, documentation, export of Customer Data "
            "in an open format, cooperation with a successor supplier and continued access to the "
            "Services at a reduced scope. 26.4 Data Return. The Supplier shall return or make available "
            "all Customer Data within thirty days of a written request and shall certify deletion "
            "afterwards. 26.5 Survival. This Clause survives termination."
        ),
        "tags": ["escrow", "source code", "transition assistance", "exit", "data return"],
        "risk_level": "medium",
    },
    # ------------------------------------------------------------------
    # US — privacy, sector regulation and securities disclosure
    # ------------------------------------------------------------------
    {
        "id": "us-ccpa-cpra-service-provider-obligations",
        "jurisdiction": "US",
        "source": "Pile of Law — Cal. Civ. Code §§ 1798.100, 1798.140 (adapted)",
        "doc_type": "statute",
        "title": "CCPA and CPRA service provider contract requirements",
        "text": (
            "Section 1798.100 et seq. of the California Civil Code, as amended by the California "
            "Privacy Rights Act, imposes direct obligations on businesses that determine the purposes "
            "and means of processing and imposes contractual requirements on service providers and "
            "contractors. A business that discloses personal information to a service provider must "
            "have a written contract that: prohibits the service provider from selling or sharing the "
            "personal information; prohibits retaining, using or disclosing it for any purpose other "
            "than the business purposes specified in the contract, including any commercial purpose "
            "of the service provider; requires compliance with the CCPA and provides the same level of "
            "privacy protection as the business is required to provide; grants the business the right "
            "to take reasonable and appropriate steps to ensure that the service provider uses the "
            "personal information in a manner consistent with its obligations, including monitoring "
            "and remediation; requires notification to the business if the service provider determines "
            "that it can no longer meet its obligations; and permits the business to stop and remediate "
            "unauthorised use. A service provider that uses personal information for its own purposes, "
            "including training a general model, is treated as a business with respect to that "
            "processing and may face enforcement by the California Privacy Protection Agency. The CPRA "
            "adds obligations for sensitive personal information, data minimisation and retention "
            "disclosure, and risk assessments for processing that presents significant risk to "
            "consumers."
        ),
        "tags": ["ccpa", "cpra", "service provider", "privacy", "data processing", "california"],
        "risk_level": "high",
    },
    {
        "id": "us-cpra-sensitive-data-and-risk-assessments",
        "jurisdiction": "US",
        "source": "Pile of Law — 11 C.C.R. §§ 7000-7304 (adapted)",
        "doc_type": "regulation",
        "title": "Sensitive personal information, opt-out signals and risk assessments",
        "text": (
            "Regulations adopted by the California Privacy Protection Agency, Title 11 of the "
            "California Code of Regulations, Sections 7000 to 7304, set out the operative detail of "
            "the CPRA. Consumers have the right to limit the use and disclosure of sensitive personal "
            "information, including social security numbers, driver's licence numbers, precise "
            "geolocation, racial or ethnic origin, religious beliefs, union membership, contents of "
            "mail and email, genetic data and information concerning health or sex life, to the "
            "purposes permitted by Civil Code Section 1798.121, namely performing services, ensuring "
            "security and integrity, short-term transient use, quality control and specified internal "
            "research. Businesses must provide a limit-the-use-of-sensitive-personal-information link "
            "where they collect sensitive data for inferential purposes. Consumers also hold rights to "
            "know, delete, correct and opt out of sale or sharing, and to non-discrimination; opt-out "
            "preference signals such as the Global Privacy Control must be honoured. Businesses must "
            "conduct and document a risk assessment before initiating processing that presents a "
            "significant risk to consumers' privacy, including selling or sharing personal "
            "information, processing sensitive data, using automated decision-making technology and "
            "training artificial intelligence models on personal information. Risk assessments must be "
            "reviewed and updated at least every three years and provided to the Agency on request, "
            "and cybersecurity audits are required for businesses whose processing presents "
            "significant risk."
        ),
        "tags": ["cpra", "sensitive data", "risk assessment", "opt-out", "global privacy control"],
        "risk_level": "high",
    },
    {
        "id": "us-state-privacy-law-patchwork",
        "jurisdiction": "US",
        "source": "Pile of Law — survey of state comprehensive privacy statutes (adapted)",
        "doc_type": "guidance",
        "title": "Multi-state privacy compliance and processor contract terms",
        "text": (
            "Beyond California, comprehensive consumer privacy statutes are in force in Virginia, "
            "Colorado, Connecticut, Utah, Texas, Oregon, Montana, Delaware, Iowa, Indiana, Tennessee, "
            "New Hampshire, New Jersey, Kentucky, Maryland, Minnesota, Nebraska and Rhode Island, with "
            "effective dates and amendment cycles rolling forward. The core grant is similar, namely "
            "rights to access, correct, delete and obtain a portable copy of personal data and to opt "
            "out of targeted advertising, sale and certain profiling, but the mechanics diverge in "
            "ways that matter to contracting. Colorado, Connecticut and Virginia require a controller "
            "to conduct a data protection assessment for processing that presents a heightened risk of "
            "harm, and several states require recognition of universal opt-out signals. Processor "
            "contracts must typically require the processor to adhere to the controller's instructions, "
            "assist with consumer requests and assessments, ensure confidentiality, engage "
            "subcontractors only under written contract and delete or return data at the controller's "
            "direction; some states require the processor to make available all information necessary "
            "to demonstrate compliance. Maryland and Minnesota impose heightened duties regarding "
            "sensitive data and minors. Washington's My Health My Data Act reaches health data outside "
            "HIPAA and includes a private right of action, and Texas, Illinois and Washington have "
            "specific biometric statutes. Drafters should therefore avoid a single national template "
            "and select governing law, consumer-rights and breach-notification terms by reference to "
            "the states in which the customer's consumers reside."
        ),
        "tags": ["state privacy laws", "privacy", "data protection assessment", "biometric", "multi-state"],
        "risk_level": "medium",
    },
    {
        "id": "us-hipaa-business-associate-agreement",
        "jurisdiction": "US",
        "source": "Pile of Law — 45 C.F.R. §§ 164.502(e), 164.504(e) (adapted)",
        "doc_type": "regulation",
        "title": "HIPAA business associate agreement requirements",
        "text": (
            "Where a vendor creates, receives, maintains or transmits protected health information on "
            "behalf of a covered entity, 45 C.F.R. Sections 164.502(e) and 164.504(e) require a "
            "written business associate agreement. The agreement must: establish the permitted and "
            "required uses and disclosures of protected health information, which may not be expanded "
            "except as permitted by the Privacy Rule; prohibit the business associate from using or "
            "disclosing the information other than as permitted by the agreement or required by law; "
            "require appropriate administrative, physical and technical safeguards and compliance with "
            "the Security Rule at 45 C.F.R. Part 164, Subpart C, including encryption, access controls "
            "and audit logging; require reporting of security incidents and breaches of unsecured "
            "protected health information without unreasonable delay and in no case later than sixty "
            "days after discovery; require the business associate to ensure that any subcontractor "
            "that creates, receives, maintains or transmits protected health information agrees in "
            "writing to the same restrictions and conditions; require access to the information to "
            "enable the covered entity to respond to an individual's request for access and amendment; "
            "require the business associate to make its internal practices, books and records "
            "available to the Secretary of Health and Human Services; and require return or destruction "
            "of the information at termination where feasible."
        ),
        "tags": ["hipaa", "business associate", "protected health information", "security rule", "breach"],
        "risk_level": "high",
    },
    {
        "id": "us-pci-dss-cardholder-data-obligations",
        "jurisdiction": "US",
        "source": "Pile of Law — PCI DSS v4.0 requirements 1-12 (adapted)",
        "doc_type": "guidance",
        "title": "PCI DSS obligations and payment card contract terms",
        "text": (
            "PCI DSS version 4.0, published by the PCI Security Standards Council and enforced through "
            "the card brand operating rules and acquiring bank agreements, applies to every entity "
            "that stores, processes or transmits account data or that can affect the security of the "
            "cardholder data environment. The twelve principal requirements cover network security "
            "controls, secure configuration, protection of stored account data, encryption of "
            "cardholder data in transit, malware protection, secure development and vulnerability "
            "management, access control on a business need to know basis, identification and "
            "authentication including multi-factor authentication, physical security, logging and "
            "monitoring, security testing, and an information security policy supported by risk "
            "assessment. Version 4.0 introduces a targeted risk analysis, requires authenticated "
            "internal vulnerability scans every three months and, for service providers, requires "
            "documented support of customers and integrators. In contracts, the merchant or acquirer "
            "should require the service provider to attest to compliance through an annual Report on "
            "Compliance or Self-Assessment Questionnaire together with an Attestation of Compliance, "
            "to accept responsibility for the portions of the cardholder data environment it controls, "
            "to notify the customer of any security incident or loss of certification within "
            "twenty-four hours, and to permit termination on loss of compliance. Storing sensitive "
            "authentication data after authorisation is prohibited, and the card brands permit the "
            "allocation of fines and assessments where the loss arises from the provider's "
            "non-compliance."
        ),
        "tags": ["pci dss", "cardholder data", "payment cards", "compliance", "security incident"],
        "risk_level": "high",
    },
    {
        "id": "us-sox-internal-controls-and-certification",
        "jurisdiction": "US",
        "source": "Pile of Law — Sarbanes-Oxley Act §§ 302, 404, 802 (adapted)",
        "doc_type": "statute",
        "title": "Sarbanes-Oxley certification and internal control over financial reporting",
        "text": (
            "Sections 302 and 404 of the Sarbanes-Oxley Act of 2002, together with the rules of the "
            "Securities and Exchange Commission and the auditing standards of the Public Company "
            "Accounting Oversight Board, require an issuer's principal executive and financial "
            "officers to certify in each periodic report that they have reviewed the report, that it "
            "does not contain an untrue statement of a material fact or omit a material fact, that the "
            "financial statements fairly present in all material respects the financial condition and "
            "results of operations, that they are responsible for establishing and maintaining "
            "disclosure controls and procedures and internal control over financial reporting, that "
            "they have disclosed to the auditors and the audit committee all significant deficiencies "
            "and material weaknesses in the design or operation of internal control and any fraud "
            "involving management or employees with a significant role in internal control, and that "
            "they have designed and evaluated the effectiveness of those controls. Section 404(b) "
            "requires an attestation on internal control over financial reporting by the registered "
            "public accounting firm. Section 802 imposes criminal penalties for the destruction, "
            "alteration or falsification of records with intent to impede or influence an "
            "investigation. Software and services agreements with issuers should therefore include an "
            "obligation to maintain a control environment that supports the issuer's certification, to "
            "provide SOC 1 Type II reports, to notify the customer of any change in controls that "
            "materially affects the customer's internal control over financial reporting, and to "
            "preserve records for the statutory period."
        ),
        "tags": ["sox", "internal controls", "certification", "audit", "financial reporting"],
        "risk_level": "high",
    },
    {
        "id": "us-sec-materiality-and-incident-disclosure",
        "jurisdiction": "US",
        "source": "Pile of Law — Basic Inc. v. Levinson, 485 U.S. 224 (1988); Form 8-K Item 1.05 (adapted)",
        "doc_type": "case_note",
        "title": "Materiality and securities disclosure of incidents",
        "text": (
            "Case note on materiality and incident disclosure. In Basic Inc. v. Levinson, 485 U.S. 224 "
            "(1988), the Supreme Court held that materiality in the context of contingent or "
            "speculative events depends on balancing the indicated probability that the event will "
            "occur against the anticipated magnitude of the event in light of the totality of the "
            "company's activity; a fact is material if there is a substantial likelihood that a "
            "reasonable investor would consider it important in making an investment decision. The "
            "SEC rules on cybersecurity disclosure, Item 1.05 of Form 8-K and Regulation S-K Item 106, "
            "require disclosure of a cybersecurity incident within four business days after a "
            "determination that the incident is material, together with a description of the material "
            "aspects of the nature, scope and timing of the incident and its material impact or "
            "reasonably likely material impact, and annual disclosure of processes for assessing, "
            "identifying and managing material risks from cybersecurity threats. Item 1.05 does not "
            "require a filing for incidents determined not to be material, and a delay may be "
            "requested where disclosure would pose a substantial risk to national security or public "
            "safety. Contracts with service providers should therefore require notification of an "
            "incident without unreasonable delay and in any event within twenty-four to forty-eight "
            "hours, sufficient information for the customer to make its own materiality assessment, "
            "cooperation with counsel, no unilateral public statements and preservation of forensic "
            "evidence."
        ),
        "tags": ["sec disclosure", "materiality", "cybersecurity incident", "securities", "form 8-k"],
        "risk_level": "high",
    },
    # ------------------------------------------------------------------
    # US — Delaware corporate law
    # ------------------------------------------------------------------
    {
        "id": "us-delaware-board-approval-and-authority",
        "jurisdiction": "US",
        "source": "Pile of Law — 8 Del. C. §§ 141(a), 144, 251, 271 (adapted)",
        "doc_type": "statute",
        "title": "Board approval, corporate authority and interested transactions",
        "text": (
            "Section 141(a) of the Delaware General Corporation Law provides that the business and "
            "affairs of every corporation shall be managed by or under the direction of a board of "
            "directors, except as otherwise provided by statute or in the certificate of "
            "incorporation. Section 251 requires that a merger agreement be adopted by the board of "
            "directors and approved by a majority of the outstanding stock entitled to vote, and "
            "Section 271 requires board approval and, in most cases, stockholder approval for the sale "
            "of all or substantially all assets. Where a transaction is with an interested director or "
            "a controlling stockholder, the board should appoint a committee of disinterested "
            "directors, and Section 144 provides safe harbours where the transaction is approved by a "
            "majority of the disinterested directors or by disinterested stockholders, or is otherwise "
            "fair as to the corporation. Section 157 governs the issuance of rights and options, and "
            "Section 152 governs the consideration for stock. Practitioners should confirm authority "
            "in the certificate of incorporation and bylaws, check for charter provisions requiring "
            "supermajority votes, document that the directors were provided with the information "
            "reasonably necessary to make an informed decision, and record the process in minutes "
            "contemporaneously. Board and committee minutes, resolutions and written consents are the "
            "primary evidence of the approval process in subsequent litigation."
        ),
        "tags": ["delaware", "dgcl", "board approval", "corporate authority", "merger"],
        "risk_level": "medium",
    },
    {
        "id": "us-delaware-fiduciary-duties",
        "jurisdiction": "US",
        "source": "Pile of Law — Revlon, Unocal, Corwin and Match Group line of cases (adapted)",
        "doc_type": "case_note",
        "title": "Fiduciary duties and standards of judicial review",
        "text": (
            "Case note on the standard of review applied to board decisions. The business judgment "
            "rule presumes that directors acted on an informed basis, in good faith and in the honest "
            "belief that the action was in the best interests of the corporation; the plaintiff bears "
            "the burden of rebutting the presumption by showing fraud, waste, bad faith or a disabling "
            "conflict of interest. Where a defensive measure is adopted in response to a threat, "
            "Unocal requires that the response be reasonable in relation to the threat posed. Where "
            "the corporation is for sale or the transaction results in a change of control, Revlon "
            "requires the board to seek the best value reasonably available to stockholders; Revlon "
            "does not mandate a particular process or an auction, but the board must act reasonably and "
            "on an informed basis. Where a controlling stockholder stands on both sides of a "
            "transaction, the entire fairness standard applies unless the transaction is conditioned "
            "on the approval of both an independent special committee and a majority of the minority "
            "stockholders, as held in In re Match Group. Where a stockholder challenges a merger, "
            "Corwin provides that a fully informed, uncoerced vote of disinterested stockholders "
            "invokes the business judgment rule and cleanses the claim. Directors should record the "
            "reasons for the decision, the alternatives considered and the advice received."
        ),
        "tags": ["delaware", "fiduciary duties", "business judgment rule", "revlon", "entire fairness"],
        "risk_level": "high",
    },
    {
        "id": "us-delaware-director-indemnification",
        "jurisdiction": "US",
        "source": "Pile of Law — 8 Del. C. §§ 102(b)(7), 145 (adapted)",
        "doc_type": "statute",
        "title": "Indemnification and advancement of expenses for directors and officers",
        "text": (
            "Section 145 of the Delaware General Corporation Law permits a corporation to indemnify "
            "directors and officers against expenses, judgments, fines and amounts paid in settlement "
            "actually and reasonably incurred in connection with a threatened, pending or completed "
            "action, suit or proceeding, provided the person acted in good faith and in a manner "
            "reasonably believed to be in or not opposed to the best interests of the corporation and, "
            "in a criminal proceeding, had no reasonable cause to believe the conduct was unlawful. In "
            "a derivative action, indemnification is limited to expenses and requires a determination "
            "that the person acted in good faith; no indemnification is permitted where the person is "
            "adjudged liable to the corporation unless the court determines otherwise. Section 145(f) "
            "permits indemnification and advancement in addition to those provided by statute, and "
            "Section 145(e) authorises advancement of expenses on receipt of an undertaking to repay "
            "if it is ultimately determined that the person is not entitled to indemnification. "
            "Section 102(b)(7) permits a charter provision eliminating the personal liability of "
            "directors for breach of the duty of care, but not for breach of the duty of loyalty, acts "
            "not in good faith, intentional misconduct, knowing violations of law, unlawful "
            "distributions or transactions from which the director derived an improper personal "
            "benefit. The corporation may purchase directors and officers liability insurance under "
            "Section 145(g)."
        ),
        "tags": ["delaware", "indemnification", "directors and officers", "advancement", "dgcl"],
        "risk_level": "medium",
    },
    {
        "id": "us-governing-law-and-venue-delaware",
        "jurisdiction": "US",
        "source": "CUAD v1 — Master Services Agreement, cl. 22 (adapted)",
        "doc_type": "contract_clause",
        "title": "Delaware governing law, exclusive forum and jury waiver",
        "text": (
            "22.1 Governing Law. This Agreement shall be governed by and construed in accordance with "
            "the laws of the State of Delaware, without giving effect to any conflict of laws rule or "
            "principle that would cause the application of the law of any other jurisdiction. The "
            "United Nations Convention on Contracts for the International Sale of Goods does not "
            "apply. 22.2 Venue. Each party irrevocably submits to the exclusive jurisdiction of the "
            "Court of Chancery of the State of Delaware and, to the extent that court lacks subject "
            "matter jurisdiction, the Superior Court of the State of Delaware or the United States "
            "District Court for the District of Delaware, for any action arising out of or relating to "
            "this Agreement, and waives any objection based on venue or forum non conveniens. 22.3 "
            "Jury Waiver. Each party knowingly, voluntarily and irrevocably waives any right to a trial "
            "by jury in any action arising out of or relating to this Agreement, and acknowledges that "
            "this waiver is a material inducement to enter into this Agreement. 22.4 Enforcement. Each "
            "party consents to service of process by any means permitted by the forum, including "
            "registered mail, and agrees that a final judgment is conclusive and may be enforced in "
            "any jurisdiction. 22.5 Fees. The prevailing party in any action is entitled to recover "
            "its reasonable attorneys' fees and costs."
        ),
        "tags": ["governing law", "delaware", "forum selection", "jury waiver", "venue"],
        "risk_level": "low",
    },
    {
        "id": "us-arbitration-aaa-federal-arbitration-act",
        "jurisdiction": "US",
        "source": "CUAD v1 — Software Licence Agreement, cl. 23 (adapted)",
        "doc_type": "contract_clause",
        "title": "AAA arbitration, Federal Arbitration Act and class action waiver",
        "text": (
            "23.1 Agreement to Arbitrate. Any dispute, claim or controversy arising out of or relating "
            "to this Agreement or the breach, termination, enforcement, interpretation or validity of "
            "it, including the determination of the scope or applicability of this agreement to "
            "arbitrate, shall be determined by binding arbitration administered by the American "
            "Arbitration Association under its Commercial Arbitration Rules and Mediation Procedures, "
            "before one arbitrator. 23.2 Seat and Procedure. The seat of the arbitration shall be "
            "Wilmington, Delaware. The arbitrator shall be a retired judge or an attorney with at "
            "least fifteen years of experience in commercial technology matters. The Federal "
            "Arbitration Act, 9 U.S.C. Sections 1 to 16, governs the interpretation and enforcement of "
            "this Clause. Judgment on the award may be entered in any court of competent jurisdiction. "
            "23.3 Discovery and Remedies. The arbitrator may order discovery consistent with the "
            "Federal Rules of Civil Procedure but limited to what is relevant and material, and may "
            "award any remedy available at law or in equity, including injunctive relief. 23.4 "
            "Carve-outs. Either party may seek temporary or preliminary injunctive relief in court to "
            "protect its intellectual property or confidential information pending arbitration. 23.5 "
            "Class Waiver. All disputes shall be arbitrated on an individual basis only; there shall be "
            "no class, collective or representative arbitration, and the arbitrator may not consolidate "
            "claims without the consent of all parties."
        ),
        "tags": ["arbitration", "aaa", "federal arbitration act", "class waiver", "dispute resolution"],
        "risk_level": "low",
    },
    {
        "id": "us-state-ai-laws-and-automated-decisions",
        "jurisdiction": "US",
        "source": "Pile of Law — state and local AI statutes and rules (adapted)",
        "doc_type": "guidance",
        "title": "State and local artificial intelligence and automated decision rules",
        "text": (
            "There is no single United States artificial intelligence statute, so compliance is "
            "assembled from sectoral and state law. Colorado's artificial intelligence Act imposes "
            "duties on developers and deployers of high-risk systems, including impact assessments, "
            "disclosure to consumers, an opportunity to correct data and a right to appeal an adverse "
            "consequential decision, and Illinois, Texas and Washington regulate biometric identifiers "
            "and facial recognition, with Illinois imposing a private right of action and statutory "
            "damages for failure to obtain written consent and publish a retention schedule. New York "
            "City Local Law 144 requires an annual independent bias audit of automated employment "
            "decision tools and publication of the results together with a candidate notice. Utah "
            "requires disclosure when a consumer interacts with generative artificial intelligence, "
            "and California's bot disclosure statute requires clear identification of automated "
            "accounts. Sector regulators add further expectations: the Equal Employment Opportunity "
            "Commission and the Department of Justice have addressed disparate impact in algorithmic "
            "hiring, the Consumer Financial Protection Bureau applies adverse action notice "
            "requirements to credit models, and the Federal Trade Commission has brought enforcement "
            "actions for deceptive accuracy claims and for unfair use of sensitive data in training. "
            "Contracts should allocate responsibility for notices, impact assessments, bias testing "
            "and audit evidence between the vendor and the deployer, and should not leave both parties "
            "assuming the other is responsible."
        ),
        "tags": ["ai regulation", "automated decision", "bias audit", "biometric", "state law"],
        "risk_level": "medium",
    },
    # ------------------------------------------------------------------
    # US — liability, indemnity, termination
    # ------------------------------------------------------------------
    {
        "id": "us-limitation-of-liability-cap",
        "jurisdiction": "US",
        "source": "CUAD v1 — Master Services Agreement, cl. 24 (adapted)",
        "doc_type": "contract_clause",
        "title": "Aggregate liability cap and UCC enforceability limits",
        "text": (
            "24.1 Cap. Except as provided in Section 24.3, the aggregate liability of each party for "
            "all claims arising out of or relating to this Agreement, whether in contract, tort, "
            "warranty, strict liability, statute or otherwise, shall not exceed the greater of (a) the "
            "total fees paid and payable by Customer under the applicable Order Form during the twelve "
            "months preceding the event giving rise to the claim, and (b) one hundred thousand "
            "dollars. 24.2 Single Recovery. Multiple claims shall not enlarge the limitation; all "
            "claims arising from the same or a related series of facts constitute a single claim. 24.3 "
            "Exclusions from the Cap. The limitation does not apply to: Customer's payment "
            "obligations; either party's indemnification obligations under Section 26; liability for "
            "death or bodily injury or damage to tangible property caused by negligence; breach of "
            "confidentiality; infringement or misappropriation of the other party's intellectual "
            "property; or fraud, wilful misconduct or gross negligence. 24.4 Enforceability. The "
            "parties intend that this Section be enforced to the maximum extent permitted by "
            "applicable law. The parties acknowledge that under Section 2-719 of the Uniform "
            "Commercial Code a limitation of consequential damages may be unenforceable where it would "
            "be unconscionable, and that a limitation which fails of its essential purpose may not bar "
            "recovery; accordingly, the remedies expressly provided in this Agreement are intended to "
            "be minimum adequate remedies."
        ),
        "tags": ["limitation of liability", "liability cap", "ucc", "unconscionability", "damages"],
        "risk_level": "medium",
    },
    {
        "id": "us-consequential-damages-waiver",
        "jurisdiction": "US",
        "source": "CUAD v1 — Service Agreement, cl. 25 (adapted)",
        "doc_type": "contract_clause",
        "title": "Mutual waiver of consequential and indirect damages",
        "text": (
            "25.1 Waiver of Consequential Damages. Except as provided in Section 25.3, neither party "
            "shall be liable to the other for any consequential, incidental, indirect, special, "
            "exemplary or punitive damages, or for any lost profits, lost revenue, lost business "
            "opportunity, loss of goodwill, loss of data or cost of substitute services, however "
            "caused and under any theory of liability, whether in contract, tort, warranty, strict "
            "liability or otherwise, and whether or not the party was advised of the possibility of "
            "such damages. 25.2 Direct Damages. The parties agree that the categories waived in Section "
            "25.1 are waived regardless of whether a court would characterise them as direct or "
            "indirect, and that this allocation reflects the parties' agreement that the risk of "
            "unforeseeable loss should be borne by the party better able to insure against or mitigate "
            "it, consistent with the principle of Hadley v. Baxendale, 156 Eng. Rep. 145 (1854). 25.3 "
            "Exceptions. The waiver does not apply to the excluded matters listed in Section 24.3, to "
            "amounts payable to third parties under an indemnity, or to a breach of Section 14 (Data "
            "Protection) where the loss is a regulatory fine imposed on Customer as a result of "
            "Supplier's breach. 25.4 Acknowledgment. Each party acknowledges that the fees reflect "
            "this allocation of risk and that the waiver is a material term of the bargain."
        ),
        "tags": ["consequential damages", "indirect damages", "waiver", "lost profits", "hadley"],
        "risk_level": "medium",
    },
    {
        "id": "us-indemnification-third-party-claims",
        "jurisdiction": "US",
        "source": "CUAD v1 — Software Licence Agreement, cl. 26 (adapted)",
        "doc_type": "contract_clause",
        "title": "Mutual indemnification for third-party claims",
        "text": (
            "26.1 Supplier Indemnity. Supplier shall defend, indemnify and hold harmless Customer and "
            "its officers, directors, employees and affiliates from and against any and all "
            "third-party claims, actions, losses, damages, liabilities, fines, penalties, costs and "
            "expenses, including reasonable attorneys' fees, arising out of or relating to: (a) any "
            "allegation that the Services or Deliverables infringe or misappropriate a United States "
            "patent, copyright, trademark, trade secret or other intellectual property right; (b) "
            "Supplier's breach of its confidentiality or data protection obligations; (c) bodily "
            "injury, death or damage to tangible property caused by Supplier's negligent acts or "
            "omissions; and (d) Supplier's violation of applicable law, including the Foreign Corrupt "
            "Practices Act and export control and sanctions laws. 26.2 Customer Indemnity. Customer "
            "shall defend, indemnify and hold harmless Supplier from and against claims arising out of "
            "Customer's materials or Customer's violation of applicable law. 26.3 Procedure. The "
            "indemnified party shall give prompt written notice, tender control of the defence, provide "
            "reasonable cooperation at the indemnifying party's expense, and make no admission or "
            "settlement without consent. 26.4 Settlement. The indemnifying party may not settle a claim "
            "in a manner that imposes any non-monetary obligation on, or includes an admission of "
            "liability by, the indemnified party without that party's written consent."
        ),
        "tags": ["indemnification", "indemnity", "third-party claims", "ip infringement", "defence costs"],
        "risk_level": "medium",
    },
    {
        "id": "us-termination-for-convenience",
        "jurisdiction": "US",
        "source": "CUAD v1 — Maintenance and Support Agreement, cl. 27 (adapted)",
        "doc_type": "contract_clause",
        "title": "Termination for convenience, for cause and for legal compliance",
        "text": (
            "27.1 Termination for Convenience. Customer may terminate this Agreement or any Order Form "
            "for convenience, in whole or in part, upon sixty days prior written notice to Supplier. "
            "Supplier may terminate this Agreement for convenience upon one hundred eighty days prior "
            "written notice, provided that Supplier may not exercise this right during the first "
            "twenty-four months of the initial term. 27.2 Effect on Fees. Upon Customer's termination "
            "for convenience, Customer shall pay all fees accrued through the effective date of "
            "termination and any pre-approved, non-cancellable third-party commitments expressly "
            "incurred by Supplier for Customer's benefit, and Supplier shall refund any prepaid fees "
            "covering the period after termination. 27.3 Termination for Cause. Either party may "
            "terminate this Agreement upon written notice if the other party materially breaches this "
            "Agreement and fails to cure within thirty days after receiving written notice describing "
            "the breach, or immediately upon a party's insolvency, assignment for the benefit of "
            "creditors, filing of a petition in bankruptcy that is not dismissed within sixty days, or "
            "cessation of business. 27.4 Repeated Breach. Customer may terminate immediately if "
            "Supplier fails to meet the availability commitment in three consecutive months. 27.5 "
            "Termination for Legal Compliance. Either party may terminate immediately if continued "
            "performance would violate applicable law or a sanctions programme, provided it gives "
            "written notice and a reasonable opportunity to implement a compliant alternative where "
            "one exists."
        ),
        "tags": ["termination for convenience", "termination for cause", "bankruptcy", "cure period"],
        "risk_level": "low",
    },
    {
        "id": "us-termination-for-cause-and-cure",
        "jurisdiction": "US",
        "source": "CUAD v1 — Service Agreement, cl. 27.3 (adapted); internal negotiation playbook",
        "doc_type": "playbook",
        "title": "Negotiation playbook for termination and cure periods",
        "text": (
            "Negotiation playbook for termination and cure provisions in United States commercial "
            "contracts. Preferred customer position: termination for cause after thirty days written "
            "notice and failure to cure a material breach; immediate termination for insolvency, "
            "fraud, wilful misconduct, loss of a required licence, failure to maintain required "
            "insurance, or a security incident caused by gross negligence; termination for repeated "
            "breach of the same obligation following two prior notices within twelve months; and "
            "termination of any affected Order Form without terminating the master agreement. "
            "Preferred supplier position: notice must describe the breach with specificity; the cure "
            "period runs from receipt of notice and may be extended where cure reasonably requires "
            "more than thirty days, provided the breaching party diligently pursues a remediation "
            "plan; disputes about whether a breach is material go to the escalation process before "
            "termination; and payment obligations survive. Drafting points: define material breach by "
            "reference to measurable service levels or deliverables rather than a general standard; "
            "provide that termination is without prejudice to accrued rights and to provisions that "
            "survive; require the terminating party to mitigate; address the return or destruction of "
            "confidential information and customer data; and confirm that termination does not relieve "
            "the customer of the obligation to pay for services rendered. Avoid automatic termination "
            "for a failure that is capable of cure, and avoid cross-default provisions that allow "
            "termination of unrelated order forms."
        ),
        "tags": ["termination", "cure period", "material breach", "playbook", "exit rights"],
        "risk_level": "medium",
    },
    # ------------------------------------------------------------------
    # US — intellectual property, confidentiality, personnel
    # ------------------------------------------------------------------
    {
        "id": "us-ip-ownership-work-made-for-hire",
        "jurisdiction": "US",
        "source": "CUAD v1 — Development Agreement, cl. 28 (adapted)",
        "doc_type": "contract_clause",
        "title": "Work made for hire, assignment of work product and licence-back",
        "text": (
            "28.1 Customer Materials. Customer retains all right, title and interest in Customer "
            "Materials and Customer Data. 28.2 Work Product. Supplier agrees that all deliverables, "
            "software, documentation, designs, inventions, improvements and other work product created "
            "by Supplier specifically for Customer under this Agreement, excluding Supplier "
            "Pre-existing Materials, constitute works made for hire under the United States Copyright "
            "Act, 17 U.S.C. Section 101, to the maximum extent permitted by law. To the extent any "
            "element does not qualify as a work made for hire, Supplier hereby irrevocably assigns to "
            "Customer all right, title and interest in it, including all copyrights, patents, trade "
            "secrets, mask work rights and moral rights to the extent waivable, and agrees to execute "
            "further documents and to cause its employees and contractors to do the same. 28.3 "
            "Licence-back. Customer grants Supplier a non-exclusive, worldwide, royalty-free licence to "
            "use and reproduce the work product solely to perform this Agreement and to improve its "
            "general products and services, provided that Supplier does not disclose Customer "
            "Confidential Information. 28.4 Supplier Pre-existing Materials. Supplier grants Customer a "
            "perpetual, irrevocable, worldwide, royalty-free, non-exclusive licence to use Supplier "
            "Pre-existing Materials embedded in the deliverables, including any materials required to "
            "use the deliverables for their intended purpose. 28.5 Personnel. Supplier shall obtain "
            "written assignments from all employees, contractors and subcontractors who contribute to "
            "the work product."
        ),
        "tags": ["intellectual property", "work made for hire", "assignment", "licence back", "ownership"],
        "risk_level": "medium",
    },
    {
        "id": "us-open-source-software-diligence",
        "jurisdiction": "US",
        "source": "Pile of Law — open source diligence memorandum (adapted)",
        "doc_type": "playbook",
        "title": "Open source diligence, copyleft exposure and remediation",
        "text": (
            "Playbook for open source diligence in United States acquisitions and enterprise "
            "contracts. Require a complete software composition analysis of the code base using at "
            "least two recognised scanners, together with a written bill of materials in SPDX or "
            "CycloneDX format, and reconcile the results against the build system and package "
            "manifests. Triage findings into four buckets: permissive licences such as MIT, BSD, "
            "Apache-2.0 and ISC that require only notice retention; weak copyleft licences such as "
            "LGPL, MPL and EPL that require the ability to relink or to publish modifications to the "
            "licensed files; strong copyleft licences such as GPL and AGPL that can reach proprietary "
            "code through static or dynamic linking and, in the case of AGPL, through network use; and "
            "prohibited or unapproved licences, including those with non-commercial, field-of-use or "
            "advertising terms. Inspect for the absence of licence text, files marked with conflicting "
            "notices and code copied from repositories without a licence, all of which create "
            "infringement risk and expose the customer to claims from contributors and from "
            "enforcement entities such as the Software Freedom Conservancy. Negotiate a representation "
            "that no open source component has been used in a manner that requires disclosure of "
            "proprietary source code, a covenant to remediate within a defined period, an indemnity "
            "for breach, audit rights and delivery of a remediation report before closing where the "
            "product is central to the transaction."
        ),
        "tags": ["open source", "copyleft", "sbom", "gpl", "diligence", "indemnity"],
        "risk_level": "high",
    },
    {
        "id": "us-confidentiality-and-trade-secrets",
        "jurisdiction": "US",
        "source": "CUAD v1 — Non-Disclosure Agreement, cl. 29 (adapted)",
        "doc_type": "contract_clause",
        "title": "Confidentiality, trade secret protection and whistleblower immunity",
        "text": (
            "29.1 Definition. Confidential Information means non-public information disclosed by or on "
            "behalf of a party, in any form, that is designated as confidential or that reasonably "
            "should be understood to be confidential, including source code, technical data, product "
            "roadmaps, pricing, customer lists, business plans, security information and Personal "
            "Information. 29.2 Obligations. The receiving party shall use the disclosing party's "
            "Confidential Information solely to perform this Agreement, shall protect it with at least "
            "a reasonable degree of care and no less than the care it uses for its own similar "
            "information, and shall restrict access to employees and contractors with a need to know "
            "who are bound by written confidentiality obligations. 29.3 Trade Secrets. The parties "
            "acknowledge that certain Confidential Information constitutes a trade secret under the "
            "Defend Trade Secrets Act of 2016, 18 U.S.C. Section 1836, and applicable state law, and "
            "that obligations with respect to trade secrets continue for so long as the information "
            "remains a trade secret. 29.4 Immunity Notice. Under 18 U.S.C. Section 1833(b), an "
            "individual may not be held criminally or civilly liable for disclosing a trade secret in "
            "confidence to a government official or an attorney solely for the purpose of reporting or "
            "investigating a suspected violation of law, or under seal in a court filing. 29.5 "
            "Compelled Disclosure. The receiving party shall provide prompt notice where lawful and "
            "reasonable assistance to contest. 29.6 Survival. These obligations survive for five years "
            "after termination and indefinitely for trade secrets."
        ),
        "tags": ["confidentiality", "trade secrets", "dtsa", "nda", "survival"],
        "risk_level": "medium",
    },
    {
        "id": "us-employment-classification-and-ip-assignment",
        "jurisdiction": "US",
        "source": "Pile of Law — Dynamex Operations West, Inc. v. Superior Court, 4 Cal. 5th 903 (2018) (adapted)",
        "doc_type": "guidance",
        "title": "Worker classification and invention assignment by employees and contractors",
        "text": (
            "Guidance on contractor classification and invention assignment in the United States. The "
            "Internal Revenue Service and the Department of Labor apply multi-factor tests, and "
            "California applies the stricter ABC test established in Dynamex Operations West, Inc. v. "
            "Superior Court, 4 Cal. 5th 903 (2018) and codified in Labor Code Section 2775, under "
            "which a worker is presumed to be an employee unless the hiring entity demonstrates that "
            "the worker is free from the control and direction of the hirer, performs work outside the "
            "usual course of the hiring entity's business, and is customarily engaged in an "
            "independently established trade, occupation or business. Misclassification exposes the "
            "principal to wage and hour liability, expense reimbursement claims, unpaid payroll taxes "
            "and penalties, and representative actions under the Private Attorneys General Act. "
            "Contracts should be reviewed for indicia of control: fixed schedules, exclusive service, "
            "use of company equipment, integration into the organisation chart and day-to-day "
            "supervision. On intellectual property, employee inventions are governed by state "
            "statutes, notably California Labor Code Sections 2870 to 2872, which void assignments of "
            "inventions developed entirely on the employee's own time without use of the employer's "
            "resources and unrelated to the employer's business. Agreements with contractors should "
            "contain present-tense assignments, work-made-for-hire language, confidentiality and "
            "return-of-materials covenants and a schedule of prior inventions."
        ),
        "tags": ["employment", "contractor classification", "abc test", "invention assignment", "california"],
        "risk_level": "medium",
    },
    {
        "id": "us-non-compete-enforceability",
        "jurisdiction": "US",
        "source": "Pile of Law — state non-compete statutes and federal rulemaking (adapted)",
        "doc_type": "case_note",
        "title": "Enforceability of non-compete and non-solicitation covenants",
        "text": (
            "Case note on restrictive covenants following the recent wave of state and federal action. "
            "California Business and Professions Code Section 16600 voids contracts in restraint of "
            "trade, and Section 16600.5, effective in 2024, extends that policy to contracts signed "
            "out of state where the employee later works in California, subject to litigation about "
            "its extraterritorial reach. Minnesota banned new non-competition agreements effective "
            "July 2023, and Colorado, Illinois, Maine, Maryland, Massachusetts, Nevada, New Hampshire, "
            "Oregon, Rhode Island, Virginia, Washington and the District of Columbia impose income "
            "thresholds, notice requirements or outright bans, particularly for lower wage workers. "
            "The Federal Trade Commission adopted a rule in 2024 declaring most employee non-competes "
            "unfair methods of competition; enforcement has been stayed by litigation and the rule's "
            "ultimate fate is uncertain, so employers should not rely on it. What generally survives "
            "is narrower: non-solicitation of customers and employees, confidentiality, invention "
            "assignment, non-disparagement where lawful, and garden leave. Drafting recommendations: "
            "use state-specific carve-outs and severability clauses; identify the legitimate "
            "protectable interest; tie any restriction to a defined territory, duration and activity; "
            "provide consideration; and notify the employee that the covenant may not be enforceable "
            "in certain states. For senior executives, a sale-of-business exception receives more "
            "favourable treatment."
        ),
        "tags": ["non-compete", "restrictive covenants", "non-solicitation", "trade secrets", "employment"],
        "risk_level": "high",
    },
    {
        "id": "us-fees-invoicing-taxes",
        "jurisdiction": "US",
        "source": "CUAD v1 — Master Services Agreement, cl. 30 (adapted)",
        "doc_type": "contract_clause",
        "title": "Fees, invoicing, late charges, taxes and set-off",
        "text": (
            "30.1 Fees and Payment. Customer shall pay the fees stated in each Order Form in United "
            "States dollars. Invoices are issued monthly in arrears for subscription fees and for "
            "professional services as delivered. Customer shall pay all undisputed amounts within "
            "thirty days of the invoice date. 30.2 Late Payment. Overdue undisputed amounts accrue "
            "interest at the lesser of one and one-half per cent per month or the highest rate "
            "permitted by applicable law, calculated from the due date until paid, and Customer shall "
            "reimburse Supplier's reasonable costs of collection, including attorneys' fees. Supplier "
            "may suspend the Services if an undisputed invoice remains unpaid more than thirty days "
            "after written notice. 30.3 Disputed Amounts. Customer may withhold only amounts disputed "
            "in good faith, provided that it notifies Supplier within fifteen days after the invoice "
            "date and pays the undisputed portion. 30.4 Taxes. Fees are exclusive of sales, use, value "
            "added, gross receipts, excise and similar taxes, which Customer shall pay, excluding taxes "
            "based on Supplier's net income, property and employment. If Customer claims an exemption, "
            "it shall provide a valid exemption certificate. 30.5 Expenses. Reasonable, pre-approved "
            "travel and out-of-pocket expenses are reimbursed at cost against receipts. 30.6 Set-off. "
            "Neither party may set off amounts owed under this Agreement against other amounts except "
            "as permitted by applicable law."
        ),
        "tags": ["payment terms", "invoicing", "late charges", "sales tax", "set-off", "expenses"],
        "risk_level": "low",
    },
    # ------------------------------------------------------------------
    # US — service delivery, risk management and regulatory compliance
    # ------------------------------------------------------------------
    {
        "id": "us-service-levels-and-service-credits",
        "jurisdiction": "US",
        "source": "CUAD v1 — Service Level Agreement, cl. 31 (adapted)",
        "doc_type": "contract_clause",
        "title": "Availability commitment, service credits and chronic failure",
        "text": (
            "31.1 Service Levels. Supplier shall make the Services available at least 99.95 per cent "
            "of the time in each calendar month, measured as the total minutes in the month less the "
            "minutes of unplanned unavailability, divided by the total minutes in the month, excluding "
            "scheduled maintenance of up to four hours per month announced at least five business days "
            "in advance, emergency security maintenance, and failures of Customer's systems or of "
            "third-party networks outside Supplier's reasonable control. 31.2 Service Credits. If "
            "availability falls below the commitment, Customer shall receive a credit against the next "
            "invoice equal to ten per cent of the monthly fee for availability below 99.95 per cent but "
            "at or above 99.5 per cent, twenty-five per cent below 99.5 per cent but at or above 99.0 "
            "per cent, and fifty per cent below 99.0 per cent, capped at fifty per cent of the monthly "
            "fee for the affected service. 31.3 Request and Application. Customer must request credits "
            "within thirty days after the end of the affected month, and credits are applied against "
            "future invoices and may not be redeemed for cash. 31.4 Chronic Failure. If availability "
            "falls below 99.5 per cent in any three months within a rolling twelve-month period, "
            "Customer may terminate the affected Services without liability and receive a pro rata "
            "refund of prepaid fees. 31.5 Sole Remedy. Except for chronic failure and termination "
            "rights, service credits are Customer's sole and exclusive remedy for availability "
            "failures."
        ),
        "tags": ["service levels", "sla", "service credits", "availability", "uptime"],
        "risk_level": "medium",
    },
    {
        "id": "us-warranties-and-remedies",
        "jurisdiction": "US",
        "source": "CUAD v1 — Software Licence Agreement, cl. 32 (adapted)",
        "doc_type": "contract_clause",
        "title": "Express warranties, exclusive remedy and implied warranty disclaimer",
        "text": (
            "32.1 Warranties. Supplier warrants that: the Services will perform materially in "
            "accordance with the documentation; the Services will be provided in a professional and "
            "workmanlike manner by qualified personnel; the Deliverables will be free from material "
            "defects for ninety days after acceptance; Supplier will comply with all laws applicable "
            "to its performance, including the Foreign Corrupt Practices Act, export control and "
            "sanctions laws, and applicable data protection and privacy laws; and Supplier will not "
            "introduce malicious code into the Customer environment. 32.2 Exclusive Remedy. If Supplier "
            "breaches a warranty, it shall, at its expense, re-perform the deficient Services or repair "
            "or replace the non-conforming Deliverable within thirty days after written notice or, if "
            "it fails to do so, Customer may terminate the affected Order Form and receive a refund of "
            "prepaid fees for the affected period. 32.3 Disclaimer. EXCEPT FOR THE EXPRESS WARRANTIES "
            "IN THIS SECTION, THE SERVICES AND DELIVERABLES ARE PROVIDED AS IS, AND SUPPLIER DISCLAIMS "
            "ALL IMPLIED WARRANTIES, INCLUDING THE IMPLIED WARRANTIES OF MERCHANTABILITY, FITNESS FOR A "
            "PARTICULAR PURPOSE, TITLE AND NON-INFRINGEMENT, AND ANY WARRANTY ARISING FROM COURSE OF "
            "DEALING OR USAGE OF TRADE. Some jurisdictions do not permit the exclusion of implied "
            "warranties, so the foregoing exclusion may not apply to the extent prohibited by law, and "
            "the parties acknowledge that the Magnuson-Moss Warranty Act may limit disclaimer "
            "obligations where a written warranty is given to a consumer."
        ),
        "tags": ["warranties", "disclaimer", "implied warranty", "remedies", "magnuson-moss"],
        "risk_level": "medium",
    },
    {
        "id": "us-force-majeure-and-business-continuity",
        "jurisdiction": "US",
        "source": "CUAD v1 — Service Agreement, cl. 33 (adapted)",
        "doc_type": "contract_clause",
        "title": "Force majeure, business continuity and disaster recovery",
        "text": (
            "33.1 Force Majeure. Neither party is liable for a failure or delay in performance caused "
            "by an event beyond its reasonable control, including acts of God, fire, flood, earthquake, "
            "severe weather, epidemic or pandemic, war, terrorism, civil disturbance, embargo, "
            "sanctions, government order, labour dispute other than one involving its own workforce, or "
            "failure of the public internet or power grid. 33.2 Notice and Mitigation. The affected "
            "party shall notify the other within five business days, describe the event and its "
            "expected duration, use commercially reasonable efforts to mitigate and resume "
            "performance, and implement its business continuity plan. The excuse applies only while "
            "the event and its effects continue and does not excuse payment obligations accrued before "
            "the event. 33.3 Extended Event. If the event continues for more than thirty consecutive "
            "days and prevents performance of a material portion of the Services, either party may "
            "terminate the affected Order Form on written notice without liability, and Supplier shall "
            "refund prepaid fees for the terminated period. 33.4 Business Continuity. Supplier shall "
            "maintain a documented disaster recovery plan with a recovery time objective of four hours "
            "and a recovery point objective of fifteen minutes for production data, test it at least "
            "annually, provide the test results and any remediation plan on request, and notify "
            "Customer of material changes. 33.5 No Hardship Relief. Economic hardship, increased cost "
            "or adverse market conditions do not constitute force majeure, and the doctrine of "
            "commercial impracticability under Section 2-615 of the Uniform Commercial Code applies "
            "only as provided by law."
        ),
        "tags": ["force majeure", "business continuity", "disaster recovery", "impracticability"],
        "risk_level": "medium",
    },
    {
        "id": "us-assignment-subcontracting-change-of-control",
        "jurisdiction": "US",
        "source": "CUAD v1 — Master Services Agreement, cl. 34 (adapted)",
        "doc_type": "contract_clause",
        "title": "Assignment, change of control and subcontracting",
        "text": (
            "34.1 Assignment. Neither party may assign this Agreement, in whole or in part, whether by "
            "operation of law or otherwise, without the prior written consent of the other party, "
            "except that either party may assign this Agreement to a successor in interest in "
            "connection with a merger, acquisition or sale of all or substantially all of its assets "
            "or voting securities, provided that the assignee assumes all obligations, is not a "
            "competitor of the other party and is not listed on a restricted party list maintained by "
            "the Office of Foreign Assets Control. Any purported assignment in violation of this "
            "Section is void. 34.2 Change of Control. Customer may terminate this Agreement upon sixty "
            "days written notice if Supplier undergoes a change of control resulting in a direct "
            "competitor of Customer acquiring control of Supplier, or if the change of control would "
            "reasonably be expected to impair Supplier's ability to perform its obligations. 34.3 "
            "Subcontracting. Supplier may use subcontractors to perform the Services provided that "
            "Supplier remains fully responsible for their performance and compliance, imposes written "
            "obligations no less protective than those in this Agreement, and identifies subcontractors "
            "performing material functions on request. 34.4 Binding Effect. This Agreement binds and "
            "benefits the parties and their permitted successors and assigns."
        ),
        "tags": ["assignment", "change of control", "subcontracting", "successors", "ofac"],
        "risk_level": "medium",
    },
    {
        "id": "us-audit-rights-and-record-keeping",
        "jurisdiction": "US",
        "source": "CUAD v1 — Outsourcing Agreement, cl. 35 (adapted)",
        "doc_type": "contract_clause",
        "title": "Audit rights, third-party reports and regulatory examination support",
        "text": (
            "35.1 Audit. During the term and for two years after termination, Supplier shall maintain "
            "accurate records relating to the Services, including security, availability, processing "
            "integrity, confidentiality and privacy controls, personnel training, incident response "
            "and compliance certifications. Upon at least thirty days written notice, Customer may "
            "audit Supplier's compliance with this Agreement not more than once per twelve-month "
            "period, or more frequently if a security incident or regulatory inquiry occurs. Audits "
            "shall be conducted during business hours, in a manner that does not unreasonably interfere "
            "with operations, and subject to reasonable confidentiality obligations. 35.2 Third-Party "
            "Reports. Supplier may satisfy the audit right by providing a current SOC 2 Type II report, "
            "an ISO/IEC 27001 certificate, a PCI DSS Attestation of Compliance or an equivalent "
            "independent report, together with a bridge letter covering any period after the report "
            "date. 35.3 Regulatory Cooperation. Supplier shall cooperate with Customer's regulators, "
            "including by responding to examination requests and providing information reasonably "
            "required for Customer's filings and disclosures. 35.4 Costs. Customer bears the cost of "
            "the audit unless the audit reveals a material breach or an overcharge of more than three "
            "per cent, in which case Supplier shall reimburse the reasonable audit costs and credit the "
            "overcharge."
        ),
        "tags": ["audit rights", "soc 2", "record keeping", "regulatory examination", "compliance"],
        "risk_level": "medium",
    },
    {
        "id": "us-export-control-and-sanctions",
        "jurisdiction": "US",
        "source": "Pile of Law — 15 C.F.R. Parts 730-774; 22 C.F.R. Parts 120-130 (adapted)",
        "doc_type": "regulation",
        "title": "Export Administration Regulations, ITAR and OFAC sanctions",
        "text": (
            "United States export controls are administered principally by the Bureau of Industry and "
            "Security under the Export Administration Regulations, 15 C.F.R. Parts 730 to 774, which "
            "govern dual-use items listed on the Commerce Control List and impose licence requirements "
            "based on the item's classification, the destination, the end user and the end use. The "
            "International Traffic in Arms Regulations, 22 C.F.R. Parts 120 to 130, control defence "
            "articles and defence services on the United States Munitions List, and require "
            "registration with the Directorate of Defense Trade Controls for manufacturers, exporters "
            "and brokers of defence articles. The Office of Foreign Assets Control administers "
            "sanctions programmes under 31 C.F.R. Chapter V, including comprehensive embargoes and "
            "list-based restrictions such as the Specially Designated Nationals and Blocked Persons "
            "List and the Entity List. Critical compliance points for contracts include: deemed "
            "exports and deemed re-exports of controlled technology to foreign nationals, including "
            "employees and contractors located in the United States; the prohibition on providing "
            "services to listed parties or for prohibited end uses; the application of the general "
            "prohibitions to software and technology transmitted electronically; and recordkeeping for "
            "at least five years. Contracts should require the counterparty to represent that it is "
            "not a restricted party, to obtain any required authorisation, to cooperate on "
            "classification and screening, and to permit suspension or termination without liability "
            "where continued performance would violate export controls or sanctions."
        ),
        "tags": ["export control", "sanctions", "ear", "itar", "ofac", "screening"],
        "risk_level": "high",
    },
    {
        "id": "us-fcpa-anti-bribery",
        "jurisdiction": "US",
        "source": "Pile of Law — 15 U.S.C. §§ 78dd-1 to 78dd-3 (adapted)",
        "doc_type": "statute",
        "title": "Foreign Corrupt Practices Act anti-bribery and accounting provisions",
        "text": (
            "The Foreign Corrupt Practices Act, 15 U.S.C. Sections 78dd-1 to 78dd-3 and Section 78m(b), "
            "prohibits the offer, promise, authorisation or payment of anything of value to a foreign "
            "official, foreign political party or candidate for the purpose of obtaining or retaining "
            "business or securing an improper advantage. It applies to issuers, to domestic concerns "
            "and to persons acting while in the United States, as well as to third parties acting on "
            "their behalf with knowledge that all or a portion of the payment will be passed to a "
            "foreign official. The accounting provisions require issuers to keep books and records "
            "that accurately and fairly reflect transactions and to maintain a system of internal "
            "accounting controls sufficient to provide reasonable assurances that transactions are "
            "authorised and recorded properly. The Department of Justice and the Securities and "
            "Exchange Commission have resolved enforcement actions based on the acts of agents, "
            "distributors, joint venture partners and subcontractors, so contracts should include "
            "anti-corruption representations; an obligation to comply with the FCPA and, where "
            "applicable, the UK Bribery Act 2010; a requirement to maintain policies, training and "
            "internal controls; audit and termination rights; and a requirement to notify the "
            "counterparty of any government investigation. Facilitation payments are not exempt under "
            "the FCPA, and the affirmative defence for reasonable and bona fide promotional expenses is "
            "narrow."
        ),
        "tags": ["fcpa", "anti-bribery", "corruption", "internal controls", "compliance"],
        "risk_level": "high",
    },
    {
        "id": "us-insurance-requirements",
        "jurisdiction": "US",
        "source": "CUAD v1 — Services Agreement, cl. 36 (adapted)",
        "doc_type": "contract_clause",
        "title": "Insurance coverages, evidence and additional insured status",
        "text": (
            "36.1 Coverages. Throughout the term and for two years after termination, Supplier shall "
            "maintain, with insurers rated A- or better by A.M. Best, the following policies: (a) "
            "commercial general liability, including contractual liability, products and completed "
            "operations, of at least two million dollars per occurrence and five million dollars in the "
            "aggregate; (b) professional liability or technology errors and omissions, including cyber "
            "liability and privacy breach response, of at least five million dollars per claim and in "
            "the aggregate; (c) workers compensation as required by statute and employers liability of "
            "at least one million dollars; (d) commercial automobile liability where applicable; and "
            "(e) umbrella or excess liability of at least five million dollars. 36.2 Evidence. "
            "Supplier shall furnish certificates of insurance and additional insured endorsements "
            "naming Customer before the start of work, at each renewal and upon request, and shall "
            "provide at least thirty days notice of cancellation or material reduction in coverage. "
            "36.3 Waiver of Subrogation. Supplier's insurers waive subrogation against Customer to the "
            "extent permitted by the policies. 36.4 No Limitation of Liability. The insurance "
            "requirements do not limit Supplier's liability, and the limits of insurance do not "
            "increase the liability cap in Section 24."
        ),
        "tags": ["insurance", "errors and omissions", "cyber liability", "additional insured"],
        "risk_level": "medium",
    },
    {
        "id": "us-acceptance-testing-deemed-acceptance",
        "jurisdiction": "US",
        "source": "CUAD v1 — Development Agreement, cl. 37 (adapted)",
        "doc_type": "contract_clause",
        "title": "Acceptance testing, correction periods and deemed acceptance",
        "text": (
            "37.1 Acceptance Period. Customer shall have thirty days after delivery of a Deliverable or "
            "completion of a professional services milestone (the Acceptance Period) to test conformity "
            "with the acceptance criteria in the applicable Statement of Work. 37.2 Notice. Customer "
            "shall deliver written notice of acceptance, rejection or conditional acceptance before the "
            "end of the Acceptance Period. A rejection notice must describe the specific failure and "
            "the acceptance criterion not satisfied in sufficient detail to permit reproduction. 37.3 "
            "Correction. Supplier shall have thirty days after receipt of a rejection notice to correct "
            "the failure and resubmit the Deliverable, after which Customer shall have fifteen days to "
            "retest the previously failed items only. If Supplier fails to correct the failure within "
            "the correction period, Customer may terminate the affected Statement of Work and recover "
            "prepaid fees for the rejected Deliverable. 37.4 Deemed Acceptance. A Deliverable is deemed "
            "accepted if Customer does not deliver a rejection notice within the Acceptance Period, or "
            "if Customer uses the Deliverable in production other than in a pilot, evaluation or "
            "parallel-run environment. 37.5 Warranty Period. Acceptance does not waive the warranties "
            "in Section 32, and the ninety-day warranty period begins on the date of acceptance or "
            "deemed acceptance. 37.6 Payment. Payment for accepted Deliverables is due in accordance "
            "with Section 30."
        ),
        "tags": ["acceptance testing", "deemed acceptance", "deliverables", "warranty period"],
        "risk_level": "low",
    },
    {
        "id": "us-source-code-escrow-and-transition",
        "jurisdiction": "US",
        "source": "CUAD v1 — Software Licence Agreement, cl. 38 (adapted)",
        "doc_type": "contract_clause",
        "title": "Source code escrow, release events and transition services",
        "text": (
            "38.1 Escrow. For any Supplier software licensed to Customer that is not provided under an "
            "open source licence, Supplier shall place in escrow with a nationally recognised escrow "
            "agent, within thirty days after the Effective Date and at each major release, the source "
            "code, build scripts, configuration files, database schemas, technical documentation and a "
            "list of third-party dependencies necessary for a skilled developer to compile, deploy, "
            "maintain and support the software. 38.2 Release Events. Customer may request release of "
            "the escrow materials upon: Supplier's bankruptcy, insolvency, dissolution or cessation of "
            "business; Supplier's discontinuation of support for the software; or Supplier's failure to "
            "cure a material breach of its maintenance obligations within thirty days after written "
            "notice. 38.3 Licence on Release. Released materials may be used solely for Customer's "
            "internal use to maintain and support the software, may not be distributed or used to "
            "develop a competing product, and remain subject to the confidentiality obligations of "
            "Section 29. 38.4 Transition Assistance. Upon termination or expiry, Supplier shall "
            "provide up to one hundred eighty days of transition services at its standard rates, "
            "including knowledge transfer, data export in a non-proprietary format and reasonable "
            "cooperation with a successor provider. 38.5 Survival. This Section survives termination."
        ),
        "tags": ["escrow", "source code", "transition services", "exit", "continuity"],
        "risk_level": "medium",
    },
    {
        "id": "us-government-contract-flowdowns",
        "jurisdiction": "US",
        "source": "Pile of Law — Federal Acquisition Regulation and DFARS flow-down clauses (adapted)",
        "doc_type": "regulation",
        "title": "Federal contract flow-down clauses for commercial subcontracts",
        "text": (
            "Where a Customer contracts with the federal government, the Federal Acquisition Regulation "
            "at 48 C.F.R. Part 52 and the Defense Federal Acquisition Regulation Supplement require "
            "that certain clauses be flowed down to subcontractors, and the prime contractor remains "
            "liable to the government for its subcontractors' compliance. The clause at 52.244-6 "
            "requires the prime contractor to include in subcontracts for commercial products and "
            "commercial services the applicable clauses listed in paragraph (c), including those "
            "governing equal opportunity, veterans and disabled veterans employment, subcontracting "
            "plans for small business concerns, restriction on gratuities, buy American and trade "
            "agreements, and prohibition on certain telecommunications and video surveillance equipment. "
            "Where the subcontract exceeds the simplified acquisition threshold, the clause at 52.203-13 "
            "requires a written code of business ethics and conduct, an ongoing business ethics "
            "awareness and compliance programme, and timely disclosure to the government of credible "
            "evidence of a violation of federal criminal law involving fraud, conflict of interest, "
            "bribery or gratuity. The clause at 52.204-21 establishes fifteen basic safeguarding "
            "requirements for contractor information systems, and DFARS 252.204-7012 requires "
            "safeguarding of covered defence information in accordance with NIST Special Publication "
            "800-171 and reporting of cyber incidents within seventy-two hours. Contracts should "
            "identify the specific clauses flowed down, require flow-down to lower tiers and preserve "
            "audit and termination rights."
        ),
        "tags": ["far", "dfars", "government contracts", "flow-down", "cybersecurity", "compliance"],
        "risk_level": "medium",
    },
]
