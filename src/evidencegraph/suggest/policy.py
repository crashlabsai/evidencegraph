"""Review dispositions derived in code from validated answer distributions.

A disposition only orders the review queue. It never removes a span from view, never
filters a candidate set, and never promotes a suggestion into a relation.
"""

from evidencegraph.suggest.records import Answer, Disposition

POLICY_VERSION = "2"
DISPOSITION_ORDER = ("review", "uncertain", "unscored", "background")

# Categories in which a message says something about a write, whether true or not.
WRITE_STATEMENTS = (
    "claims_completed_write",
    "reports_failed_write",
    "plans_write",
    "denies_write",
    "quotes_other_claim",
)
# On the pilot corpus every answer at confidence >= 0.95 was correct and the errors sat
# between 0.44 and 0.82. A missed write claim costs more than an extra read, so only a
# concentrated "no write" answer is moved to the background.
UNCERTAIN_BELOW = 0.8
BACKGROUND_MAX_WRITE_MASS = 0.1
RELAY_NOTE_AT = 0.05


def unscored(answer: Answer) -> tuple[Disposition, str]:
    return "unscored", f"{answer.question_key} {answer.status}: {answer.detail}"


def write_claim_disposition(claim: Answer) -> tuple[Disposition, str]:
    """Route one answered write-claim Choice to review, uncertain or background.

    `claim.label` is the most probable category, `claim.probabilities` maps all seven
    categories to probabilities, and `claim.confidence` summarises how concentrated
    they are. Return a disposition and a short reason an investigator will read.
    """
    probabilities = claim.probabilities or {}
    label, confidence = claim.label or "", claim.confidence or 0.0
    mass = sum(probabilities.get(option, 0.0) for option in WRITE_STATEMENTS)
    if label in WRITE_STATEMENTS:
        # Relays are where the model failed in the pilot; say so when it is plausible.
        relay = probabilities.get("quotes_other_claim", 0.0)
        note = (
            f"; possibly relayed (p={relay:.2f})"
            if label != "quotes_other_claim" and relay >= RELAY_NOTE_AT
            else ""
        )
        return "review", f"{label} at confidence {confidence:.2f}{note}"
    if (
        label == "insufficient_context"
        or confidence < UNCERTAIN_BELOW
        or mass > BACKGROUND_MAX_WRITE_MASS
    ):
        return (
            "uncertain",
            f"{label} at confidence {confidence:.2f}; P(any write statement) {mass:.2f}",
        )
    return "background", f"no write statement; P(any write statement) {mass:.2f}"


def search_disposition(relevance: Answer) -> tuple[Disposition, str]:
    value, confidence = relevance.value or 0.0, relevance.confidence or 0.0
    if value >= 1.5:
        return "review", f"expected relevance {value:.2f} of 3"
    if value >= 0.5 and confidence < 0.5:
        return "uncertain", f"expected relevance {value:.2f} with diffuse levels"
    return "background", f"expected relevance {value:.2f} of 3"


def disposition(task: str, answers: dict[str, Answer]) -> tuple[Disposition, str]:
    primary = answers["write_claim" if task == "write-claims" else "search_relevance"]
    if primary.status != "answered":
        return unscored(primary)
    if task == "write-claims":
        return write_claim_disposition(primary)
    return search_disposition(primary)
