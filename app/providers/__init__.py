"""AI provider backends.

Each provider module exposes the same two functions:

    stream(system, user_text, model, on_progress) -> (text, usage_dict)
    credentials_available() -> bool

`usage_dict` always has the keys input_tokens / output_tokens /
cache_creation_tokens / cache_read_tokens so app.costs can price any
provider's answer the same way. notes.py picks the module from the
AI_PROVIDER setting.
"""
