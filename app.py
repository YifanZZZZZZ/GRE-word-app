# word_quiz_app/app.py
from flask import Flask, request, jsonify, render_template
import pandas as pd
import random
from collections import defaultdict
import datetime
import os
import math

app = Flask(__name__)

# ---------------- Config ----------------
NEW_WORDS_EXCEL = os.environ.get("NEW_WORDS_EXCEL", "./daily_new_words.xlsx")

# ---------------- Global State ----------------
asked_words = []
wrong_words = []
retry_mode = False
retry_pool = []
current_mode = "all"  # 'all' or 'review'
current_section = None  # e.g., "section_1.txt" or "new_words_2025-08-10.txt"
section_words = []
total_in_round = 0
current_review_log_base = None
meanings_only_mode = False   # <<< NEW: true when quizzing a new_words_* section

log_dir = "wrong_logs"
section_dir = "sections"
os.makedirs(log_dir, exist_ok=True)
os.makedirs(section_dir, exist_ok=True)

shown_synonyms_cache = {}

# ---------------- Load Data ----------------
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
                word = str(word).strip()
                if word:
                    word_to_meanings[word].append(meaning)
                    meaning_to_words[meaning].append(word)

    return word_to_meanings, meaning_to_words, list(set(all_meanings))

word_to_meanings, meaning_to_words, all_meanings = load_word_meaning_map("./GRE同义词.xlsx")
all_words = list(word_to_meanings.keys())

# ---------------- Section Handling ----------------
def create_sections_if_not_exist():
    existing_files = [f for f in os.listdir(section_dir) if f.endswith(".txt")]
    if any(f.startswith("section_") for f in existing_files):
        return

    shuffled = all_words[:]
    random.shuffle(shuffled)
    num_sections = math.ceil(len(shuffled) / 300)
    for i in range(num_sections):
        start_idx = i * 300
        end_idx = min((i + 1) * 300, len(shuffled))
        section_list = shuffled[start_idx:end_idx]
        with open(os.path.join(section_dir, f"section_{i+1}.txt"), "w", encoding="utf-8") as f:
            for w in section_list:
                f.write(w + "\n")

def load_section_words(section_name):
    filepath = os.path.join(section_dir, section_name)
    if not os.path.exists(filepath):
        return []
    with open(filepath, "r", encoding="utf-8") as f:
        return [line.strip() for line in f if line.strip()]

def list_all_sections():
    files = [f for f in os.listdir(section_dir) if f.endswith(".txt")]
    files.sort()
    return files

def section_base_name():
    if current_section:
        base = os.path.splitext(os.path.basename(current_section))[0]
        return base or "all"
    return "all"

def create_new_words_section_for_date(excel_path, date_str, first_column_is_meaning=True):
    try:
        df = pd.read_excel(excel_path, sheet_name=date_str, header=None)
    except Exception as e:
        return None, f"Sheet '{date_str}' not found or cannot be read: {e}"

    if first_column_is_meaning and df.shape[1] >= 2:
        values = df.iloc[:, 1:].values.flatten()
    else:
        values = df.values.flatten()

    words = []
    for v in values:
        if pd.notna(v):
            w = str(v).strip()
            if w:
                words.append(w)

    words = sorted(set(words))
    if not words:
        return None, "No words found in the sheet."

    filename = f"new_words_{date_str}.txt"
    filepath = os.path.join(section_dir, filename)
    with open(filepath, "w", encoding="utf-8") as f:
        for w in words:
            f.write(w + "\n")

    return filename, len(words)

create_sections_if_not_exist()

# ---------------- Routes ----------------
@app.route("/")
def index():
    return render_template("index.html")

@app.route("/api/sections")
def list_sections():
    return jsonify({"sections": list_all_sections()})

