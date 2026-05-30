from pathlib import Path                                                             (the original one before chat)

from groq import Groq

client = Groq()


# "compound-beta",
# "compound-beta-mini",
# "gemma2-9b-it",
# "llama-3.1-8b-instant",
# "llama-3.3-70b-versatile",
# "meta-llama/llama-4-maverick-17b-128e-instruct",
# "meta-llama/llama-4-scout-17b-16e-instruct",
# "meta-llama/llama-guard-4-12b",
# "moonshotai/kimi-k2-instruct",
# "openai/gpt-oss-120b",
# "openai/gpt-oss-20b",
# "qwen/qwen3-32b",
# read prompt from text file
prompt = Path(__file__).parent.joinpath("prompt.txt").read_text()
completion = client.chat.completions.create(
    model="qwen/qwen3-32b",
    messages=[
      {
        "role": "user",
        "content": prompt
      }
    ],
    temperature=1,
    max_completion_tokens=8192,
    top_p=1,
    # reasoning_effort="medium",
    stream=True,
    stop=None
)

for chunk in completion:
    print(chunk.choices[0].delta.content or "", end="")