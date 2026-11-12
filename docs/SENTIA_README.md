# 🧠 SENTIA — Multimodal Emotion-Aware Conversational AI
### *Fine-Tuned Small Language Models (SLMs) • Parameter-Efficient Adaptation (LoRA/PEFT) • Multimodal Fusion • Vector RAG*

[![Python: 3.10+](https://img.shields.io/badge/Python-3.10%2B-blue.svg)](https://www.python.org/)
[![PyTorch](https://img.shields.io/badge/PyTorch-2.0%2B-EE4C2C.svg)](https://pytorch.org/)
[![Hugging Face Transformers](https://img.shields.io/badge/%F0%9F%A4%97-Transformers-yellow)](https://huggingface.co/)
[![Model: Llama 3.2 3B Instruct](https://img.shields.io/badge/SLM-Llama_3.2_3B_Instruct-orange.svg)](https://huggingface.co/meta-llama/Llama-3.2-3B-Instruct)
[![LoRA: PEFT](https://img.shields.io/badge/Fine--Tuning-LoRA%20%2F%20PEFT-brightgreen)](https://github.com/huggingface/peft)
[![Classifier: DistilBERT](https://img.shields.io/badge/Classifier-DistilBERT-red)](https://huggingface.co/distilbert-base-uncased)
[![FAISS: Vector Search](https://img.shields.io/badge/Vector_DB-FAISS-blueviolet)](https://github.com/facebookresearch/faiss)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

**SENTIA** is an end-to-end, multimodal conversational AI platform engineered to detect emotional nuance across both **spoken acoustics** and **natural language text**, adapting response demeanor dynamically using a domain fine-tuned **Small Language Model (SLM)**.

Architected specifically for resource-conscious and edge-viable inference, SENTIA integrates parameter-efficient fine-tuning (**LoRA / QLoRA** on **Meta Llama 3.2 3B**), a fine-tuned **DistilBERT** emotion classifier, acoustic feature extraction with **Librosa**, an offline **Vosk** ASR engine, and a **FAISS**-backed semantic episodic memory pipeline.

---

## 🌟 Key Technical Highlights

* 🎯 **Parameter-Efficient SLM Fine-Tuning**: Fine-tuned **Meta Llama 3.2 3B Instruct** using **Unsloth** and **Hugging Face PEFT / LoRA**. Targeted all 7 transformer projection modules (`q_proj`, `k_proj`, `v_proj`, `o_proj`, `gate_proj`, `up_proj`, `down_proj`), updating **48.63 Million parameters** while freezing base weights for minimal GPU/VRAM overhead.
* 📊 **High-Precision Emotion Classification**: Fine-tuned **DistilBERT** on 7 distinct emotional categories (`happy`, `sad`, `angry`, `anxious`, `lonely`, `neutral`, `stressed`), achieving **91.39% accuracy** and a **91.40% Macro F1-score** on validation benchmarks.
* 🎙️ **Multimodal Emotion Fusion**: Implements a confidence-weighted decision engine (`voice_fusion_logic.py`) that synchronizes acoustic prosody (pitch, energy, MFCCs) with semantic textual sentiment, resolving edge cases (e.g., sarcasm or discordant sentiment).
* ⚡ **Offline & Edge-Friendly Automatic Speech Recognition**: Built-in **Vosk** offline acoustic model (`vosk-model-small-en-us-0.15`) with dynamic FFmpeg audio resampling (16 kHz mono 16-bit PCM), enabling zero-cloud-dependency voice interaction.
* 🗄️ **Episodic Vector Memory (RAG)**: Integrates **FAISS** (dense vector similarity search) with `all-MiniLM-L6-v2` 384-dimensional embeddings and an asynchronous SQLite transactional database for persistent contextual recall across chat turns.

---

## 🏗️ System Architecture

```
                      ┌────────────────────────────────────────┐
                      │        User Voice / Text Input         │
                      └───────────────────┬────────────────────┘
                                          │
                   ┌──────────────────────┴──────────────────────┐
                   ▼                                             ▼
         [ Audio Pipeline ]                            [ Text Pipeline ]
    16kHz Mono PCM Audio Stream                       Raw Text Dialogue
                   │                                             │
         ┌─────────┴─────────┐                                   │
         ▼                   ▼                                   ▼
  [ Vosk STT Engine ] [ Acoustic Model ]               [ DistilBERT Classifier ]
  Offline Edge Speech   Librosa Features / MFCCs       7-Class Contextual NLP
  Recognition          Prosody Emotion (Voice Conf)    Accuracy: 91.39%, F1: 91.40%
         │                   │                                   │
         └─────────┬─────────┘                                   │
                   │ (Transcribed Text)                          │
                   └──────────────────────┬──────────────────────┘
                                          │
                                          ▼
                      ┌────────────────────────────────────────┐
                      │    Multimodal Emotion Fusion Engine    │
                      │  - Normalizes acoustic & text labels   │
                      │  - Dynamic confidence weighting        │
                      │  - Conflict resolution & safety gates  │
                      └───────────────────┬────────────────────┘
                                          │ Fused Emotion & Confidence Score
                                          ▼
                      ┌────────────────────────────────────────┐
                      │    Dynamic Context & RAG Builder       │
                      │  - Tone Modulation Engine              │
                      │  - FAISS Vector Similarity (Top-K)     │
                      │  - SQLite Episodic Memory Recall       │
                      └───────────────────┬────────────────────┘
                                          │ Augmented Empathetic Prompt
                                          ▼
                      ┌────────────────────────────────────────┐
                      │      Fine-Tuned Llama 3.2 3B SLM       │
                      │  - LoRA Adapted (48.63M Trainable)     │
                      │  - Local Quantized Serving (Ollama)    │
                      │  - Sub-second Token Generation         │
                      └───────────────────┬────────────────────┘
                                          │
                                          ▼
                      ┌────────────────────────────────────────┐
                      │          Empathetic Response           │
                      └────────────────────────────────────────┘
```

---

## 🔬 Model Engineering & Fine-Tuning Specifications

### 1. Small Language Model (SLM) Adaptation
* **Base Architecture**: Meta Llama 3.2 3B Instruct
* **Fine-Tuning Framework**: Unsloth + Hugging Face PEFT (Parameter-Efficient Fine-Tuning)
* **Adaptation Technique**: Low-Rank Adaptation (LoRA)
* **Target Modules**: `q_proj`, `k_proj`, `v_proj`, `o_proj`, `gate_proj`, `up_proj`, `down_proj` (all 7 attention and MLP projection layers)
* **LoRA Hyperparameters**:
  * LoRA Rank ($r$): `16`
  * LoRA Alpha ($\alpha$): `32`
  * LoRA Dropout: `0.05`
  * Trainable Parameters: **48,627,712** (~1.5% of total base parameters)
* **Training Dataset**: Curated corpus of **5,000+** domain-specific dialogue pairs, cleaned and balanced using custom automated data-cleansing pipelines (`dataset_gen.py`).
* **Deployment Format**: Quantized GGUF executed via local Ollama runtime (`sentia_ft_2:latest`), optimized for low-latency CPU/edge execution.

### 2. DistilBERT Emotion Classification
* **Base Model**: `distilbert-base-uncased`
* **Target Classes (7)**: `happy`, `sad`, `angry`, `anxious`, `lonely`, `neutral`, `stressed`
* **Performance Benchmarks**:
  * **Classification Accuracy**: **91.39%**
  * **Macro F1-Score**: **91.40%**
  * **Inference Latency**: `< 15ms` per utterance on CPU

### 3. Acoustic Prosody & Fusion
* **Acoustic Extraction**: Zero-crossing rate, RMS energy, and Mel-Frequency Cepstral Coefficients (MFCCs) via **Librosa**.
* **Fusion Logic**: Resolves acoustic vs. semantic divergence through confidence thresholding:
  $$\text{Fused Score} = \max(\text{Conf}_{\text{text}}, \text{Conf}_{\text{voice}}) \quad \text{if discordant}$$
  $$\text{Fused Score} = \frac{\text{Conf}_{\text{text}} + \text{Conf}_{\text{voice}}}{2} \quad \text{if concordant}$$

---

## 📊 Evaluation & Benchmark Metrics

| Component | Model / Engine | Metric | Value | Deployment Footprint |
|---|---|---|---|---|
| **SLM Core** | Llama 3.2 3B (LoRA) | Perplexity & Coherence | Fine-tuned across 5K+ turns | ~2.2 GB RAM (Quantized GGUF) |
| **Emotion NLP** | DistilBERT Sequence Classifier | **Accuracy** | **91.39%** | ~260 MB |
| **Emotion NLP** | DistilBERT Sequence Classifier | **Macro F1-Score** | **91.40%** | ~260 MB |
| **ASR (Speech)** | Vosk Small EN-US | WER / Latency | Offline, Real-time | ~45 MB RAM |
| **RAG Embeddings** | all-MiniLM-L6-v2 | Top-1 Semantic Retrieval | cosine sim > 0.82 | ~90 MB RAM |

---

## 🗂️ Project Directory Structure

```
Sentia_AI/
├── api/                        # API route configurations and wrappers
├── core/
│   ├── emotion.py              # DistilBERT classifier & keyword safety gates
│   ├── llm_engine.py           # Local SLM inference engine & text sanitizer
│   ├── context_builder.py      # Emotional tone mapping & context synthesis
│   ├── voice_fusion_logic.py   # Acoustic + semantic multimodal fusion logic
│   ├── stt_engine.py           # Vosk offline speech recognition engine
│   ├── voice_detection.py      # Audio feature extraction & acoustic classification
│   ├── router.py               # Multimodal pipeline dispatcher
│   └── main_pipeline.py        # End-to-end turn orchestrator
├── database/
│   ├── memory.db               # SQLite relational storage for conversation history
│   ├── faiss.index             # FAISS dense vector index
│   └── id_map.pkl              # ID mapping for vector entries
├── dataset/
│   ├── dataset_gen.py          # Data ingestion, cleaning, deduplication & balancing
│   ├── balanced_data.jsonl     # Balanced training corpus for emotion recognition
│   └── distil.jsonl            # Distilled raw dialogue dataset
├── frontend/                   # Responsive interactive web interface
├── memory/
│   └── memory.py               # Episodic memory manager & FAISS vector search
├── models/
│   ├── emotion_model/          # DistilBERT fine-tuned model checkpoint
│   ├── embedding_model/        # SentenceTransformer all-MiniLM-L6-v2
│   └── vosk-model-small-en-us/ # Offline ASR model
├── app.py                      # Flask REST API server with audio conversion
└── requirements.txt            # Python dependencies
```

---

## 🚀 Getting Started

### 1. Prerequisites
* Python 3.10 or higher
* [FFmpeg](https://ffmpeg.org/) installed and available in system `PATH`
* [Ollama](https://ollama.ai/) installed with the adapted SLM (`sentia_ft_2`)

### 2. Installation
```bash
git clone https://github.com/nmashokkumar/sentia_emotional_ai.git
cd sentia_emotional_ai

# Create and activate virtual environment
python -m venv venv
# Windows:
venv\Scripts\activate
# Linux/macOS:
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt
```

### 3. Model Setup
Download the required base models or load checkpoints directly:
```bash
# Emotion model is automatically fetched from Hugging Face on first boot
# To preload embedding weights:
python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('all-MiniLM-L6-v2', cache_folder='models/embedding_model')"
```

### 4. Running the Application
```bash
python app.py
```
The Flask server will launch on `http://127.0.0.1:5000`.

---

## 🛡️ Edge & Mission-Critical Architecture Relevance

SENTIA was designed with key engineering constraints that align directly with edge deployment principles:
1. **Zero External API Dependency**: Runs completely offline using quantized local SLMs and on-premise neural models, ensuring uninterrupted functionality under severed network conditions.
2. **Deterministic Fallbacks**: Multi-tier safety gates ensure that if model confidence drops below threshold ($< 0.40$), rule-based deterministic guards prevent hallucination and maintain predictable operational behavior.
3. **Low-Compute Footprint**: Designed to execute efficiently on commodity CPU hardware without demanding high-wattage GPUs.

---

## 👨‍💻 Author

**Ashok Kumar N**  
*AI & Machine Learning Engineer*  
*Specializing in Small Language Models (SLMs), Parameter-Efficient Fine-Tuning (PEFT/LoRA), and Edge AI Systems.*  
* [GitHub](https://github.com/nmashokkumar) • [LinkedIn](https://linkedin.com/in/nmashokkumar) • [Portfolio](https://nmashokkumarportfolio.netlify.app)
