import os
from functools import lru_cache


@lru_cache(maxsize=1)
def get_openai_client(api_key: str):
    import openai

    client = openai.AsyncOpenAI(api_key=api_key)
    if os.getenv("LANGSMITH_TRACING", "false").lower() == "true":
        from langsmith.wrappers import wrap_openai

        return wrap_openai(client)
    return client
