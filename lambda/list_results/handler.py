"""
List-results Lambda — returns recent extraction records for the history
view.

TODO: implement once ComputeStack wiring is in place. v1 has no auth, so
this is a single shared list (most-recent-first), not scoped per user.
"""


def handler(event, context):
    raise NotImplementedError("list_results handler not yet implemented")
