# word_quiz_app/app.py
from flask import Flask, request, jsonify, render_template
import pandas as pd
import random
from collections import defaultdict
import datetime
import os

app = Flask(__name__)

# Global state instead of session
asked_words = []
wrong_words = []
retry_mode = False
retry_pool = []
current_mode = "all"  # 'all' or 'review'
log_dir = "wrong_logs"
os.makedirs(log_dir, exist_ok=True)

# Load the Excel data
def load_word_meaning_map(file_path):
    df = pd.read_excel(file_path, header=None)
    word_to_meanings = defaultdict(list)
    meaning_to_words = defaultdict(list)
    all_meanings = []

    for _, row in df.iterrows():
        meaning = row[0]
        all_meanings.append(meaning)
        for word in row[1:]:
            if pd.notna(word):
                word = word.strip()
                word_to_meanings[word].append(meaning)
                meaning_to_words[meaning].append(word)

    return word_to_meanings, meaning_to_words, list(set(all_meanings))

# Load on startup
word_to_meanings, meaning_to_words, all_meanings = load_word_meaning_map("/Users/zhangyifan/Desktop/WordTest/GRE同义词.xlsx")
all_words = list(word_to_meanings.keys())
total_words = len(all_words)

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/api/set_mode", methods=["POST"])
def set_mode():
    global asked_words, wrong_words, retry_mode, retry_pool, current_mode
    data = request.json
    mode = data.get("mode")
    filename = data.get("log")
    asked_words = []
    retry_pool = []
    retry_mode = False

    if mode == "review" and filename:
        with open(os.path.join(log_dir, filename), "r", encoding="utf-8") as f:
            lines = f.readlines()
        reviewed_words = [line.strip().split(": ")[1] for line in lines if line.startswith("Word: ")]
        retry_pool = list(set(reviewed_words))
        retry_mode = True
        current_mode = "review"
    else:
        current_mode = "all"

    return jsonify({"message": f"Mode set to {current_mode}"})

@app.route("/api/logs")
def list_logs():
    files = [f for f in os.listdir(log_dir) if f.endswith(".txt")]
    return jsonify({"logs": files})

@app.route("/api/question")
def question():
    global asked_words, retry_mode, retry_pool
    pool = retry_pool if retry_mode else all_words

    if len(asked_words) >= len(pool):
        return jsonify({"done": True, "retry_mode": retry_mode})

    remaining = list(set(pool) - set(asked_words))
    word = random.choice(remaining)
    meanings = word_to_meanings[word]

    # Select 1-2 synonyms from the same meaning group
    synonym_candidates = []
    for m in meanings:
        synonym_candidates += [w for w in meaning_to_words[m] if w != word]
    correct_synonyms = random.sample(synonym_candidates, min(len(synonym_candidates), random.choice([1, 2])))

    # Create distractor meanings (not associated with this word)
    distractor_meanings = [m for m in all_meanings if m not in meanings]
    distractor_meanings = random.sample(distractor_meanings, min(4, len(distractor_meanings)))
    meaning_options = list(set(meanings + distractor_meanings))
    random.shuffle(meaning_options)

    # Create distractor synonyms (words that don’t share the same meaning)
    unrelated_words = [w for w in all_words if w not in synonym_candidates and w != word]
    distractor_synonyms = random.sample(unrelated_words, min(4, len(unrelated_words)))
    synonym_options = list(set(correct_synonyms + distractor_synonyms))
    random.shuffle(synonym_options)

    asked_words.append(word)

    return jsonify({
        "word": word,
        "meanings": meanings,
        "synonyms": correct_synonyms,
        "meaning_options": meaning_options,
        "synonym_options": synonym_options,
        "progress": len(asked_words),
        "total": len(pool),
        "done": False,
        "retry_mode": retry_mode
    })

@app.route("/api/submit", methods=["POST"])
def submit():
    global wrong_words, retry_mode, retry_pool

    data = request.json
    word = data["word"]
    selected_synonyms = set(data["selected_synonyms"])
    selected_meanings = set(data["selected_meanings"])

    correct_synonyms = set()
    for m in word_to_meanings[word]:
        correct_synonyms.update([w for w in meaning_to_words[m] if w != word])

    correct_meanings = set(word_to_meanings[word])

    is_correct = (selected_synonyms == correct_synonyms) and (selected_meanings == correct_meanings)

    if not is_correct:
        today = datetime.datetime.now().strftime("%Y-%m-%d")
        log_filename = os.path.join(log_dir, f"wrong_log_{today}.txt")
        with open(log_filename, "a", encoding="utf-8") as f:
            f.write(f"Word: {word}\n")
            f.write(f"Correct meanings: {', '.join(correct_meanings)}\n")
            f.write(f"Correct synonyms: {', '.join(correct_synonyms)}\n")
            f.write(f"Your meanings: {', '.join(selected_meanings)}\n")
            f.write(f"Your synonyms: {', '.join(selected_synonyms)}\n")
            f.write("---\n")

    if retry_mode:
        if is_correct and word in retry_pool:
            retry_pool.remove(word)
        elif not is_correct and word not in retry_pool:
            retry_pool.append(word)
    else:
        if not is_correct and word not in wrong_words:
            wrong_words.append(word)

    return jsonify({
        "result": is_correct,
        "correct_meanings": list(correct_meanings),
        "correct_synonyms": list(correct_synonyms)
    })

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5001))
    app.run(debug=False, host="0.0.0.0", port=port)