@app.route("/api/create_today_section", methods=["POST"])
def create_today_section():
    """
    Body (all optional):
      - date: "YYYY-MM-DD" (defaults to today)
      - excel_path: path to the daily Excel (defaults to NEW_WORDS_EXCEL)
      - first_column_is_meaning: bool (defaults True)
    """
    data = request.json or {}
    date_str = data.get("date") or datetime.date.today().strftime("%Y-%m-%d")
    excel_path = data.get("excel_path", NEW_WORDS_EXCEL)
    first_col_meaning = data.get("first_column_is_meaning", True)

    filename, result = create_new_words_section_for_date(
        excel_path, date_str, first_column_is_meaning=first_col_meaning
    )
    if filename is None:
        return jsonify({"ok": False, "error": result}), 400

    return jsonify({"ok": True, "section": filename, "count": result})

@app.route("/api/set_mode", methods=["POST"])
def set_mode():
    global asked_words, wrong_words, retry_mode, retry_pool, current_mode, current_section, section_words
    global total_in_round, current_review_log_base, meanings_only_mode
    global word_to_meanings, meaning_to_words, all_meanings 

    data = request.json or {}
    mode = data.get("mode")
    filename = data.get("log")
    section_file = data.get("section")

    asked_words = []
    retry_pool = []
    retry_mode = False
    section_words = []
    current_section = None
    total_in_round = 0
    current_review_log_base = None
    meanings_only_mode = False

    if mode == "review" and filename:
        # REVIEW MODE
        log_path = os.path.join(log_dir, filename)
        if os.path.exists(log_path):
            with open(log_path, "r", encoding="utf-8") as f:
                lines = f.readlines()
            reviewed_words = [line.strip().split(": ")[1] for line in lines if line.startswith("Word: ")]
        else:
            reviewed_words = []

        retry_pool = list(sorted(set(reviewed_words)))
        retry_mode = True
        current_mode = "review"
        total_in_round = len(retry_pool)
        current_review_log_base = os.path.splitext(os.path.basename(filename))[0]

        # 🔹 Reload mapping based on log file name
        if filename.startswith("new_words_"):
            excel_path = "./daily_new_words.xlsx"
            meanings_only_mode = True   # also enable meanings-only for wrong word logs from new_words
        else:
            excel_path = "./GRE同义词.xlsx"
        word_to_meanings, meaning_to_words, all_meanings = load_word_meaning_map(excel_path)

    else:
        # ALL / SECTION MODE
        current_mode = "all"
        if section_file:
            if section_file.startswith("new_words"):
                excel_path = "./daily_new_words.xlsx"
                meanings_only_mode = True
            else: 
                excel_path = "./GRE同义词.xlsx"
            word_to_meanings, meaning_to_words, all_meanings = load_word_meaning_map(excel_path)
            section_words = load_section_words(section_file)
            current_section = section_file
            total_in_round = len(section_words)
        else:
            total_in_round = len(all_words)

    return jsonify({
        "message": f"Mode set to {current_mode}, Section: {current_section}",
        "total": total_in_round,
        "meanings_only": meanings_only_mode
    })

@app.route("/api/logs")
def list_logs():
    files = [f for f in os.listdir(log_dir) if f.endswith(".txt")]
    files.sort()
    return jsonify({"logs": files})

