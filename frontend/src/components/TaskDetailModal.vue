<template>
  <div v-if="show" class="modal-overlay" @click="close">
    <div class="modal-content" @click.stop>
      <div class="modal-header">
        <h3>Task Details: #{{ task?.id }}</h3>
        <button @click="close" class="close-btn">&times;</button>
      </div>
      
      <div class="modal-body" v-if="task">
        <div class="two-column-layout">
          <!-- Left Column -->
          <div class="left-column">
            <div class="detail-item">
              <label class="detail-label">Task Name</label>
              <div class="detail-value">{{ task.shortName || task.name }}</div>
            </div>
            <div class="detail-item">
              <label class="detail-label">Task Instruction</label>
              <textarea v-model="editableInput" class="edit-textarea" rows="4"></textarea>
              <button @click="saveInput" class="btn btn-sm edit-btn" :disabled="saving.input">
                {{ saving.input ? 'Saving...' : 'Save Input' }}
              </button>
            </div>
            <div class="detail-item">
              <label class="detail-label">Task Output</label>
              <textarea v-model="editableOutput" class="edit-textarea" rows="6"></textarea>
              <button @click="saveOutput" class="btn btn-sm edit-btn" :disabled="saving.output">
                {{ saving.output ? 'Saving...' : 'Save Output' }}
              </button>
            </div>
          </div>

          <!-- Right Column -->
          <div class="right-column">
            <div class="detail-item">
              <div class="detail-label-header">
                <label class="detail-label">Task Contexts</label>
                <button @click="regenerateContext" class="btn btn-sm btn-regenerate" title="Regenerate the initial AI-generated context for this task.">Regenerate Context</button>
              </div>
              <div v-if="contexts.length > 0" class="task-context-display">
                <div v-for="(ctx, index) in contexts" :key="ctx.label" class="context-record">
                  <div class="context-header">
                    <h5>{{ formatLabel(ctx.label) }}</h5>
                    <div class="context-actions">
                      <button v-if="!editingState[ctx.label]" @click="enterEditMode(ctx)" class="btn-edit">Edit</button>
                      <button v-if="editingState[ctx.label]" @click="saveContext(ctx.label)" class="btn-save">Save</button>
                      <button v-if="editingState[ctx.label]" @click="cancelEdit(ctx.label)" class="btn-cancel">Cancel</button>
                    </div>
                  </div>
                  
                  <div v-if="editingState[ctx.label]" class="context-editor">
                    <label>Content (Combined)</label>
                    <textarea v-model="editingState[ctx.label].combined" rows="5"></textarea>
                    
                    <div class="editor-toolbar">
                      <label>Meta (JSON)</label>
                      <div>
                        <button class="btn-json-util" @click="formatJson('meta', ctx.label)">Format</button>
                        <button class="btn-json-util" @click="validateJson('meta', ctx.label)">Validate</button>
                      </div>
                    </div>
                    <textarea v-model="editingState[ctx.label].meta" rows="4"></textarea>

                    <div class="editor-toolbar">
                      <label>Sections (JSON)</label>
                      <div>
                        <button class="btn-json-util" @click="formatJson('sections', ctx.label)">Format</button>
                        <button class="btn-json-util" @click="validateJson('sections', ctx.label)">Validate</button>
                      </div>
                    </div>
                    <textarea v-model="editingState[ctx.label].sections" rows="4"></textarea>
                  </div>

                  <div v-else class="context-content-display">
                    <details>
                      <summary>View Content</summary>
                      <pre class="context-text">{{ ctx.combined || 'No content' }}</pre>
                    </details>
                    <details>
                      <summary>View Meta</summary>
                      <JsonViewer :data="ctx.meta" />
                    </details>
                    <details>
                      <summary>View Sections</summary>
                      <JsonViewer :data="ctx.sections" />
                    </details>
                  </div>
                </div>
              </div>
              <div v-else class="empty-context">
                <p>No context information available for this task.</p>
              </div>
            </div>
          </div>
        </div>
      </div>
      
      <div class="modal-footer">
        <button @click="deleteThisTask" class="btn btn-danger">Delete Task</button>
        <button @click="close" class="btn btn-secondary">Close</button>
        <button @click="runThisTask" class="btn btn-primary">Rerun Task</button>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, watch, toRefs } from 'vue';
