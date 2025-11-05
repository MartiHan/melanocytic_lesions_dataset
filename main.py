import os
import json
import re
import unicodedata
import streamlit as st
from PIL import Image
from bs4 import BeautifulSoup
from highlight_component import highlight_text
from streamlit_scroll_to_top import scroll_to_here

# =========================================================
# Streamlit setup
# =========================================================
st.set_page_config(page_title="Pathology Caption Review", layout="wide")
st.title("Histopathology Figures Caption Enrichment Review Tool")

# =========================================================
# Utility functions
# =========================================================
@st.cache_data(show_spinner=False)
def load_json_data(json_path):
    with open(json_path, "r", encoding="utf-8") as f:
        return json.load(f)

@st.cache_data(show_spinner=False)
def find_selected_he_jsons(base_dir):
    json_files = []
    for root, _, files in os.walk(base_dir):
        if os.path.basename(root) == "selected_he":
            json_files += [
                os.path.join(root, f)
                for f in files if f.endswith("_captions.json")
            ]
    return sorted(json_files)

@st.cache_data(show_spinner=False)
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
            full_name = " ".join(filter(None, [
                given.get_text() if given else "",
                surname.get_text() if surname else ""
            ]))
            authors.append(full_name.strip())
    doi_tag = soup.find("article-id", {"pub-id-type": "doi"})
    doi = doi_tag.get_text(strip=True) if doi_tag else "N/A"
    journal_tag = soup.find("journal-title")
    journal = journal_tag.get_text(strip=True) if journal_tag else "N/A"
    year_tag = soup.find("year")
    year = year_tag.get_text(strip=True) if year_tag else "N/A"
    return {"title": title, "authors": authors, "doi": doi, "journal": journal, "year": year}

@st.cache_data(show_spinner=False)
def load_image(image_path):
    return Image.open(image_path)

def normalize_for_matching(s):
    if not s:
        return ""
    s = unicodedata.normalize("NFKD", s)
    s = s.replace("‘", "'").replace("’", "'").replace("“", '"').replace("”", '"')
    s = s.replace("–", "-").replace("—", "-").replace("−", "-")
    s = re.sub(r"\s+", " ", s.strip())
    return s.lower()

def highlight_verbatims(text, verbatims, color="#a7d8ff"):
    if not verbatims or not text:
        return text
    matches = []
    norm_text = normalize_for_matching(text)
    for v in verbatims:
        v = v.strip()
        if not v:
            continue
        for m in re.finditer(re.escape(normalize_for_matching(v)), norm_text, flags=re.IGNORECASE):
            start = int(m.start() / len(norm_text) * len(text))
            end = int(m.end() / len(norm_text) * len(text))
            matches.append((start, end))
    if not matches:
        return text
    matches.sort()
    merged = []
    for s, e in matches:
        if not merged or s > merged[-1][1]:
            merged.append([s, e])
        else:
            merged[-1][1] = max(merged[-1][1], e)
    out, last = [], 0
    for s, e in merged:
        out.append(text[last:s])
        out.append(f"<mark style='background-color:{color}; padding:2px 4px; border-radius:4px'>{text[s:e]}</mark>")
        last = e
    out.append(text[last:])
    return "".join(out)

# =========================================================
# Load and select paper
# =========================================================
base_dir = "data/pubmed_fulltexts"
json_files = find_selected_he_jsons(base_dir)
if not json_files:
    st.error("No *_captions.json found under 'selected_he' folders.")
    st.stop()

selected_json = st.selectbox("Select article JSON to review", json_files, key="json_selector")
data = load_json_data(selected_json)

pmc_folder = os.path.basename(os.path.dirname(os.path.dirname(os.path.dirname(selected_json))))
nxml_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(selected_json))), pmc_folder)
nxml_path = next((os.path.join(nxml_dir, f) for f in os.listdir(nxml_dir) if f.endswith(".nxml")), None)
metadata = parse_paper_metadata(nxml_path) if nxml_path else {"title": "Untitled", "authors": [], "doi": "N/A", "journal": "N/A", "year": "N/A"}

def annotations_path_for(selected_json_path: str) -> str:
    root_dir = os.path.dirname(selected_json_path)
    ann_base = os.path.basename(selected_json_path).replace("_captions.json", "_annotations.json")
    return os.path.join(root_dir, ann_base)

ANNOT_PATH = annotations_path_for(selected_json)

if "active_json" not in st.session_state:
    st.session_state.active_json = selected_json
if "annotations" not in st.session_state:
    st.session_state.annotations = {}

if selected_json != st.session_state.active_json:
    prev_ann_path = annotations_path_for(st.session_state.active_json)
    if st.session_state.annotations:
        with open(prev_ann_path, "w", encoding="utf-8") as f:
            json.dump(st.session_state.annotations, f, indent=2, ensure_ascii=False)
        st.toast(f"💾 Saved annotations for {os.path.basename(prev_ann_path)}", icon="✅")

    if os.path.exists(ANNOT_PATH):
        with open(ANNOT_PATH, "r", encoding="utf-8") as f:
            st.session_state.annotations = json.load(f)
    else:
        st.session_state.annotations = {}

    st.session_state.page_index = 0
    st.session_state.active_json = selected_json
    st.rerun()

annotations = st.session_state.annotations

# =========================================================
# Paper metadata display
# =========================================================
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
# Pagination
# =========================================================
image_list = [img for img in sorted(data.keys()) if img.lower().endswith((".jpg", ".jpeg", ".png", ".tif", ".tiff"))]
if not image_list:
    st.warning("No image entries found.")
    st.stop()

