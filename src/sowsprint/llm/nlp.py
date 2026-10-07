"""Deterministic, dependency-free requirement analysis.

This module is the analytical core of the offline engine. It replaces probabilistic
inference with an explicit, auditable rule set: lexicons plus pattern matching that
mine a raw requirement document for the same scoping variables a cloud LLM would
produce.

Two properties make it valuable beyond credential-free operation:

* **Determinism** — identical input yields byte-identical output, so the whole
  agent graph is reproducible in CI without network access.
* **Explainability** — every inference returns the matched signal text, which the UI
  surfaces as the ``detected_signals`` trail.

The rule set is deliberately conservative. When the rules cannot justify a value the
variable is reported as *missing*, which is exactly the condition that triggers the
Triage agent's clarification loop.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from ..models import Jurisdiction

# --------------------------------------------------------------------------------------
# Lexicons
# --------------------------------------------------------------------------------------

#: technology token -> architectural layer
TECH_LEXICON: dict[str, str] = {    # frontend
    "react": "frontend",
    "next.js": "frontend",
    "nextjs": "frontend",
    "vue": "frontend",
    "angular": "frontend",
    "svelte": "frontend",
    "typescript": "frontend",
    "tailwind": "frontend",
    "react native": "mobile",
    "flutter": "mobile",
    "swift": "mobile",
    "kotlin": "mobile",
    "ios": "mobile",
    "android": "mobile",
    # backend
    "python": "backend",
    "fastapi": "backend",
    "django": "backend",
    "flask": "backend",
    "node": "backend",
    "node.js": "backend",
    "express": "backend",
    "java": "backend",
    "spring": "backend",
    "go": "backend",
    "golang": "backend",
    "c#": "backend",
    ".net": "backend",
    "rust": "backend",
    "graphql": "backend",
    "rest": "backend",
    "grpc": "backend",
    # data
    "postgres": "data",
    "postgresql": "data",
    "mysql": "data",
    "mongodb": "data",
    "redis": "data",
    "elasticsearch": "data",
    "snowflake": "data",
    "bigquery": "data",
    "databricks": "data",
    "kafka": "data",
    "airflow": "data",
    "dbt": "data",
    "spark": "data",
    "qdrant": "data",
    "pinecone": "data",
    "pgvector": "data",
    # ai
    "openai": "ai",
    "gpt": "ai",
    "claude": "ai",
    "anthropic": "ai",
    "llm": "ai",
    "langchain": "ai",
    "langgraph": "ai",
    "rag": "ai",
    "vector database": "ai",
    "machine learning": "ai",
    "computer vision": "ai",
    "ocr": "ai",
    "recommendation engine": "ai",
    # infrastructure
    "aws": "infrastructure",
    "azure": "infrastructure",
    "gcp": "infrastructure",
    "google cloud": "infrastructure",
    "kubernetes": "infrastructure",
    "docker": "infrastructure",
    "terraform": "infrastructure",
    "serverless": "infrastructure",
    "lambda": "infrastructure",
    "cloudflare": "infrastructure",
    "vercel": "infrastructure",
    # enterprise systems
    "salesforce": "integration",
    "sap": "integration",
    "dynamics": "integration",
    "hubspot": "integration",
    "netsuite": "integration",
    "workday": "integration",
    "servicenow": "integration",
    "shopify": "integration",
    "stripe": "integration",
    "quickbooks": "integration",
    "xero": "integration",
    "jira": "integration",
    "notion": "integration",
    "slack": "integration",
    "teams": "integration",
    "okta": "integration",
    "auth0": "integration",
    "snowflake bi": "integration",
}

#: Preferred display casing for tokens whose brand capitalisation differs from
#: ``str.title()``. Anything absent falls back to title case.
TECH_DISPLAY: dict[str, str] = {
    "react": "React",
    "next.js": "Next.js",
    "nextjs": "Next.js",
    "vue": "Vue",
    "angular": "Angular",
    "svelte": "Svelte",
    "typescript": "TypeScript",
    "tailwind": "Tailwind CSS",
    "react native": "React Native",
    "flutter": "Flutter",
    "swift": "Swift",
    "kotlin": "Kotlin",
    "ios": "iOS",
    "android": "Android",
    "python": "Python",
    "fastapi": "FastAPI",
    "django": "Django",
    "flask": "Flask",
    "node": "Node.js",
    "node.js": "Node.js",
    "express": "Express",
    "java": "Java",
    "spring": "Spring Boot",
    "go": "Go",
    "golang": "Go",
    "c#": "C#",
    ".net": ".NET",
    "rust": "Rust",
    "graphql": "GraphQL",
    "rest": "REST",
    "grpc": "gRPC",
    "postgres": "PostgreSQL",
    "postgresql": "PostgreSQL",
    "mysql": "MySQL",
    "mongodb": "MongoDB",
    "redis": "Redis",
    "elasticsearch": "Elasticsearch",
    "snowflake": "Snowflake",
    "bigquery": "BigQuery",
    "databricks": "Databricks",
    "kafka": "Apache Kafka",
    "airflow": "Apache Airflow",
    "dbt": "dbt",
    "spark": "Apache Spark",
    "qdrant": "Qdrant",
    "pinecone": "Pinecone",
    "pgvector": "pgvector",
    "openai": "OpenAI",
    "gpt": "GPT",
    "claude": "Claude",
    "anthropic": "Anthropic",
    "llm": "LLM",
    "langchain": "LangChain",
    "langgraph": "LangGraph",
    "rag": "RAG",
    "vector database": "Vector database",
    "machine learning": "Machine learning",
    "computer vision": "Computer vision",
    "ocr": "OCR",
    "recommendation engine": "Recommendation engine",
    "aws": "AWS",
    "azure": "Azure",
    "gcp": "GCP",
    "google cloud": "Google Cloud",
    "kubernetes": "Kubernetes",
    "docker": "Docker",
    "terraform": "Terraform",
    "serverless": "Serverless",
    "lambda": "AWS Lambda",
    "cloudflare": "Cloudflare",
    "vercel": "Vercel",
    "salesforce": "Salesforce",
    "sap": "SAP",
    "dynamics": "Microsoft Dynamics",
    "hubspot": "HubSpot",
    "netsuite": "NetSuite",
    "workday": "Workday",
    "servicenow": "ServiceNow",
    "shopify": "Shopify",
    "stripe": "Stripe",
    "quickbooks": "QuickBooks",
    "xero": "Xero",
    "jira": "Jira",
    "notion": "Notion",
    "slack": "Slack",
    "teams": "Microsoft Teams",
    "okta": "Okta",
    "auth0": "Auth0",
}


def display_tech(token: str) -> str:
    """Return the preferred human-readable name for a technology token."""
    return TECH_DISPLAY.get(token, token.title())

#: compliance trigger pattern -> human-readable flag
_COMPLIANCE_RULES: list[tuple[str, str]] = [
    (r"\bgdpr\b|general data protection", "GDPR — EU personal data processing"),
    (r"\bpersonal data\b|\bpii\b|personally identifiable", "GDPR — EU personal data processing"),
    (r"\bdata subject\b|\bright to erasure\b|\bright to be forgotten\b", "GDPR data-subject rights"),
    (r"\bdata protection officer\b|\bdpo\b", "GDPR DPO appointment"),
    (r"\bai act\b|\bhigh[- ]risk ai\b|\balgorithmic transparency\b", "EU AI Act — high-risk system obligations"),
    (r"\bautomated decision", "EU AI Act — automated decision-making"),
    (r"\bccpa\b|\bcpra\b|california consumer privacy", "CCPA/CPRA — California privacy"),
    (r"\bhipaa\b|protected health information|\bphi\b", "HIPAA — protected health information"),
    (r"\bpci[- ]dss\b|\bcard data\b|\bcredit card\b|\bpayment card\b", "PCI DSS — cardholder data"),
    (r"\bsox\b|sarbanes[- ]oxley", "SOX — financial reporting controls"),
    (r"\bsec\b.{0,20}\breport|\bsecurities\b", "SEC — securities disclosure"),
    (r"\bdelaware\b", "Delaware corporate law"),
    (r"\baml\b|anti[- ]money laundering|\bkyc\b|know your customer", "AML/KYC — financial crime controls"),
    (r"\bmifid\b|\bmifid ii\b|\bdcsa\b", "EU financial-market regulation"),
    (r"\bnist\b|\biso 27001\b|\bsoc 2\b|\bsoc2\b", "Information-security certification"),
    (r"\bchildren'?s data\b|\bcoppa\b|\bminors?\b", "Children's data — special protection"),
    (r"\bbiometric\b|\bface recognition\b|\bfacial recognition\b", "Biometric data — EU AI Act high risk"),
    (r"\bmedical device\b|\bfda\b|\bmdr\b", "Medical device regulation"),
    (r"\baccessibility\b|\bwcag\b|\bada\b", "Accessibility compliance (WCAG/ADA)"),
    (r"\bemployment\b|\bhr data\b|\bcandidate\b", "Employment data — GDPR Art. 88"),
]

#: jurisdiction cue -> weight. Positive favours EU, negative favours US.
_JURISDICTION_CUES: list[tuple[str, int, str]] = [
    (r"\bgdpr\b", 3, "GDPR"),
    (r"\beu ai act\b|\bai act\b", 3, "EU AI Act"),
    (r"\beuropean union\b|\beu\b", 2, "European Union"),
    (r"\bgermany\b|\bberlin\b|\bmunich\b|\bdeutsch", 3, "Germany"),
    (r"\bfrance\b|\bparis\b|\bfrench\b", 3, "France"),
    (r"\bnetherlands\b|\bamsterdam\b", 3, "Netherlands"),
    (r"\bspain\b|\bmadrid\b|\bbarcelona\b", 3, "Spain"),
    (r"\bitaly\b|\bmilan\b|\brome\b", 3, "Italy"),
    (r"\bpoland\b|\bwarsaw\b", 3, "Poland"),
    (r"\bsweden\b|\bstockholm\b", 3, "Sweden"),
    (r"\bireland\b|\bdublin\b", 3, "Ireland"),
    (r"\buk\b|\bunited kingdom\b|\blondon\b|\buk gdpr\b", 2, "United Kingdom"),
    (r"\beu-based\b|\beuropean\b", 2, "European"),
    (r"\bdelaware\b", -3, "Delaware"),
    (r"\bsec\b|\bsecurities and exchange\b", -3, "SEC"),
    (r"\bunited states\b|\busa\b|\bu\.s\.\b|\bus-based\b", -2, "United States"),
    (r"\bcalifornia\b|\bccpa\b|\bcpra\b", -3, "California"),
    (r"\bnew york\b|\bny law\b", -3, "New York"),
    (r"\btexas\b|\bflorida\b|\bwashington state\b", -2, "US state"),
    (r"\bhipaa\b", -2, "HIPAA"),
    (r"\bsox\b", -3, "SOX"),
]

_DELIVERABLE_VERBS = (
    "build", "develop", "create", "implement", "deliver", "design", "migrate",
    "integrate", "provide", "set up", "setup", "launch", "ship", "deploy",
    "modernise", "modernize", "replace", "automate", "refactor", "roll out",
    "configure", "produce", "establish", "introduce", "enable", "add",
)

_OUT_OF_SCOPE_MARKERS = (
    "out of scope", "not in scope", "excluded", "not included", "phase 2",
    "phase two", "future phase", "later phase", "will not", "won't", "no need",
    "beyond the scope", "deferred", "backlog",
)

_CONSTRAINT_MARKERS = (
    "must", "shall", "required", "mandatory", "cannot", "can't", "may not",
    "within", "deadline", "budget", "compliance", "regulated", "sla",
    "uptime", "latency", "performance", "on-premise", "on-prem", "data residency",
    "iso ", "soc 2", "audit",
)

_RISK_MARKERS = (
    "risk", "concern", "tight", "uncertain", "unknown", "dependency", "legacy",
    "technical debt", "difficult", "complex", "pending", "blocked", "delay",
)

_METRIC_PATTERNS = [
    r"\b\d+(?:\.\d+)?\s?%",
    r"\b\d+(?:\.\d+)?x\b",
    r"\b\d+\s?(?:hours?|days?|minutes?|seconds?)\b",
    r"\bsla\b",
    r"\bconversion\b",
    r"\bretention\b",
    r"\bchurn\b",
    r"\bthroughput\b",
    r"\bcost reduction\b",
    r"\brevenue\b",
    r"\bnps\b",
]

_USER_PATTERNS = [
    r"(?:for|by|to)\s+(?:our\s+|the\s+)?([a-z][a-z\s\-]{2,40}?(?:s|staff|team|users?|customers?|managers?|operators?|admins?|administrators?|engineers?|agents?|analysts?|partners?|clients?))",
]

_INTEGRATION_PATTERNS = [
    r"(?:integrat\w*|connect\w*|sync\w*|link\w*)\s+(?:with|to|into)\s+([A-Z][\w\.\- ]{1,40})",
    r"([A-Z][\w\.\-]{2,30})\s+(?:API|api|webhook|integration)",
]

# --------------------------------------------------------------------------------------
# Primitive helpers
# --------------------------------------------------------------------------------------

_BULLET_RE = re.compile(r"^\s*(?:[-*•‣–]|\d+[.)])\s+(.*\S)\s*$")
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?;])\s+(?=[A-Z0-9])|\n+")
_WS_RE = re.compile(r"[ \t\u00a0]+")


def normalise(text: str) -> str:
    """Collapse horizontal whitespace while preserving line structure."""
    return "\n".join(_WS_RE.sub(" ", line).strip() for line in text.splitlines()).strip()


def split_sentences(text: str) -> list[str]:
    """Split into sentence-like units, keeping list items intact."""
    if not text:
        return []
    units: list[str] = []
    for raw_line in text.splitlines():
        bullet = _BULLET_RE.match(raw_line)
        if bullet:
            units.append(bullet.group(1).strip())
            continue
        stripped = raw_line.strip()
        if not stripped:
            continue
        units.extend(part.strip() for part in _SENTENCE_SPLIT_RE.split(stripped) if part.strip())
    return [u for u in units if len(u) > 2]


def bullet_lines(text: str) -> list[str]:
    """Return only the explicitly bulleted/numbered lines."""
    return [
        match.group(1).strip()
        for line in text.splitlines()
        if (match := _BULLET_RE.match(line))
    ]


def dedupe(items: list[str], *, casefold: bool = True) -> list[str]:
    """Order-preserving de-duplication."""
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        key = item.casefold().strip() if casefold else item.strip()
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(item.strip())
    return out


def _matches(text: str, markers: tuple[str, ...]) -> list[str]:
    lowered = text.casefold()
    return [m for m in markers if m in lowered]


def _clean_fragment(fragment: str, limit: int = 220) -> str:
    cleaned = fragment.strip(" .,;:-\u2013\u2014")
    if len(cleaned) > limit:
        cleaned = cleaned[: limit - 1].rsplit(" ", 1)[0] + "…"
    return cleaned[:1].upper() + cleaned[1:] if cleaned else cleaned


# --------------------------------------------------------------------------------------
# Extractors
# --------------------------------------------------------------------------------------


_TITLE_NOISE_PREFIXES = (
    "we need to", "we want to", "we would like to", "the client needs to", "client needs to",
    "the customer needs to", "customer needs to", "we require", "they need to", "needs to",
    "requires", "needs", "wants", "must", "please", "looking to", "aims to",
)

_TITLE_LEAD_VERBS = (
    "build", "develop", "create", "implement", "deliver", "design", "migrate",
    "integrate", "provide", "set up", "launch", "deploy", "modernise", "modernize",
)

#: "<Proper Noun Organisation> needs|requires|wants …" — strips the buyer from the title.
_LEADING_ORG_RE = re.compile(
    r"^(?:[A-Z][\w&\.\-]*\s+){1,5}"
    r"(?:GmbH|Inc\.?|Ltd\.?|LLC|AG|BV|B\.V\.|SA|S\.A\.|PLC|Corp\.?|SE|NV|AB|Oy|SpA|Srl)\s+"
    r"(?:\w+\s+){0,3}?"
    r"(?:needs?|requires?|wants?|is looking to|would like to|aims to)\s+"
    r"|^(?:[A-Z][\w&\.\-]*\s+){1,5}(?:needs?|requires?|wants?|is looking to|would like to)\s+",
)


def extract_title(text: str) -> str:
    """Derive a compact engagement title from a heading or the opening sentence."""
    for line in text.splitlines():
        stripped = line.strip().strip("#").strip()
        if stripped and (line.strip().startswith("#") or stripped.isupper()) and len(stripped) > 6:
            return _clean_fragment(stripped, 80)

    candidate = ""
    for unit in split_sentences(text):
        lowered = unit.casefold()
        if any(k in lowered for k in ("project", "platform", "system", "portal", "app", "migration")):
            candidate = unit
            break
    if not candidate:
        sentences = split_sentences(text)
        candidate = sentences[0] if sentences else "B2B Engagement"

    return _compact_title(candidate)


def _compact_title(sentence: str) -> str:
    """Strip conversational scaffolding so the title reads like a project name."""
    text = sentence.strip().strip(".")
    lowered = text.casefold()

    # Drop a leading client name ("Nordwind Logistics GmbH needs a ...").
    client = extract_client_name(text)
    if client and lowered.startswith(client.casefold()):
        text = text[len(client) :].lstrip(" ,:-")
        lowered = text.casefold()

    stripped_org = _LEADING_ORG_RE.sub("", text)
    if stripped_org:
        text = stripped_org.lstrip(" ,:-")
        lowered = text.casefold()

    for prefix in _TITLE_NOISE_PREFIXES:
        if lowered.startswith(prefix):
            text = text[len(prefix) :].lstrip(" ,:-")
            lowered = text.casefold()
            break

    for verb in _TITLE_LEAD_VERBS:
        if lowered.startswith(verb + " "):
            text = text[len(verb) :].lstrip(" ,:-")
            lowered = text.casefold()
            break

    # Drop leading articles so the title starts on a content word.
    for article in ("a ", "an ", "the ", "our ", "new "):
        if lowered.startswith(article):
            text = text[len(article) :]
            lowered = text.casefold()

    return _clean_fragment(" ".join(text.split()[:9]), 80)


_CLIENT_PATTERNS = [
    r"(?:client|customer|company|organisation|organization)\s*[:\-]\s*([A-Z][\w&\.\- ]{1,50})",
    r"\bfor\s+([A-Z][A-Za-z0-9&\.\-]+(?:\s+[A-Z][A-Za-z0-9&\.\-]+){0,3})\s*(?:GmbH|Inc|Ltd|LLC|AG|BV|S\.A\.|SA|PLC|Corp)?\b",
]


def extract_client_name(text: str) -> str | None:
    """Find an explicit client/organisation name."""
    for pattern in _CLIENT_PATTERNS:
        match = re.search(pattern, text)
        if match:
            candidate = match.group(1).strip()
            if 2 < len(candidate) < 60 and candidate.casefold() not in {
                "the", "our", "their", "this", "a", "an",
            }:
                return candidate
    return None


def detect_technologies(text: str) -> dict[str, list[str]]:
    """Return detected technologies grouped by architectural layer."""
    lowered = text.casefold()
    grouped: dict[str, list[str]] = {}
    for token, layer in TECH_LEXICON.items():
        pattern = rf"(?<![a-z0-9]){re.escape(token)}(?![a-z0-9])"
        if re.search(pattern, lowered):
            grouped.setdefault(layer, []).append(token)
    return {layer: dedupe(values) for layer, values in grouped.items()}


def detect_compliance_flags(text: str) -> tuple[list[str], dict[str, list[str]]]:
    """Return compliance flags plus the exact signal text that triggered each."""
    lowered = text.casefold()
    flags: list[str] = []
    signals: dict[str, list[str]] = {}
    for pattern, label in _COMPLIANCE_RULES:
        matches = re.findall(pattern, lowered)
        if matches:
            flags.append(label)
            signals.setdefault(label, [])
            for match in matches[:3]:
                snippet = match if isinstance(match, str) else " ".join(match)
                if snippet and snippet not in signals[label]:
                    signals[label].append(snippet)
    return dedupe(flags), signals


def infer_jurisdiction(text: str) -> tuple[Jurisdiction, dict[str, list[str]]]:
    """Score jurisdiction cues; ties resolve to EU (the platform default regime)."""
    lowered = text.casefold()
    score = 0
    signals: dict[str, list[str]] = {"EU": [], "US": []}
    for pattern, weight, label in _JURISDICTION_CUES:
        if re.search(pattern, lowered):
            score += weight
            signals["EU" if weight > 0 else "US"].append(label)
    signals = {k: dedupe(v) for k, v in signals.items()}
    return (Jurisdiction.US if score < 0 else Jurisdiction.EU), signals


def extract_timeline_weeks(text: str) -> tuple[int | None, str | None]:
    """Extract a delivery timeline in weeks from natural phrasing."""
    lowered = text.casefold()
    patterns: list[tuple[str, float]] = [
        (r"(\d+)\s*(?:calendar\s+)?weeks?", 1.0),
        (r"(\d+)\s*months?", 4.345),
        (r"(\d+)\s*sprints?", 2.0),
        (r"(\d+)\s*days?", 1 / 7),
        (r"(\d+)\s*quarters?", 13.0),
    ]
    for pattern, multiplier in patterns:
        match = re.search(pattern, lowered)
        if match:
            value = float(match.group(1))
            weeks = max(1, round(value * multiplier))
            return weeks, match.group(0).strip()
    return None, None


_CURRENCY = r"(?:[€$£]|EUR|USD|GBP|CHF|SEK|NOK|DKK|PLN)\s?"
_AMOUNT = rf"{_CURRENCY}?\d[\d,\.]*\s?(?:k|m|million|thousand|bn)?"
_BUDGET_PATTERNS = [
    rf"(?:budget|invest(?:ment)?|spend|cost|priced?|envelope)\s*(?:of|is|:)?\s*({_AMOUNT}(?:\s?(?:-|to|–)\s?{_AMOUNT})?)",
    rf"({_AMOUNT})\s*(?:budget|available|allocated|approved)",
    rf"({_CURRENCY}\s?\d[\d,\.]*\s?(?:k|m|million|thousand)?)",
]


def extract_budget(text: str) -> tuple[str | None, str | None]:
    """Extract a stated budget or budget range verbatim.

    Handles symbol and ISO-code currencies, thousands suffixes and ranges
    ("Budget: EUR 120k", "$250,000", "between €80k and €150k").
    """
    for pattern in _BUDGET_PATTERNS:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            value = match.group(1).strip()
            # Reject a bare number that carries no currency information.
            if not re.search(r"[€$£]|EUR|USD|GBP|CHF|SEK|NOK|DKK|PLN", value, re.IGNORECASE):
                continue
            return value, match.group(0).strip()
    return None, None


def extract_team_size(text: str) -> int | None:
    match = re.search(
        r"\b(?:team|squad|group)\s+of\s+(\d{1,3})|(\d{1,3})[\s-](?:person|people|developer|engineer)",
        text,
        re.IGNORECASE,
    )
    if match:
        value = match.group(1) or match.group(2)
        if value and 1 <= int(value) <= 500:
            return int(value)
    return None


def extract_out_of_scope(text: str) -> list[str]:
    """Lines explicitly marked as excluded from the engagement."""
    results = []
    for unit in split_sentences(text):
        if _matches(unit, _OUT_OF_SCOPE_MARKERS):
            cleaned = re.sub(
                r"^(?:out of scope|not in scope|excluded|not included)\s*[:\-]?\s*",
                "",
                unit,
                flags=re.IGNORECASE,
            )
            results.append(_clean_fragment(cleaned))
    return dedupe(results)


def extract_constraints(text: str) -> list[str]:
    """Lines carrying a binding constraint (must/shall/SLA/data residency…)."""
    results = []
    for unit in split_sentences(text):
        if _matches(unit, _CONSTRAINT_MARKERS) and not _matches(unit, _OUT_OF_SCOPE_MARKERS):
            results.append(_clean_fragment(unit))
    return dedupe(results)[:12]


def extract_risks(text: str) -> list[str]:
    results = [
        _clean_fragment(unit)
        for unit in split_sentences(text)
        if _matches(unit, _RISK_MARKERS)
    ]
    return dedupe(results)[:10]


def extract_success_metrics(text: str) -> list[str]:
    results = []
    for unit in split_sentences(text):
        if any(re.search(p, unit, re.IGNORECASE) for p in _METRIC_PATTERNS):
            results.append(_clean_fragment(unit))
    return dedupe(results)[:8]


def extract_target_users(text: str) -> list[str]:
    results: list[str] = []
    for pattern in _USER_PATTERNS:
        for match in re.finditer(pattern, text, re.IGNORECASE):
            candidate = match.group(1).strip().casefold()
            if 3 < len(candidate) < 45:
                results.append(candidate)
    return dedupe(results)[:8]


def extract_integrations(text: str, *, known_tech: list[str] | None = None) -> list[str]:
    """Find systems the solution must talk to."""
    results: list[str] = []
    for pattern in _INTEGRATION_PATTERNS:
        for match in re.finditer(pattern, text):
            candidate = match.group(1).strip(" .,;:")
            if 2 < len(candidate) < 45:
                results.append(candidate)

    lowered = text.casefold()
    if known_tech:
        for tech in known_tech:
            if TECH_LEXICON.get(tech) == "integration" and tech in lowered:
                results.append(tech.title())
    return dedupe(results)[:10]


def extract_deliverables(text: str) -> list[str]:
    """Identify what the vendor is contracted to produce.

    Priority order: explicit bullet lists (highest signal), then sentences carrying a
    delivery verb. Constraint/metric/exclusion lines are filtered out first so the
    deliverables list stays clean.
    """
    excluded = set(
        extract_out_of_scope(text) + extract_constraints(text) + extract_success_metrics(text)
    )
    candidates: list[str] = []

    for bullet in bullet_lines(text):
        if bullet in excluded:
            continue
        if any(bullet.startswith(e[:40]) for e in excluded if e):
            continue
        candidates.append(bullet)

    for unit in split_sentences(text):
        lowered = unit.casefold()
        if any(verb in lowered for verb in _DELIVERABLE_VERBS):
            candidates.append(unit)

    cleaned: list[str] = []
    for candidate in candidates:
        fragment = _clean_fragment(candidate)
        if 8 < len(fragment) <= 220 and not _matches(fragment, _OUT_OF_SCOPE_MARKERS):
            cleaned.append(fragment)

    result = dedupe(cleaned)
    if not result:
        # Absolute fallback: the substantive sentences of the brief.
        result = [
            _clean_fragment(u)
            for u in split_sentences(text)
            if len(u) > 25
        ][:5]
    return result[:12]


#: Intent markers ranked by how strongly they express a business outcome. Ranking
#: matters: "We need a new portal." contains the weak marker "we need" and would win a
#: naive first-match scan, masking the actual goal stated later in the brief.
_BUSINESS_GOAL_MARKERS: tuple[tuple[str, int], ...] = (
    ("our goal", 4),
    ("the goal", 4),
    ("business goal", 4),
    ("objective", 4),
    ("our objective", 4),
    ("aim is", 4),
    ("purpose", 3),
    ("in order to", 3),
    ("so that", 3),
    ("we want to", 3),
    ("we would like to", 3),
    ("looking to", 3),
    ("helps us", 2),
    ("enable us", 2),
    ("will enable", 2),
    ("to reduce", 2),
    ("to increase", 2),
    ("to improve", 2),
    ("we want", 2),
    ("we need", 1),
    ("we require", 1),
)


def extract_business_goal(text: str) -> str:
    """Find the sentence that best expresses the buying intent.

    Candidates are scored by marker strength rather than taken in document order, so a
    throwaway "we need a portal" never outranks an explicit "our goal is to …".
    """
    best: tuple[int, int, str] | None = None
    for position, unit in enumerate(split_sentences(text)):
        lowered = unit.casefold()
        score = max(
            (weight for marker, weight in _BUSINESS_GOAL_MARKERS if marker in lowered),
            default=0,
        )
        if score == 0:
            continue
        # Higher score wins; on a tie the earlier sentence wins.
        if best is None or score > best[0]:
            best = (score, position, unit)

    if best is not None:
        return _clean_fragment(best[2], 300)

    sentences = split_sentences(text)
    return _clean_fragment(sentences[0], 300) if sentences else ""


def extract_problem_statement(text: str) -> str:
    markers = ("problem", "pain", "challenge", "currently", "today we", "manual", "struggle", "issue", "bottleneck")
    for unit in split_sentences(text):
        if _matches(unit, markers):
            return _clean_fragment(unit, 300)
    return ""


@dataclass(slots=True)
class RequirementSignals:
    """Aggregated deterministic analysis of one requirement document."""

    title: str
    client_name: str | None
    business_goal: str
    problem_statement: str
    deliverables: list[str] = field(default_factory=list)
    out_of_scope: list[str] = field(default_factory=list)
    constraints: list[str] = field(default_factory=list)
    integrations: list[str] = field(default_factory=list)
    target_users: list[str] = field(default_factory=list)
    success_metrics: list[str] = field(default_factory=list)
    risks: list[str] = field(default_factory=list)
    compliance_flags: list[str] = field(default_factory=list)
    jurisdiction: Jurisdiction = Jurisdiction.EU
    timeline_weeks: int | None = None
    budget_range: str | None = None
    team_size: int | None = None
    technologies: dict[str, list[str]] = field(default_factory=dict)
    signals: dict[str, list[str]] = field(default_factory=dict)

    @property
    def all_technologies(self) -> list[str]:
        return dedupe([t for group in self.technologies.values() for t in group])


def analyse(text: str) -> RequirementSignals:
    """Run the full rule set over a raw requirement document."""
    text = normalise(text)
    technologies = detect_technologies(text)
    compliance_flags, compliance_signals = detect_compliance_flags(text)
    jurisdiction, jurisdiction_signals = infer_jurisdiction(text)
    timeline_weeks, timeline_signal = extract_timeline_weeks(text)
    budget_range, budget_signal = extract_budget(text)

    signals: dict[str, list[str]] = {
        "compliance": compliance_signals,
        "jurisdiction": jurisdiction_signals,
    }
    if timeline_signal:
        signals["timeline"] = [timeline_signal]
    if budget_signal:
        signals["budget"] = [budget_signal]
    if technologies:
        signals["technology"] = [f"{k}: {', '.join(v)}" for k, v in technologies.items()]

    return RequirementSignals(
        title=extract_title(text),
        client_name=extract_client_name(text),
        business_goal=extract_business_goal(text),
        problem_statement=extract_problem_statement(text),
        deliverables=extract_deliverables(text),
        out_of_scope=extract_out_of_scope(text),
        constraints=extract_constraints(text),
        integrations=extract_integrations(text, known_tech=dedupe([t for g in technologies.values() for t in g])),
        target_users=extract_target_users(text),
        success_metrics=extract_success_metrics(text),
        risks=extract_risks(text),
        compliance_flags=compliance_flags,
        jurisdiction=jurisdiction,
        timeline_weeks=timeline_weeks,
        budget_range=budget_range,
        team_size=extract_team_size(text),
        technologies=technologies,
        signals={k: v for k, v in signals.items() if v},
    )
