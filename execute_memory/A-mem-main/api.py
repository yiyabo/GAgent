"""
FastAPI interface for Amem memory system
Provides endpoints for adding and querying memories
"""
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any
import uvicorn
import configparser
from datetime import datetime
import logging
from agentic_memory.memory_system import AgenticMemorySystem

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Initialize FastAPI app
app = FastAPI(
    title="Amem Memory System API",
    description="API for managing and querying memories using Amem",
    version="1.0.0"
)

# Global memory system instance
memory_system: Optional[AgenticMemorySystem] = None


# Pydantic models for request/response
class MemoryAddRequest(BaseModel):
    """Request model for adding a new memory"""
    content: str = Field(..., description="The content of the memory to store")
    timestamp: Optional[str] = Field(None, description="Timestamp in format YYYYMMDDHHMM")
    keywords: Optional[List[str]] = Field(None, description="Optional keywords for the memory")
    context: Optional[str] = Field(None, description="Optional context for the memory")
    tags: Optional[List[str]] = Field(None, description="Optional tags for categorization")

    class Config:
        schema_extra = {
            "example": {
                "content": "Today we discussed the architecture of microservices and how to handle distributed transactions",
                "timestamp": "202501110900",
                "keywords": ["microservices", "distributed transactions", "architecture"],
                "context": "Technical discussion about system design",
                "tags": ["software engineering", "architecture", "microservices"]
            }
        }


class MemoryAddResponse(BaseModel):
    """Response model for memory addition"""
    success: bool
    memory_id: str
    message: str


class MemoryQueryRequest(BaseModel):
    """Request model for querying memories"""
    query: str = Field(..., description="The query text to search for relevant memories")
    top_k: int = Field(5, ge=1, le=20, description="Number of top results to return (1-20)")

    class Config:
        schema_extra = {
            "example": {
                "query": "What did we discuss about microservices?",
                "top_k": 5
            }
        }


class MemoryItem(BaseModel):
    """Model for a single memory item"""
    id: str
    content: str
    context: str
    keywords: List[str]
    tags: List[str]
    timestamp: str
    score: Optional[float] = None


class MemoryQueryResponse(BaseModel):
    """Response model for memory queries"""
    success: bool
    query: str
    results: List[MemoryItem]
    count: int


@app.on_event("startup")
async def startup_event():
    """Initialize the memory system on startup"""
    global memory_system

    try:
        # Try to load config from config file or environment
        config = configparser.ConfigParser()
        
        # 尝试多个可能的配置文件路径
        possible_paths = [
            "config.cfg",
            "../config.cfg",
            "/home/jovyan/work/Mem/Mems/config.cfg"
        ]
        
        config_loaded = False
        for config_path in possible_paths:
            try:
                if config.read(config_path):
                    config_loaded = True
                    logger.info(f"Loaded config from: {config_path}")
                    break
            except Exception:
                continue

        # Default values - 优先使用环境变量
        import os
        llm_backend = os.getenv("AMEM_LLM_BACKEND", "openai")
        llm_model = os.getenv("AMEM_LLM_MODEL", "gpt-4o-mini")
        model_name = os.getenv("AMEM_EMBEDDING_MODEL", "all-MiniLM-L6-v2")
        api_key = os.getenv("AMEM_API_KEY") or os.getenv("QWEN_API_KEY") or os.getenv("GLM_API_KEY") or os.getenv("OPENAI_API_KEY")
        evo_threshold = int(os.getenv("AMEM_EVO_THRESHOLD", "100"))

        # 从配置文件覆盖（如果存在）
        if config_loaded and 'DEFAULT' in config:
            llm_backend = config['DEFAULT'].get('llm_backend', llm_backend)
            llm_model = config['DEFAULT'].get('llm_model', llm_model)
            model_name = config['DEFAULT'].get('model_name', model_name)
            config_api_key = config['DEFAULT'].get('api_key')
            if config_api_key and config_api_key != 'your-openai-api-key-here':
                api_key = config_api_key
            evo_threshold = int(config['DEFAULT'].get('evo_threshold', evo_threshold))
        
        if not api_key:
            logger.warning("No API key found. A-mem will work but LLM analysis will be limited.")

        # Initialize memory system
        memory_system = AgenticMemorySystem(
            model_name=model_name,
            llm_backend=llm_backend,
            llm_model=llm_model,
            evo_threshold=evo_threshold,
            api_key=api_key
        )

        logger.info("Memory system initialized successfully")
        logger.info(f"Using LLM: {llm_backend}/{llm_model}")
        logger.info(f"Using embedding model: {model_name}")

    except Exception as e:
        logger.error(f"Failed to initialize memory system: {e}")
        raise


