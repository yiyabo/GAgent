# Context-Aware LLM Task Orchestrator

*Read this in Chinese: [README_cn.md](./README_cn.md)*

A production-grade AI task orchestration system that transforms goals into executable plans with intelligent context awareness, dependency management, and budget controls.

## 🚀 Core Features

- **Smart Planning**: Auto-generate executable task plans from high-level goals
- **Context Intelligence**: Multi-source context assembly (dependencies, TF-IDF retrieval, global index)
- **Dependency Awareness**: DAG-based scheduling with cycle detection
- **Budget Management**: Token/character limits with intelligent content summarization
- **Reproducible Execution**: Context snapshots and deterministic ordering
- **Production Ready**: FastAPI backend, comprehensive testing, mock mode for development

## 📋 Quick Start

### Prerequisites
```bash
# Install dependencies
conda run -n LLM python -m pip install -r requirements.txt

# Set up environment
export GLM_API_KEY=your_key_here
# or use mock mode for development
# export LLM_MOCK=1
```

### Start the Server
```bash
conda run -n LLM python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

### Example Workflow
```bash
# 1. Propose a plan
curl -X POST http://127.0.0.1:8000/plans/propose \
  -H "Content-Type: application/json" \
  -d '{"goal": "Write a technical whitepaper on gene editing"}'

# 2. Approve the plan (edit if needed)
curl -X POST http://127.0.0.1:8000/plans/approve \
  -H "Content-Type: application/json" \
  --data-binary @plan.json

# 3. Execute with context awareness
curl -X POST http://127.0.0.1:8000/run \
  -H "Content-Type: application/json" \
  -d '{
    "title": "Gene Editing Whitepaper",
    "schedule": "dag",
    "use_context": true,
    "context_options": {
      "include_deps": true,
      "tfidf_k": 2,
      "max_chars": 1200,
      "save_snapshot": true
    }
  }'

# 4. Get final assembled output
curl http://127.0.0.1:8000/plans/Gene%20Editing%20Whitepaper/assembled
```

## 🧠 How It Works

### System Architecture
The system follows a **Plan → Review → Execute** workflow with intelligent context orchestration:

```
Goal Input → Plan Generation → Human Review → Plan Approval → Task Scheduling → Context Assembly → Budget Control → LLM Execution → Result Assembly
```

### Core Workflow

1. **Plan Generation** (`/plans/propose`)
   - LLM analyzes user goal and generates structured task breakdown
   - Returns JSON plan with tasks, priorities, and initial prompts
   - No data persistence - allows human review and editing

2. **Plan Approval** (`/plans/approve`)
   - Persists approved plan to database
   - Tasks are prefixed with plan title: `[Plan Title] Task Name`
   - Individual task prompts stored for context preservation

3. **Intelligent Scheduling**
   - **BFS Mode**: Priority-based execution `(priority ASC, id ASC)`
   - **DAG Mode**: Dependency-aware topological sorting with cycle detection
   - Supports both global execution and plan-specific execution

4. **Context Assembly** (`app/services/context.py`)
   - **Global Index**: Always includes `INDEX.md` as highest priority context
   - **Dependencies**: Gathers `requires` and `refers` linked tasks
   - **Plan Siblings**: Includes related tasks from the same plan
   - **TF-IDF Retrieval**: Semantic search across existing task outputs
   - **Manual Selection**: User-specified tasks

5. **Budget Management** (`app/services/context_budget.py`)
   - **Priority-based allocation**: `index > dep:requires > dep:refers > retrieved > sibling > manual`
   - **Multi-level limits**: Total character budget + per-section limits
   - **Smart summarization**: Sentence-boundary truncation or direct truncation
   - **Deterministic**: Same inputs produce identical results

6. **Execution & Storage**
   - LLM execution with retry logic and exponential backoff
   - Context snapshots for reproducibility
   - Structured output storage with metadata

### Data Model

```sql
-- Core task management
tasks (id, name, status, priority)
task_inputs (task_id, prompt)
task_outputs (task_id, content)

