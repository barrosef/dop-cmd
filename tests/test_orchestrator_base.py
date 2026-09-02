import inspect
import pytest
from dop.runtime.orchestrator.base import RuntimeProvider, ServiceStatus


def test_service_status_fields():
    s = ServiceStatus(name="optum-support-be", service="optum-support-be",
                      state="running", health="healthy", port=8080, up=True)
    assert s.name == "optum-support-be"
    assert s.up is True


def test_runtime_provider_is_abstract():
    with pytest.raises(TypeError):
        RuntimeProvider()  # ABC sem implementação


def test_runtime_provider_interface():
    for method in ("up", "stop", "restart", "status", "logs", "run_ephemeral", "clean"):
        assert hasattr(RuntimeProvider, method)
        assert inspect.isfunction(getattr(RuntimeProvider, method))
