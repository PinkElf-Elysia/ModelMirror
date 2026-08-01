from __future__ import annotations


META_AGENT_SYSTEM_PROMPT = (
    "You are ModelMirror's meta-agent planner. Create compact, executable "
    "agent workflows that can run inside ModelMirror's native workflow runner."
)


def build_generation_prompt(goal: str, max_tasks: int) -> str:
    return f"""
Given the user's goal, design an Xpert-aligned structured workflow plan for ModelMirror.

Rules:
1. Return one valid JSON object only. Do not wrap it in Markdown unless unavoidable.
2. Use 1 to {max_tasks} sub_tasks.
3. Each sub_task must have: name, description, reason, inputs, outputs, agent.
4. The first task must accept input "goal".
5. Later task inputs may use "goal" and outputs from earlier tasks only.
6. Use concise snake_case names for tasks and variable names.
7. Each task needs at least one output.
8. Each agent.prompt should reference inputs with ModelMirror template placeholders
   such as <input>{{goal}}</input> or {{goal}}.
9. Each agent.prompt must produce the task outputs as titled sections, for example
   "## research_summary".
10. Do not invent external tools. If a task can run without tools, set
    tool_names to null or [].

Output JSON shape:
{{
  "thought": "brief planning rationale",
  "sub_tasks": [
    {{
      "name": "requirements_analysis",
      "description": "Analyze the goal and clarify success criteria.",
      "reason": "This gives later tasks a stable brief.",
      "inputs": [
        {{
          "name": "goal",
          "type": "string",
          "required": true,
          "description": "The user's goal."
        }}
      ],
      "outputs": [
        {{
          "name": "requirements_brief",
          "type": "string",
          "required": true,
          "description": "A concise requirements brief."
        }}
      ],
      "agent": {{
        "name": "requirements_agent",
        "description": "Clarifies the goal and acceptance criteria.",
        "prompt": "### Objective\\nClarify the user goal.\\n\\n### Instructions\\nRead <input>{{goal}}</input> and extract success criteria.\\n\\n### Output Format\\n## requirements_brief\\nA concise requirements brief.",
        "tool_names": null
      }}
    }}
  ]
}}

User goal:
{goal}
""".strip()
