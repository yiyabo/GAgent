# Amem Memory System FastAPI Interface

AmemFastAPI，

## 

- ****: ，
- ****: 
- ****: 
- **ChromaDB**: 

## 

### 1. 

```bash
cd A-mem-main
pip install -r requirements.txt
```

### 2. 

：

```bash
cp config.example.cfg ../config.cfg
```

 `config.cfg` ，API：

```ini
[DEFAULT]
llm_backend = openai
llm_model = gpt-4o-mini
model_name = all-MiniLM-L6-v2
api_key = your-openai-api-key-here
evo_threshold = 100
```

### 3. 

```bash
python api.py
```

 `http://0.0.0.0:8000` 

 `http://localhost:8000/docs` API

## API

### 1.  (POST /add_memory)



**:**

```bash
curl -X POST "http://localhost:8000/add_memory" \
  -H "Content-Type: application/json" \
  -d '{
    "content": "",
    "timestamp": "202501110900",
    "keywords": ["", "", ""],
    "context": "",
    "tags": ["", "", ""]
  }'
```

**:**

```json
{
  "success": true,
  "memory_id": "550e8400-e29b-41d4-a716-446655440000",
  "message": "Memory added successfully"
}
```

**:**

- `content` (): 
- `timestamp` (): ， YYYYMMDDHHMM
- `keywords` (): ，
- `context` (): ，
- `tags` (): ，

### 2.  (POST /query_memory)



**:**

```bash
curl -X POST "http://localhost:8000/query_memory" \
  -H "Content-Type: application/json" \
  -d '{
    "query": "？",
    "top_k": 5
  }'
```

**:**

```json
{
  "success": true,
  "query": "？",
  "count": 2,
  "results": [
    {
      "id": "550e8400-e29b-41d4-a716-446655440000",
      "content": "",
      "context": "",
      "keywords": ["", "", ""],
      "tags": ["", "", ""],
      "timestamp": "202501110900",
      "score": 0.85
    }
  ]
}
```

**:**

- `query` (): 
- `top_k` (): ，5，1-20

### 3.  (GET /health)



**:**

```bash
curl "http://localhost:8000/health"
```

**:**

```json
{
  "status": "healthy",
  "memory_count": 42,
  "timestamp": "2025-01-11T09:00:00"
}
```

## Python

```python
import requests

# APIURL
BASE_URL = "http://localhost:8000"

# 
def add_memory(content, **kwargs):
    response = requests.post(
        f"{BASE_URL}/add_memory",
        json={
            "content": content,
            **kwargs
        }
    )
    return response.json()

# 
def query_memory(query, top_k=5):
    response = requests.post(
        f"{BASE_URL}/query_memory",
        json={
            "query": query,
            "top_k": top_k
        }
    )
    return response.json()

# 
if __name__ == "__main__":
    # 
    result = add_memory(
        content="FastAPI",
        tags=["Python", "FastAPI", ""]
    )
    print(f"Memory added: {result['memory_id']}")

    # 
    results = query_memory("FastAPI", top_k=3)
    print(f"Found {results['count']} memories:")
    for memory in results['results']:
        print(f"- {memory['content']}")
```

## 

### 

，keywordscontexttags，LLM：

- **Keywords**: 
- **Context**: 
- **Tags**: 

### 

：

1. 
2. 
3. 
4. 

### 

：

1. 
2. 
3. 

## 

 `config.cfg` ：

- `llm_backend`: LLM (openai  ollama)
- `llm_model`: LLM
- `model_name`: 
- `api_key`: LLMAPI
- `evo_threshold`: 

## 

1. ChromaDB，
2. API（OpenAI）
3. ChromaDB
4. ChromaDB

## 

### ChromaDB

ChromaDB，ChromaDB：

```bash
rm -rf chroma_db/
python api.py
```

### API

 `config.cfg` API，

### 

，：

```bash
pip install --upgrade pip
pip install -r requirements.txt --no-cache-dir
```

## 

### 

```bash
pytest tests/
```

### 

 `api.py` ：

```python
logging.basicConfig(level=logging.DEBUG)
```
