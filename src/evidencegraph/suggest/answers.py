"""Strict validation of provider responses against the questions actually sent.

Nothing is repaired. A missing, extra or mistyped answer, an option outside the
criteria, a non-finite or out-of-range probability, or a distribution that does not
sum to one within rounding makes that answer `invalid`. A failed exchange makes every
answer `unavailable`, which is different from a semantic "insufficient context".
"""

import json
import math

from evidencegraph.strict_json import (
    StrictJsonError,
    finite_json_float,
    reject_json_constant,
    unique_json_object,
)
from evidencegraph.suggest.records import Answer, ExchangeRecord

# Providers report probabilities rounded to two decimals, so each value may be off by
# up to 0.005; a distribution must sum to one within that rounding for every option.
ROUNDING = 0.005
# Aliases resolve to whichever version is current; any other name is a pinned version
# and an answer from a different model is rejected rather than silently accepted.
MOVING_ALIASES = frozenset({"jev-latest", "jev-preview"})


def parse(body: bytes) -> object:
    return json.loads(
        body,
        object_pairs_hook=unique_json_object,
        parse_float=finite_json_float,
        parse_constant=reject_json_constant,
    )


def unavailable(questions: dict[str, dict], detail: str) -> dict[str, Answer]:
    return {key: Answer(question_key=key, status="unavailable", detail=detail) for key in questions}


def invalid(key: str, detail: str) -> Answer:
    return Answer(question_key=key, status="invalid", detail=detail)


def probability(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        number = float(value)
    except OverflowError:
        return None
    return number if math.isfinite(number) and 0 <= number <= 1 else None


def distribution(raw: object, options: list[str]) -> dict[str, float] | str:
    if not isinstance(raw, dict) or set(raw) != set(options):
        return "probabilities do not name exactly the offered options"
    values = {option: probability(raw[option]) for option in options}
    if any(v is None for v in values.values()):
        return "probability outside [0, 1] or not a number"
    checked = {k: v for k, v in values.items() if v is not None}
    if abs(sum(checked.values()) - 1) > ROUNDING * len(options) + 1e-9:
        return "probabilities do not sum to one"
    return checked


def check_answer(key: str, question: dict, raw: object) -> Answer:
    if not isinstance(raw, dict) or raw.get("type") != question["type"]:
        return invalid(key, "answer type differs from the question")
    kind = question["type"]
    if kind == "noul":
        value = probability(raw.get("noul"))
        if value is None or set(raw) != {"type", "noul"}:
            return invalid(key, "malformed noul answer")
        return Answer(question_key=key, status="answered", value=value)
    confidence = probability(raw.get("confidence"))
    if confidence is None:
        return invalid(key, "confidence outside [0, 1] or not a number")
    if kind == "choice":
        options = list(question["criteria"])
        if set(raw) != {"type", "choice", "probabilities", "confidence"}:
            return invalid(key, "unexpected choice answer fields")
        probs = distribution(raw["probabilities"], options)
        if isinstance(probs, str):
            return invalid(key, probs)
        chosen = raw["choice"]
        if not isinstance(chosen, str) or chosen not in probs:
            return invalid(key, "choice outside the offered options")
        if probs[chosen] + 1e-9 < max(probs.values()):
            return invalid(key, "choice is not the highest-probability option")
        return Answer(
            question_key=key,
            status="answered",
            label=chosen,
            probabilities=probs,
            confidence=confidence,
        )
    levels = [str(i) for i in range(len(question["criteria"]))]
    if set(raw) != {"type", "score", "legend", "probabilities", "confidence"}:
        return invalid(key, "unexpected score answer fields")
    if raw["legend"] != {
        level: text for level, text in zip(levels, question["criteria"], strict=True)
    }:
        return invalid(key, "score legend differs from the levels sent")
    probs = distribution(raw["probabilities"], levels)
    if isinstance(probs, str):
        return invalid(key, probs)
    score = raw["score"]
    if isinstance(score, bool) or not isinstance(score, (int, float)):
        return invalid(key, "score is not a number")
    score = float(score)
    expected = sum(int(level) * p for level, p in probs.items())
    if (
        not math.isfinite(score)
        or abs(score - expected) > ROUNDING * sum(range(len(levels))) + 0.01
    ):
        return invalid(key, "score is not the expectation of its distribution")
    top = max(levels, key=lambda level: (probs[level], -int(level)))
    return Answer(
        question_key=key,
        status="answered",
        label=top,
        probabilities=probs,
        confidence=confidence,
        value=float(score),
    )


def validate_exchange(
    request_body: bytes,
    exchange: ExchangeRecord,
    response: bytes | None,
    *,
    requested_model: str,
) -> tuple[str | None, dict[str, Answer]]:
    """Validate one recorded exchange; returns the resolved model and one answer per question."""
    request = parse(request_body)
    if not isinstance(request, dict) or not isinstance(request.get("questions"), dict):
        raise ValueError("recorded request has no questions")
    questions: dict[str, dict] = request["questions"]
    if exchange.status != "ok" or response is None:
        return None, unavailable(questions, exchange.error or exchange.status)
    try:
        body = parse(response)
    except (StrictJsonError, ValueError, UnicodeError, RecursionError):
        # The parser's own message varies between Python versions; replay must not.
        return None, {k: invalid(k, "response is not strict JSON") for k in questions}
    if not isinstance(body, dict) or not isinstance(body.get("answers"), dict):
        return None, {k: invalid(k, "response has no answers object") for k in questions}
    model = body.get("model")
    if not isinstance(model, str):
        return None, {k: invalid(k, "response does not name its model") for k in questions}
    if requested_model not in MOVING_ALIASES and model != requested_model:
        detail = f"pinned {requested_model} but {model} answered"
        return model, {k: invalid(k, detail) for k in questions}
    extra = set(body["answers"]) - set(questions)
    if extra:
        detail = "response answered questions that were not asked"
        return model, {k: invalid(k, detail) for k in questions}
    return model, {
        key: total_check(key, question, body["answers"][key])
        if key in body["answers"]
        else invalid(key, "question left unanswered")
        for key, question in questions.items()
    }


def total_check(key: str, question: dict, raw: object) -> Answer:
    """Any answer shape the checks did not anticipate is invalid, never a crash."""
    try:
        return check_answer(key, question, raw)
    except (TypeError, ValueError, OverflowError, RecursionError):
        return invalid(key, "malformed answer")
