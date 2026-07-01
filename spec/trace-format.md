# Diogenes Trace Format — v1

A Diogenes trace is a valid OpenTelemetry trace extended with attributes
in the `diogenes.*` namespace. Any OTel-compatible backend can ingest
Diogenes traces; the `diogenes.*` attributes are simply ignored by
backends that don't understand them.

## Span hierarchy

```
diogenes.run                          ← root span, one per agent run
  ├── diogenes.llm_call               ← one span per LLM API call
  ├── diogenes.tool.<name>            ← one span per tool execution
  ├── diogenes.llm_call
  └── diogenes.tool.<name>
```

The trace ID of the root span is the canonical **run ID**.

## OTel GenAI attributes (consumed)

These follow the OpenTelemetry GenAI semantic conventions working group.
Diogenes reads and surfaces these but does not define them.

| Attribute                    | Type   | Description                        |
|------------------------------|--------|------------------------------------|
| `gen_ai.system`              | string | Provider: `"anthropic"`, `"openai"` |
| `gen_ai.operation.name`      | string | Always `"chat"` for completions    |
| `gen_ai.request.model`       | string | Model identifier                   |
| `gen_ai.usage.input_tokens`  | int    | Prompt token count                 |
| `gen_ai.usage.output_tokens` | int    | Completion token count             |

## Diogenes extension attributes

### Run span (`diogenes.run`)

| Attribute                   | Type   | Required | Description                  |
|-----------------------------|--------|----------|------------------------------|
| `diogenes.run.name`         | string | yes      | Human-readable run label     |
| `diogenes.run.meta.*`       | string | no       | Arbitrary run metadata       |

### LLM call span (`diogenes.llm_call`)

| Attribute                        | Type   | Required | Description                        |
|----------------------------------|--------|----------|------------------------------------|
| `diogenes.llm.output_text`       | string | no       | First text block (truncated 2 000) |
| `diogenes.llm.tool_calls`        | string | no       | JSON array of `{name, input}`      |
| `diogenes.llm.tool_calls_count`  | int    | no       | Number of tool calls requested     |

### Tool span (`diogenes.tool.<name>`)

| Attribute                | Type   | Required | Description                     |
|--------------------------|--------|----------|---------------------------------|
| `diogenes.tool.name`     | string | yes      | Tool function name              |
| `diogenes.tool.input`    | string | no       | JSON-serialised inputs (≤500)   |
| `diogenes.tool.output`   | string | no       | String repr of output (≤1 000)  |

## Schema versioning

The schema version is communicated via the `service.version` OTel resource
attribute set by the SDK. Breaking changes bump the major version.
Non-breaking additions are minor bumps.

Current version: **1.0**