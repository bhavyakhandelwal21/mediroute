"""Arrival-temperature decision support for blood components and supplies.

The configured ranges are prototype defaults supplied for MediRoute. An
assessment does not release a product for use or independently authorize
disposal; staff must apply product-specific guidance and local procedures.
"""
from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel, Field

router = APIRouter(prefix="/api/temperature", tags=["Arrival temperature"])

Product = Literal[
    "Platelets",
    "Red cells",
    "Whole blood",
    "Frozen plasma",
    "Insulin",
    "Vaccines",
]

# lower bound, upper bound; None means no lower bound was supplied.
LIMITS_C: dict[str, tuple[float | None, float]] = {
    "Platelets": (20.0, 24.0),
    "Red cells": (2.0, 6.0),
    "Whole blood": (2.0, 6.0),
    "Frozen plasma": (None, -18.0),
    "Insulin": (2.0, 8.0),
    "Vaccines": (2.0, 8.0),
}


class ArrivalCheck(BaseModel):
    product: Product
    arrival_temp_c: float = Field(allow_inf_nan=False, ge=-100, le=60)
    excursion_duration_minutes: int | None = Field(default=None, ge=0, le=100000)


class Assessment(BaseModel):
    product: Product
    arrival_temp_c: float
    allowed_range_c: str
    deviation_c: float
    status: Literal["optimal", "mild_excursion", "review_required", "severe_breach_review"]
    recommended_action: str
    excursion_duration_minutes: int | None
    clinical_use_authorized: Literal[False] = False


@router.post("/check", response_model=Assessment)
def check_arrival_temperature(data: ArrivalCheck) -> Assessment:
    """Assess a delivery reading and return a cautious review recommendation."""
    lower, upper = LIMITS_C[data.product]
    if lower is None:
        in_range = data.arrival_temp_c <= upper
        deviation = max(0.0, data.arrival_temp_c - upper)
        range_label = f"≤ {upper:g}°C"
    else:
        in_range = lower <= data.arrival_temp_c <= upper
        deviation = max(lower - data.arrival_temp_c, data.arrival_temp_c - upper, 0.0)
        range_label = f"{lower:g}–{upper:g}°C"

    if in_range:
        status = "optimal"
        action = (
            "Within the configured range. Record the reading and follow the receiving SOP; "
            "this assessment does not itself authorize clinical use."
        )
    elif lower is not None and data.arrival_temp_c < lower and deviation > 4.0:
        status = "severe_breach_review"
        action = (
            "Do not issue. Maintain appropriate storage, document the excursion, and escalate "
            "to the responsible lead. Dispose only if an authorized product-specific assessment "
            "determines it is unusable."
        )
    elif data.arrival_temp_c > upper:
        warm_excursion = data.arrival_temp_c - upper
        if 2.0 <= warm_excursion <= 4.0:
            status = "mild_excursion"
            action = (
                "Flag as a fast-track OPD candidate for prompt review. Keep separate from usable "
                "stock; issue only after approval by the responsible clinician, pharmacist, or "
                "blood-bank lead under product-specific guidance."
            )
        elif warm_excursion > 4.0:
            status = "severe_breach_review"
            action = (
                "Do not issue. Maintain appropriate storage, document the excursion, and escalate "
                "to the responsible lead. Dispose only if an authorized product-specific assessment "
                "determines it is unusable."
            )
        else:
            status = "review_required"
            action = (
                "Do not issue pending product-specific review. Maintain appropriate storage and "
                "document the reading and exposure duration."
            )
    else:
        status = "review_required"
        action = (
            "Do not issue pending product-specific review. Maintain appropriate storage and "
            "document the reading and exposure duration."
        )

    return Assessment(
        product=data.product,
        arrival_temp_c=data.arrival_temp_c,
        allowed_range_c=range_label,
        deviation_c=round(deviation, 2),
        status=status,
        recommended_action=action,
        excursion_duration_minutes=data.excursion_duration_minutes,
    )