import { tasksApi } from '../services/api';
import JsonViewer from './JsonViewer.vue';

const props = defineProps({ task: Object, show: Boolean });
const emit = defineEmits(['close', 'task-rerun', 'task-deleted']);

const { task } = toRefs(props);

const editableInput = ref('');
const editableOutput = ref('');
const contexts = ref([]);
const saving = ref({ input: false, output: false });
const editingState = ref({}); // To hold the state of contexts being edited

const formatLabel = (label) => {
  if (label === 'ai-initial') {
    return 'Initial AI Analysis';
  }
  return label;
};

watch(task, async (newTask) => {
  if (newTask && newTask.id) {
    try {
      // Always fetch input, output, and context.
      // The API layer will gracefully handle 404s for tasks without output.
      const [inputRes, outputRes, contextRes] = await Promise.all([
        tasksApi.getTaskInput(newTask.id).catch(() => ''),
        tasksApi.getTaskOutput(newTask.id), 
        tasksApi.getTaskContextSnapshots(newTask.id).catch(() => ({ snapshots: [] }))
      ]);

      editableInput.value = inputRes || '';
      editableOutput.value = outputRes || '';
      contexts.value = contextRes.snapshots || [];
      editingState.value = {}; // Reset editing state on new task
    } catch (error) {
      console.error("Failed to load task details:", error);
      editableOutput.value = 'Error loading details.';
    }
  }
});

const enterEditMode = (context) => {
  editingState.value[context.label] = {
    combined: context.combined,
    meta: JSON.stringify(context.meta, null, 2),
    sections: JSON.stringify(context.sections, null, 2),
  };
};

const cancelEdit = (label) => {
  delete editingState.value[label];
};

const formatJson = (field, label) => {
  try {
    const currentJson = JSON.parse(editingState.value[label][field]);
    editingState.value[label][field] = JSON.stringify(currentJson, null, 2);
  } catch (e) {
    alert('Invalid JSON cannot be formatted.');
  }
};

const validateJson = (field, label) => {
  try {
    JSON.parse(editingState.value[label][field]);
    alert('JSON is valid!');
  } catch (e) {
    alert(`Invalid JSON: ${e.message}`);
  }
};

const saveContext = async (label) => {
  const editedContext = editingState.value[label];
  if (!editedContext) return;

  try {
    const payload = {
      content: editedContext.combined,
      meta: JSON.parse(editedContext.meta),
      sections: JSON.parse(editedContext.sections),
    };
    await tasksApi.updateTaskContext(task.value.id, label, payload);
    alert('Context updated successfully!');
    delete editingState.value[label];
    // Refresh contexts
    const contextRes = await tasksApi.getTaskContextSnapshots(task.value.id);
    contexts.value = contextRes.snapshots || [];
  } catch (e) {
    alert('Failed to save context: ' + e.message);
    console.error("Error parsing JSON or saving context:", e);
  }
};

const close = () => emit('close');
const saveInput = async () => {
  if (!task.value || !task.value.id) return;
  saving.value.input = true;
  try {
    await tasksApi.updateTaskInput(task.value.id, editableInput.value);
    alert('Input saved successfully!');
    // Refresh input from server to confirm it was saved
    const newInput = await tasksApi.getTaskInput(task.value.id);
    editableInput.value = newInput || '';
  } catch (error) {
    console.error('Failed to save task input:', error);
    alert('Failed to save input.');
  } finally {
    saving.value.input = false;
  }
};

