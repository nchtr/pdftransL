# Дообучение локальной модели на вашей памяти переводов

Каждый переведённый документ и каждая ручная правка оседают в
translation memory. Когда там накопится несколько тысяч пар — это уже
готовый датасет: можно дообучить локальную модель под ваш домен и
стиль, и она станет заметно точнее именно на ваших статьях.

## Шаг 1. Выгрузить датасет

```bash
pdftransl tm export tm_dataset.jsonl
```

Получится JSONL вида
`{"source": ..., "target": ..., "src_lang": "en", "tgt_lang": "ru", "origin": "human"}`.
Пары с `origin: "human"` — самые ценные (их правил человек); имеет
смысл продублировать их в датасете 2–3 раза.

## Шаг 2. Привести к формату чата

```python
import json

SYSTEM = ("You are a professional translator of scientific papers "
          "from English to Russian. Translate preserving terminology.")

with open("tm_dataset.jsonl") as fin, open("train.jsonl", "w") as fout:
    for line in fin:
        row = json.loads(line)
        weight = 3 if row["origin"] == "human" else 1
        example = {"messages": [
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": row["source"]},
            {"role": "assistant", "content": row["target"]},
        ]}
        for _ in range(weight):
            fout.write(json.dumps(example, ensure_ascii=False) + "\n")
```

## Шаг 3. LoRA через unsloth (одна GPU или Apple Silicon через MLX)

```python
# pip install unsloth
from unsloth import FastLanguageModel
from trl import SFTTrainer
from datasets import load_dataset

model, tokenizer = FastLanguageModel.from_pretrained(
    "unsloth/Qwen2.5-7B-Instruct", load_in_4bit=True, max_seq_length=4096,
)
model = FastLanguageModel.get_peft_model(model, r=16, lora_alpha=32)

dataset = load_dataset("json", data_files="train.jsonl", split="train")
dataset = dataset.map(lambda ex: {
    "text": tokenizer.apply_chat_template(ex["messages"], tokenize=False)
})

SFTTrainer(
    model=model, tokenizer=tokenizer, train_dataset=dataset,
    dataset_text_field="text", max_seq_length=4096,
    args=dict(per_device_train_batch_size=2, gradient_accumulation_steps=8,
              num_train_epochs=2, learning_rate=2e-4, output_dir="lora_out"),
).train()

model.save_pretrained_gguf("qwen-transl", tokenizer, quantization_method="q4_k_m")
```

(Вариант через axolotl — та же идея, конфиг в YAML; см. их README.)

## Шаг 4. Завести результат в Ollama

```bash
cat > Modelfile <<'EOF'
FROM ./qwen-transl/unsloth.Q4_K_M.gguf
PARAMETER num_ctx 16384
EOF
ollama create qwen-transl -f Modelfile
```

И подключить к pdftransl:

```bash
pdftransl translate статья.pdf --provider ollama --model qwen-transl
```

## Когда это стоит делать

- В TM больше ~2–5 тысяч пар по одному домену — меньше просто не даст
  эффекта, RAG-примеры справятся не хуже.
- Терминология стабильно правится руками в одну и ту же сторону —
  модель это выучит.
- Проверяйте на отложенной выборке: возьмите 50 пар, не показывайте их
  тренировке, сравните перевод до/после (хоть глазами, хоть
  `--score`).
