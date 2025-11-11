# Local Embedding Setup Guide

## 🎯 Overview

This guide explains how to set up and use local embedding models instead of GLM API for academic paper writing and research.

## 📦 Installation

### Step 1: Install Dependencies

```bash
# Activate your conda environment
conda activate LLM

# Install sentence-transformers
pip install sentence-transformers

# Optional: Install PyTorch with CUDA support for GPU acceleration
# For CUDA 11.8:
pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu118

# For CPU only:
pip install torch torchvision torchaudio
```

### Step 2: Configure Environment

```bash
# Copy the example configuration
cp .env.local_embedding.example .env

# Edit .env and set:
# USE_LOCAL_EMBEDDING=true
# LOCAL_EMBEDDING_MODEL=sentence-transformers/all-mpnet-base-v2
```

## 🌟 Recommended Models for Academic Writing

### 1. all-mpnet-base-v2 (Recommended) ⭐⭐⭐⭐⭐

**Best for**: High-quality academic paper retrieval and writing

```env
LOCAL_EMBEDDING_MODEL=sentence-transformers/all-mpnet-base-v2
GLM_EMBEDDING_DIMENSION=768
```

**Specs**:
- Dimension: 768
- Size: 420MB
- Speed: ~2800 sentences/sec (GPU)
- MTEB Score: 63.3

**Pros**:
- Excellent quality for academic text
- Widely used in research
- Good balance of speed and accuracy

### 2. all-MiniLM-L6-v2 (Fast Alternative) ⭐⭐⭐⭐

**Best for**: Quick prototyping and real-time search

```env
LOCAL_EMBEDDING_MODEL=sentence-transformers/all-MiniLM-L6-v2
GLM_EMBEDDING_DIMENSION=384
```

**Specs**:
- Dimension: 384
- Size: 80MB
- Speed: ~14000 sentences/sec (GPU)
- MTEB Score: 58.8

**Pros**:
- Very fast
- Small memory footprint
- Good enough for most tasks

### 3. bge-base-en-v1.5 (Retrieval Optimized) ⭐⭐⭐⭐⭐

**Best for**: Academic paper search and citation finding

```env
LOCAL_EMBEDDING_MODEL=BAAI/bge-base-en-v1.5
GLM_EMBEDDING_DIMENSION=768
```

**Specs**:
- Dimension: 768
- Size: 420MB
- MTEB Score: 63.6

**Pros**:
- Optimized for retrieval tasks
- Excellent for finding relevant papers
- State-of-the-art performance

### 4. e5-base-v2 (Multi-task) ⭐⭐⭐⭐

**Best for**: Diverse academic tasks

```env
LOCAL_EMBEDDING_MODEL=intfloat/e5-base-v2
GLM_EMBEDDING_DIMENSION=768
```

**Specs**:
- Dimension: 768
- Size: 420MB
- MTEB Score: 62.3

**Pros**:
- Microsoft research
- Good for multiple tasks
- Robust performance

## 🚀 Quick Start

### Test the Setup

```python
# Test local embedding
python -c "
from app.services.embeddings.embeddings import get_embeddings_service

service = get_embeddings_service()
print('Service info:', service.get_service_info())

# Test embedding generation
texts = ['This is a test sentence for academic writing.']
embeddings = service.get_embeddings(texts)
print(f'Generated embedding dimension: {len(embeddings[0])}')
print('✅ Local embedding working!')
"
```

### Performance Comparison

```python
# Compare local vs GLM API
import time
from app.services.embeddings.embeddings import get_embeddings_service

service = get_embeddings_service()

# Test texts
texts = [
    "Machine learning is a subset of artificial intelligence.",
    "Deep learning uses neural networks with multiple layers.",
    "Natural language processing enables computers to understand text."
] * 10  # 30 texts

# Benchmark
start = time.time()
embeddings = service.get_embeddings(texts)
elapsed = time.time() - start

print(f"Processed {len(texts)} texts in {elapsed:.2f}s")
print(f"Speed: {len(texts)/elapsed:.1f} texts/sec")
print(f"Embedding dimension: {len(embeddings[0])}")
```

## 💻 Hardware Requirements

### Minimum (CPU)
- RAM: 4GB
- Storage: 2GB
- Speed: ~50-100 texts/sec

### Recommended (GPU)
- GPU: NVIDIA with 4GB+ VRAM
- RAM: 8GB
- Storage: 2GB
- Speed: ~1000-5000 texts/sec

### Optimal (High-end GPU)
- GPU: NVIDIA RTX 3090/4090 or A100
- RAM: 16GB+
- Storage: 2GB
- Speed: ~10000+ texts/sec

## 🔧 Configuration Options

### Environment Variables

```bash
# Enable local embedding
USE_LOCAL_EMBEDDING=true

# Model selection
LOCAL_EMBEDDING_MODEL=sentence-transformers/all-mpnet-base-v2

# Dimension (must match model)
GLM_EMBEDDING_DIMENSION=768

# Batch size (adjust based on GPU memory)
GLM_BATCH_SIZE=32  # Increase to 64 or 128 for better GPU utilization
```

