# 

## 

- ****: `/Users/apple/LLM/agent/.claude/worktrees/flamboyant-chandrasekhar/`
- ****: AI  — Python FastAPI  + React/TypeScript 
- ****: ~97,000 
- ****: ， 300-800 

## 

### 

```
57 passed, 6 failed (6 ，)
```

：`python -m pytest app/tests/ -q --tb=no`

### 

 `app/routers/chat/` ， `chat_routes.py`（ 8,676 ） 6 ：

|  |  |  |
|------|------|------|
| `chat/models.py` | 354 | Pydantic （ChatMessage, ChatRequest  16 ） |
| `chat/session_helpers.py` | 942 | （34 ：DB PhageScope） |
| `chat/tool_results.py` | 575 | （sanitizesummarizetruncatedrop_callables  6 ） |
| `chat/background.py` | 63 |  |
| `chat/confirmation.py` | 71 | （confirmation ID ） |
| `chat/__init__.py` | 95 | ，re-export  |

****：`chat_routes.py`  `from .chat.xxx import ...` ， `from app.routers.chat_routes import ChatMessage` 

###  chat_routes.py （6,937 ）

```
 1-190:     import  re-export（~190 ）
 191-601:    handlers — session CRUDconfirm（~410 ）
 602-737:    _execute_confirmed_actions （~135 ）
 738-1130:  chat_message  handler（~392 ）
 1131-1607: chat_stream  handler（~477 ）
 1608-2538:  _generate_tool_analysis  + _execute_action_run（~930 ）
 2539-2851: action status  + _build_action_status_payloads（~312 ）
 2852-6937: StructuredChatAgent （~4,085 ，53 ）
```

### 

|  |  |
|------|------|
| `web-ui/src/components/chat/ChatMessage.tsx` | 1,103 |
| `web-ui/src/components/dag/DAG3DView.tsx` | 1,067 |
| `web-ui/src/components/chat/JobLogPanel.tsx` | 1,043 |
| `web-ui/src/store/slices/createMessageSlice.ts` | 884 |
| `web-ui/src/components/tasks/TaskDetailDrawer.tsx` | 828 |
| `web-ui/src/types/index.ts` | 783 |

### 

|  |  |
|------|------|
| `tool_box/integration.py` | 422（ register_tool ） |

---

## 

1. ****： `__init__.py` re-export  import 
2. ****： `python -m pytest app/tests/ -q --tb=short`， 57 pass / 6 fail 
3. ****： `from ...database import get_db` 
4. ****： `@staticmethod`  `self` ， `attr = staticmethod(fn)` 
5. **SOLID **：，

---

##  2：StructuredChatAgent 

****： StructuredChatAgent （guardrail）

###  2.1： `app/routers/chat/guardrails.py`

：

```python
#  @staticmethod  self 
#  chat_routes.py StructuredChatAgent 

_extract_task_id_from_text      # line 3205, @staticmethod
_is_status_query_only           # line 3224, @staticmethod
_reply_promises_execution       # line 3258, @staticmethod
_looks_like_completion_claim    # line 3349, @staticmethod
_extract_declared_absolute_paths # line 3367, @staticmethod
_is_task_executable_status      # line 3403, @staticmethod
_is_generic_plan_confirmation   # line 3473, @staticmethod
_explicit_manuscript_request    # line 2990, @staticmethod
```

****：
1.  chat_routes.py （）
2.  `app/routers/chat/guardrails.py` （ `self` ）
3.  chat_routes.py ，：
   ```python
   _extract_task_id_from_text = staticmethod(extract_task_id_from_text)
   ```
4.  chat_routes.py  import 
5.  `chat/__init__.py`  re-export
6. 

###  2.2： `app/routers/chat/guardrail_handlers.py`

 `self`  `self.plan_session``self.extra_context` ， `self` ：

