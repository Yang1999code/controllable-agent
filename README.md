# my-agent

AI Agent framework — multi-agent collaboration + self-evolution.

## Architecture

```
ai/          ← Zero dependency: pure types + abstract interfaces
agent/       ← Core logic: 20 interface implementations
app/         ← Concrete: CLI + built-in tools + providers + config
```

## Quick Start

```bash
pip install -e .
python -m app.cli
```

## Project Structure

See [需求4技术2.md](../需求文档/需求4技术2.md) for the full technical specification.
