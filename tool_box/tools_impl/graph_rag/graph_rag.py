#!/usr/bin/env python3
"""
GraphRAG: Lightweight, LLM-agnostic Graph RAG over extracted triples.
- Loads Triples/all_triples.csv
- Builds a NetworkX graph
- Retrieves relevant triples and an optional k-hop subgraph for a query
- Produces a compact prompt string any LLM can consume
"""

from __future__ import annotations

import json
import os
import re
from typing import Any, Dict, List, Set, Tuple

import networkx as nx
import pandas as pd

TRIPLES_PATH_DEFAULT = os.path.join(
    os.path.dirname(__file__), "Triples", "all_triples.csv"
)


def _normalize(text: str) -> str:
    return (text or "").strip().lower()


def _tokenize(text: str) -> List[str]:
    return re.findall(r"\b\w+\b", _normalize(text))


class GraphRAG:
    def __init__(self, triples_path: str = TRIPLES_PATH_DEFAULT):
        self.triples_path = triples_path
        self.df = self._load_triples(triples_path)
        self.G = self._build_graph(self.df)
        # Precompute simple indices
        self.entity_texts = self._build_entity_texts()
        self.triple_texts = self._build_triple_texts()

    def _load_triples(self, path: str) -> pd.DataFrame:
        df = pd.read_csv(path)
        expected_cols = {
            "entity1",
            "entity1_type",
            "relation",
            "entity2",
            "entity2_type",
            "pdf_name",
            "source",
        }
        missing = expected_cols - set(df.columns)
        if missing:
            raise ValueError(f"Triples file missing required columns: {missing}")
        return df.fillna("")

    def _build_graph(self, df: pd.DataFrame) -> nx.MultiDiGraph:
        G = nx.MultiDiGraph()
        for _, row in df.iterrows():
            e1 = str(row["entity1"]).strip()
            e2 = str(row["entity2"]).strip()
            rel = str(row["relation"]).strip()
            e1t = str(row["entity1_type"]).strip()
            e2t = str(row["entity2_type"]).strip()
            pdf = str(row["pdf_name"]).strip()
            src = str(row["source"]).strip()

            if e1:
                G.add_node(e1, type=e1t)
            if e2:
                G.add_node(e2, type=e2t)
            if e1 and e2:
                G.add_edge(e1, e2, relation=rel, pdf_name=pdf, source=src)
        return G

    def _build_entity_texts(self) -> Dict[str, Set[str]]:
        # For each entity, collect tokens from its name and connected sources
        ent_tokens: Dict[str, Set[str]] = {}
        for n, data in self.G.nodes(data=True):
            toks = set(_tokenize(n))
            ent_tokens[n] = toks
        # Augment with neighbor edge source tokens
        for u, v, k, data in self.G.edges(keys=True, data=True):
            for node in (u, v):
                ent_tokens.setdefault(node, set()).update(
                    _tokenize(data.get("relation", ""))
                )
                ent_tokens[node].update(_tokenize(data.get("source", "")))
        return ent_tokens

    def _build_triple_texts(self) -> List[Tuple[int, str, Set[str]]]:
        texts: List[Tuple[int, str, Set[str]]] = []
        for idx, row in self.df.iterrows():
            s = f"{row['entity1']} --{row['relation']}--> {row['entity2']} | src: {row['source']}"
            texts.append((idx, s, set(_tokenize(s))))
        return texts

    def _score(self, q_tokens: Set[str], doc_tokens: Set[str]) -> float:
        if not q_tokens or not doc_tokens:
            return 0.0
        inter = q_tokens & doc_tokens
        return len(inter) / (len(q_tokens) ** 0.5 * len(doc_tokens) ** 0.5)

    def search_triples(self, query: str, top_k: int = 15) -> List[Dict[str, Any]]:
        q_tokens = set(_tokenize(query))
        if not q_tokens:
            return []
        scored = []
        for idx, text, tokens in self.triple_texts:
            base = self._score(q_tokens, tokens)
            # Boost if entity name exact substring matches
            row = self.df.iloc[idx]
            boost = 0.0
            for ent in (row["entity1"], row["entity2"]):
                if _normalize(ent) and _normalize(ent) in _normalize(query):
                    boost += 0.3
            scored.append((base + boost, idx))
        scored.sort(key=lambda x: x[0], reverse=True)
        results = []
        for sc, idx in scored[:top_k]:
            row = self.df.iloc[idx]
            results.append({
                "score": round(float(sc), 4),
                "entity1": row["entity1"],
                "entity1_type": row["entity1_type"],
                "relation": row["relation"],
                "entity2": row["entity2"],
                "entity2_type": row["entity2_type"],
                "pdf_name": row["pdf_name"],
                "source": row["source"],
            })
        return results

    def expand_subgraph(
        self, triples: List[Dict[str, Any]], hops: int = 1, max_nodes: int = 200
    ) -> nx.MultiDiGraph:
        nodes: Set[str] = set()
        for t in triples:
            nodes.add(t["entity1"])
            nodes.add(t["entity2"])
        frontier = set(nodes)
        visited = set(nodes)
        for _ in range(max(0, hops)):
            new_frontier = set()
            for n in list(frontier):
                for nbr in self.G.predecessors(n):
                    visited.add(nbr)
                    new_frontier.add(nbr)
                for nbr in self.G.successors(n):
                    visited.add(nbr)
                    new_frontier.add(nbr)
            frontier = new_frontier
            nodes.update(frontier)
            if len(nodes) >= max_nodes:
                break
        SG = nx.MultiDiGraph()
        for n in nodes:
            if n in self.G:
                SG.add_node(n, **self.G.nodes[n])
        for u, v, k, data in self.G.edges(keys=True, data=True):
            if u in nodes and v in nodes:
                SG.add_edge(u, v, **data)
        return SG

    def subgraph_to_json(self, SG: nx.MultiDiGraph) -> Dict[str, Any]:
        nodes = []
        node_ids = {}
        for i, (n, data) in enumerate(SG.nodes(data=True)):
            node_ids[n] = i
            nodes.append({
                "id": i,
                "name": n,
                **({"type": data.get("type")} if data.get("type") else {}),
            })
        edges = []
        for u, v, k, data in SG.edges(keys=True, data=True):
            edges.append({
                "source": node_ids[u],
                "target": node_ids[v],
                "relation": data.get("relation"),
                "pdf_name": data.get("pdf_name"),
                "source_text": data.get("source"),
            })
        return {"nodes": nodes, "edges": edges}

    def format_prompt(self, query: str, triples: List[Dict[str, Any]]) -> str:
        lines = [
            "You are a Phage–Host interaction knowledge graph expert. Answer strictly using the triples below and cite pdf_name when necessary.",
            "If the evidence is insufficient, state that information is insufficient and do not fabricate content unrelated to phage research.",
            "",
            "[Knowledge Graph Triples]",
        ]
        for i, t in enumerate(triples, 1):
            lines.append(
                f"{i}. ({t['entity1']} : {t['entity1_type']}) --[{t['relation']}]--> ({t['entity2']} : {t['entity2_type']}); src_pdf={t['pdf_name']}"
            )
        lines += [
            "",
            f"[Question] {query}",
            "[Instruction] Provide a factual answer. Cite as (source: pdf_name) when appropriate.",
        ]
        return "\n".join(lines)

    def query(
        self, query: str, top_k: int = 15, hops: int = 1, return_subgraph: bool = True
    ) -> Dict[str, Any]:
        triples = self.search_triples(query, top_k=top_k)
        result: Dict[str, Any] = {
            "query": query,
            "triples": triples,
            "prompt": self.format_prompt(query, triples),
        }
        if return_subgraph:
            SG = self.expand_subgraph(triples, hops=hops)
            result["subgraph"] = self.subgraph_to_json(SG)
        return result


def demo():
    gr = GraphRAG()
    q = "噬菌体如何感染细菌？"
    out = gr.query(q, top_k=12, hops=1)
    print("\n=== Prompt for LLM ===\n")
    print(out["prompt"])  # LLM-agnostic prompt
    print("\n=== Subgraph JSON (nodes/edges) ===\n")
    print(json.dumps(out["subgraph"], ensure_ascii=False)[:1000] + "...")


if __name__ == "__main__":
    demo()
