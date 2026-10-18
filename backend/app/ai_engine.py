"""Small, auditable AI helpers for MediRoute.

The module works offline by default. Scikit-learn is used for consumption
imputation when installed; otherwise the imputer falls back to weekday
medians and last-observation-carried-forward. Outbreak extraction is local
and heuristic. An OpenAI-compatible endpoint is used for transfer memo wording
only when ``OPENAI_API_KEY`` is explicitly configured; deterministic wording
is always available.

These helpers support inventory planning and administration. They do not make
clinical decisions, establish medicine equivalence, or replace human review.
"""
from __future__ import annotations

from collections import Counter
from difflib import SequenceMatcher
import json
import math
import os
from pathlib import Path
import re
from typing import Any, Optional
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import numpy as np
import pandas as pd
from pydantic import BaseModel, Field, ValidationError

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parents[1] / ".env")
except ImportError:  # pragma: no cover - environment may omit optional dotenv
    pass

try:  # Optional ML dependency: the module remains usable without it.
    from sklearn.ensemble import RandomForestRegressor
    from sklearn.impute import SimpleImputer
    from sklearn.pipeline import Pipeline
except ImportError:  # pragma: no cover - depends on local environment
    RandomForestRegressor = None  # type: ignore[assignment,misc]
    SimpleImputer = None  # type: ignore[assignment,misc]
    Pipeline = None  # type: ignore[assignment,misc]


def load_nlem_catalog(path: Optional[str] = None) -> list[str]:
    """Load the bundled NLEM 2022 starter catalog or a caller-supplied JSON.

    ``NLEM_CATALOG_PATH`` may point to a JSON object with an ``entries`` list
    or directly to a JSON list. The bundled file is a practical starter subset
    of the official NLEM alphabetical list; organizations should load their
    complete approved catalog for production matching.
    """
    catalog_path = Path(path or os.getenv("NLEM_CATALOG_PATH") or Path(__file__).with_name("nlem_2022_catalog.json"))
    try:
        payload = json.loads(catalog_path.read_text(encoding="utf-8"))
        entries = payload if isinstance(payload, list) else payload.get("entries", [])
        cleaned = [str(entry).strip() for entry in entries if str(entry).strip()]
        if cleaned:
            return cleaned
    except (OSError, json.JSONDecodeError, AttributeError, TypeError):
        pass
    return ["Amoxicillin", "Amoxicillin (A) + Clavulanic acid (B)", "Oral rehydration salts", "Paracetamol", "Ringer lactate", "Sodium chloride"]


