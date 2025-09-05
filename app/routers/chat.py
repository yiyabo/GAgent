from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from typing import List, AsyncGenerator
import json
import asyncio

from .. import models
from ..repository import chat as chat_repo
from ..services.conversational_agent import ConversationalAgent

router = APIRouter(
    prefix="/chat",
    tags=["Chat"],
)

@router.post("/plans/{plan_id}/conversations", response_model=models.Conversation)
def create_new_conversation(plan_id: int, conversation_in: models.ConversationCreate):
    """Create a new conversation for a plan."""
    conversation = chat_repo.create_conversation(plan_id=plan_id, title=conversation_in.title)
    if not conversation:
        raise HTTPException(status_code=500, detail="Failed to create conversation.")
    return conversation

@router.get("/plans/{plan_id}/conversations", response_model=List[models.Conversation])
def get_plan_conversations(plan_id: int):
    """Get all conversations for a specific plan."""
    return chat_repo.get_conversations_for_plan(plan_id=plan_id)

@router.get("/conversations/{conversation_id}", response_model=models.ConversationWithMessages)
def get_conversation_details(conversation_id: int):
    """Get a single conversation with all its messages."""
    conversation = chat_repo.get_conversation(conversation_id=conversation_id)
    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found.")
    
    messages = chat_repo.get_messages_for_conversation(conversation_id=conversation_id)
    return {**conversation, "messages": messages}

@router.post("/conversations/{conversation_id}/messages")
def post_message(conversation_id: int, message_in: models.MessageCreate):
    """增强版消息处理，返回消息和可视化指令"""
    # 1. Save the user's message
    user_message = chat_repo.create_message(
        conversation_id=conversation_id, 
        sender=message_in.sender, 
        text=message_in.text
    )
    if not user_message:
        raise HTTPException(status_code=500, detail="Failed to save user message.")

    # 2. Get the plan_id from conversation
    conversation = chat_repo.get_conversation(conversation_id)
    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found.")
    
    # 3. Use enhanced ConversationalAgent to process command
    try:
        agent = ConversationalAgent(plan_id=conversation.get("plan_id"))
        result = agent.process_command(message_in.text)
        
        # 4. Save agent's response
        agent_message = chat_repo.create_message(
            conversation_id=conversation_id,
            sender='agent',
            text=result["response"]
        )
        if not agent_message:
            raise HTTPException(status_code=500, detail="Failed to save agent response.")
        
        # 5. Check if we need to execute tasks
        if result.get("action_result", {}).get("should_execute"):
            # Trigger task execution if needed
            if result["intent"] == "execute_plan":
                plan_id = result["action_result"].get("plan_id")
                if plan_id:
                    # 使用后端的 /run 接口来执行任务
                    import threading
                    import requests
                    
                    def execute_plan_async():
                        try:
                            # 调用本地的 /run 接口
                            response = requests.post(
                                "http://127.0.0.1:8000/run",
                                json={
                                    "plan_id": int(plan_id),
                                    "use_context": True,
                                    "schedule": "postorder",
                                    "rerun_all": False
                                }
                            )
                            if response.status_code == 200:
                                print(f"Successfully started execution for plan {plan_id}")
                            else:
                                print(f"Failed to execute plan {plan_id}: {response.text}")
                        except Exception as e:
                            print(f"Error executing plan {plan_id}: {e}")
                    
                    # 在后台线程中执行
                    thread = threading.Thread(target=execute_plan_async, daemon=True)
                    thread.start()
            
            elif result["intent"] == "rerun_task":
                task_id = result["action_result"].get("task", {}).get("id")
                if task_id:
                    # 重新执行任务
                    import threading
                    import requests
                    
                    def rerun_task_async():
                        try:
                            response = requests.post(
                                f"http://127.0.0.1:8000/tasks/{task_id}/rerun",
                                json={
                                    "use_context": True
                                }
                            )
                            if response.status_code == 200:
                                print(f"Successfully rerun task {task_id}")
                            else:
                                print(f"Failed to rerun task {task_id}: {response.text}")
                        except Exception as e:
                            print(f"Error rerunning task {task_id}: {e}")
                    
                    thread = threading.Thread(target=rerun_task_async, daemon=True)
                    thread.start()
        
        # 6. Return complete response with visualization
        return {
            "message": agent_message,
            "visualization": result.get("visualization", {
                "type": "none",
                "data": {},
                "config": {}
            }),
            "intent": result.get("intent", "unknown"),
            "success": result.get("success", True)
        }
        
    except Exception as e:
        # Error handling with visualization
        import traceback
        print(f"Error in post_message: {e}")
        print(traceback.format_exc())
        
        error_message = f"抱歉，处理您的请求时出现错误：{str(e)}"
        agent_message = chat_repo.create_message(
            conversation_id=conversation_id,
            sender='agent',
            text=error_message
        )
        
        return {
            "message": agent_message if agent_message else {"text": error_message, "sender": "agent"},
            "visualization": {
                "type": "none",
                "data": {},
                "config": {}
            },
            "intent": "unknown",
            "success": False
        }

