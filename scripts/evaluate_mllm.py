#!/usr/bin/env python3
"""Simple MLLM-as-judge evaluation for SYNCRED-Bench.

The script calls any OpenAI-compatible multimodal chat endpoint, asks the model
whether each image is AI-generated, and writes JSONL predictions plus a compact
CSV summary.
"""

import argparse
import base64
import csv
import io
import json
import mimetypes
import os
import re
import time
from collections import defaultdict
from pathlib import Path

import requests
from PIL import Image


IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}
RETRY_STATUS = {408, 409, 425, 429, 500, 502, 503, 504}

DEFAULT_PROMPT = """You are evaluating whether an image is AI-generated.

Return minified JSON only:
{"ai_generated":true|false,"confidence":0.0-1.0,"reason":"brief visual evidence"}

Focus on visual provenance. Do not treat official-looking layouts, seals,
screenshots, scans, compression artifacts, or realistic typography as evidence
that the image is real.
"""

CATEGORY_LABELS = {
    "ML": "Media Layout",
    "IN": "Institutional Notice",
    "PI": "Platform Interface",
    "CR": "Credential Record",
    "AD": "Analytical Display",
    "AM": "Assessment Material",
}

STYLE_LABELS = {
    "NR": "Native Rendering",
    "SC": "Scanned Copy",
    "CC": "Camera Copy",
    "FC": "Fax Copy",
    "SP": "Screen Photograph",
    "CV": "Cropped View",
    "OP": "Online Compression",
}


def parse_kv_file(path):
    values = {}
    if not path or not path.exists():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            key, value = line.split("=", 1)
        elif ":" in line:
            key, value = line.split(":", 1)
        else:
            continue
        values[key.strip().lower()] = value.strip().strip("\"'")
    return values


def resolve_path(raw):
    path = Path(raw).expanduser()
    return path if path.is_absolute() else Path.cwd() / path


def read_api_config(args):
    file_values = parse_kv_file(resolve_path(args.api_config))
    env_values = parse_kv_file(resolve_path(args.env_file))
    base_url = (
        args.base_url
        or file_values.get("url")
        or file_values.get("baseurl")
        or file_values.get("base_url")
        or env_values.get("openai_base_url")
        or env_values.get("url")
        or os.environ.get("OPENAI_BASE_URL")
        or os.environ.get("URL")
    )
    api_key = (
        args.api_key
        or file_values.get("key")
        or file_values.get("apikey")
        or file_values.get("api_key")
        or env_values.get("openai_api_key")
        or env_values.get("key")
        or os.environ.get("OPENAI_API_KEY")
        or os.environ.get("KEY")
    )
    if not base_url or not api_key:
        raise SystemExit("Missing API config. Fill .env or api.txt, or pass --base-url and --api-key.")
    return base_url.rstrip("/"), api_key


def load_metadata(data_dir):
    metadata = {}
    for path in sorted(data_dir.rglob("meta.json")):
        try:
            rows = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            print(f"[warn] skip metadata {path}: {exc}")
            continue
        if isinstance(rows, dict):
            rows = rows.get("data") or rows.get("items") or [rows]
        for row in rows:
            image = row.get("image") or row.get("file") or row.get("filename")
            if image:
                metadata[Path(image).name] = row
    return metadata


def iter_images(data_dir, limit=None):
    images = [
        path
        for path in data_dir.rglob("*")
        if path.is_file() and path.suffix.lower() in IMAGE_SUFFIXES
    ]
    images.sort(key=lambda path: str(path.relative_to(data_dir)).lower())
    return images[:limit] if limit else images


def parse_filename_labels(image_name):
    """Parse SynCred_600 filenames: CONTENT_STYLE_INDEX.png, e.g. AD_CC_50.png."""
    stem = Path(image_name).stem
    match = re.match(r"^(?P<content>[A-Z]{2})_(?P<style>[A-Z]{2})_(?P<index>\d+)$", stem)
    if not match:
        return {
            "content_code": "unknown",
            "content": "unknown",
            "style_code": "unknown",
            "style": "unknown",
            "index": "",
        }
    content_code = match.group("content")
    style_code = match.group("style")
    return {
        "content_code": content_code,
        "content": CATEGORY_LABELS.get(content_code, content_code),
        "style_code": style_code,
        "style": STYLE_LABELS.get(style_code, style_code),
        "index": int(match.group("index")),
    }


