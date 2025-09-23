# 基本数据结构

## 目录
1. [数组 (Array)](#数组-array)
2. [链表 (Linked List)](#链表-linked-list)
3. [树 (Tree)](#树-tree)
4. [图 (Graph)](#图-graph)

---

## 数组 (Array)

### 定义
数组是一种线性数据结构，在内存中连续存储相同类型的数据元素。

### 特点
- **连续内存分配**：元素在内存中连续存储
- **固定大小**：大多数编程语言中数组大小固定
- **随机访问**：可以通过索引直接访问任意元素（O(1)时间复杂度）
- **插入删除困难**：在中间位置插入或删除元素需要移动其他元素

### 常见操作
```cpp
// C++ 数组示例
#include <iostream>
using namespace std;

int main() {
    // 声明和初始化
    int arr[5] = {1, 2, 3, 4, 5};
    
    // 访问元素
    cout << "第一个元素: " << arr[0] << endl;
    cout << "第三个元素: " << arr[2] << endl;
    
    // 修改元素
    arr[1] = 10;
    
    // 遍历数组
    for (int i = 0; i < 5; i++) {
        cout << arr[i] << " ";
    }
    cout << endl;
    
    return 0;
}
```

### 时间复杂度
- 访问：O(1)
- 搜索：O(n) - 线性搜索
- 插入：O(n) - 需要移动元素
- 删除：O(n) - 需要移动元素

---

## 链表 (Linked List)

### 定义
链表是一种线性数据结构，由一系列节点组成，每个节点包含数据和指向下一个节点的指针。

### 类型
1. **单链表**：每个节点只包含指向下一个节点的指针
2. **双链表**：每个节点包含指向前后两个节点的指针
3. **循环链表**：最后一个节点指向第一个节点，形成循环

### 特点
- **非连续内存**：节点在内存中可以分散存储
- **动态大小**：可以动态添加和删除节点
- **插入删除高效**：在已知位置插入或删除只需要O(1)时间
- **访问较慢**：访问需要从头开始遍历（O(n)时间复杂度）

### 单链表示例
```cpp
// C++ 单链表实现
#include <iostream>
using namespace std;

struct Node {
    int data;
    Node* next;
    
    Node(int val) : data(val), next(nullptr) {}
};

class LinkedList {
private:
    Node* head;
    
public:
    LinkedList() : head(nullptr) {}
    
    // 插入节点到头部
    void insertAtHead(int val) {
        Node* newNode = new Node(val);
        newNode->next = head;
        head = newNode;
    }
    
    // 插入节点到尾部
    void insertAtTail(int val) {
        Node* newNode = new Node(val);
        if (head == nullptr) {
            head = newNode;
            return;
        }
        
        Node* current = head;
        while (current->next != nullptr) {
            current = current->next;
        }
        current->next = newNode;
    }
    
    // 打印链表
    void printList() {
        Node* current = head;
        while (current != nullptr) {
            cout << current->data << " ";
            current = current->next;
        }
        cout << endl;
    }
    
    // 删除节点
    void deleteNode(int val) {
        if (head == nullptr) return;
        
        if (head->data == val) {
            Node* temp = head;
            head = head->next;
            delete temp;
            return;
        }
        
        Node* current = head;
        while (current->next != nullptr && current->next->data != val) {
            current = current->next;
        }
        
        if (current->next != nullptr) {
            Node* temp = current->next;
            current->next = current->next->next;
            delete temp;
        }
    }
};

int main() {
    LinkedList list;
    list.insertAtTail(1);
    list.insertAtTail(2);
    list.insertAtTail(3);
    list.insertAtHead(0);
    
    cout << "链表内容: ";
    list.printList(); // 输出: 0 1 2 3
    
    list.deleteNode(2);
    cout << "删除2后的链表: ";
    list.printList(); // 输出: 0 1 3
    
    return 0;
}
```

### 时间复杂度
- 访问：O(n)
- 搜索：O(n)
- 插入：O(1) - 如果已知位置
- 删除：O(1) - 如果已知位置

---

## 树 (Tree)

### 定义
树是一种非线性数据结构，由节点和边组成，具有层次结构。

### 基本概念
- **根节点**：树的最顶节点
- **叶子节点**：没有子节点的节点
- **父节点**：直接连接到某个节点的节点
- **子节点**：被父节点连接的节点
- **深度**：从根节点到该节点的边数
- **高度**：从该节点到最远叶子节点的边数

### 常见树类型
1. **二叉树**：每个节点最多有两个子节点
2. **二叉搜索树 (BST)**：左子树 < 根节点 < 右子树
3. **平衡树**：AVL树、红黑树等
4. **堆**：最大堆、最小堆

### 二叉搜索树示例
```cpp
// C++ 二叉搜索树实现
#include <iostream>
using namespace std;

struct TreeNode {
    int data;
    TreeNode* left;
    TreeNode* right;
    
    TreeNode(int val) : data(val), left(nullptr), right(nullptr) {}
};

class BST {
private:
    TreeNode* root;
    
    // 插入辅助函数
    TreeNode* insertHelper(TreeNode* node, int val) {
        if (node == nullptr) {
            return new TreeNode(val);
        }
        
        if (val < node->data) {
            node->left = insertHelper(node->left, val);
        } else if (val > node->data) {
            node->right = insertHelper(node->right, val);
        }
        
        return node;
    }
    
    // 搜索辅助函数
    bool searchHelper(TreeNode* node, int val) {
        if (node == nullptr) return false;
        if (node->data == val) return true;
        if (val < node->data) return searchHelper(node->left, val);
        return searchHelper(node->right, val);
    }
    
    // 中序遍历
    void inorderHelper(TreeNode* node) {
        if (node == nullptr) return;
        inorderHelper(node->left);
        cout << node->data << " ";
        inorderHelper(node->right);
    }
    
public:
    BST() : root(nullptr) {}
    
    void insert(int val) {
        root = insertHelper(root, val);
    }
    
    bool search(int val) {
        return searchHelper(root, val);
    }
    
    void inorder() {
        inorderHelper(root);
        cout << endl;
    }
};

int main() {
    BST tree;
    tree.insert(50);
    tree.insert(30);
    tree.insert(70);
    tree.insert(20);
    tree.insert(40);
    tree.insert(60);
    tree.insert(80);
    
    cout << "中序遍历结果: ";
    tree.inorder(); // 输出: 20 30 40 50 60 70 80
    
    cout << "搜索40: " << (tree.search(40) ? "找到" : "未找到") << endl;
    cout << "搜索90: " << (tree.search(90) ? "找到" : "未找到") << endl;
    
    return 0;
}
```

### 时间复杂度
- 搜索：平均O(log n)，最坏O(n)
- 插入：平均O(log n)，最坏O(n)
- 删除：平均O(log n)，最坏O(n)

---

## 图 (Graph)

### 定义
图是由顶点（节点）和边组成的非线性数据结构，用于表示多对多的关系。

### 基本概念
- **顶点 (Vertex)**：图中的节点
- **边 (Edge)**：连接两个顶点的线
- **有向图**：边有方向
- **无向图**：边无方向
- **加权图**：边有权重
- **度**：与顶点相连的边数

### 图的表示方法
1. **邻接矩阵**：二维数组表示
2. **邻接表**：链表数组表示

### 邻接表实现示例
```cpp
// C++ 图的邻接表实现
#include <iostream>
#include <vector>
#include <list>
using namespace std;

class Graph {
private:
    int V; // 顶点数
    vector<list<int>> adj; // 邻接表
    
public:
    Graph(int vertices) : V(vertices) {
        adj.resize(V);
    }
    
    // 添加边
    void addEdge(int v, int w) {
        adj[v].push_back(w);
        // 如果是无向图，取消下面这行的注释
        // adj[w].push_back(v);
    }
    
    // BFS遍历
    void BFS(int start) {
        vector<bool> visited(V, false);
        list<int> queue;
        
        visited[start] = true;
        queue.push_back(start);
        
        while (!queue.empty()) {
            int current = queue.front();
            cout << current << " ";
            queue.pop_front();
            
            for (int neighbor : adj[current]) {
                if (!visited[neighbor]) {
                    visited[neighbor] = true;
                    queue.push_back(neighbor);
                }
            }
        }
        cout << endl;
    }
    
    // DFS遍历
    void DFSUtil(int v, vector<bool>& visited) {
        visited[v] = true;
        cout << v << " ";
        
        for (int neighbor : adj[v]) {
            if (!visited[neighbor]) {
                DFSUtil(neighbor, visited);
            }
        }
    }
    
    void DFS(int start) {
        vector<bool> visited(V, false);
        DFSUtil(start, visited);
        cout << endl;
    }
    
    // 打印邻接表
    void printAdjList() {
        for (int i = 0; i < V; ++i) {
            cout << "顶点 " << i << ": ";
            for (int neighbor : adj[i]) {
                cout << neighbor << " ";
            }
            cout << endl;
        }
    }
};

int main() {
    // 创建一个有7个顶点的图
    Graph g(7);
    
    // 添加边（有向图）
    g.addEdge(0, 1);
    g.addEdge(0, 2);
    g.addEdge(1, 3);
    g.addEdge(1, 4);
    g.addEdge(2, 5);
    g.addEdge(2, 6);
    
    cout << "邻接表:" << endl;
    g.printAdjList();
    
    cout << "BFS从顶点0开始: ";
    g.BFS(0); // 输出: 0 1 2 3 4 5 6
    
    cout << "DFS从顶点0开始: ";
    g.DFS(0); // 输出: 0 1 3 4 2 5 6
    
    return 0;
}
```

### 时间复杂度
- 邻接表空间复杂度：O(V + E)
- 邻接矩阵空间复杂度：O(V²)
- BFS/DFS时间复杂度：O(V + E)

---

## 总结

| 数据结构 | 访问时间 | 搜索时间 | 插入时间 | 删除时间 | 空间复杂度 |
|---------|---------|---------|---------|---------|-----------|
| 数组    | O(1)    | O(n)    | O(n)    | O(n)    | O(n)      |
| 链表    | O(n)    | O(n)    | O(1)    | O(1)    | O(n)      |
| 树      | O(log n)| O(log n)| O(log n)| O(log n)| O(n)      |
| 图      | O(V+E)  | O(V+E)  | O(1)    | O(1)    | O(V+E)    |

### 选择建议
- **数组**：需要频繁随机访问，数据量相对固定
- **链表**：需要频繁插入删除，数据量动态变化
- **树**：需要有序存储，支持快速搜索
- **图**：表示复杂关系，如网络、社交关系等

每种数据结构都有其适用场景，选择合适的数据结构对程序性能至关重要。