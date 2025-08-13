from pydantic import BaseModel
from typing import List, Optional

class TaskCreate(BaseModel):
    name: str

class Task(BaseModel):
    id: int
    name: str
    status: str

class PlanTaskIn(BaseModel):
    name: str
    prompt: Optional[str] = None
    priority: Optional[int] = None


class PlanIn(BaseModel):
    title: str
    tasks: List[PlanTaskIn]


class PlanProposal(BaseModel):
    goal: str
    title: Optional[str] = None
    sections: Optional[int] = None
    style: Optional[str] = None
    notes: Optional[str] = None


class PlanApproval(BaseModel):
    title: str
    tasks: List[PlanTaskIn]