### Programmatic Configuration

```python
from app.services.foundation.config import get_config

config = get_config()
print(f"Using local embedding: {config.use_local_embedding}")
print(f"Model: {config.local_embedding_model}")
print(f"Dimension: {config.embedding_dimension}")
```

## 🎓 Academic Use Cases

### 1. Paper Similarity Search

```python
from app.services.embeddings.embeddings import get_embeddings_service

service = get_embeddings_service()

# Your paper abstract
query = "We propose a novel attention mechanism for transformer models..."

# Candidate papers
candidates = [
    "Attention is all you need introduces the transformer architecture...",
    "BERT uses bidirectional transformers for language understanding...",
    "GPT-3 is a large language model with 175 billion parameters..."
]

# Get embeddings
query_emb = service.get_single_embedding(query)
candidate_embs = service.get_embeddings(candidates)

# Find most similar
similarities = service.compute_similarities(query_emb, candidate_embs)
for i, sim in enumerate(similarities):
    print(f"Paper {i+1}: {sim:.3f} - {candidates[i][:50]}...")
```

### 2. Citation Recommendation

```python
# Find relevant citations for your work
your_section = "Our method improves upon previous work in neural machine translation..."

# Database of potential citations
citation_db = load_citation_database()  # Your citation database

# Get embeddings
section_emb = service.get_single_embedding(your_section)
citation_embs = service.get_embeddings([c['abstract'] for c in citation_db])

# Find top 5 most relevant
top_citations = service.find_most_similar(
    section_emb, 
    citation_embs, 
    k=5, 
    min_similarity=0.7
)
```

### 3. Literature Review Clustering

```python
# Group similar papers for literature review
papers = load_papers()  # Your paper collection
paper_texts = [p['title'] + ' ' + p['abstract'] for p in papers]

# Get embeddings
embeddings = service.get_embeddings(paper_texts)

# Cluster similar papers
from sklearn.cluster import KMeans
kmeans = KMeans(n_clusters=5)
clusters = kmeans.fit_predict(embeddings)

# Group by cluster
for i in range(5):
    cluster_papers = [papers[j] for j in range(len(papers)) if clusters[j] == i]
    print(f"\nCluster {i+1}: {len(cluster_papers)} papers")
```

## 🔄 Switching Between Local and GLM API

### Use Local by Default

```env
USE_LOCAL_EMBEDDING=true
LOCAL_EMBEDDING_MODEL=sentence-transformers/all-mpnet-base-v2
```

### Switch to GLM API

```env
USE_LOCAL_EMBEDDING=false
GLM_API_KEY=your_api_key
```

### Hybrid Approach (Code)

```python
# Fallback to GLM if local fails
from app.services.embeddings.local_embedding_client import LocalEmbeddingClient
from app.services.embeddings.glm_api_client import GLMApiClient

try:
    client = LocalEmbeddingClient(config)
    embeddings = client.get_embeddings(texts)
except Exception as e:
    print(f"Local failed: {e}, falling back to GLM")
    client = GLMApiClient(config)
    embeddings = client.get_embeddings(texts)
```

## 📊 Performance Tips

### 1. Optimize Batch Size

```python
# For GPU with 8GB VRAM
GLM_BATCH_SIZE=64

# For GPU with 16GB+ VRAM
GLM_BATCH_SIZE=128

# For CPU
GLM_BATCH_SIZE=16
```

### 2. Use Caching

The system automatically caches embeddings. Check cache stats:

```python
from app.services.embeddings.cache import get_embedding_cache

cache = get_embedding_cache()
stats = cache.get_stats()
print(f"Cache hit rate: {stats['hit_rate']:.2%}")
```

### 3. Precompute Embeddings

```python
# Precompute for large datasets
papers = load_large_paper_database()
texts = [p['abstract'] for p in papers]

# Async precomputation with progress
future = service.precompute_embeddings_async(
    texts,
    progress_callback=lambda done, total: print(f"{done}/{total}")
)

result = future.result()
print(f"Precomputed {result['total']} embeddings")
```

## 🐛 Troubleshooting

### Model Download Issues

```bash
# Manually download model
python -c "
from sentence_transformers import SentenceTransformer
model = SentenceTransformer('sentence-transformers/all-mpnet-base-v2')
print('Model downloaded successfully')
"
```

### GPU Not Detected

```python
import torch
print(f"CUDA available: {torch.cuda.is_available()}")
print(f"CUDA device: {torch.cuda.get_device_name(0) if torch.cuda.is_available() else 'None'}")
```

### Memory Issues

```bash
# Reduce batch size
GLM_BATCH_SIZE=16

# Or use smaller model
LOCAL_EMBEDDING_MODEL=sentence-transformers/all-MiniLM-L6-v2
```

## 📚 References

- [Sentence Transformers Documentation](https://www.sbert.net/)
- [MTEB Leaderboard](https://huggingface.co/spaces/mteb/leaderboard)
- [Model Cards on HuggingFace](https://huggingface.co/models?library=sentence-transformers)
