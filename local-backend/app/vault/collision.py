"""Collision checking. Owner: Role 2.

Every generated synthetic value must be checked against the original prompt
and against other generated values in the same conversation before use
(docs/architecture.md 1.3). Unit test this before Week 2 integration pressure.
"""

def has_collision(candidate_fake_value: str, original_prompt_text: str, conversation_id: str) -> bool:
    # TODO: check candidate against original_prompt_text (substring)
    # TODO: check candidate against existing fake_values for this conversation_id
    raise NotImplementedError
