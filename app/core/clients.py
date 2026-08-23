from functools import lru_cache


@lru_cache(maxsize=1)
def get_openai_client(api_key: str):
    import openai

    return openai.AsyncOpenAI(api_key=api_key)
