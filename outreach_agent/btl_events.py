"""BTL protocol event types — for documentation and validation.

All BTL events follow the domain.action convention from events.py.
Publishers (growth_brain, experiment_engine, veto_system, revenue_tracker,
bandit_framework, competitor_tracker) emit these events; subscribers
(master agent, digest builder, analytics) consume them.

Keeping this list centralised gives us:
  1. A single place to grep when an event name changes.
  2. A quick reference for anyone writing a new subscriber.
  3. A validation hook for future event-schema testing.
"""

BTL_EVENT_TYPES = [
    # Experiment lifecycle
    "experiment.proposed",
    "experiment.started",
    "experiment.completed",
    "experiment.analyzed",
    "experiment.vetoed",
    # Veto queue
    "proposal.pending",
    "proposal.executed",
    "proposal.vetoed",
    # Budget / revenue
    "budget.donation",
    "budget.spend",
    # Channel portfolio
    "channel.activated",
    "channel.paused",
    "channel.reallocated",
    # Self-assessment
    "score.calculated",
    # Learning layers
    "insight.discovered",
    "bandit.updated",
    "bandit.breakthrough",
    # Competitor intel
    "competitor.spike_detected",
]
