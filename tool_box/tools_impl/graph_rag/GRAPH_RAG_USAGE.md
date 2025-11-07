# GraphRAG Usage (LLM-agnostic)

Minimal pipeline to use triples for Graph-RAG with any LLM API.

## Install

pip install pandas networkx

## Quick start

```python
from graph_rag import GraphRAG

rag = GraphRAG()  # loads Triples/all_triples.csv
res = rag.query("噬菌体如何感染细菌？", top_k=12, hops=1)

# Give res['prompt'] to any LLM (OpenAI, Qwen, DeepSeek, etc.)
print(res['prompt'])

# Optional: consume subgraph JSON (for UI/visualization)
subgraph = res['subgraph']
```

## What it returns
- triples: top-k relevant triples with metadata
- prompt: compact instruction+triples+question string
- subgraph: k-hop induced subgraph as JSON (nodes, edges)

## LLM call example (pseudo)

```python
# openai-like
from openai import OpenAI
client = OpenAI(base_url=..., api_key=...)
msg = client.chat.completions.create(
  model="your-model",
  messages=[{"role":"user","content":res['prompt']}],
  temperature=0
)
print(msg.choices[0].message.content)
```

## Notes
- No vendor lock-in: GraphRAG builds only text prompt and JSON subgraph.
- If you want embedding search, combine with your vector DB; this module stays symbolic.
