import os
import json
import re
import unicodedata
from difflib import SequenceMatcher
import streamlit as st
from PIL import Image
from bs4 import BeautifulSoup
from highlight_component import highlight_text

# =========================================================
# Utility functions
# =========================================================
@st.cache_data
def load_json_data(json_path):
    with open(json_path, "r", encoding="utf-8") as f:
        return json.load(f)


def find_selected_he_jsons(base_dir):
    json_files = []
    for root, _, files in os.walk(base_dir):
        if os.path.basename(root) == "selected_he":
            for f in files:
                if f.endswith("_captions.json"):
                    json_files.append(os.path.join(root, f))
    return sorted(json_files)


def is_image_file(filename):
    ext = os.path.splitext(filename)[1].lower()
    return ext in {".jpg", ".jpeg", ".png", ".tif", ".tiff"}


def parse_paper_metadata(nxml_path):
    if not os.path.exists(nxml_path):
        return {"title": "Unknown", "authors": [], "doi": "N/A", "journal": "N/A", "year": "N/A"}

    with open(nxml_path, "r", encoding="utf-8") as f:
        soup = BeautifulSoup(f, "xml")

    title = soup.find("article-title")
    title = title.get_text(strip=True) if title else "Untitled"

    authors = []
    for contrib in soup.find_all("contrib", {"contrib-type": "author"}):
        name = contrib.find("name")
        if name:
            given = name.find("given-names")
            surname = name.find("surname")
            full_name = " ".join(
                filter(None, [given.get_text() if given else "", surname.get_text() if surname else ""])
            )
            authors.append(full_name.strip())

    doi_tag = soup.find("article-id", {"pub-id-type": "doi"})
    doi = doi_tag.get_text(strip=True) if doi_tag else "N/A"
    journal_tag = soup.find("journal-title")
    journal = journal_tag.get_text(strip=True) if journal_tag else "N/A"
    year_tag = soup.find("year")
    year = year_tag.get_text(strip=True) if year_tag else "N/A"

    return {"title": title, "authors": authors, "doi": doi, "journal": journal, "year": year}


# =========================================================
# Highlighting helper functions (for verbatims)
# =========================================================

def normalize_for_matching(s):
    if not s:
        return ""
    s = unicodedata.normalize("NFKD", s)
    s = s.replace("‘", "'").replace("’", "'").replace("“", '"').replace("”", '"')
    s = s.replace("–", "-").replace("—", "-").replace("−", "-")
    s = re.sub(r"\s+", " ", s.strip())
    return s.lower()


def fuzzy_find(substring, text, threshold=0.85):
    matches = []
    sub_norm = normalize_for_matching(substring)
    text_norm = normalize_for_matching(text)
    len_sub = len(sub_norm)
    for i in range(max(0, len(text_norm) - len_sub + 1)):
        window = text_norm[i:i + len_sub]
        ratio = SequenceMatcher(None, sub_norm, window).ratio()
        if ratio >= threshold:
            start = int(i / len(text_norm) * len(text))
            end = int((i + len_sub) / len(text_norm) * len(text))
            matches.append((start, end))
    return matches


def highlight_verbatims(text, verbatims, color="#a7d8ff"):
    if not verbatims or not text:
        return text

    matches = []
    for v in verbatims:
        v = v.strip()
        if not v:
            continue
        exact_pat = re.escape(normalize_for_matching(v))
        norm_text = normalize_for_matching(text)
        for m in re.finditer(exact_pat, norm_text, flags=re.IGNORECASE):
            start = int(m.start() / len(norm_text) * len(text))
            end = int(m.end() / len(norm_text) * len(text))
            matches.append((start, end))
        if not matches:
            matches.extend(fuzzy_find(v, text))

    if not matches:
        return text

    matches.sort()
    merged = []
    for s, e in matches:
        if not merged or s > merged[-1][1]:
            merged.append([s, e])
        else:
            merged[-1][1] = max(merged[-1][1], e)

    out = []
    last = 0
    for s, e in merged:
        out.append(text[last:s])
        out.append(f"<mark style='background-color:{color}; padding:2px 4px; border-radius:4px'>{text[s:e]}</mark>")
        last = e
    out.append(text[last:])
    return "".join(out)


# =========================================================
# Streamlit App
# =========================================================

st.set_page_config(page_title="Pathology Caption Review", layout="wide")

st.title("Histopathology Figures Caption Enrichment Review Tool")

base_dir = "data/pubmed_fulltexts"

# --- Load data ---
json_files = find_selected_he_jsons(base_dir)
if not json_files:
    st.error("No *_captions.json found under 'selected_he' folders.")
    st.stop()

# Dropdown menu
selected_json = st.selectbox("Select article JSON to review", json_files, key="json_selector")

# --- Ensure app reruns when a new file is chosen ---
if "prev_json" not in st.session_state:
    st.session_state.prev_json = None

if st.session_state.prev_json != selected_json:
    st.session_state.prev_json = selected_json
    st.session_state.prev_json = selected_json
    st.rerun()

# Now load data based on selected file
data = load_json_data(selected_json)

