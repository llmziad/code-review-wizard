"""Base interface for analysis passes.

Every pass — maintainability, correctness, security, accessibility — implements
this contract. The orchestrator (`router.py`) decides which passes to run on a
given bundle; passes themselves don't know about each other.

The interview point: adding a new pass is a single file. The router decides
when it earns its tokens. The filters don't change. Cost summary aggregates
across whatever ran.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from ..models import ContextBundle, PassResult


class AnalysisPass(ABC):
    """One specialized lens on the changed code."""

    name: str  # subclasses set this; used in PassResult and ReviewComment

    @abstractmethod
    def should_run(self, bundle: ContextBundle) -> bool:
        """Quick predicate: is it worth spending tokens on this pass?

        The router calls this before incurring LLM cost. Returning False is
        free; returning True commits to a `run` call.
        """

    @abstractmethod
    async def run(self, bundle: ContextBundle) -> PassResult:
        """Execute the pass against the bundle. Must not raise on LLM failures;
        return a PassResult with `error` populated instead.
        """