IMAGES_PER_PAGE = 5
if "page_index" not in st.session_state:
    st.session_state.page_index = 0

# --- Smooth scroll only when page changes ---
if "last_page_index" not in st.session_state:
    st.session_state.last_page_index = st.session_state.get("page_index", 0)

if st.session_state.get("page_index", 0) != st.session_state.last_page_index:
    scroll_to_here(0, key="page_scroll")
    st.session_state.last_page_index = st.session_state.get("page_index", 0)


start_idx = st.session_state.page_index * IMAGES_PER_PAGE
end_idx = start_idx + IMAGES_PER_PAGE
current_images = image_list[start_idx:end_idx]

# =========================================================
# Display Figures (5 per page)
# =========================================================
for current_img in current_images:
    info = data[current_img]
    st.markdown("---")

    cols = st.columns([1, 2])
    with cols[0]:
        img_path = os.path.join(os.path.dirname(selected_json), current_img)
        if os.path.exists(img_path):
            st.image(load_image(img_path), caption=current_img, use_container_width=True)

        st.markdown("#### Caption Enrichment Rating")

        rating_key = f"rating_{current_img}"
        if rating_key not in st.session_state:
            st.session_state[rating_key] = annotations.get("ratings", {}).get(current_img, 0)

        cols_rating = st.columns(6)
        for n, c in enumerate(cols_rating[1:], start=1):
            if c.button(str(n), key=f"{rating_key}_{n}_{current_img}", use_container_width=True):
                st.session_state[rating_key] = n
                annotations.setdefault("ratings", {})[current_img] = n

        if cols_rating[0].button("🚫", key=f"{rating_key}_NR_{current_img}", use_container_width=True):
            st.session_state[rating_key] = "NR"
            annotations.setdefault("ratings", {})[current_img] = "NR"

        selected_rating = st.session_state[rating_key]
        annotations.setdefault("ratings", {})[current_img] = selected_rating

        if selected_rating == "NR":
            st.markdown("<p style='text-align:center;font-size:1.3em;'>🚫 <b>Figure is Not Relevant</b></p>", unsafe_allow_html=True)
        elif isinstance(selected_rating, int) and selected_rating > 0:
            st.markdown(f"<p style='text-align:center;font-size:1.3em;'><b>Score:</b> {selected_rating}/5</p>", unsafe_allow_html=True)

    with cols[1]:
        st.markdown(f"**Panel:** {info.get('panel', '—')} | **Label:** {info.get('label', '—')}")
        key = f"highlight_{current_img}"
        prev_highlights = annotations.get("highlights", {}).get(current_img, [])
        new_highlights = highlight_text(
            text=info.get("enriched_caption", ""),
            key=f"highlight_{current_img}",
            highlights=prev_highlights,
        )
        if new_highlights and new_highlights != prev_highlights:
            annotations.setdefault("highlights", {})[current_img] = new_highlights

        st.markdown("##### Original Caption")
        st.markdown(highlight_verbatims(info.get("caption", ""), info.get("used_verbatims", [])), unsafe_allow_html=True)
        st.markdown("##### Context Paragraph(s)")
        st.markdown(highlight_verbatims(info.get("context", ""), info.get("used_verbatims", [])), unsafe_allow_html=True)

# =========================================================
# Sidebar Navigation
# =========================================================
st.sidebar.header("Reviewed Figures")

total_pages = (len(image_list) - 1) // IMAGES_PER_PAGE + 1
ratings_dict = annotations.get("ratings", {})
clicked_page = None

for page_num in range(total_pages):
    start_i = page_num * IMAGES_PER_PAGE
    end_i = min(start_i + IMAGES_PER_PAGE, len(image_list))
    page_imgs = image_list[start_i:end_i]
    is_current = page_num == st.session_state.page_index

    btn_label = f"📄 Page {page_num + 1}"
    if is_current:
        st.sidebar.markdown(
            f"<div style='background-color:#cfe2ff;color:#000;font-weight:600;padding:6px 10px;border-radius:6px;margin-top:6px;margin-bottom:4px;'>"
            f"{btn_label}</div>", unsafe_allow_html=True)
    else:
        if st.sidebar.button(btn_label, key=f"page_btn_{page_num}", use_container_width=True):
            clicked_page = page_num

    for img in page_imgs:
        score = ratings_dict.get(img, 0)
        if score == "NR":
            mark, score_str = "🚫", ""
        elif isinstance(score, int) and score > 0:
            mark, score_str = "✅", f" — **{score}/5**"
        else:
            mark, score_str = "▫️", ""
        label = f"{mark} {img.split('-')[-1]}{score_str}"
        if is_current:
            st.sidebar.markdown(f"**{label}**")
        else:
            st.sidebar.markdown(label)

    st.sidebar.markdown("")

if clicked_page is not None and clicked_page != st.session_state.page_index:
    st.session_state.page_index = clicked_page
    st.rerun()

# =========================================================
# Save & Export + Navigation
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

nav_cols = st.columns([1, 6, 1])
with nav_cols[0]:
    if st.button("⬅ Previous Page") and st.session_state.page_index > 0:
        st.session_state.page_index -= 1
        st.rerun()
with nav_cols[1]:
    st.markdown(
        f"<p style='text-align:center;font-size:1.1em;'>Page {st.session_state.page_index + 1} of {total_pages}</p>",
        unsafe_allow_html=True)
with nav_cols[2]:
    if st.button("Next Page ➡") and st.session_state.page_index < total_pages - 1:
        st.session_state.page_index += 1
        st.rerun()