const saveOutput = async () => {
  if (!task.value || !task.value.id) return;
  saving.value.output = true;
  try {
    await tasksApi.updateTaskOutput(task.value.id, editableOutput.value);
    alert('Output saved successfully!');
    // Refresh output from server to confirm it was saved
    const newOutput = await tasksApi.getTaskOutput(task.value.id);
    editableOutput.value = newOutput || '';
  } catch (error) {
    console.error('Failed to save task output:', error);
    alert('Failed to save output.');
  } finally {
    saving.value.output = false;
  }
};
const runThisTask = () => emit('task-rerun', task.value.id);

const regenerateContext = async () => {
  if (!task.value || !task.value.id) return;
  if (confirm('Are you sure you want to regenerate the initial AI context? This will overwrite the existing \'ai-initial\' context.')) {
    try {
      await tasksApi.regenerateTaskContext(task.value.id);
      alert('Context regenerated successfully!');
      // Refresh contexts to show the new one
      const contextRes = await tasksApi.getTaskContextSnapshots(task.value.id);
      contexts.value = contextRes.snapshots || [];
    } catch (error) {
      console.error('Failed to regenerate context:', error);
      alert('Failed to regenerate context. See console for details.');
    }
  }
};

const deleteThisTask = async () => {
  if (!task.value || !task.value.id) return;
  
  if (confirm(`Are you sure you want to delete task "${task.value.name}" and all its sub-tasks? This action cannot be undone.`)) {
    try {
      await tasksApi.deleteTask(task.value.id);
      alert('Task deleted successfully!');
      emit('task-deleted');
      close();
    } catch (error) {
      console.error('Failed to delete task:', error);
      alert('Failed to delete task. See console for details.');
    }
  }
};

</script>

<style scoped>
/* Modal Overlay */
.modal-overlay {
  position: fixed;
  top: 0;
  left: 0;
  width: 100%;
  height: 100%;
  background-color: rgba(0, 0, 0, 0.5);
  display: flex;
  justify-content: center;
  align-items: center;
  z-index: 1000;
  backdrop-filter: blur(2px);
  animation: fadeIn 0.3s ease-out;
}

@keyframes fadeIn {
  from {
    opacity: 0;
  }
  to {
    opacity: 1;
  }
}

/* Modal Content */
.modal-content {
  background: white;
  border-radius: 12px;
  box-shadow: 0 20px 40px rgba(0, 0, 0, 0.15);
  width: 90%;
  max-width: 1200px;
  max-height: 90vh;
  display: flex;
  flex-direction: column;
  animation: slideUp 0.3s ease-out;
  overflow: hidden;
}

@keyframes slideUp {
  from {
    transform: translateY(20px);
    opacity: 0;
  }
  to {
    transform: translateY(0);
    opacity: 1;
  }
}

/* Modal Header */
.modal-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 24px 32px;
  border-bottom: 1px solid #e5e7eb;
  background: #f9fafb;
  border-radius: 12px 12px 0 0;
}

.modal-header h3 {
  margin: 0;
  font-size: 20px;
  font-weight: 600;
  color: #111827;
}

.close-btn {
  background: none;
  border: none;
  font-size: 28px;
  cursor: pointer;
  color: #6b7280;
  line-height: 1;
  padding: 4px;
  border-radius: 4px;
  transition: all 0.2s ease;
}

.close-btn:hover {
  color: #374151;
  background-color: #f3f4f6;
}

/* Modal Body */
.modal-body {
  flex: 1;
  overflow-y: auto;
  padding: 32px;
}

/* Two Column Layout */
.two-column-layout {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 32px;
  height: 100%;
}

.left-column,
.right-column {
  display: flex;
  flex-direction: column;
  gap: 24px;
}

