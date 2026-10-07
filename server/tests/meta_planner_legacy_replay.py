"""Pre-recipe GraphIntent fault replays, not current model-entry acceptance.

Only the model wire-format gate is bypassed. Authorization, types, resources,
control flow, compilation, Patch repair and publication checks stay unchanged.
Never install this helper globally or use it for transport/recipe acceptance.
"""
from server.meta_agent.meta_planner_v2 import MetaPlannerV2Service


class LegacyGraphReplayService(MetaPlannerV2Service):
    def _compile_and_validate(self, **kwargs):
        kwargs.pop("require_recipe", None)
        return super()._compile_and_validate(**kwargs)
