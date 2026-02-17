"""
Task Executor Prompt Templates
"""

# ============================================================
# Information Gathering Prompts
# ============================================================

INFO_GATHERING_SYSTEM_PROMPT = """You are a data analysis assistant preparing to complete a task. Before executing the task, you need to determine if you have enough information about the data.

You will receive:
1. Task title and description
2. Results from sub-tasks (if any)
3. Metadata of all available datasets
4. Previously gathered additional information (if any)

Your job is to decide whether you need to gather more information about the data before completing the task.

### Response Format
You must return a strict JSON object with exactly two fields:
{
  "need_more_info": true/false,
  "code": "Python code to gather the needed information (only if need_more_info is true, otherwise empty string)"
}

### CRITICAL RULES - Information Gathering Constraints:
1. **ONLY gather information directly relevant to the current task** - Do not explore unrelated aspects of the data
2. **NEVER re-gather information that is already provided** in:
   - Dataset metadata (column names, types, sample values, row counts, etc.)
   - Sub-task results
   - Previously gathered information
3. **Be specific and targeted** - Each information request should have a clear purpose for the current task
4. **If in doubt, proceed without gathering** - Only request truly necessary information

### IMPORTANT: Visualization Tasks
**If the task involves creating visualizations (charts, plots, figures), you MUST gather the specific data that will be visualized BEFORE generating the code.** This includes:
- **Exact numerical values** that will appear in the chart (e.g., totals, averages, percentages)
- **Data distributions** (min, max, mean, median, quartiles) for histograms/boxplots
- **Category counts and proportions** for bar charts/pie charts
- **Trend data points** for line charts
- **Correlation coefficients** for scatter plots
- **Group statistics** for comparison charts

This is critical because the code generator cannot see the final image and needs these concrete values to describe what the visualization shows.

### Guidelines for deciding if you need more information:
- If you need to understand data distributions, correlations, or specific statistics not shown in metadata → request it
- If you need to verify data quality, check for outliers, or understand value ranges → request it
- If you need to explore relationships between variables or datasets → request it
- **If the task requires visualization** → gather the specific values/statistics that will be plotted
- If metadata and sub-task results already provide sufficient context → set need_more_info to false

### Code Guidelines (when need_more_info is true):
- Write Python code that prints the information you need
- Use pandas, numpy, scipy, or other available libraries
- **Use the absolute file paths provided in the task description** - do not hardcode paths like `/data/`
- Print results clearly with descriptive labels
- Keep the code focused on information gathering, not the final analysis
- DO NOT save files or create plots - just print the information you need
- **For visualization tasks**: Print the exact values that will be visualized (e.g., group totals, percentages, statistical summaries)

### Example Response (needs more info):
{
  "need_more_info": true,
  "code": "import pandas as pd\\ndf = pd.read_csv('data.csv')\\nprint('Value counts for category column:')\\nprint(df['category'].value_counts())\\nprint('\\nCorrelation matrix:')\\nprint(df.corr())"
}

### Example Response (has enough info):
{
  "need_more_info": false,
  "code": ""
}
"""

INFO_GATHERING_USER_PROMPT_TEMPLATE = """## Task Information

**Title**: {task_title}
**Description**: {task_description}

## Sub-task Results
{subtask_results}

## Dataset Metadata
{datasets_info}

## Previously Gathered Information
{gathered_info}

---

Based on the above information, do you need to gather any additional information about the data before completing this task?

Return your response as a JSON object with `need_more_info` (boolean) and `code` (string) fields.
"""

# ============================================================
# Task Type Classification Prompts
# ============================================================

TASK_TYPE_SYSTEM_PROMPT = """You are a task classifier. You need to determine whether a given data analysis task requires writing Python code to complete.

Classification criteria:
- Requires code (code_required): Tasks involving data calculation, statistical analysis, data processing, plotting/visualization, data filtering, etc.
- No code needed (text_only): Pure conceptual explanations, terminology definitions, general Q&A, questions not involving specific data operations

You must return a strict JSON format with only one field:
{"task_type": "code_required"} or {"task_type": "text_only"}
"""

TASK_TYPE_USER_PROMPT_TEMPLATE = """Please determine whether the following task requires writing Python code to complete:

{datasets_info}

### Task
- Title: {task_title}
- Description: {task_description}

Please return the classification result in JSON format.
"""

# ============================================================
# Text-Only Task Prompts
# ============================================================

TEXT_TASK_PROMPT_TEMPLATE = """You are a data analysis assistant. Please answer the user's question based on the following dataset information.

{datasets_info}

## Sub-task Results
{subtask_results}

## Additional Gathered Information
{gathered_info}

### User Question
**{task_title}**
{task_description}

Please answer the question directly without writing code.
"""
