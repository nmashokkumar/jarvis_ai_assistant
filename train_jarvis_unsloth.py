"""
Fine-tuning Llama 3.2 3B Instruct as JARVIS with Unsloth AI + LoRA
Ready to run in Google Colab (Free T4 GPU, ~10-12 minutes)
"""

# Step 1: Install Unsloth and dependencies
# !pip install "unsloth[colab-new] @ git+https://github.com/unslothai/unsloth.git"
# !pip install --no-deps "xformers<0.0.29" trl peft accelerate bitsandbytes

import torch
from unsloth import FastLanguageModel
from datasets import load_dataset
from trl import SFTTrainer
from transformers import TrainingArguments
from unsloth.chat_templates import get_chat_template

# 1. Configuration
max_seq_length = 2048
model_name = "unsloth/Llama-3.2-3B-Instruct"

print(f"Loading {model_name} in 4-bit...")
model, tokenizer = FastLanguageModel.from_pretrained(
    model_name=model_name,
    max_seq_length=max_seq_length,
    dtype=None,  # Auto-detection
    load_in_4bit=True,
)

# 2. Add LoRA Adapters
model = FastLanguageModel.get_peft_model(
    model,
    r=16,
    target_modules=[
        "q_proj", "k_proj", "v_proj", "o_proj",
        "gate_proj", "up_proj", "down_proj",
    ],
    lora_alpha=16,
    lora_dropout=0,  # Optimized for 0 with Unsloth
    bias="none",
    use_gradient_checkpointing="unsloth",
    random_state=3407,
)

# 3. Apply Llama 3.2 Chat Template
tokenizer = get_chat_template(
    tokenizer,
    chat_template="llama-3.2",
)

def formatting_prompts_func(examples):
    convos = examples["messages"]
    texts = [
        tokenizer.apply_chat_template(convo, tokenize=False, add_generation_prompt=False)
        for convo in convos
    ]
    return {"text": texts}

# 4. Load the 250-sample dataset
dataset = load_dataset("json", data_files="jarvis_llama32_train_250.jsonl", split="train")
dataset = dataset.map(formatting_prompts_func, batched=True)

# 5. Training Arguments
trainer = SFTTrainer(
    model=model,
    tokenizer=tokenizer,
    train_dataset=dataset,
    dataset_text_field="text",
    max_seq_length=max_seq_length,
    dataset_num_proc=2,
    packing=False,
    args=TrainingArguments(
        per_device_train_batch_size=2,
        gradient_accumulation_steps=4,
        warmup_steps=10,
        max_steps=-1,
        num_train_epochs=3,
        learning_rate=2e-4,
        fp16=not torch.cuda.is_bf16_supported(),
        bf16=torch.cuda.is_bf16_supported(),
        logging_steps=10,
        optim="adamw_8bit",
        weight_decay=0.01,
        lr_scheduler_type="cosine",
        seed=3407,
        output_dir="outputs",
    ),
)

# 6. Train!
print("Starting fine-tuning...")
trainer.train()

# 7. Save directly as GGUF for Ollama
print("Exporting model to GGUF (q4_k_m) for Ollama...")
model.save_pretrained_gguf(
    "jarvis-llama3.2-3b-q4",
    tokenizer,
    quantization_method="q4_k_m"
)

print("Training & GGUF export complete! File saved as jarvis-llama3.2-3b-q4-unsloth.Q4_K_M.gguf")
