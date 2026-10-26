def embedding_profile_key(provider: str, model: str, dimensions: int) -> str:
    """Return the durable identity of one compatible vector space."""
    return f"{provider}:{model}:{dimensions}"