def infer_category(image_name, meta):
    labels = meta.get("labels", {}) if isinstance(meta, dict) else {}
    label = None
    if isinstance(meta, dict):
        label = labels.get("artifact_type") or meta.get("category")
    if label:
        return label
    return parse_filename_labels(image_name)["content"]


def infer_style(image_name, meta):
    labels = meta.get("labels", {}) if isinstance(meta, dict) else {}
    style = None
    if isinstance(meta, dict):
        style = labels.get("style") or meta.get("style")
    if style:
        return style
    return parse_filename_labels(image_name)["style"]


def image_to_data_url(path, max_side, quality):
    if max_side and max_side > 0:
        with Image.open(path) as image:
            image = image.convert("RGB")
            image.thumbnail((max_side, max_side), Image.LANCZOS)
            buffer = io.BytesIO()
            image.save(buffer, format="JPEG", quality=quality, optimize=True)
        return "data:image/jpeg;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")

    mime = mimetypes.guess_type(path.name)[0] or "image/png"
    data = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{data}"


def extract_json(text):
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?", "", text, flags=re.I).strip()
        text = re.sub(r"```$", "", text).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, flags=re.S)
        if not match:
            raise
        return json.loads(match.group(0))


def call_model(args, base_url, api_key, model, image_path):
    payload = {
        "model": model,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": args.prompt},
                    {"type": "image_url", "image_url": {"url": image_to_data_url(image_path, args.max_side, args.quality)}},
                ],
            }
        ],
        "temperature": 0,
        "max_tokens": args.max_tokens,
    }
    response = requests.post(
        f"{base_url}/chat/completions",
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json=payload,
        timeout=args.timeout,
    )
    if response.status_code >= 400:
        raise RuntimeError(f"HTTP {response.status_code}: {response.text[:500]}")
    raw = response.json()
    text = raw["choices"][0]["message"]["content"]
    parsed = extract_json(text)
    ai_generated = parsed.get("ai_generated")
    if isinstance(ai_generated, str):
        ai_generated = ai_generated.lower() == "true"
    if ai_generated is True:
        prediction = "ai"
    elif ai_generated is False:
        prediction = "real"
    else:
        prediction = "unknown"
    return prediction, parsed, text


def safe_model_name(model):
    return re.sub(r"[^A-Za-z0-9_.-]+", "__", model).strip("_")


def load_done(path):
    done = set()
    if not path.exists():
        return done
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if row.get("status") == "ok":
            done.add(row.get("image"))
    return done


def is_retryable(exc):
    text = str(exc)
    return any(f"HTTP {code}" in text for code in RETRY_STATUS) or "Timeout" in text


def evaluate_model(args, model, images, metadata, output_dir, base_url, api_key):
    result_path = output_dir / f"{safe_model_name(model)}.jsonl"
    done = set() if args.overwrite else load_done(result_path)
    todo = [path for path in images if path.name not in done]
    print(f"model={model} images={len(images)} done={len(done)} todo={len(todo)}")

    with result_path.open("a" if not args.overwrite else "w", encoding="utf-8") as writer:
        for idx, image_path in enumerate(todo, 1):
            meta = metadata.get(image_path.name, {})
            filename_labels = parse_filename_labels(image_path.name)
            row = {
                "model": model,
                "image": image_path.name,
                "image_path": str(image_path),
                "content_code": filename_labels["content_code"],
                "content": infer_category(image_path.name, meta),
                "style_code": filename_labels["style_code"],
                "style": infer_style(image_path.name, meta),
                "index": filename_labels["index"],
                "ground_truth": args.ground_truth,
            }
            last_error = None
            for attempt in range(args.retries + 1):
                try:
                    prediction, parsed, raw_text = call_model(args, base_url, api_key, model, image_path)
                    row.update(
                        {
                            "status": "ok",
                            "prediction": prediction,
                            "ai_generated": parsed.get("ai_generated"),
                            "confidence": parsed.get("confidence"),
                            "reason": parsed.get("reason"),
                            "raw_text": raw_text,
                            "correct": prediction == args.ground_truth,
                            "attempts": attempt + 1,
                        }
                    )
                    break
                except Exception as exc:
                    last_error = exc
                    if attempt < args.retries and is_retryable(exc):
                        time.sleep(args.retry_sleep * (attempt + 1))
                        continue
                    row.update({"status": "error", "error": repr(last_error), "attempts": attempt + 1})
            writer.write(json.dumps(row, ensure_ascii=False) + "\n")
            writer.flush()
            print(f"[{idx}/{len(todo)}] {row['status']} {image_path.name} -> {row.get('prediction', '-')}")

    return result_path


