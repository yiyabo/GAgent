class TaskNode:
    def __init__(self, id, name):
        self.id = id
        self.name = name
        self.dependencies = []


class TaskGraph:
    def __init__(self):
        self.nodes = {}

    def add_task(self, id, name):
        self.nodes[id] = TaskNode(id, name)

    def add_dependency(self, task_id, dep_id):
        self.nodes[task_id].dependencies.append(dep_id)