@app.get("/")
async def root():
    """Root endpoint with API information"""
    return {
        "message": "Amem Memory System API",
        "version": "1.0.0",
        "endpoints": {
            "/add_memory": "POST - Add a new memory to the system",
            "/query_memory": "POST - Query memories by content",
            "/health": "GET - Check system health"
        }
    }


@app.get("/health")
async def health_check():
    """Health check endpoint"""
    if memory_system is None:
        raise HTTPException(status_code=503, detail="Memory system not initialized")

    return {
        "status": "healthy",
        "memory_count": len(memory_system.memories),
        "timestamp": datetime.now().isoformat()
    }


@app.post("/add_memory", response_model=MemoryAddResponse)
async def add_memory(request: MemoryAddRequest):
    """
    Add a new memory to the system

    The memory will be automatically analyzed to extract:
    - Keywords (if not provided)
    - Context (if not provided)
    - Tags (if not provided)

    The system will also check for related memories and potentially evolve connections.
    """
    if memory_system is None:
        raise HTTPException(status_code=503, detail="Memory system not initialized")

    try:
        # Prepare kwargs for optional parameters
        kwargs = {}
        if request.keywords:
            kwargs['keywords'] = request.keywords
        if request.context:
            kwargs['context'] = request.context
        if request.tags:
            kwargs['tags'] = request.tags

        # Add the memory
        memory_id = memory_system.add_note(
            content=request.content,
            time=request.timestamp,
            **kwargs
        )

        logger.info(f"Added memory with ID: {memory_id}")

        return MemoryAddResponse(
            success=True,
            memory_id=memory_id,
            message="Memory added successfully"
        )

    except Exception as e:
        logger.error(f"Error adding memory: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to add memory: {str(e)}")


@app.post("/query_memory", response_model=MemoryQueryResponse)
async def query_memory(request: MemoryQueryRequest):
    """
    Query memories using semantic search

    The system will:
    1. Search for semantically similar memories
    2. Include related memories through links
    3. Return ranked results with scores
    """
    if memory_system is None:
        raise HTTPException(status_code=503, detail="Memory system not initialized")

    try:
        # Search for memories
        results = memory_system.search_agentic(request.query, k=request.top_k)

        # Convert to response format
        memory_items = []
        for result in results:
            memory_items.append(MemoryItem(
                id=result.get('id', ''),
                content=result.get('content', ''),
                context=result.get('context', ''),
                keywords=result.get('keywords', []),
                tags=result.get('tags', []),
                timestamp=result.get('timestamp', ''),
                score=result.get('score')
            ))

        logger.info(f"Query '{request.query}' returned {len(memory_items)} results")

        return MemoryQueryResponse(
            success=True,
            query=request.query,
            results=memory_items,
            count=len(memory_items)
        )

    except Exception as e:
        logger.error(f"Error querying memory: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to query memory: {str(e)}")


if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="A-mem Memory System API")
    parser.add_argument("--port", type=int, default=8000, help="Port to run the server on")
    parser.add_argument("--host", type=str, default="0.0.0.0", help="Host to bind to")
    args = parser.parse_args()
    
    uvicorn.run(
        "api:app",
        host=args.host,
        port=args.port,
        reload=True,
        log_level="info"
    )
