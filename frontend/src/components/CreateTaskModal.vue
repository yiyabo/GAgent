<template>
  <div v-if="show" class="modal-overlay" @click="close">
    <div class="modal-content" @click.stop>
      <div class="modal-header">
        <h3>创建新任务</h3>
        <button @click="close" class="close-btn">&times;</button>
      </div>
      <div class="modal-body">
        <!-- Required Fields -->
        <div class="detail-item">
          <label class="detail-label">任务名称 (必填)</label>
          <input v-model="newTask.name" type="text" class="edit-input" placeholder="输入新任务的名称" />
        </div>
        <div class="detail-item">
          <label class="detail-label">父任务 (必填)</label>
          <select v-model="newTask.parentId" class="edit-input">
            <option :value="null">-- 无 (根任务) --</option>
            <option v-for="task in existingTasks" :key="task.id" :value="task.id">
              {{ task.shortName || task.name }} (ID: {{ task.id }})
            </option>
          </select>
        </div>
        <div class="detail-item">
          <label class="detail-label">任务类型 (必填)</label>
          <select v-model="newTask.taskType" class="edit-input">
            <option value="atomic">Atomic</option>
            <option value="composite">Composite</option>
          </select>
        </div>

        <!-- Optional Fields -->
        <div class="detail-item">
          <label class="detail-label">任务指令 (可选)</label>
          <textarea v-model="newTask.prompt" class="edit-textarea" placeholder="输入详细的任务指令或描述..." rows="4"></textarea>
        </div>

        <!-- Contexts Section -->
        <div class="detail-item">
          <label class="detail-label">附加上下文 (可选)</label>
          <div class="context-creator">
            <div v-for="(context, index) in newTask.contexts" :key="index" class="context-item">
              <span><strong>{{ context.label }}:</strong> {{ context.content.substring(0, 50) }}...</span>
              <button @click="removeContext(index)" class="btn-remove">×</button>
            </div>
            <div class="context-form">
              <input v-model="newContext.label" placeholder="Context Label" class="edit-input-small" />
              <textarea v-model="newContext.content" placeholder="Context Content" rows="3" class="edit-textarea-small"></textarea>
              <button @click="addContext" class="btn btn-sm">添加上下文</button>
            </div>
          </div>
        </div>

      </div>
      <div class="modal-footer">
        <button @click="close" class="btn btn-secondary">取消</button>
        <button @click="submit" class="btn btn-primary" :disabled="!isFormValid">确认创建</button>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, watch } from 'vue';
import { tasksApi } from '../services/api';

const props = defineProps({
  show: {
    type: Boolean,
    required: true,
  },
  planId: {
    type: Number,
    required: true,
  },
  existingTasks: {
    type: Array,
    default: () => [],
  },
});

const emit = defineEmits(['close', 'task-created']);

const newTask = ref({});
const newContext = ref({ label: '', content: '' });

const resetForm = () => {
  newTask.value = {
    name: '',
    parentId: null,
    taskType: 'atomic',
    prompt: '',
    contexts: [],
  };
  newContext.value = { label: '', content: '' };
};

watch(() => props.show, (newVal) => {
  if (newVal) {
    resetForm();
  }
});

const isFormValid = computed(() => {
  return newTask.value.name && newTask.value.name.trim() !== '';
});

const addContext = () => {
  if (newContext.value.label.trim() && newContext.value.content.trim()) {
    newTask.value.contexts.push({ ...newContext.value });
    newContext.value = { label: '', content: '' };
  }
};

const removeContext = (index) => {
  newTask.value.contexts.splice(index, 1);
};

const close = () => {
  emit('close');
};

const submit = async () => {
  if (!isFormValid.value) {
    alert('请输入任务名称');
    return;
  }
  try {
    const createdTask = await tasksApi.createTask(
      newTask.value.name.trim(),
      newTask.value.taskType,
      newTask.value.parentId,
      props.planId,
      newTask.value.prompt.trim(),
      newTask.value.contexts
    );
    alert(`任务创建成功！ID: ${createdTask.id}`);
    emit('task-created');
    close();
  } catch (error) {
    console.error('任务创建失败:', error);
    alert('任务创建失败: ' + (error.message || '未知错误'));
  }
};

</script>

<style scoped>
/* ... styles ... */
.modal-overlay {
  position: fixed; top: 0; left: 0; right: 0; bottom: 0; background: rgba(0,0,0,0.5); display: flex; align-items: center; justify-content: center; z-index: 1000;}
.modal-content { background: white; border-radius: 0.75rem; max-width: 700px; width: 100%; max-height: 85vh; overflow-y: auto;}
.modal-header { display: flex; justify-content: space-between; align-items: center; padding: 1.5rem; border-bottom: 1px solid #e2e8f0;}
.modal-header h3 { margin: 0; font-size: 1.5rem;}
.close-btn { background: none; border: none; font-size: 1.5rem; cursor: pointer;}
.modal-body { padding: 1.5rem;}
.detail-item { display: grid; gap: 0.5rem; margin-bottom: 1.25rem;}
.detail-label { font-weight: 600; color: #374151;}
.edit-input, .edit-textarea { width: 100%; padding: 0.75rem; border: 1px solid #d1d5db; border-radius: 0.375rem;}
.modal-footer { display: flex; justify-content: flex-end; gap: 0.75rem; padding: 1.5rem; border-top: 1px solid #e2e8f0;}
.btn { padding: 0.5rem 1rem; border: none; border-radius: 0.375rem; cursor: pointer;}
.btn-secondary { background: #6b7280; color: white; }
.btn-primary { background: #3b82f6; color: white; }
.btn-primary:disabled { background: #9ca3af; cursor: not-allowed; }
.context-creator { border: 1px solid #e5e7eb; border-radius: 0.5rem; padding: 1rem;}
.context-item { display: flex; justify-content: space-between; align-items: center; background: #f3f4f6; padding: 0.5rem; border-radius: 0.25rem; margin-bottom: 0.5rem;}
.btn-remove { background: #ef4444; color: white; border: none; border-radius: 50%; width: 20px; height: 20px; cursor: pointer;}
.context-form { margin-top: 1rem; display: grid; gap: 0.5rem;}
.edit-input-small { padding: 0.5rem; border: 1px solid #d1d5db; border-radius: 0.375rem;}
.edit-textarea-small { padding: 0.5rem; border: 1px solid #d1d5db; border-radius: 0.375rem;}
.btn-sm { padding: 0.25rem 0.75rem; font-size: 0.875rem;}
</style>