```python
#  agent 
_apply_phagescope_fallback                    # line 3007
_apply_task_execution_followthrough_guardrail  # line 3289
_resolve_followthrough_target_task_id          # line 3303
_apply_completion_claim_guardrail              # line 3392
_first_executable_atomic_descendant            # line 3407
_match_atomic_task_by_keywords                 # line 3426
_infer_plan_seed_message                       # line 3503
_apply_plan_first_guardrail                    # line 3522
_should_force_plan_first                       # line 3533
```

****： `get_structured_response`  `execute_structured`  `self` ：

```python
# guardrail_handlers.py 
def apply_completion_claim_guardrail(structured, plan_session, extra_context, ...):
    ...

# chat_routes.py 
def _apply_completion_claim_guardrail(self, structured):
    return apply_completion_claim_guardrail(
        structured, self.plan_session, self.extra_context, ...
    )
```

****： agent ，：

```python
# guardrail_handlers.py
def apply_completion_claim_guardrail(agent, structured):
    #  agent.plan_session 
    ...

# chat_routes.py 
def _apply_completion_claim_guardrail(self, structured):
    return apply_completion_claim_guardrail(self, structured)
```

， agent ，

****：
1. 
2.  `guardrail_handlers.py`， `agent` 
3. ：`def _xxx(self, ...): return xxx(self, ...)`
4.  import  re-export
5. 

---

##  3：StructuredChatAgent 

****： `_handle_*_action` 

###  3.1： `app/routers/chat/action_handlers.py`

：

```python
_handle_tool_action       # line 4953, async — （~1200 ）
_handle_plan_action       # line 6150, async — （~220 ）
_handle_task_action       # line 6372, async — （~380 ）
_handle_context_request   # line 6751, async — （~30 ）
_handle_system_action     # line 6781, async — （~10 ）
_handle_unknown_action    # line 6790, async — （~5 ）
```

** 2.2 **： `agent` 

```python
# action_handlers.py
async def handle_tool_action(agent, action: LLMAction) -> AgentStep:
    ...

# chat_routes.py 
async def _handle_tool_action(self, action):
    return await handle_tool_action(self, action)
```

###  3.2： `app/routers/chat/plan_helpers.py`

：

```python
_require_plan_bound       # line 6813
_refresh_plan_tree        # line 6821
_auto_decompose_plan      # line 6843（async）
_persist_if_dirty         # line 6920
_coerce_int               # line 6835, @staticmethod
_build_suggestions        # line 6794
```

###  3.3： `app/routers/chat/prompt_builder.py`

：

```python
_build_prompt             # line 4623
_format_memories          # line 4669
_compose_plan_status      # line 4685
_compose_plan_catalog     # line 4696
_compose_action_catalog   # line 4706
_compose_guidelines       # line 4720
_get_structured_agent_prompts  # line 4731, @staticmethod
_format_history           # line 4836
_strip_code_fence         # line 4846, @staticmethod
```

****：
1. ， 3 
2. 
3.  import  `__init__.py`
4. 

---

##  4：

****： `chat_routes.py`  handler 

###  4.1： `app/routers/chat/routes.py`

 `@router.*`  handler（ stream）：

```python
#  @router.xxx  async 
list_chat_sessions            # line 192, GET /sessions
update_chat_session           # line 264, PATCH /sessions/{id}
autotitle_chat_session        # line 388, POST
bulk_autotitle_chat_sessions  # line 419, POST
head_chat_session             # line 452, HEAD
delete_chat_session           # line 472, DELETE /sessions/{id}
confirm_pending_action        # line 522, POST /confirm
get_pending_confirmation_status # line 584, GET /confirm/{id}
_execute_confirmed_actions    # line 602（， confirm ）
chat_status                   # line 642, GET /status
get_chat_history              # line 706, GET /history/{id}
chat_message                  # line 738, POST /message
```

****：`router = APIRouter(prefix="/chat", tags=["Chat"])`  routes.py  chat_routes.py 

