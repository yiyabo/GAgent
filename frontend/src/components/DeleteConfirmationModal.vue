<template>
  <div v-if="show" class="modal-overlay" @click="$emit('cancel')">
    <div class="modal-content" @click.stop>
      <div class="modal-header">
        <h3>确认删除</h3>
        <button @click="$emit('cancel')" class="close-btn">&times;</button>
      </div>
      <div class="modal-body">
        <p>确定要删除计划「{{ planTitle }}」吗？此操作将删除该计划及所有相关任务，无法撤销。</p>
      </div>
      <div class="modal-footer">
        <button @click="$emit('cancel')" class="btn btn-secondary">取消</button>
        <button @click="$emit('confirm')" class="btn btn-danger">确认删除</button>
      </div>
    </div>
  </div>
</template>

<script setup>
const props = defineProps({
  show: {
    type: Boolean,
    required: true,
  },
  planTitle: {
    type: String,
    default: ''
  }
});

defineEmits(['confirm', 'cancel']);
</script>

<style scoped>
/* ... Modal styles ... */
.modal-overlay {
  position: fixed;
  top: 0;
  left: 0;
  right: 0;
  bottom: 0;
  background: rgba(0, 0, 0, 0.5);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 1000;
}
.modal-content {
  background: white;
  padding: 1.5rem;
  border-radius: 0.5rem;
  max-width: 500px;
  width: 100%;
}
.modal-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  border-bottom: 1px solid #e5e7eb;
  padding-bottom: 1rem;
  margin-bottom: 1rem;
}
.modal-footer {
  display: flex;
  justify-content: flex-end;
  gap: 1rem;
  margin-top: 1.5rem;
}
.btn-secondary {
  background: #6b7280;
  color: white;
}
.btn-danger {
  background: #dc2626;
  color: white;
}
.btn {
  padding: 0.5rem 1rem;
  border: none;
  border-radius: 0.375rem;
  cursor: pointer;
}
</style>