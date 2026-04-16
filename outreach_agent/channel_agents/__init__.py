"""Channel agent base class and registry for the BTL protocol.

Every growth channel (email outreach, TikTok clip, IG reel, etc.) is modelled
as a `ChannelAgent`. Subclasses:

  - declare a stable `channel_id` that matches `channel_registry.json`,
  - declare their bandit `arms` (dict of arm_name -> list of candidate values),
  - implement `can_run()`, `execute(config)`, and `get_metrics(days)`.

Constructing a subclass auto-registers the instance in the module-level
`_REGISTRY` so the orchestrator can look it up by id (`get_agent`) or iterate
over the entire fleet (`list_agents`). Each agent gets a `Bandit` wired to its
own arms, giving every channel automatic Thompson-Sampling-backed arm
selection through `select_arms()` / `record_outcome()`.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from bandit_framework import Bandit


# Process-global registry of instantiated channel agents, keyed by channel_id.
# Tests should clear this between cases (`_REGISTRY.clear()` in setup).
_REGISTRY: dict[str, "ChannelAgent"] = {}


class ChannelAgent(ABC):
    """Base class for all BTL channel agents.

    Subclasses must define:
      - channel_id: str — matches channel_registry.json
      - arms: dict[str, list[str]] — bandit arms for this channel

    And implement:
      - can_run() -> bool
      - execute(config) -> dict
      - get_metrics(days) -> dict
    """

    channel_id: str = ""
    arms: dict[str, list[str]] = {}

    def __init__(self) -> None:
        # Auto-register the instance under its channel_id. An empty channel_id
        # means "abstract / not yet a real channel" — skip registration and the
        # bandit so test scaffolding can exist without polluting the registry.
        if self.channel_id:
            _REGISTRY[self.channel_id] = self
            if self.arms:
                self._bandit: Bandit | None = Bandit(self.channel_id, self.arms)
            else:
                self._bandit = None
        else:
            self._bandit = None

    # ─── Abstract surface (subclass must implement) ───────────────────────────

    @abstractmethod
    def can_run(self) -> bool:
        """Return True if the agent has all preconditions met to run now."""
        ...

    @abstractmethod
    def execute(self, config: dict) -> dict:
        """Run one cycle of the channel's work. Return summary metrics."""
        ...

    @abstractmethod
    def get_metrics(self, days: int = 7) -> dict:
        """Return rolled-up performance metrics for the last `days` days."""
        ...

    # ─── Bandit passthroughs ──────────────────────────────────────────────────

    def select_arms(self) -> dict[str, str]:
        """Pick one value per arm dimension via the channel's bandit.

        Returns an empty dict when the agent declared no arms.
        """
        if self._bandit:
            return self._bandit.select()
        return {}

    def record_outcome(self, arm_values: dict[str, str], reward: float) -> None:
        """Feed a (arm_values, reward) observation back to the bandit."""
        if self._bandit:
            self._bandit.record(arm_values, reward)

    def get_bandit_stats(self) -> dict:
        """Return current posterior summary for this channel's bandit."""
        if self._bandit:
            return self._bandit.get_stats()
        return {}


# ─── Registry helpers ─────────────────────────────────────────────────────────

def get_agent(channel_id: str) -> ChannelAgent | None:
    """Look up a registered channel agent by its channel_id."""
    return _REGISTRY.get(channel_id)


def list_agents() -> list[ChannelAgent]:
    """Return every channel agent currently registered in the process."""
    return list(_REGISTRY.values())
