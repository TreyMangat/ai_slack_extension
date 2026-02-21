from __future__ import annotations

from app.api.routes import api as api_routes
from app.models import FeatureRequest
from app.security import AuthenticatedUser
from app.services.feature_service import BuildAlreadyInProgressError
from app.state_machine import BUILDING


class _Result:
    def __init__(self, feature: FeatureRequest) -> None:
        self._feature = feature

    def scalars(self) -> "_Result":
        return self

    def first(self) -> FeatureRequest:
        return self._feature


class _DbStub:
    def __init__(self, feature: FeatureRequest) -> None:
        self.feature = feature
        self.commit_called = False

    def execute(self, *_args, **_kwargs) -> _Result:
        return _Result(self.feature)

    def commit(self) -> None:
        self.commit_called = True

    def rollback(self) -> None:  # pragma: no cover - defensive only
        pass


def _user() -> AuthenticatedUser:
    return AuthenticatedUser(
        user_id="builder",
        email="builder@example.com",
        groups={"engineering"},
        auth_source="disabled",
    )


def test_start_build_duplicate_returns_idempotent_payload(monkeypatch) -> None:
    feature = FeatureRequest(id="f-1", status=BUILDING, title="Idempotency", spec={}, active_build_job_id="job-123")
    db = _DbStub(feature)

    def _raise_duplicate(_feature: FeatureRequest) -> None:
        raise BuildAlreadyInProgressError(job_id="job-123")

    class _Queue:
        enqueue_called = False

        def enqueue(self, *_args, **_kwargs):
            self.enqueue_called = True
            raise AssertionError("enqueue should not be called for duplicate build")

    queue = _Queue()
    monkeypatch.setattr(api_routes, "transition_feature_to_building", _raise_duplicate)
    monkeypatch.setattr(api_routes, "get_queue", lambda: queue)

    result = api_routes.start_build(feature_id=feature.id, payload=None, db=db, user=_user())

    assert result["ok"] is True
    assert result["enqueued"] is False
    assert result["idempotent"] is True
    assert result["feature_id"] == feature.id
    assert result["job_id"] == "job-123"
    assert db.commit_called is False


def test_start_build_enqueues_when_not_duplicate(monkeypatch) -> None:
    feature = FeatureRequest(id="f-2", status="ready_for_build", title="Start build", spec={})
    db = _DbStub(feature)

    monkeypatch.setattr(api_routes, "transition_feature_to_building", lambda _feature: None)

    class _Job:
        id = "job-456"

    class _Queue:
        def enqueue(self, *_args, **_kwargs):
            return _Job()

    monkeypatch.setattr(api_routes, "get_queue", lambda: _Queue())
    monkeypatch.setattr(api_routes, "log_event", lambda *_args, **_kwargs: None)

    result = api_routes.start_build(feature_id=feature.id, payload=None, db=db, user=_user())

    assert result == {"ok": True, "enqueued": True, "feature_id": feature.id, "job_id": "job-456"}
    assert feature.active_build_job_id == "job-456"
    assert db.commit_called is True
