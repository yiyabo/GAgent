import { createApp } from 'vue'
import { createPinia } from 'pinia'
import ElementPlus from 'element-plus'
import 'element-plus/dist/index.css'
import App from './App.vue'
import router from './router'
import './style.css'

const app = createApp(App)
const pinia = createPinia()

// Inject the router into all Pinia stores
pinia.use(({ store }) => {
  store.router = router
})

app.use(pinia)
app.use(router)
app.use(ElementPlus)

app.mount('#app')