def _clean_drug_name(value: str) -> str:
    """Normalize common packaging words and spacing while preserving dose."""
    text = str(value or "").lower().strip()
    text = re.sub(r"\b(tablets?|tabs?|capsules?|caps?|syrup|injection|inj)\b", " ", text)
    text = re.sub(r"(\d)(mg|mcg|g|ml)\b", r"\1 \2", text)
    text = re.sub(r"[^a-z0-9.]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _expand_drug_aliases(text: str) -> str:
    """Expand a few common short forms before fuzzy catalog matching."""
    aliases = {
        r"\bmox\b": "amoxicillin",
        r"\bamox\b": "amoxicillin",
        r"\bpcm\b": "paracetamol",
        r"\bacetaminophen\b": "paracetamol",
        r"\bcalpol\b": "paracetamol",
        r"\basa\b": "aspirin",
    }
    for pattern, replacement in aliases.items():
        text = re.sub(pattern, replacement, text)
    return text


def _ngrams(text: str, low: int = 2, high: int = 4) -> Counter[str]:
    """Return a character n-gram count vector, padded to retain word edges."""
    padded = f"  {text}  "
    return Counter(padded[i:i + n] for n in range(low, high + 1)
                   for i in range(max(0, len(padded) - n + 1)))


def _cosine_counts(a: Counter[str], b: Counter[str]) -> float:
    """Cosine similarity for sparse count vectors without a vectorizer."""
    if not a or not b:
        return 0.0
    common = a.keys() & b.keys()
    dot = sum(a[k] * b[k] for k in common)
    denom = math.sqrt(sum(v * v for v in a.values()) * sum(v * v for v in b.values()))
    return dot / denom if denom else 0.0


def normalize_drug_name(raw_name: str, canonical_catalog: list[str]) -> tuple[str, float]:
    """Map a messy medicine label to its closest supplied catalog entry.

    Uses a weighted blend of character n-gram cosine similarity, token
    overlap, and sequence similarity. The catalog is deliberately supplied by
    the caller (for example, a locally maintained NLEM list); no external
    catalog or embedding service is contacted. A few common abbreviations are
    expanded, but the returned name is always exactly one of the supplied
    canonical entries when the catalog is non-empty.

    Args:
        raw_name: Free-text label from a dispensary, invoice, or PDF.
        canonical_catalog: Standard medicine names to search.

    Returns:
        ``(best_catalog_name, confidence)`` where confidence is in ``[0, 1]``.
        For an empty catalog, returns the cleaned input and ``0.0``.
    """
    if not canonical_catalog:
        return str(raw_name or "").strip(), 0.0

    query = _expand_drug_aliases(_clean_drug_name(raw_name))
    q_tokens = set(query.split())
    q_grams = _ngrams(query)
    best_name, best_score = canonical_catalog[0], -1.0
    for candidate in canonical_catalog:
        target = _expand_drug_aliases(_clean_drug_name(candidate))
        target_tokens = set(target.split())
        union = q_tokens | target_tokens
        token_score = len(q_tokens & target_tokens) / len(union) if union else 0.0
        char_score = _cosine_counts(q_grams, _ngrams(target))
        sequence_score = SequenceMatcher(None, query, target).ratio()
        score = 0.45 * char_score + 0.30 * sequence_score + 0.25 * token_score
        if query == target:
            score = 1.0
        # A matching numeric dose is useful evidence; a conflicting one is not.
        q_dose = re.findall(r"\b\d+(?:\.\d+)?\s*(?:mg|mcg|g|ml)\b", query)
        t_dose = re.findall(r"\b\d+(?:\.\d+)?\s*(?:mg|mcg|g|ml)\b", target)
        if q_dose and t_dose:
            score += 0.08 if q_dose == t_dose else -0.12
        score = min(1.0, max(0.0, score))
        if score > best_score:
            best_name, best_score = candidate, score
    return best_name, round(best_score, 4)


def _imputation_pipeline() -> Optional[Any]:
    """Build a deterministic, lightweight sklearn regression pipeline."""
    if Pipeline is None or SimpleImputer is None or RandomForestRegressor is None:
        return None
    return Pipeline([
        ("imputer", SimpleImputer(strategy="median")),
        ("regressor", RandomForestRegressor(
            n_estimators=80, max_depth=6, min_samples_leaf=2, random_state=17, n_jobs=1
        )),
    ])


def impute_missing_consumption(df_logs: pd.DataFrame) -> pd.DataFrame:
    """Fill missing daily consumption without treating missing logs as zero.

    Each facility/item series is expanded to a complete daily calendar. When
    enough observations exist and scikit-learn is installed, a small random
    forest uses day of week, facility/item baseline, and the preceding
    seven-day average. A fallback uses weekday medians and forward fill. Any
    leading gap is filled from the earliest observed value, then the series
    median. Actual recorded zeroes remain zero; only absent dates and NaNs are
    imputed. The returned frame adds ``was_imputed`` and
    ``imputation_method`` columns for auditability.

    Args:
        df_logs: DataFrame containing ``facility_id``, ``item_id``, ``date``,
            and ``units_dispensed``. Duplicate facility/item/date rows are
            combined by summing non-null usage.

    Returns:
        A sorted daily DataFrame with inferred non-negative numeric usage.

    Raises:
        TypeError: If the input is not a pandas DataFrame.
        ValueError: If required columns are missing or non-null dates cannot
            be parsed.
    """
    if not isinstance(df_logs, pd.DataFrame):
        raise TypeError("df_logs must be a pandas DataFrame")
    required = {"facility_id", "item_id", "date", "units_dispensed"}
    missing_columns = required - set(df_logs.columns)
    if missing_columns:
        raise ValueError(f"Missing required columns: {', '.join(sorted(missing_columns))}")
    if df_logs.empty:
        return pd.DataFrame(columns=["facility_id", "item_id", "date", "units_dispensed", "was_imputed", "imputation_method"])

    work = df_logs[["facility_id", "item_id", "date", "units_dispensed"]].copy()
    work["date"] = pd.to_datetime(work["date"], errors="coerce").dt.normalize()
    if work["date"].isna().any():
        raise ValueError("Every input row must have a parseable date")
    work["units_dispensed"] = pd.to_numeric(work["units_dispensed"], errors="coerce")
    work = work.groupby(["facility_id", "item_id", "date"], as_index=False, dropna=False)["units_dispensed"].sum(min_count=1)

    results: list[pd.DataFrame] = []
    for (facility_id, item_id), group in work.groupby(["facility_id", "item_id"], sort=False, dropna=False):
        group = group.sort_values("date").set_index("date")
        calendar = pd.date_range(group.index.min(), group.index.max(), freq="D")
        observed = group["units_dispensed"].reindex(calendar).astype(float)
        missing_mask = observed.isna()
        if not missing_mask.any():
            results.append(pd.DataFrame({
                "facility_id": facility_id, "item_id": item_id, "date": calendar,
                "units_dispensed": observed.to_numpy(), "was_imputed": False,
                "imputation_method": "observed",
            }))
            continue

        values = observed.copy()
        baseline = float(observed.median()) if observed.notna().any() else 0.0
        # Features use only prior observations to avoid target leakage.
        prior = observed.ffill()
        rolling = prior.shift(1).rolling(7, min_periods=1).mean()
        features = pd.DataFrame({
            "day_of_week": calendar.dayofweek.astype(float),
            "facility_item_baseline": baseline,
            "rolling_7_day_mean": rolling.fillna(baseline).to_numpy(),
        }, index=calendar)
        train_mask = observed.notna()
        model = _imputation_pipeline()
        method = "weekday_median_locf"
        predictions: Optional[np.ndarray] = None
        if model is not None and int(train_mask.sum()) >= 14:
            try:
                model.fit(features.loc[train_mask], observed.loc[train_mask])
                predictions = model.predict(features.loc[missing_mask])
                method = "random_forest"
            except (ValueError, TypeError):
                predictions = None

        if predictions is not None:
            values.loc[missing_mask] = np.maximum(0.0, predictions)
        else:
            weekday_medians = observed.groupby(calendar.dayofweek).median()
            locf = observed.ffill().bfill()
            for day in calendar[missing_mask]:
                weekday_value = weekday_medians.get(day.dayofweek, np.nan)
                fill_value = weekday_value if pd.notna(weekday_value) else locf.loc[day]
                if pd.isna(fill_value):
                    fill_value = baseline
                values.loc[day] = max(0.0, float(fill_value))

        results.append(pd.DataFrame({
            "facility_id": facility_id,
            "item_id": item_id,
            "date": calendar,
            "units_dispensed": values.to_numpy(),
            "was_imputed": missing_mask.to_numpy(),
            "imputation_method": [method if flag else "observed" for flag in missing_mask.to_numpy()],
        }))

    result = pd.concat(results, ignore_index=True)
    result["units_dispensed"] = result["units_dispensed"].astype(float).clip(lower=0)
    return result.sort_values(["facility_id", "item_id", "date"], kind="stable").reset_index(drop=True)


class OutbreakMultiplier(BaseModel):
    """Validated shape for a locally extracted outbreak demand signal."""
    region: str = Field(min_length=1, max_length=120)
    drug_category: str = Field(min_length=1, max_length=80)
    surge_multiplier: float = Field(ge=1.0, le=10.0)
    confidence: float = Field(ge=0.0, le=1.0)


def _model_dump_compat(model: BaseModel) -> dict[str, Any]:
    """Serialize a Pydantic v1/v2 model consistently."""
    dump = getattr(model, "model_dump", None)
    return dump() if dump else model.dict()


def extract_outbreak_multipliers(raw_text: str) -> dict[str, Any]:
    """Extract region, affected supply category, and demand multiplier.

    This is an offline, explainable heuristic parser rather than an LLM call.
    Explicit percentages such as ``45% surge`` become ``1.45``. If no numeric
    surge is present, the multiplier is ``1.0`` with lower confidence. The
    result is validated and serialized through Pydantic.

    Args:
        raw_text: Plain-text advisory, bulletin, or circular excerpt.

    Returns:
        Dictionary matching ``OutbreakMultiplier``.
    """
    text = re.sub(r"\s+", " ", str(raw_text or "")).strip()
    if not text:
        raise ValueError("raw_text cannot be empty")

    region = "Unspecified region"
    # Common field-report structure: "East Delhi PHCs reporting ...".
    region_match = re.search(
        r"\b([A-Z][A-Za-z.&'-]*(?:\s+[A-Z][A-Za-z.&'-]*){0,3})\s+"
        r"(?:PHCs?|primary health cent(?:er|re)s?|hospitals?|clinics?|district|region)\b",
        text,
    )
    if not region_match:
        region_match = re.search(
            r"\b(?:in|across|within|for)\s+([A-Za-z][A-Za-z.&'-]*(?:\s+[A-Za-z][A-Za-z.&'-]*){0,3})"
            r"(?=\s+(?:reporting|seeing|with|area|region|district)\b|[,;.]|$)", text, re.I
        )
    if region_match:
        region = re.sub(r"\s+", " ", region_match.group(1)).strip(" ,.;")

    category_rules = [
        (r"\b(?:iv\s*fluids?|intravenous fluids?|normal saline|ringer(?:'s)? lactate)\b", "IV_FLUIDS"),
        (r"\b(?:platelets?|thrombocytopenia|platelet kits?)\b", "PLATELET_KITS"),
        (r"\b(?:ors|oral rehydration salts?)\b", "ORS"),
        (r"\b(?:paracetamol|acetaminophen|calpol)\b", "PARACETAMOL"),
        (r"\b(?:antimalarial|artemether|lumefantrine)\b", "ANTIMALARIALS"),
        (r"\bdengue\b", "DENGUE_SUPPORTIVE_CARE"),
    ]
    category = next((label for pattern, label in category_rules if re.search(pattern, text, re.I)), "UNSPECIFIED")

    pct = re.search(r"(\d+(?:\.\d+)?)\s*%\s*(?:surge|increase|rise|higher|growth|above)", text, re.I)
    if pct:
        percent = float(pct.group(1))
    else:
        pct = re.search(r"(?:surge|increase|rise|growth)\s+(?:of\s+)?(\d+(?:\.\d+)?)\s*%", text, re.I)
        percent = float(pct.group(1)) if pct else 0.0
    if pct:
        multiplier = 1.0 + percent / 100.0
        surge_confidence = 0.92
    else:
        factor = re.search(r"\b(\d+(?:\.\d+)?)\s*x\b", text, re.I)
        multiplier = float(factor.group(1)) if factor else 1.0
        surge_confidence = 0.78 if factor else 0.25

    confidence = 0.32 + (0.22 if region != "Unspecified region" else 0.0)
    confidence += 0.22 if category != "UNSPECIFIED" else 0.0
    confidence += surge_confidence * 0.24
    result = OutbreakMultiplier(
        region=region,
        drug_category=category,
        surge_multiplier=min(10.0, max(1.0, multiplier)),
        confidence=min(1.0, confidence),
    )
    return _model_dump_compat(result)


def outbreak_multiplier_for_pair(
    medicine_name: str,
    facility_name: str,
    locality: Optional[str],
    category: str,
    region: str,
    multiplier: float,
) -> float:
    """Return a parsed surge factor only for a matching region and item class.

    This deliberately uses a narrow keyword map: uncertain category matches
    do not silently inflate every medicine in the database.
    """
    if not category or category == "UNSPECIFIED" or not math.isfinite(float(multiplier)) or multiplier <= 1:
        return 1.0
    medicine = _clean_drug_name(medicine_name)
    category_terms = {
        "IV_FLUIDS": ("normal saline", "sodium chloride", "ringer", "glucose", "intravenous", "iv fluid"),
        "PLATELET_KITS": ("platelet", "cryoprecipitate"),
        "ORS": ("oral rehydration", " ors ", "ors"),
        "PARACETAMOL": ("paracetamol", "acetaminophen"),
        "ANTIMALARIALS": ("artemether", "lumefantrine", "artesunate", "chloroquine", "antimalarial"),
        "DENGUE_SUPPORTIVE_CARE": ("paracetamol", "normal saline", "sodium chloride", "ringer", "platelet"),
    }
    terms = category_terms.get(category.upper())
    if not terms or not any(term in medicine for term in terms):
        return 1.0
    if region and region.casefold() not in f"{facility_name} {locality or ''}".casefold():
        return 1.0
    return min(10.0, max(1.0, float(multiplier)))


def _transfer_facts(transfer_dict: dict[str, Any]) -> dict[str, Any]:
    """Validate and format required memo facts without inferring missing data."""
    required = {
        "source_name", "target_name", "item_name", "qty", "source_expiry_days",
        "target_stockout_days", "donor_reserve_days", "transit_distance_km", "is_cold_chain",
    }
    missing = required - set(transfer_dict)
    if missing:
        raise ValueError(f"Missing transfer fields: {', '.join(sorted(missing))}")
    facts: dict[str, Any] = dict(transfer_dict)
    try:
        for key in ("qty", "source_expiry_days"):
            facts[key] = int(facts[key])
        for key in ("target_stockout_days", "donor_reserve_days", "transit_distance_km"):
            facts[key] = float(facts[key])
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("Transfer quantity, expiry, stockout, reserve, and distance must be numeric") from exc
    if not isinstance(facts["is_cold_chain"], bool):
        raise ValueError("is_cold_chain must be a boolean")
    for key in ("target_stockout_days", "donor_reserve_days", "transit_distance_km"):
        if not math.isfinite(facts[key]):
            raise ValueError(f"{key} must be a finite number")
    for key in ("source_name", "target_name", "item_name"):
        facts[key] = str(facts[key]).strip()
        if not facts[key]:
            raise ValueError(f"{key} cannot be empty")
    if facts["qty"] <= 0 or facts["source_expiry_days"] < 0 or facts["transit_distance_km"] < 0:
        raise ValueError("Quantity must be positive; expiry days and distance cannot be negative")
    if facts["target_stockout_days"] < 0:
        raise ValueError("target_stockout_days cannot be negative")
    if facts["donor_reserve_days"] < 21:
        raise ValueError("Transfer rejected: donor_reserve_days must be at least 21")
    return facts


def _deterministic_memo(facts: dict[str, Any]) -> str:
    """Create a two-sentence memo using only supplied inventory facts."""
    source = str(facts["source_name"]).strip()
    target = str(facts["target_name"]).strip()
    item = str(facts["item_name"]).strip()
    expiry = facts["source_expiry_days"]
    sentence_one = (
        f"Move {facts['qty']} units of {item} from {source} to {target} over the {facts['transit_distance_km']:.1f} km route; "
        f"{target} is projected to stock out in {facts['target_stockout_days']:.1f} days, while the source batch expires in {expiry} days, "
        "so this transfer may reduce shortage and expiry-wastage risk."
    )
    if facts["is_cold_chain"]:
        handling = "Cold-chain transit is recorded as confirmed for this movement."
    else:
        handling = "Cold-chain transit is not confirmed; do not dispatch temperature-controlled items until compliant handling is verified."
    sentence_two = (
        f"The donor retains {facts['donor_reserve_days']:.1f} days of reserve, meeting the 21-day minimum; {handling}"
    )
    return f"{sentence_one} {sentence_two}"


def _openai_memo(facts: dict[str, Any]) -> Optional[str]:
    """Optionally request constrained wording from an OpenAI-compatible API."""
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        return None
    base_url = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/")
    model = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
    system = (
        "Write an administrative inventory-transfer memo for a hospital Chief Medical Officer. "
        "Return exactly two concise sentences. Use only supplied facts. Do not claim clinical approval, "
        "guaranteed prevention, or cold-chain compliance unless explicitly confirmed. If cold-chain is false, "
        "state that temperature-controlled items must not dispatch until compliant handling is confirmed."
    )
    payload = json.dumps({
        "model": model,
        "temperature": 0,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": json.dumps(facts, ensure_ascii=False)},
        ],
    }).encode("utf-8")
    request = Request(
        f"{base_url}/chat/completions", data=payload,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=float(os.getenv("OPENAI_TIMEOUT_SECONDS", "8"))) as response:
            content = json.loads(response.read().decode("utf-8"))["choices"][0]["message"]["content"].strip()
    except (HTTPError, URLError, TimeoutError, ValueError, KeyError, IndexError, TypeError, json.JSONDecodeError):
        return None
    # Accept only a concise two-sentence response; otherwise use the safe template.
    sentences = re.findall(r"[^.!?]+[.!?](?=\s|$)", content)
    return content if len(sentences) == 2 and len(content) <= 900 else None


