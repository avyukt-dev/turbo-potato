import json
from types import SimpleNamespace

import pytest
from news_ai_events import ReconciliationMode, ReconciliationReport
from news_ai_runtime.cli import main


class Service:
    def __init__(self, *, error_code=None):
        self.calls = []
        self.error_code = error_code

    async def reconcile(self, **kwargs):
        self.calls.append(kwargs)
        return ReconciliationReport(
            mode=kwargs["mode"],
            publishing_paused=True,
            limit=kwargs["limit"],
            scanned=0,
            replay_required=0,
            replayed=0,
            already_processed=0,
            domain_complete=0,
            manual_review_required=0,
            unsupported=0,
            invalid=0,
            error_code=self.error_code,
        )


class Stack:
    def __init__(self, service):
        self.service = service
        self.closed = False

    async def close(self):
        self.closed = True


def test_newsctl_reconciliation_defaults_to_bounded_read_only_json(capsys):
    service, stack = Service(), Stack(Service())
    stack.service = service
    assert main(["events", "reconcile", "--dry-run"], reconciliation_stack=stack) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["mode"] == "DRY_RUN" and output["limit"] == 100
    assert service.calls == [
        {
            "mode": ReconciliationMode.DRY_RUN,
            "limit": 100,
            "reason": None,
            "event_type": None,
            "after_outbox_id": None,
        }
    ]
    assert stack.closed


def test_newsctl_apply_is_explicit_reasoned_and_propagates_normalized_failure(capsys):
    service = Service(error_code="PUBLISHING_NOT_PAUSED")
    stack = Stack(service)
    code = main(
        [
            "events",
            "reconcile",
            "--apply",
            "--limit",
            "12",
            "--reason",
            "verified Redis restoration",
            "--event-type",
            "publication.scheduled",
        ],
        reconciliation_stack=stack,
    )
    assert code == 2
    output = json.loads(capsys.readouterr().out)
    assert output["error_code"] == "PUBLISHING_NOT_PAUSED"
    assert service.calls[0]["mode"] is ReconciliationMode.APPLY
    assert service.calls[0]["reason"] == "verified Redis restoration"


@pytest.mark.parametrize(
    "arguments",
    [
        ["events", "reconcile"],
        ["events", "reconcile", "--dry-run", "--apply"],
        ["events", "reconcile", "--dry-run", "--limit", "501"],
        ["events", "reconcile", "--apply", "--reason", "ok", "--stream", "arbitrary"],
    ],
)
def test_newsctl_reconciliation_rejects_unsafe_or_unbounded_arguments(arguments, capsys):
    if "--limit" in arguments:
        assert main(arguments, reconciliation_stack=SimpleNamespace()) == 2
        assert json.loads(capsys.readouterr().out) == {"error_code": "INVALID_LIMIT"}
    else:
        with pytest.raises(SystemExit) as error:
            main(arguments, reconciliation_stack=SimpleNamespace())
        assert error.value.code == 2


def test_newsctl_apply_requires_a_nonempty_reason(capsys):
    assert (
        main(
            ["events", "reconcile", "--apply"],
            reconciliation_stack=SimpleNamespace(),
        )
        == 2
    )
    assert json.loads(capsys.readouterr().out) == {"error_code": "REASON_REQUIRED"}


def test_newsctl_reconciliation_never_renders_raw_failure(capsys):
    class BrokenService:
        async def reconcile(self, **kwargs):
            raise RuntimeError("token=SUPER_SECRET_RECONCILIATION_TOKEN_123")

    assert (
        main(
            ["events", "reconcile", "--dry-run"],
            reconciliation_stack=Stack(BrokenService()),
        )
        == 2
    )
    assert json.loads(capsys.readouterr().out) == {"error_code": "OPERATION_UNAVAILABLE"}
