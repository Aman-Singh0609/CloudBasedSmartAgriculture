"""Crop analyses: create, list, read, delete and trend data.

Every handler passes the authenticated user's ID to the service layer, which
scopes each database query by owner. Asking for someone else's record yields
404, the same as a record that does not exist.
"""

from __future__ import annotations

import math

from flask import Blueprint, current_app, g, request, session, url_for

from app.extensions import limiter
from app.utils.payload import request_payload
from app.utils.responses import success_response
from app.utils.security import SESSION_WEATHER, login_required, rate_limit_user_key
from app.utils.validators import parse_object_id, validate_pagination, validate_prediction_inputs

bp = Blueprint("predictions", __name__, url_prefix="/api/predictions")


def _recommendation():
    return current_app.extensions["services"].recommendation


def _pagination(page: int, per_page: int, total: int) -> dict:
    total_pages = max(1, math.ceil(total / per_page))
    return {
        "page": page,
        "per_page": per_page,
        "total": total,
        "total_pages": total_pages,
        "has_previous": page > 1,
        "has_next": page < total_pages,
    }


@bp.post("")
@login_required
@limiter.limit("30 per minute", key_func=rate_limit_user_key)
def create():
    """Validate the seven readings, run the model and save the analysis."""
    inputs, out_of_range = validate_prediction_inputs(request_payload())
    document = _recommendation().create(
        user_id=g.current_user.id,
        inputs=inputs,
        out_of_range_fields=out_of_range,
        weather_context=session.get(SESSION_WEATHER),
    )
    response, status = success_response(
        _recommendation().describe(document), "Prediction generated successfully", 201
    )
    response.headers["Location"] = url_for("predictions.detail", prediction_id=str(document["_id"]))
    return response, status


@bp.get("")
@login_required
def list_predictions():
    page, per_page = validate_pagination(request.args)
    items, total = _recommendation().list_for_user(g.current_user.id, page=page, per_page=per_page)
    return success_response(
        {"items": items, "pagination": _pagination(page, per_page, total)},
        "Predictions retrieved successfully",
    )


@bp.get("/trends")
@login_required
def trends():
    """Soil readings across the user's recent analyses, for the history charts."""
    return success_response(
        {"trends": _recommendation().trend_for_user(g.current_user.id)}, "Trends retrieved successfully"
    )


@bp.get("/<prediction_id>")
@login_required
def detail(prediction_id: str):
    document = _recommendation().get_for_user(parse_object_id(prediction_id), g.current_user.id)
    return success_response(_recommendation().describe(document), "Prediction retrieved successfully")


@bp.delete("/<prediction_id>")
@login_required
@limiter.limit("60 per minute", key_func=rate_limit_user_key)
def delete(prediction_id: str):
    _recommendation().delete_for_user(parse_object_id(prediction_id), g.current_user.id)
    return success_response({"id": prediction_id}, "Prediction deleted successfully")
