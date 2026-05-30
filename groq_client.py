from pathlib import Path
from groq import Groq
import argparse
import json

client = Groq()

parser = argparse.ArgumentParser(
    description="Extract an intermediate OpenX-ready JSON scenario from an accident report."
)
parser.add_argument(
    "--report",
    help="Accident report text. If omitted, --report-file is used.",
)
parser.add_argument(
    "--report-file",
    default="accident_report.txt",
    help="Path to a UTF-8 text file containing the accident report.",
)
parser.add_argument(
    "--prompt-file",
    default="prompt.txt",
    help="Path to the prompt template containing {ACCIDENT_REPORT_TEXT}.",
)
parser.add_argument(
    "--model",
    default="openai/gpt-oss-120b",
    help="Groq model name.",
)
args = parser.parse_args()

prompt_template = Path(args.prompt_file).read_text(encoding="utf-8")

if "{ACCIDENT_REPORT_TEXT}" in prompt_template:
    if args.report:
        accident_report = args.report
    else:
        report_path = Path(args.report_file)
        if not report_path.exists():
            raise FileNotFoundError(
                f"No accident report provided. Pass --report or create {report_path}."
            )
        accident_report = report_path.read_text(encoding="utf-8")

    prompt = prompt_template.replace("{ACCIDENT_REPORT_TEXT}", accident_report.strip())
else:
    prompt = prompt_template

completion = client.chat.completions.create(
    model=args.model,
    messages=[
        {
            "role": "system",
            "content": "You are an expert in traffic-accident scenario extraction. Return only valid JSON. Do not include explanations, commentary, or reasoning."
        },
        {
            "role": "user",
            "content": prompt
        }
    ],
    temperature=0.2,
    max_completion_tokens=4000,
    response_format={"type": "json_object"},
    stream=False
)

response_text = completion.choices[0].message.content
finish_reason = completion.choices[0].finish_reason

print("Finish reason:", finish_reason)
print(response_text)

Path("raw_output.txt").write_text(response_text, encoding="utf-8")

try:
    parsed = json.loads(response_text)
    Path("output.json").write_text(
        json.dumps(parsed, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )
    print("\nValid JSON saved to output.json")
except json.JSONDecodeError as e:
    print("\nJSON parsing failed:")
    print(e)
