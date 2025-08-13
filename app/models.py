from pydantic import BaseModel

class TaskCreate(BaseModel):
    name: str

class Task(BaseModel):
    id: int
    name: str
    status: str