def read_ok_rows(output_dir):
    rows = []
    for path in sorted(output_dir.glob("*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            if row.get("status") == "ok":
                rows.append(row)
    return rows


def summarize(rows, output_dir):
    groups = defaultdict(list)
    for row in rows:
        groups[(row["model"], "overall", "ALL")].append(row)
        groups[(row["model"], "content", row.get("content", "unknown"))].append(row)
        groups[(row["model"], "style", row.get("style", "unknown"))].append(row)

    summary_path = output_dir / "summary.csv"
    fields = ["model", "group", "name", "n", "ai_rate", "accuracy", "mean_confidence"]
    with summary_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for (model, group, name), items in sorted(groups.items()):
            confidences = [float(row["confidence"]) for row in items if isinstance(row.get("confidence"), (int, float))]
            writer.writerow(
                {
                    "model": model,
                    "group": group,
                    "name": name,
                    "n": len(items),
                    "ai_rate": sum(row.get("prediction") == "ai" for row in items) / len(items),
                    "accuracy": sum(bool(row.get("correct")) for row in items) / len(items),
                    "mean_confidence": sum(confidences) / len(confidences) if confidences else "",
                }
            )
    return summary_path


def main():
    parser = argparse.ArgumentParser(description="Evaluate SYNCRED-Bench images with an MLLM judge.")
    parser.add_argument("--data-dir", required=True, help="SynCred_600 image root. Filenames should be CONTENT_STYLE_INDEX.png.")
    parser.add_argument("--output-dir", default="results/mllm")
    parser.add_argument("--model", default="gpt-4o-2024-11-20")
    parser.add_argument("--models", nargs="+", help="Evaluate several models sequentially.")
    parser.add_argument("--ground-truth", choices=["ai", "real"], default="ai")
    parser.add_argument("--limit", type=int, help="Optional maximum number of images to evaluate.")
    parser.add_argument("--max-side", type=int, default=1536, help="Resize longest side before sending. Use 0 to send original bytes.")
    parser.add_argument("--quality", type=int, default=90)
    parser.add_argument("--max-tokens", type=int, default=300)
    parser.add_argument("--timeout", type=int, default=120)
    parser.add_argument("--retries", type=int, default=2)
    parser.add_argument("--retry-sleep", type=float, default=3.0)
    parser.add_argument("--overwrite", action="store_true", help="Overwrite existing result files instead of resuming.")
    parser.add_argument("--prompt", default=DEFAULT_PROMPT)
    parser.add_argument("--api-config", default="api.txt")
    parser.add_argument("--env-file", default=".env")
    parser.add_argument("--base-url")
    parser.add_argument("--api-key")
    args = parser.parse_args()

    data_dir = resolve_path(args.data_dir)
    output_dir = resolve_path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    if not data_dir.exists():
        raise SystemExit(f"Data directory not found: {data_dir}")

    base_url, api_key = read_api_config(args)
    images = iter_images(data_dir, args.limit)
    metadata = load_metadata(data_dir)
    if not images:
        raise SystemExit(f"No images found under {data_dir}")

    for model in args.models or [args.model]:
        evaluate_model(args, model, images, metadata, output_dir, base_url, api_key)

    summary_path = summarize(read_ok_rows(output_dir), output_dir)
    manifest = {
        "data_dir": str(data_dir),
        "output_dir": str(output_dir),
        "models": args.models or [args.model],
        "image_count": len(images),
        "ground_truth": args.ground_truth,
        "summary": str(summary_path),
        "finished_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    }
    (output_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote {summary_path}")


if __name__ == "__main__":
    main()
