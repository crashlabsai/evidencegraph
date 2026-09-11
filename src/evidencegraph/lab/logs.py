"""Write genuine Inspect logs without running a model or a network request."""

from datetime import datetime
from pathlib import Path

from inspect_ai.event import Event
from inspect_ai.log import (
    EvalConfig,
    EvalDataset,
    EvalLog,
    EvalPlan,
    EvalSample,
    EvalSpec,
    write_eval_log,
)
from inspect_ai.model import ChatMessage, ModelOutput


def sample(
    label: str,
    messages: list[ChatMessage],
    events: list[Event],
    started: datetime,
    stopped: datetime,
) -> EvalSample:
    return EvalSample(
        id=f"agent-{label}",
        epoch=1,
        uuid=f"agent-{label}",
        input="Prepare registry artifacts",
        target="",
        messages=messages,
        output=ModelOutput(model="synthetic/scripted", completion="Done"),
        events=events,
        started_at=started.isoformat(),
        completed_at=stopped.isoformat(),
    )


def write_eval(path: Path, samples: list[EvalSample], started: datetime) -> Path:
    log = EvalLog(
        version=2,
        status="success",
        eval=EvalSpec(
            run_id="scripted-stage",
            task="registry-stage",
            task_id="registry-stage",
            created=started.isoformat(),
            model="synthetic/scripted",
            dataset=EvalDataset(
                name="scripted", samples=len(samples), sample_ids=[s.id for s in samples]
            ),
            config=EvalConfig(epochs=1),
        ),
        plan=EvalPlan(name="scripted", steps=[]),
        samples=samples,
    )
    write_eval_log(log, str(path))
    return path