###  4.2： `app/routers/chat/stream.py`

 SSE ：

```python
chat_stream                   # line 1131, POST /stream （~477 ）
```

###  4.3： `app/routers/chat/action_execution.py`

：

```python
_generate_tool_analysis       # line 1608
_generate_tool_summary        # line 1713
_collect_created_tasks_from_steps  # line 1747
_generate_action_analysis     # line 1762
_build_brief_action_summary   # line 1837
_execute_action_run           # line 1867
get_action_status             # line 2540, GET /actions/{id}
retry_action_run              # line 2638, POST /actions/{id}/retry
_build_action_status_payloads # line 2754
```

###  4.4： `app/routers/chat/claude_code_helpers.py`

 Claude Code ：

```python
_resolve_claude_code_task_context      # line 3623
_normalize_csv_arg                     # line 3664, @staticmethod
_summarize_amem_experiences_for_cc     # line 3701, @staticmethod
_compose_claude_code_atomic_task_prompt # line 3747, @staticmethod
_resolve_previous_path                 # line 3799
_resolve_placeholders_in_value         # line 3827
_resolve_action_placeholders           # line 3851
```

###  4.5：

 `app/routers/__init__.py`  auto-load ：
-  `app.routers.chat_routes`
-  `app.routers.chat.routes`（ chat_routes.py  router ）

****： `chat_routes.py` （< 50 ），：
```python
from .chat.routes import router  # noqa: F401
```

###  4 

```
app/routers/chat/
├── __init__.py              # re-export 
├── models.py                # Pydantic  (~354 )
├── session_helpers.py       #  (~942 )
├── tool_results.py          #  (~575 )
├── background.py            #  (~63 )
├── confirmation.py          #  (~71 )
├── guardrails.py            #  (~300 ) []
├── guardrail_handlers.py    #  (~500 ) []
├── action_handlers.py       #  (~1,800 ) []
├── plan_helpers.py          #  (~200 ) []
├── prompt_builder.py        #  (~300 ) []
├── claude_code_helpers.py   # Claude Code  (~300 ) []
├── routes.py                #  handlers (~600 ) []
├── stream.py                # SSE  (~500 ) []
└── action_execution.py      #  (~1,000 ) []

app/routers/chat_routes.py   #  + StructuredChatAgent  (~800 )
```

**chat_routes.py **：
- StructuredChatAgent （`__init__``handle``get_structured_response``execute_structured``_invoke_llm``_execute_action` ）
- /
-  ~800-1200 

---

##  5：tool_box/integration.py 

****： `register_tool()` 

****：`tool_box/integration.py`（422 ），：
```python
register_tool("tool_name", handler_fn, description="...", parameters={...})
```

###  5.1：

 `tool_box/tool_registry.py`：

```python
# tool_registry.py
TOOL_DEFINITIONS = [
    {
        "name": "web_search",
        "handler": "tool_box.tools.web_search:handle",
        "description": "Search the web...",
        "parameters": {...}
    },
    # ... 
]

def register_all_tools():
    for defn in TOOL_DEFINITIONS:
        handler = _resolve_handler(defn["handler"])
        register_tool(defn["name"], handler, ...)
```

###  5.2： integration.py

 `integration.py` ：
```python
from .tool_registry import register_all_tools
register_all_tools()
```

****： `python -c "from tool_box import execute_tool; print('OK')"`

---

##  6： types/index.ts 

****： `web-ui/src/types/index.ts`（783 ）

###  6.1：

 `types/index.ts`，：
- `types/chat.ts` — 
- `types/task.ts` — 
- `types/tool.ts` — 
- `types/dag.ts` — DAG 
- `types/settings.ts` — 
- `types/common.ts` — （）

###  6.2：

 100-200 

###  6.3： index.ts  re-export hub

```typescript
export * from './chat';
export * from './task';
export * from './tool';
// ...
```