# --- Metadata extraction ---
pmc_folder = os.path.basename(os.path.dirname(os.path.dirname(os.path.dirname(selected_json))))
nxml_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(selected_json))), pmc_folder)
nxml_path = next((os.path.join(nxml_dir, f) for f in os.listdir(nxml_dir) if f.endswith(".nxml")), None)
metadata = parse_paper_metadata(nxml_path) if nxml_path else {"title": "Untitled", "authors": [], "doi": "N/A", "journal": "N/A", "year": "N/A"}

# --- Annotations file path ---
def annotations_path_for(selected_json_path: str) -> str:
    root_dir = os.path.dirname(selected_json_path)
    ann_base = os.path.basename(selected_json_path).replace("_captions.json", "_annotations.json")
    return os.path.join(root_dir, ann_base)

ANNOT_PATH = annotations_path_for(selected_json)
annotations = json.load(open(ANNOT_PATH, "r", encoding="utf-8")) if os.path.exists(ANNOT_PATH) else {}

# --- Metadata header ---
doi_html = f"<a href='https://doi.org/{metadata['doi']}' target='_blank'>{metadata['doi']}</a>" if metadata['doi'] != "N/A" else "N/A"
st.markdown(f"""
<div style="background-color:#f0f2f6;padding:15px;border-radius:10px;margin-bottom:20px">
  <h3 style="margin-bottom:5px;">{metadata['title']}</h3>
  <p><strong>Authors:</strong> {', '.join(metadata['authors']) if metadata['authors'] else 'Unknown'}</p>
  <p><strong>Journal:</strong> {metadata['journal']} ({metadata['year']})</p>
  <p><strong>DOI:</strong> {doi_html}</p>
</div>
""", unsafe_allow_html=True)


# =========================================================
# Review Loop
# =========================================================
for i, img_name in enumerate(sorted(data.keys())):
    if not is_image_file(img_name):
        continue

    info = data[img_name]
    st.markdown("---")
    cols = st.columns([1, 2])

    # === LEFT: Image + Controls ===
    with cols[0]:
        img_path = os.path.join(os.path.dirname(selected_json), img_name)
        if os.path.exists(img_path):
            st.image(Image.open(img_path), caption=img_name, use_column_width=True)

        # --- Rating (numbers 1-5) ---
        st.markdown("#### Caption Enrichment Rating")
        rating_key = f"rating_{img_name}"
        if rating_key not in st.session_state:
            st.session_state[rating_key] = annotations.get("ratings", {}).get(img_name, 0)

        cols_rating = st.columns(5)
        for n, c in enumerate(cols_rating, start=1):
            label = str(n)
            btn_style = (
                "background-color:#007bff;color:white;font-weight:bold;"
                "border:none;border-radius:6px;padding:6px 0;width:100%;font-size:1.1em;"
                if st.session_state[rating_key] == n
                else "background-color:#f0f2f6;border:1px solid #ccc;"
                     "border-radius:6px;padding:6px 0;width:100%;font-size:1.1em;"
            )
            if c.button(label, key=f"{rating_key}_{n}", use_container_width=True):
                st.session_state[rating_key] = n

        selected_rating = st.session_state[rating_key]
        annotations.setdefault("ratings", {})[img_name] = selected_rating
        st.markdown(
            f"<p style='text-align:center;font-size:1.5em;color:#333;margin-top:10px'>"
            f"<strong>Score:</strong> {selected_rating}/5</p>",
            unsafe_allow_html=True,
        )

        # --- Custom toggle (larger & centered) ---
        toggle_key = f"relevance_{img_name}"
        if toggle_key not in st.session_state:
            st.session_state[toggle_key] = annotations.get("relevance", {}).get(img_name, True)
        relevance = st.session_state[toggle_key]

    # === RIGHT: Captions and Context ===
    with cols[1]:
        st.markdown(f"**Panel:** {info.get('panel', '—')} | **Label:** {info.get('label', '—')}")

        # --- Enriched Caption (interactive highlighter) ---
        key = f"highlight_{img_name}_{i}"
        prev_highlights = annotations.get("highlights", {}).get(img_name, [])
        new_highlights = highlight_text(
            text=info.get("enriched_caption", ""),
            key=key,
            highlights=prev_highlights,
        )

        if new_highlights and new_highlights != prev_highlights:
            annotations.setdefault("highlights", {})[img_name] = new_highlights

        # --- Original Caption ---
        st.markdown("##### Original Caption")
        st.markdown(
            highlight_verbatims(info.get("caption", ""), info.get("used_verbatims", []), color="#a7d8ff"),
            unsafe_allow_html=True,
        )

        # --- Context ---
        st.markdown("##### Context Paragraph(s)")
        st.markdown(
            highlight_verbatims(info.get("context", ""), info.get("used_verbatims", []), color="#a7d8ff"),
            unsafe_allow_html=True,
        )

# =========================================================
# Save & Export
# =========================================================
st.markdown("---")
annotations_json = json.dumps(annotations, indent=2, ensure_ascii=False)
st.download_button(
    label=f"💾 Export Review to {os.path.basename(ANNOT_PATH)}",
    data=annotations_json,
    file_name=os.path.basename(ANNOT_PATH),
    mime="application/json",
    help="Click to export all annotations as JSON",
)
