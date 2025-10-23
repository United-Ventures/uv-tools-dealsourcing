import os
from openai import OpenAI

def get_openai():
    api_key = os.getenv("OPENAI_API_KEY", "")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY assente")
    return OpenAI(api_key=api_key)

def get_model():
    return os.getenv("OPENAI_MODEL", "gpt-4o-mini")