****： `cd web-ui && npx tsc --noEmit`（ tsc ） import 

---

##  7：ChatMessage.tsx 

****： `web-ui/src/components/chat/ChatMessage.tsx`（1,103 ） ~10 

###  7.1：

，：
- 
- 
-  UI 

###  7.2：

```
web-ui/src/components/chat/message/
├── index.tsx                #  ChatMessage （，< 200 ）
├── MessageBubble.tsx        # 
├── MessageContent.tsx       # /Markdown 
├── ToolCallCard.tsx         # 
├── ActionStepList.tsx       # 
├── CodeBlock.tsx            # 
├── ImagePreview.tsx         # 
├── FileAttachment.tsx       # 
├── ThinkingIndicator.tsx    # 
├── MessageActions.tsx       # （）
└── hooks/
    └── useMessageState.ts   #  hook
```

###  7.3：

 `import { ChatMessage } from '../ChatMessage'`  `./message` ， re-export：
```typescript
// ChatMessage.tsx（）
export { default as ChatMessage } from './message';
```

****：`cd web-ui && npm run build`（ `npx tsc --noEmit`）

---

##  8：DAG3DView.tsx 

****： `web-ui/src/components/dag/DAG3DView.tsx`（1,067 ）

### 

- 3D （Three.js / React Three Fiber）
- /
- 
- 

### 

```
web-ui/src/components/dag/
├── DAG3DView.tsx            # （< 300 ）
├── DAGNode.tsx              # 
├── DAGEdge.tsx              # 
├── DAGControls.tsx          # 
├── hooks/
│   ├── useDAGLayout.ts      #  hook
│   └── useDAGInteraction.ts #  hook
└── utils/
    └── dagHelpers.ts        # 
```

---

##  9：JobLogPanel.tsx 

****： `web-ui/src/components/chat/JobLogPanel.tsx`（1,043 ）

### 

```
web-ui/src/components/chat/job-log/
├── index.tsx                # （< 300 ）
├── LogEntry.tsx             # 
├── LogFilter.tsx            # 
├── LogTimeline.tsx          # 
├── LogDetail.tsx            # 
└── hooks/
    └── useLogStream.ts      #  hook
```

---

##  10：createMessageSlice.ts 

****： `web-ui/src/store/slices/createMessageSlice.ts`（884 ）

### 

```
web-ui/src/store/slices/message/
├── index.ts                 #  slice （< 200 ）
├── actions.ts               # action creators（）
├── selectors.ts             # 
├── helpers.ts               # 
└── types.ts                 # slice 
```

---

##  11：TaskDetailDrawer.tsx 

****： `web-ui/src/components/tasks/TaskDetailDrawer.tsx`（828 ）

### 

```
web-ui/src/components/tasks/detail/
├── index.tsx                #  Drawer （< 200 ）
├── TaskHeader.tsx           # 
├── TaskProgress.tsx         # 
├── TaskActions.tsx          # 
├── TaskLogs.tsx             # 
└── SubtaskList.tsx          # 
```

---

## 

### 

：

****：
```bash
python -m pytest app/tests/ -q --tb=short
# ：57 passed, 6 failed（）
```

****：
```bash
cd web-ui && npx tsc --noEmit
#  npm run build
```

### 

