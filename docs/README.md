# DeckDNA documentation

These pages describe the running system as it is, grounded in the code. They
distinguish implemented, prototype and planned behaviour throughout, per
blueprint section 21.

## Start here

| Page | Read it to learn |
|---|---|
| [Architecture](architecture.md) | the module map and the single layout source of truth |
| [Pipeline](pipeline.md) | one source through every stage, with the artifacts each writes |
| [Status and roadmap](status-and-roadmap.md) | what is implemented, prototype, or planned - and the honest limits |

## Using DeckDNA

| Page | Read it to learn |
|---|---|
| [CLI reference](cli.md) | every script, flag, exit code and note format |
| [Web app](web-app.md) | the five pages and the full `/v1` JSON API |
| [Configuration](configuration.md) | the optional Gemini key, renderers, paths and limits |

## Understanding the system

| Page | Read it to learn |
|---|---|
| [Style learning](style-learning.md) | how a design system is learned and what each source type can teach |
| [Generation and critique](generation-and-critique.md) | the no-invention rules, capacity limits and the bounded repair loop |
| [Data model](data-model.md) | the Pydantic models, on-disk artifacts and id conventions |
| [Security and privacy](security-and-privacy.md) | what stays local, what leaves the machine and only when, and the gaps |

## Developing

| Page | Read it to learn |
|---|---|
| [Testing](testing.md) | the 295-test suite map, fixtures and how to run it |
