#!/usr/bin/env python3
"""Download all models needed for offline operation.

Run this once (with internet) before using `make run`.
Reads model IDs from config.yaml so it stays in sync automatically.

Usage:
    python download_models.py          # uses config.yaml
    python download_models.py -c other.yaml
"""

import argparse
import subprocess
import sys

import yaml


def download_hf(repo_id: str, label: str):
    from huggingface_hub import snapshot_download
    print(f"\n[{label}] Downloading {repo_id} ...")
    path = snapshot_download(repo_id=repo_id)
    print(f"[{label}] Cached at: {path}")


def ensure_ollama_model(model: str):
    print(f"\n[LLM] Checking Ollama model: {model} ...")
    try:
        result = subprocess.run(
            ["ollama", "show", model],
            capture_output=True, text=True,
        )
        if result.returncode == 0:
            print(f"[LLM] {model} already present in Ollama.")
            return
    except FileNotFoundError:
        print("[LLM] ERROR: 'ollama' not found in PATH. Install Ollama and run 'ollama serve' first.")
        return

    print(f"[LLM] Pulling {model} via Ollama (this may take a while) ...")
    subprocess.run(["ollama", "pull", model], check=True)
    print(f"[LLM] {model} ready.")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("-c", "--config", default="config.yaml")
    args = parser.parse_args()

    with open(args.config) as f:
        config = yaml.safe_load(f)

    stt_model = config["stt"]["model"]
    tts_model = config["tts"]["model_id"]
    llm_model = config["llm"]["model"]

    print("=== AIOC Bot — Model Downloader ===")
    print(f"  STT : {stt_model}")
    print(f"  TTS : {tts_model}")
    print(f"  LLM : {llm_model} (Ollama)")

    errors = []

    try:
        download_hf(stt_model, "STT")
    except Exception as e:
        print(f"[STT] FAILED: {e}")
        errors.append("STT")

    try:
        download_hf(tts_model, "TTS")
    except Exception as e:
        print(f"[TTS] FAILED: {e}")
        errors.append("TTS")

    try:
        ensure_ollama_model(llm_model)
    except Exception as e:
        print(f"[LLM] FAILED: {e}")
        errors.append("LLM")

    print()
    if errors:
        print(f"WARNING: {', '.join(errors)} download(s) failed. Check errors above.")
        sys.exit(1)
    else:
        print("All models ready. You can now use `make run` in offline mode.")


if __name__ == "__main__":
    main()