/* Detail Items */
.detail-item {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.detail-label {
  font-weight: 600;
  font-size: 14px;
  color: #374151;
  text-transform: uppercase;
  letter-spacing: 0.5px;
}

.detail-label-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.btn-regenerate {
  background-color: #f59e0b; /* amber-500 */
  color: white;
  font-weight: 500;
}

.btn-regenerate:hover:not(:disabled) {
  background-color: #d97706; /* amber-600 */
}

.detail-value {
  padding: 12px 16px;
  background-color: #f9fafb;
  border: 1px solid #e5e7eb;
  border-radius: 8px;
  font-size: 14px;
  color: #111827;
  min-height: 24px;
}

/* Textarea Styles */
.edit-textarea {
  width: 100%;
  padding: 12px 16px;
  border: 1px solid #d1d5db;
  border-radius: 8px;
  font-family: 'SF Mono', 'Monaco', 'Inconsolata', 'Roboto Mono', monospace;
  font-size: 13px;
  line-height: 1.5;
  resize: vertical;
  transition: border-color 0.2s ease, box-shadow 0.2s ease;
  background-color: white;
}

.edit-textarea:focus {
  outline: none;
  border-color: #3b82f6;
  box-shadow: 0 0 0 3px rgba(59, 130, 246, 0.1);
}

/* Button Styles */
.btn {
  padding: 8px 16px;
  border: none;
  border-radius: 6px;
  font-size: 14px;
  font-weight: 500;
  cursor: pointer;
  transition: all 0.2s ease;
  text-decoration: none;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
}

.btn:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}

.btn-sm {
  padding: 6px 12px;
  font-size: 12px;
}

.btn-primary {
  background-color: #3b82f6;
  color: white;
}

.btn-primary:hover:not(:disabled) {
  background-color: #2563eb;
  transform: translateY(-1px);
  box-shadow: 0 4px 12px rgba(59, 130, 246, 0.3);
}

.btn-secondary {
  background-color: #6b7280;
  color: white;
}

.btn-secondary:hover:not(:disabled) {
  background-color: #4b5563;
}

.btn-danger {
  background-color: #ef4444;
  color: white;
}

.btn-danger:hover:not(:disabled) {
  background-color: #dc2626;
  transform: translateY(-1px);
  box-shadow: 0 4px 12px rgba(239, 68, 68, 0.3);
}

.edit-btn {
  align-self: flex-start;
  background-color: #059669;
  color: white;
  margin-top: 8px;
}

.edit-btn:hover:not(:disabled) {
  background-color: #047857;
}

/* Context Display */
.task-context-display {
  display: flex;
  flex-direction: column;
  gap: 20px;
}

.context-record {
  border: 1px solid #e5e7eb;
  border-radius: 8px;
  overflow: hidden;
  background: white;
}

.context-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 16px 20px;
  background-color: #f9fafb;
  border-bottom: 1px solid #e5e7eb;
}

.context-header h5 {
  margin: 0;
  font-size: 16px;
  font-weight: 600;
  color: #111827;
}

.context-actions {
  display: flex;
  gap: 8px;
}

.btn-edit,
.btn-save,
.btn-cancel {
  padding: 4px 12px;
  font-size: 12px;
  border: none;
  border-radius: 4px;
  cursor: pointer;
  font-weight: 500;
  transition: all 0.2s ease;
}

.btn-edit {
  background-color: #3b82f6;
  color: white;
}

.btn-edit:hover {
  background-color: #2563eb;
}

.btn-save {
  background-color: #10b981;
  color: white;
}

.btn-save:hover {
  background-color: #059669;
}

.btn-cancel {
  background-color: #f3f4f6;
  color: #6b7280;
}

.btn-cancel:hover {
  background-color: #e5e7eb;
  color: #374151;
}