@app.route("/api/question")
def question():
    global asked_words, retry_mode, retry_pool, shown_synonyms_cache, section_words, total_in_round, meanings_only_mode

    # PRIORITIZE REVIEW POOL WHEN IN REVIEW MODE
    if retry_mode:
        pool = retry_pool
    elif current_section and section_words:
        pool = section_words
    else:
        pool = all_words

    if not pool or len(asked_words) >= len(pool):
        return jsonify({"done": True, "retry_mode": retry_mode})

    remaining = list(set(pool) - set(asked_words))
    if not remaining:
        return jsonify({"done": True, "retry_mode": retry_mode})

    word = random.choice(remaining)
    meanings = word_to_meanings.get(word, [])
    print(word_to_meanings)
    print(meanings)

    # Build options
    distractor_meanings = [m for m in all_meanings if m not in meanings]
    distractor_meanings = random.sample(distractor_meanings, min(4, len(distractor_meanings)))
    meaning_options = list(set(meanings + distractor_meanings))
    random.shuffle(meaning_options)

    # Meanings-only mode: no synonyms at all
    if meanings_only_mode:
        correct_synonyms_shown = []
        synonym_options = []
        shown_synonyms_cache[word] = set()
    else:
        # Full mode: include synonyms as before
        synonym_candidates = []
        for m in meanings:
            synonym_candidates += [w for w in meaning_to_words.get(m, []) if w != word]
        synonym_candidates = list(sorted(set(synonym_candidates)))

        correct_k = 0
        if synonym_candidates:
            correct_k = random.choice([1, 2]) if len(synonym_candidates) >= 2 else 1
        correct_synonyms_shown = random.sample(synonym_candidates, correct_k) if correct_k else []

        unrelated_words = [w for w in all_words if (w not in synonym_candidates and w != word)]
        distractor_synonyms = random.sample(unrelated_words, min(4, len(unrelated_words)))
        synonym_options = list(set(correct_synonyms_shown + distractor_synonyms))
        random.shuffle(synonym_options)
        shown_synonyms_cache[word] = set(correct_synonyms_shown)

    asked_words.append(word)

    return jsonify({
        "word": word,
        "meanings": meanings,
        "meaning_options": meaning_options,
        "synonyms": correct_synonyms_shown,   # may be []
        "synonym_options": synonym_options,   # may be []
        "meanings_only": meanings_only_mode,  # <<< tell the UI to hide the synonyms block
        "progress": len(asked_words),
        "total": total_in_round,
        "done": False,
        "retry_mode": retry_mode
    })

@app.route("/api/submit", methods=["POST"])
def submit():
    global wrong_words, retry_mode, retry_pool, shown_synonyms_cache, current_review_log_base, meanings_only_mode

    data = request.json or {}
    word = data.get("word", "")
    selected_synonyms = set(data.get("selected_synonyms", []))
    selected_meanings = set(data.get("selected_meanings", []))

    correct_meanings = set(word_to_meanings.get(word, []))
    correct_synonyms_shown = shown_synonyms_cache.get(word, set())

    # Check correctness
    if meanings_only_mode:
        is_correct = (selected_meanings == correct_meanings)
    else:
        is_correct = (selected_meanings == correct_meanings) and (selected_synonyms == correct_synonyms_shown)

    if not is_correct:
        today = datetime.date.today().strftime("%Y-%m-%d")
        base = current_review_log_base if (retry_mode and current_review_log_base) else section_base_name()
        log_filename = os.path.join(log_dir, f"{base}_{today}.txt")
        with open(log_filename, "a", encoding="utf-8") as f:
            f.write(f"Word: {word}\n")
            f.write(f"Correct meanings: {', '.join(sorted(correct_meanings))}\n")
            f.write(f"Your meanings: {', '.join(sorted(selected_meanings))}\n")
            if not meanings_only_mode:
                f.write(f"Correct synonyms (shown): {', '.join(sorted(correct_synonyms_shown))}\n")
                f.write(f"Your synonyms: {', '.join(sorted(selected_synonyms))}\n")
            f.write("---\n")

    # Keep retry_pool consistent
    if retry_mode:
        if is_correct and word in retry_pool:
            retry_pool.remove(word)
        elif not is_correct and word not in retry_pool:
            retry_pool.append(word)
    else:
        if not is_correct and word not in wrong_words:
            wrong_words.append(word)

    print(correct_meanings)

    return jsonify({
        "result": is_correct,
        "correct_meanings": list(sorted(correct_meanings)),
        "correct_synonyms": list(sorted(correct_synonyms_shown)) if not meanings_only_mode else []
    })

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5001))
    app.run(debug=False, host="0.0.0.0", port=port)
