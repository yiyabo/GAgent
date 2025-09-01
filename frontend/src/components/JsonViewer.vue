
<template>
  <div class="json-viewer">
    <div v-if="isObject(data)" class="json-object">
      <div v-for="(value, key) in data" :key="key" class="json-pair">
        <span class="json-key">"{{ key }}"</span>:
        <JsonViewer :data="value" />
      </div>
    </div>
    <div v-else-if="isArray(data)" class="json-array">
      <div v-for="(item, index) in data" :key="index" class="json-item">
        <JsonViewer :data="item" />
      </div>
    </div>
    <div v-else class="json-value" :class="valueType(data)">
      {{ formatValue(data) }}
    </div>
  </div>
</template>

<script setup>


const props = defineProps({
  data: {
    required: true,
  },
});

const isObject = (val) => typeof val === 'object' && val !== null && !Array.isArray(val);
const isArray = (val) => Array.isArray(val);

const valueType = (val) => {
  if (typeof val === 'string') return 'json-string';
  if (typeof val === 'number') return 'json-number';
  if (typeof val === 'boolean') return 'json-boolean';
  if (val === null) return 'json-null';
  return '';
};

const formatValue = (val) => {
  if (typeof val === 'string') return `"${val}"`;
  return val;
};
</script>

<style scoped>
.json-viewer {
  font-family: 'SF Mono', 'Monaco', 'Inconsolata', 'Roboto Mono', monospace;
  font-size: 13px;
  padding-left: 20px;
}
.json-pair {
  display: flex;
  gap: 5px;
}
.json-key {
  color: #a31515; /* Dark red for keys */
}
.json-value {
  display: inline;
}
.json-string {
  color: #0451a5; /* Blue for strings */
}
.json-number {
  color: #098658; /* Green for numbers */
}
.json-boolean {
  color: #0000ff; /* Strong blue for booleans */
}
.json-null {
  color: #0000ff; /* Strong blue for null */
}
.json-array, .json-object {
  display: flex;
  flex-direction: column;
}
</style>
