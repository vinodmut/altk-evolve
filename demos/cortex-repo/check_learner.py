"""Check that Evolve can learn with the configured model before starting the server.

Runs Evolve's own guideline-generation call on a tiny trajectory with one rejected commit,
using the settings in the environment (source evolve.env first). Some gateway routes reject the
JSON schema Evolve sends for structured output; this shows that up front, instead of as a
"sending session to learn from failed" loop in the Cortex log after a real session.
"""

import os
import sys

from altk_evolve.llm.guidelines.guidelines import generate_guidelines
from altk_evolve.processing.builtin import GuidelineRuntime

model = os.environ.get("EVOLVE_MODEL_NAME", "")
messages = [
    {"role": "user", "content": "Add a field and commit."},
    {
        "role": "assistant",
        "content": "",
        "tool_calls": [
            {"id": "c1", "type": "function", "function": {"name": "Bash", "arguments": '{"command": "git commit -m \'Add field\'"}'}}
        ],
    },
    {"role": "tool", "tool_call_id": "c1", "content": "[error] commit-msg: subject must be a Conventional Commit"},
    {
        "role": "assistant",
        "content": "",
        "tool_calls": [
            {"id": "c2", "type": "function", "function": {"name": "Bash", "arguments": '{"command": "git commit -m \'feat: add field\'"}'}}
        ],
    },
    {"role": "tool", "tool_call_id": "c2", "content": "[main 1a2b3c] feat: add field"},
]
try:
    generate_guidelines(messages, options=GuidelineRuntime.from_settings())
except Exception as error:
    print(f"LEARNING FAILS with {model}: {str(error).splitlines()[0][:200]}")
    sys.exit(1)
print(f"OK: Evolve can learn with {model}")
