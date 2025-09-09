<template>
  <div class="graph-view-container">
    <div ref="networkContainer" class="network-graph"></div>
  </div>
</template>

<script setup>
import { ref, onMounted, watch, nextTick } from 'vue';
import { Network } from 'vis-network';

const props = defineProps({
  tasks: {
    type: Array,
    required: true,
  },
});

const networkContainer = ref(null);
let network = null;

const statusColors = {
  pending: { border: '#a0aec0', background: '#edf2f7', highlight: '#cbd5e0' },
  running: { border: '#3182ce', background: '#ebf8ff', highlight: '#bee3f8' },
  done: { border: '#38a169', background: '#f0fff4', highlight: '#c6f6d5' },
  failed: { border: '#e53e3e', background: '#fff5f5', highlight: '#fed7d7' },
};

const renderGraph = () => {
  if (!networkContainer.value || !props.tasks) return;

  const nodes = props.tasks.map(task => {
    const color = statusColors[task.status] || statusColors.pending;
    return {
      id: task.id,
      label: task.name,
      title: `Status: ${task.status}`,
      color: {
        border: color.border,
        background: color.background,
        highlight: {
          border: color.border,
          background: color.highlight,
        },
      },
      shape: 'box',
      margin: 10,
    };
  });

  const edges = props.tasks
    .filter(task => task.parent_id !== null && task.parent_id !== undefined)
    .map(task => ({
      from: task.parent_id,
      to: task.id,
      arrows: 'to',
    }));

  const data = {
    nodes: nodes,
    edges: edges,
  };

  const options = {
    layout: {
      hierarchical: {
        direction: 'UD', // Up-Down direction
        sortMethod: 'directed',
        levelSeparation: 150,
        nodeSpacing: 200,
      },
    },
    physics: {
      hierarchicalRepulsion: {
        nodeDistance: 150,
      },
    },
    interaction: {
      hover: true,
      dragNodes: true,
      zoomView: true,
      dragView: true,
    },
    nodes: {
      font: {
        size: 14,
      },
    },
  };

  if (network) {
    network.setData(data);
  } else {
    network = new Network(networkContainer.value, data, options);
  }
};

onMounted(() => {
  nextTick(() => {
    renderGraph();
  });
});

watch(() => props.tasks, () => {
  renderGraph();
}, { deep: true });

</script>

<style scoped>
.graph-view-container {
  width: 100%;
  height: 70vh; /* Adjust height as needed */
  border: 1px solid #e2e8f0;
  border-radius: 0.5rem;
  background-color: #fdfdfd;
}

.network-graph {
  width: 100%;
  height: 100%;
}
</style>