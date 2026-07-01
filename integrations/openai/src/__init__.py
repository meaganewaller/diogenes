"""
diogenes_openai
~~~~~~~~~~~~~~~
Thin adapter that makes OpenAI clients emit diogenes.* OTel spans,
mirroring the diogenes.wrap_anthropic() pattern.

Usage:
    import openai
    import diogenes
    from diogenes_openai import wrap_openai

    diogenes.init()
    client = wrap_openai(openai.OpenAI())

    # All client.chat.completions.create() calls are now traced.

Status: stub — implementation coming once the Python SDK is stable.
"""

# TODO: implement wrap_openai() mirroring diogenes.core.wrap_anthropic()