1. **/**，
2. ****
3. **import **：`chat/`  `from ...database import get_db`（，）
4. **re-export **： `__init__.py`  re-export
5. ****： `self` ，，
6. **chat_routes.py  router **， `app/routers/__init__.py`  `importlib.import_module("app.routers.chat_routes")` 
7. ****： 2 → 3 → 4 → 5 → 6 → 7 → 8 → 9 → 10 → 11 

### StructuredChatAgent 

（ 6,937  chat_routes.py）：

```
                                                      
------  --------------------------------------------  -----------   -------------------
2858    __init__                                            
2947    handle                                        async         
2956    get_structured_response                       async         
2965    _apply_experiment_fallback                    async         guardrail_handlers
2990    _explicit_manuscript_request                  @staticmethod guardrails
3007    _apply_phagescope_fallback                          guardrail_handlers
3205    _extract_task_id_from_text                    @staticmethod guardrails
3224    _is_status_query_only                         @staticmethod guardrails
3258    _reply_promises_execution                     @staticmethod guardrails
3289    _apply_task_execution_followthrough_guardrail        guardrail_handlers
3303    _resolve_followthrough_target_task_id                guardrail_handlers
3349    _looks_like_completion_claim                  @staticmethod guardrails
3367    _extract_declared_absolute_paths              @staticmethod guardrails
3392    _apply_completion_claim_guardrail                    guardrail_handlers
3403    _is_task_executable_status                    @staticmethod guardrails
3407    _first_executable_atomic_descendant                  guardrail_handlers
3426    _match_atomic_task_by_keywords                       guardrail_handlers
3473    _is_generic_plan_confirmation                 @staticmethod guardrails
3503    _infer_plan_seed_message                             guardrail_handlers
3522    _apply_plan_first_guardrail                          guardrail_handlers
3533    _should_force_plan_first                             guardrail_handlers
3623    _resolve_claude_code_task_context                    claude_code_helpers
3664    _normalize_csv_arg                            @staticmethod claude_code_helpers
3701    _summarize_amem_experiences_for_cc            @staticmethod claude_code_helpers
3747    _compose_claude_code_atomic_task_prompt       @staticmethod claude_code_helpers
3799    _resolve_previous_path                               claude_code_helpers
3827    _resolve_placeholders_in_value                       claude_code_helpers
3851    _resolve_action_placeholders                         claude_code_helpers
3864    execute_structured                            async         
4075    _maybe_synthesize_phagescope_saveall_analysis  async         action_handlers
4244    _should_use_deep_think                               prompt_builder
4277    process_deep_think_stream                     async         
4613    _invoke_llm                                   async         
4623    _build_prompt                                        prompt_builder
4669    _format_memories                                     prompt_builder
4685    _compose_plan_status                                 prompt_builder
4696    _compose_plan_catalog                                prompt_builder
4706    _compose_action_catalog                              prompt_builder
4720    _compose_guidelines                                  prompt_builder
4731    _get_structured_agent_prompts                 @staticmethod prompt_builder
4738    _extract_tool_name                            @staticmethod （）
4744    _resolve_job_meta                                    action_execution
4776    _log_action_event                             @staticmethod action_execution
4811    _truncate_summary_text                        @staticmethod action_execution
4816    _build_actions_summary                               action_execution
4829    _append_summary_to_reply                             action_execution
4836    _format_history                                      prompt_builder
4846    _strip_code_fence                             @staticmethod prompt_builder
4865    _execute_action                               async         
4953    _handle_tool_action                           async         action_handlers
6150    _handle_plan_action                           async         action_handlers
6372    _handle_task_action                           async         action_handlers
6751    _handle_context_request                       async         action_handlers
6781    _handle_system_action                         async         action_handlers
6790    _handle_unknown_action                        async         action_handlers
6794    _build_suggestions                                   plan_helpers
6813    _require_plan_bound                                  plan_helpers
6821    _refresh_plan_tree                                   plan_helpers
6835    _coerce_int                                   @staticmethod plan_helpers
6843    _auto_decompose_plan                          async         plan_helpers
6920    _persist_if_dirty                                    plan_helpers
6929-37 staticmethod （ tool_results）            
```

###  StructuredChatAgent 

 Agent ，：

```python
__init__                    # 
handle                      # 
get_structured_response     #  LLM （）
execute_structured          # （）
process_deep_think_stream   # 
_invoke_llm                 # LLM 
_execute_action             # 
_extract_tool_name          # （1）
```

，StructuredChatAgent  ~800-1000 