@router.post("/conversations/{conversation_id}/messages/stream")
async def post_message_stream(conversation_id: int, message_in: models.MessageCreate):
    """Post a new message and stream the agent's response using SSE."""
    
    # 1. Save the user's message
    user_message = chat_repo.create_message(
        conversation_id=conversation_id,
        sender=message_in.sender,
        text=message_in.text
    )
    if not user_message:
        raise HTTPException(status_code=500, detail="Failed to save user message.")
    
    # 2. Get the plan_id from conversation
    conversation = chat_repo.get_conversation(conversation_id)
    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found.")
    
    async def event_generator() -> AsyncGenerator[str, None]:
        try:
            # Initialize agent
            agent = ConversationalAgent(plan_id=conversation["plan_id"])
            
            # Send start event
            start_data = {
                "type": "start",
                "message": "Processing your request..."
            }
            yield f"data: {json.dumps(start_data)}\n\n"
            
            # Quick intent check to decide streaming strategy
            user_text = message_in.text.lower()
            is_simple_chat = not any(keyword in user_text for keyword in [
                "create", "plan", "execute", "run", "generate plan", "新建", "创建", "执行"
            ])
            
            if is_simple_chat:
                # For simple chat, use streaming LLM
                accumulated_text = ""
                try:
                    for chunk in agent.llm.chat_stream(message_in.text):
                        accumulated_text += chunk
                        
                        # Send chunk as SSE event
                        chunk_data = {
                            "type": "chunk",
                            "content": chunk,
                            "accumulated": accumulated_text.strip()
                        }
                        yield f"data: {json.dumps(chunk_data)}\n\n"
                        
                        # Small delay to prevent overwhelming
                        await asyncio.sleep(0.01)
                        
                except Exception as e:
                    # Fallback to regular chat if streaming fails
                    accumulated_text = agent.llm.chat(message_in.text)
                    
                    # Send as one chunk
                    chunk_data = {
                        "type": "chunk",
                        "content": accumulated_text,
                        "accumulated": accumulated_text
                    }
                    yield f"data: {json.dumps(chunk_data)}\n\n"
                
                # Save message
                agent_message = chat_repo.create_message(
                    conversation_id=conversation_id,
                    sender='agent',
                    text=accumulated_text.strip()
                )
                
                # Send completion for simple chat
                completion_data = {
                    "type": "complete",
                    "message_id": agent_message["id"] if agent_message else None,
                    "full_text": accumulated_text.strip(),
                    "visualization": {
                        "type": "none",
                        "data": {},
                        "config": {}
                    },
                    "intent": "chat",
                    "success": True,
                    "action_result": {}
                }
                yield f"data: {json.dumps(completion_data)}\n\n"
                
            else:
                # For complex commands (create plan, etc), use original logic with progress updates
                progress_data = {
                    "type": "progress",
                    "message": "Analyzing command and generating plan..."
                }
                yield f"data: {json.dumps(progress_data)}\n\n"
                
                # Process command using the full agent
                result = agent.process_command(message_in.text)
                
                # Send progress update
                progress_data = {
                    "type": "progress", 
                    "message": "Plan generated, preparing response..."
                }
                yield f"data: {json.dumps(progress_data)}\n\n"
                
                # Send the complete response with visualization
                response_text = result.get("response", "")
                
                # For complex commands, send response in larger chunks
                words = response_text.split()
                accumulated_text = ""
                chunk_size = 3  # Send 3 words at a time
                
                for i in range(0, len(words), chunk_size):
                    word_chunk = " ".join(words[i:i+chunk_size]) + " "
                    accumulated_text += word_chunk
                    
                    # Send chunk as SSE event
                    chunk_data = {
                        "type": "chunk",
                        "content": word_chunk,
                        "accumulated": accumulated_text.strip()
                    }
                    yield f"data: {json.dumps(chunk_data)}\n\n"
                    
                    # Faster for complex responses
                    await asyncio.sleep(0.01)
                
                # Save complete message to database
                agent_message = chat_repo.create_message(
                    conversation_id=conversation_id,
                    sender='agent',
                    text=accumulated_text.strip()
                )
                
                # Send completion event with visualization data
                completion_data = {
                    "type": "complete",
                    "message_id": agent_message["id"] if agent_message else None,
                    "full_text": accumulated_text.strip(),
                    "visualization": result.get("visualization", {
                        "type": "none",
                        "data": {},
                        "config": {}
                    }),
                    "intent": result.get("intent", "unknown"),
                    "success": result.get("success", True),
                    "action_result": result.get("action_result", {})
                }
                yield f"data: {json.dumps(completion_data)}\n\n"
            
        except Exception as e:
            # Send error event
            error_data = {
                "type": "error",
                "message": f"Error generating response: {str(e)}"
            }
            yield f"data: {json.dumps(error_data)}\n\n"
    
    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # Disable proxy buffering
        }
    )
