"""Typed model failure state, separate from tool uncertainty and content checks."""

from app.services.execution.llm_observation import ObserverRejected
from app.services.run_budget import RunDeadlineExceeded


def remember_failure(agent, exc):
    from app.services import deep_think_agent as facade

    agent._last_model_failure = facade._classify_llm_provider_error(exc)
    agent._model_call_failed = True
    return agent._last_model_failure


def recovered(agent):
    agent._last_model_failure = None
    agent._model_call_failed = False


def terminate(agent):
    from app.services import deep_think_agent as facade

    agent._termination_reason = "llm_unavailable"
    return facade._build_llm_unavailable_final_answer(getattr(agent, "_last_model_failure", None))


async def observed_stream(agent, stream):
    try:
        async for chunk in stream:
            yield chunk
    except (ObserverRejected, RunDeadlineExceeded):
        raise
    except Exception as exc:
        remember_failure(agent, exc)
        raise
    else:
        recovered(agent)