/* Context Editor */
.context-editor {
  padding: 20px;
  background-color: #fafbfc;
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.context-editor label {
  font-weight: 600;
  font-size: 13px;
  color: #374151;
  margin-bottom: 4px;
}

.editor-toolbar {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: -8px; /* Pull textarea closer */
}

.editor-toolbar div {
  display: flex;
  gap: 8px;
}

.btn-json-util {
  padding: 2px 8px;
  font-size: 11px;
  background-color: #e5e7eb;
  color: #4b5563;
  border: 1px solid #d1d5db;
  border-radius: 4px;
  cursor: pointer;
}

.btn-json-util:hover {
  background-color: #d1d5db;
}

.context-editor textarea {
  width: 100%;
  padding: 12px;
  border: 1px solid #d1d5db;
  border-radius: 6px;
  font-family: 'SF Mono', 'Monaco', 'Inconsolata', 'Roboto Mono', monospace;
  font-size: 12px;
  line-height: 1.4;
  resize: vertical;
  background-color: white;
}

.context-editor textarea:focus {
  outline: none;
  border-color: #3b82f6;
  box-shadow: 0 0 0 2px rgba(59, 130, 246, 0.1);
}

/* Context Content Display */
.context-content-display {
  padding: 20px;
}

.context-content-display details {
  margin-bottom: 16px;
}

.context-content-display details:last-child {
  margin-bottom: 0;
}

.context-content-display summary {
  font-weight: 600;
  color: #374151;
  cursor: pointer;
  padding: 8px 12px;
  background-color: #f9fafb;
  border: 1px solid #e5e7eb;
  border-radius: 6px;
  transition: background-color 0.2s ease;
}

.context-content-display summary:hover {
  background-color: #f3f4f6;
}

.context-text {
  background-color: #f8f9fa;
  border: 1px solid #e9ecef;
  border-radius: 6px;
  padding: 16px;
  margin-top: 12px;
  font-family: 'SF Mono', 'Monaco', 'Inconsolata', 'Roboto Mono', monospace;
  font-size: 12px;
  line-height: 1.5;
  color: #495057;
  white-space: pre-wrap;
  word-break: break-word;
  overflow-x: auto;
  max-height: 200px;
  overflow-y: auto;
}

/* Empty Context */
.empty-context {
  text-align: center;
  padding: 40px 20px;
  color: #6b7280;
}

.empty-context p {
  margin: 0;
  font-style: italic;
}

/* Modal Footer */
.modal-footer {
  display: flex;
  justify-content: flex-end;
  gap: 12px;
  padding: 24px 32px;
  border-top: 1px solid #e5e7eb;
  background-color: #f9fafb;
  border-radius: 0 0 12px 12px;
}

/* Responsive Design */
@media (max-width: 968px) {
  .modal-content {
    width: 95%;
    max-height: 95vh;
  }
  
  .two-column-layout {
    grid-template-columns: 1fr;
    gap: 24px;
  }
  
  .modal-body {
    padding: 24px;
  }
  
  .modal-header,
  .modal-footer {
    padding: 20px 24px;
  }
}

@media (max-width: 640px) {
  .modal-header h3 {
    font-size: 18px;
  }
  
  .context-header {
    flex-direction: column;
    gap: 12px;
    align-items: flex-start;
  }
  
  .context-actions {
    align-self: stretch;
    justify-content: flex-end;
  }
  
  .modal-footer {
    flex-direction: column;
  }
  
  .modal-footer .btn {
    width: 100%;
  }
}

/* Scrollbar Styling */
.modal-body::-webkit-scrollbar,
.context-text::-webkit-scrollbar,
.edit-textarea::-webkit-scrollbar {
  width: 6px;
}

.modal-body::-webkit-scrollbar-track,
.context-text::-webkit-scrollbar-track,
.edit-textarea::-webkit-scrollbar-track {
  background: #f1f5f9;
  border-radius: 3px;
}

.modal-body::-webkit-scrollbar-thumb,
.context-text::-webkit-scrollbar-thumb,
.edit-textarea::-webkit-scrollbar-thumb {
  background: #cbd5e1;
  border-radius: 3px;
}

.modal-body::-webkit-scrollbar-thumb:hover,
.context-text::-webkit-scrollbar-thumb:hover,
.edit-textarea::-webkit-scrollbar-thumb:hover {
  background: #94a3b8;
}

</style>