-- Dependency graph
task_links (from_id, to_id, kind)  -- kind: requires/refers

-- Context snapshots
task_contexts (task_id, label, combined, sections, meta, created_at)
```

### Scheduling Algorithms

**BFS Scheduling (Default)**
```python
def bfs_schedule():
    rows = default_repo.list_tasks_by_status('pending')
    # Stable ordering: (priority ASC, id ASC)
    rows_sorted = sorted(rows, key=lambda r: (r.get('priority') or 100, r.get('id')))
    yield from rows_sorted
```

**DAG Scheduling (Dependency-Aware)**
```python
def requires_dag_order(title=None):
    # 1. Build dependency graph from task_links where kind='requires'
    # 2. Topological sort using Kahn's algorithm
    # 3. Priority-based tie-breaking for same-level tasks
    # 4. Cycle detection with detailed diagnostics
```

### Context Intelligence

**Multi-Source Context Assembly**
```python
def gather_context(task_id, include_deps=True, include_plan=True, k=5, tfidf_k=None):
    sections = []
    
    # Global INDEX.md (highest priority)
    sections.append(index_section())
    
    # Dependencies (requires > refers)
    deps = repo.list_dependencies(task_id)
    sections.extend(dependency_sections(deps[:k]))
    
    # Plan siblings
    siblings = repo.list_plan_tasks(title)
    sections.extend(sibling_sections(siblings[:k]))
    
    # TF-IDF semantic retrieval
    if tfidf_k:
        retrieved = tfidf_search(query, k=tfidf_k)
        sections.extend(retrieved_sections(retrieved))
    
    return {"sections": sections, "combined": combine(sections)}
```

**TF-IDF Retrieval Algorithm**
- Document tokenization with Chinese/English support
- IDF calculation with smoothing: `log(1 + N/(1 + doc_freq))`
- TF normalization by document length
- Configurable score thresholds and candidate limits

## 🔧 API Reference

### Planning Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/plans/propose` | POST | Generate task plan from goal |
| `/plans/approve` | POST | Approve and persist plan |
| `/plans` | GET | List all existing plans |
| `/plans/{title}/tasks` | GET | Get tasks for specific plan |
| `/plans/{title}/assembled` | GET | Get assembled plan output |

### Execution Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/run` | POST | Execute tasks with full configuration |
| `/tasks` | POST | Create individual task |
| `/tasks/{id}/output` | GET | Get task output |

### Context Management

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/context/links` | POST/DELETE | Manage task dependencies |
| `/context/links/{task_id}` | GET | View task relationships |
| `/tasks/{task_id}/context/preview` | POST | Preview context assembly |
| `/tasks/{task_id}/context/snapshots` | GET | List context snapshots |

### Global Index

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/index` | GET | Get global INDEX.md |
| `/index` | PUT | Update global INDEX.md |

## ⚙️ Configuration

### Environment Variables

**LLM Configuration**
```bash
GLM_API_KEY=your_api_key                    # Required for production
GLM_API_URL=https://open.bigmodel.cn/...   # API endpoint
GLM_MODEL=glm-4-flash                       # Model name
LLM_MOCK=1                                  # Enable mock mode for development
LLM_RETRIES=3                               # Retry attempts
LLM_BACKOFF_BASE=0.5                       # Exponential backoff base (seconds)
```

**Context & Retrieval**
```bash
TFIDF_MAX_CANDIDATES=500                    # TF-IDF candidate pool size
TFIDF_MIN_SCORE=0.0                         # Minimum relevance score
GLOBAL_INDEX_PATH=/path/to/INDEX.md         # Global index file location
```

**Debugging**
```bash
CTX_DEBUG=1                                 # Enable context assembly debug logs
CONTEXT_DEBUG=1                             # Enable context service debug logs
BUDGET_DEBUG=1                              # Enable budget management debug logs
```

### Context Options

