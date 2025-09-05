import { createRouter, createWebHistory } from 'vue-router'
import HomeView from '../views/HomeView.vue'
import PlansView from '../views/PlansView.vue'
import GeneratePlanView from '../views/GeneratePlanView.vue'
import PlanDetailView from '../views/PlanDetailView.vue'
import PlanExecutionView from '../views/PlanExecutionView.vue'
import TaskDetailView from '../views/TaskDetailView.vue'
import ChatView from '../views/ChatView.vue' // Import ChatView
import TestView from '../views/TestView.vue' // Import TestView

const router = createRouter({
  history: createWebHistory(),
  routes: [
    {
      path: '/',
      name: 'Home',
      component: HomeView
    },
    {
      path: '/plans',
      name: 'Plans',
      component: PlansView
    },
    {
      path: '/plans/new',
      name: 'GeneratePlan',
      component: GeneratePlanView
    },
    {
      path: '/plan/:id/edit',
      name: 'PlanEdit',
      component: PlanDetailView,
      props: true
    },
    {
      path: '/plan/:id/execute',
      name: 'PlanExecute',
      component: PlanExecutionView,
      props: true
    },
    {
      path: '/plan/:title',
      redirect: { name: 'PlanEdit' }
    },
    {
      path: '/task/:id',
      name: 'TaskDetail',
      component: TaskDetailView,
      props: true
    },
    {
      path: '/plan/:id/chat',
      name: 'Chat',
      component: ChatView,
      props: true
    },
    {
      path: '/chat',
      name: 'ChatMain',
      component: ChatView
    },
    {
      path: '/test',
      name: 'Test',
      component: TestView
    },
    {
      path: '/:pathMatch(.*)*',
      redirect: '/'
    }
  ]
})

export default router