def generate_clinical_justification(transfer_data: dict[str, Any]) -> str:
    """Generate an auditable two-sentence inventory transfer memo.

    The donor's post-transfer reserve must be at least 21 days or the function
    raises ``ValueError`` and produces no approval memo. With
    ``OPENAI_API_KEY`` configured, an OpenAI-compatible chat endpoint may
    improve phrasing; otherwise (or on any API failure) a deterministic local
    template is returned. Cold-chain statements are limited to the supplied
    ``is_cold_chain`` flag. This is an administrative rationale, not a clinical
    order or proof that a route is safe.

    Args:
        transfer_data: Mapping containing all required transfer facts.

    Returns:
        A concise two-sentence memo.

    Raises:
        ValueError: If required values are missing, invalid, or the reserve is
            below the 21-day minimum.
    """
    facts = _transfer_facts(transfer_data)
    return _openai_memo(facts) or _deterministic_memo(facts)


if __name__ == "__main__":
    # Small offline demonstration for all four public functions.
    catalog = ["Amoxicillin 500 mg", "Paracetamol 500 mg", "Oral Rehydration Salts"]
    print("Drug match:", normalize_drug_name("Tab Amoxcilin 500mg", catalog))

    sample_logs = pd.DataFrame({
        "facility_id": ["PHC-1"] * 28,
        "item_id": ["ORS"] * 28,
        "date": pd.date_range("2026-10-01", periods=28, freq="D"),
        "units_dispensed": [18 + (i % 5) + (3 if i % 7 in (5, 6) else 0) for i in range(28)],
    }).drop(index=[6, 13, 20]).reset_index(drop=True)
    sample_logs.loc[[3, 10, 17], "units_dispensed"] = np.nan
    imputed_logs = impute_missing_consumption(sample_logs)
    print("Imputed usage:\n", imputed_logs.loc[imputed_logs["was_imputed"]].head(5).to_string(index=False))

    bulletin = "East Delhi PHCs reporting 45% surge in Dengue and severe thrombocytopenia cases needing IV fluids."
    print("Outbreak signal:", extract_outbreak_multipliers(bulletin))

    transfer = {
        "source_name": "Dwarka General Hospital", "target_name": "Akash Hospital",
        "item_name": "Normal Saline 500 ml", "qty": 120,
        "source_expiry_days": 18, "target_stockout_days": 3.5,
        "donor_reserve_days": 24, "transit_distance_km": 11.2,
        "is_cold_chain": True,
    }
    print("Transfer memo:", generate_clinical_justification(transfer))
