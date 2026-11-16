# Quality prompts - Z Fold 8 benchmark

Fixed set of 10 prompts in 4 categories (reasoning, code, summarisation, factual) plus instruction following.
`run_quality_nothink.sh` (via `quality_nothink.py extract`) and the deprecated `run_bench.sh quality` read the text between
```` ```text ```` and ```` ``` ```` in each `### Pnn` section. Do not change the prompts once testing has started.
Fixed parameters: temp 0, seed 42, context 8192, the model's own chat template (`--jinja`);
n_predict 2048 in `run_quality_nothink.sh` (1024 in the deprecated llama-completion run).

## Rubric (1-5, blind grading)
| Score | Meaning |
|---|---|
| 5 | Correct, complete and clear; nothing to fix |
| 4 | Correct with minor formatting issues or small omissions |
| 3 | Partly correct; a relevant error but still usable |
| 2 | Mostly wrong or off-task |
| 1 | Wrong, incoherent, refusal or empty |

Extra criteria (record separately, yes/no): answers in the requested language, follows the requested format.
Blind grading: answers are renamed with a random code (e.g. R07) before being read; the code-to-model key is only opened at the end.

---

### P01
Category: reasoning
Expected answer: 07:40
```text
A train leaves London at 05:10 and the journey to York takes 1 hour and 55 minutes. In York there is a 12-minute stop and then another 23 minutes to the final destination. What time does it arrive at the final destination? Explain your reasoning step by step and give the final answer on a line starting with "Answer:".
```

### P02
Category: reasoning
Expected: 5 minutes (each machine makes 1 widget in 5 minutes).
```text
If 5 machines take 5 minutes to make 5 widgets, how long would it take 100 machines to make 100 widgets? Explain briefly and give the final answer on a line starting with "Answer:".
```

### P03
Category: reasoning
Expected answer: 9 sheep
```text
A farmer has 17 sheep. All but 9 die. How many sheep are left alive? Answer in one sentence and justify it.
```

### P04
Category: code
Expected: correct function, O(n), handles empty and single-value lists.
```text
Write a Python function `second_largest(nums: list[int]) -> int | None` that returns the second largest DISTINCT value in the list, or None if it does not exist. It must run in O(n) and must not sort. Include 4 assert-based tests covering edge cases.
```

### P05
Category: code
Expected: valid bash script using `find`/`du` or equivalent, handles the missing argument.
```text
Write a bash script that takes a folder as an argument and lists the 5 largest files inside it (recursively), with human-readable sizes (e.g. 1.2G). If the argument is missing, it must print a usage message and exit with code 1. Comment the main lines.
```

### P06
Category: summarisation
Expected: 3 bullet points faithful to the text, no invented numbers.
```text
Summarise the following text in exactly 3 short bullet points, without adding any information that is not in the text:

"The city council announced that the new electric bus line will connect the city centre to the hospital from March. The route will have 14 stops and a frequency of 12 minutes at peak times. The investment, partly funded by European funds, also includes fast chargers at the terminal. The opposition criticises the lack of night service and asks for the timetable to be extended until midnight. The council says it will assess demand during the first six months before deciding."
```

### P07
Category: summarisation
Expected: 2 sentences, mentions the memory vs speed trade-off and quantization.
```text
Summarize the following in exactly two sentences for a non-technical reader:

"Running large language models on phones is limited mainly by memory bandwidth and capacity rather than raw compute. Quantization reduces each weight from 16 bits to around 4 or 5 bits, which shrinks the model and speeds up generation, at a small cost in accuracy. Longer conversations need a bigger key-value cache, so a phone that can load a model may still fail when the context grows. Sustained use also heats the device, and the chip lowers its clock speed to stay within thermal limits."
```

### P08
Category: factual (hallucination check)
Expected: the Tagus (Tejo), which rises in Spain (Sierra de Albarracín); the Douro rises in Spain (Sierra de Urbión).
```text
What is the longest river that flows through Portugal, and where does it rise? And where does the Douro river rise? Answer in 2-3 sentences; if you are not sure about any fact, say so explicitly.
```

### P09
Category: factual
Expected: 1905; Nobel Prize in Physics for the photoelectric effect, awarded for 1921.
```text
In what year did Albert Einstein publish his paper on special relativity, and for what work did he receive the Nobel Prize in Physics (and in which year)? Answer in at most three sentences.
```

### P10
Category: instruction following / format
Expected: valid JSON, 3 entries, exact keys.
```text
Return ONLY a valid JSON object (no text before or after) with the key "tips", containing a list of 3 objects with the keys "title" and "description", with tips for saving battery on a phone.
```