```json
{
  "context_options": {
    "include_deps": true,          // Include dependency tasks
    "include_plan": true,          // Include plan sibling tasks
    "k": 5,                        // Max items per category
    "manual": [1, 2, 3],           // Manual task IDs
    
    "tfidf_k": 2,                  // TF-IDF retrieval count
    "tfidf_min_score": 0.15,       // Minimum relevance score
    "tfidf_max_candidates": 200,   // Candidate pool size
    
    "max_chars": 1200,             // Total character budget
    "per_section_max": 300,        // Per-section character limit
    "strategy": "sentence",        // Summarization strategy
    
    "save_snapshot": true,         // Save context snapshot
    "label": "experiment-1"        // Snapshot label
  }
}
```

## 🛠️ CLI Usage

### Basic Execution
```bash
# Execute all pending tasks
conda run -n LLM python agent_cli.py

# Execute specific plan with context
conda run -n LLM python agent_cli.py --execute-only --title "My Plan" \
  --use-context --schedule dag

# Full configuration example
conda run -n LLM python agent_cli.py --execute-only --title "Research Project" \
  --schedule dag --use-context \
  --tfidf-k 2 --tfidf-min-score 0.15 --tfidf-max-candidates 200 \
  --max-chars 1200 --per-section-max 300 --strategy sentence \
  --save-snapshot --label experiment-1
```

### Context Snapshot Management
```bash
# List snapshots for a task
conda run -n LLM python agent_cli.py --list-snapshots --task-id 12

# Export snapshot to file
conda run -n LLM python agent_cli.py --export-snapshot \
  --task-id 12 --label experiment-1 --output snapshot.md
```

### Global Index Management
```bash
# Preview INDEX.md (no file write)
conda run -n LLM python agent_cli.py --index-preview

# Export to specific path
conda run -n LLM python agent_cli.py --index-export /path/to/INDEX.md

# Generate and persist with history
conda run -n LLM python agent_cli.py --index-run-root
```

## 🧪 Testing

### Run Test Suite
```bash
# Quick test run (uses mock LLM)
conda run -n LLM python -m pytest -q

# With coverage report
conda run -n LLM python -m pip install pytest-cov
conda run -n LLM python -m pytest --cov=app --cov-report=term-missing
```

### Mock Mode for Development
```bash
export LLM_MOCK=1
# Now all LLM calls return deterministic mock responses
```

## 🏗️ Architecture

### Modular Design
- **Interfaces** (`app/interfaces/`): Abstract base classes for LLM and Repository
- **Repository** (`app/repository/`): Data access layer with SQLite implementation
- **Services** (`app/services/`): Business logic (planning, context, budgeting)
- **Scheduler** (`app/scheduler.py`): Task ordering algorithms
- **Executor** (`app/executor.py`): Task execution with context assembly
- **Utils** (`app/utils.py`): Shared utilities (JSON parsing, prefix handling)

### SOLID Principles Implementation
- **Single Responsibility**: Each service has a focused purpose
- **Open/Closed**: Extensible through interface implementations
- **Liskov Substitution**: Mock and real implementations are interchangeable
- **Interface Segregation**: Focused interfaces (LLMProvider, TaskRepository)
- **Dependency Inversion**: Services depend on abstractions, not concretions

### Key Design Patterns
- **Repository Pattern**: Data access abstraction
- **Dependency Injection**: Testable service composition
- **Strategy Pattern**: Pluggable context sources and budget strategies
- **Template Method**: Consistent execution workflows

## 🚀 Deployment

### Production Considerations
- Set appropriate `GLM_API_KEY` and configure retry/backoff parameters
- Use `GLOBAL_INDEX_PATH` to specify persistent index location
- Configure context budgets based on your LLM token limits
- Enable structured logging for observability

### Docker Deployment (Optional)
```dockerfile
FROM python:3.11-slim
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY app/ app/
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

## 🤝 Contributing

1. Fork the repository
2. Create a feature branch
3. Add tests for new functionality
4. Ensure all tests pass with `pytest`
5. Submit a pull request

---

**Built with modern AI orchestration principles**: Intelligent context management, dependency-aware scheduling, and production-ready architecture for scalable LLM task automation.