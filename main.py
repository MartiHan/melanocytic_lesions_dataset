import os
import io, zipfile
import json
import re, unicodedata, statistics
from difflib import SequenceMatcher
import streamlit as st
from PIL import Image
from bs4 import BeautifulSoup
from highlight_component import highlight_text
from streamlit_scroll_to_top import scroll_to_here

allow_loading_from_file = True

# =========================================================
# Streamlit setup
# =========================================================
st.markdown("""
<style>
    .block-container {
        padding: 1rem 0.8rem 0.2rem 0.8rem;
        max-width: 98%;
    }
</style>
""", unsafe_allow_html=True)

st.set_page_config(page_title="Melanocytic Lesions Dataset", layout="wide")
st.title("Multimodal Histopathology Dataset of Melanocytic Lesions")

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

# --- Utility ---
def normalize_for_matching(s):
    if not s:
        return ""
    s = unicodedata.normalize("NFKD", s)
    s = re.sub(r"\s+", " ", s.strip())
    return s.lower()

def expand_to_word_boundaries(text, start, end):
    """Expand highlight range to full word boundaries."""
    while start > 0 and re.match(r"\w", text[start - 1]):
        start -= 1
    while end < len(text) and re.match(r"\w", text[end]):
        end += 1
    return start, end

# --- Main function ---
def highlight_verbatims(text, verbatims, color="#a7d8ff"):
    if not text or not verbatims:
        return text

    norm_text = normalize_for_matching(text)
    matches = []
    candidate_groups = []

    # --- precompute normalized verbatims once ---
    norm_verbatims = [(v, normalize_for_matching(v)) for v in set(verbatims) if v.strip()]
    if not norm_verbatims:
        return text

    # --- search all verbatims ---
    for v, nv in norm_verbatims:
        start = 0
        spans = []
        # direct substring find instead of regex for speed
        while True:
            idx = norm_text.find(nv, start)
            if idx == -1:
                break
            spans.append((idx, idx + len(nv)))
            start = idx + len(nv)
        # small fallback fuzzy match for rare missing ones
        if not spans and len(nv) > 5:
            step = max(1, len(nv)//2)
            for i in range(0, len(norm_text)-len(nv), step):
                window = norm_text[i:i+len(nv)]
                if SequenceMatcher(None, nv, window).ratio() > 0.9:
                    spans.append((i, i + len(nv)))
        if spans:
            candidate_groups.append((v, spans))

    # --- pick anchors first (unique occurrences) ---
    anchors = [spans[0] for _, spans in candidate_groups if len(spans) == 1]
    matches.extend(anchors)

    # --- pick closest match for redundant verbatims ---
    if anchors:
        anchor_mean = statistics.mean((s + e) / 2 for s, e in anchors)
    else:
        anchor_mean = len(norm_text) / 2

    for _, spans in candidate_groups:
        if len(spans) > 1:
            best = min(spans, key=lambda s: abs(((s[0] + s[1]) / 2) - anchor_mean))
            matches.append(best)

    # --- merge and expand ---
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
        s, e = expand_to_word_boundaries(text, s, e)
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

# Track manually exported JSONs
if "exported_jsons" not in st.session_state:
    st.session_state.exported_jsons = set()
if "edited_jsons" not in st.session_state:
    st.session_state.edited_jsons = set()
# --- Build readable dropdown labels using parsed titles ---
titles = []
metadata_cache = {}
display_labels = []

for jf in json_files:
    pmc_folder = os.path.basename(os.path.dirname(os.path.dirname(os.path.dirname(jf))))
    nxml_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(jf))), pmc_folder)
    nxml_path = next((os.path.join(nxml_dir, f) for f in os.listdir(nxml_dir) if f.endswith(".nxml")), None)

    if nxml_path:
        meta = parse_paper_metadata(nxml_path)
    else:
        meta = {"title": "Untitled", "year": "N/A", "authors": [], "doi": "N/A", "journal": "N/A"}

    metadata_cache[jf] = meta
    base_label = f"{meta['title']} ({meta['year']})"

    # --- Determine emoji state ---
    if jf in st.session_state.edited_jsons:
        icon = "✏️"
    elif jf in st.session_state.exported_jsons:
        icon = "💾"
    else:
        icon = "◽"

    display_labels.append(f"{icon} {base_label}")

# --- Map label to path ---
label_to_path = {label: path for label, path in zip(display_labels, json_files)}

# --- Preserve current selection when icons change ---
active_json = st.session_state.get("active_json")
default_index = 0
if active_json:
    for i, lbl in enumerate(display_labels):
        if label_to_path[lbl] == active_json:
            default_index = i
            break

selected_label = st.selectbox(
    "Select article to review",
    display_labels,
    index=default_index,
    key="json_selector"
)
selected_json = label_to_path[selected_label]


metadata = metadata_cache[selected_json]
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
        #st.toast(f"💾 Saved annotations for {os.path.basename(prev_ann_path)}", icon="✅")

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
# Pagination
# =========================================================
image_list = [img for img in sorted(data.keys()) if img.lower().endswith((".jpg", ".jpeg", ".png", ".tif", ".tiff"))]
if not image_list:
    st.warning("No image entries found.")
    st.stop()

IMAGES_PER_PAGE = 5
if "page_index" not in st.session_state:
    st.session_state.page_index = 0

if "allow_json_loading" not in st.session_state:
    st.session_state.allow_json_loading = True

if "last_page_index" not in st.session_state:
    st.session_state.last_page_index = st.session_state.get("page_index", 0)

if st.session_state.get("page_index", 0) != st.session_state.last_page_index:
    scroll_to_here(0, key="page_scroll")
    st.session_state.last_page_index = st.session_state.get("page_index", 0)


start_idx = st.session_state.page_index * IMAGES_PER_PAGE
end_idx = start_idx + IMAGES_PER_PAGE
current_images = image_list[start_idx:end_idx]

# =========================================================
# Initialize loading permission
# =========================================================
if "allow_json_loading" not in st.session_state:
    st.session_state.allow_json_loading = True
if "last_uploaded_zip_name" not in st.session_state:
    st.session_state.last_uploaded_zip_name = None
if "upload_counter" not in st.session_state:
    st.session_state.upload_counter = 0

uploader_key = f"global_upload_zip_{st.session_state.upload_counter}"

upload_col, _, download_col = st.columns([1, 2, 1], vertical_alignment="bottom")
with upload_col:
    uploaded_zip = st.file_uploader(
        "Import ZIP of existing annotations",
        type=["zip"],
        key=uploader_key,
    )

if uploaded_zip is not None:
    current_name = uploaded_zip.name
    if current_name != st.session_state.last_uploaded_zip_name:
        st.session_state.allow_json_loading = True
        st.session_state.last_uploaded_zip_name = current_name
        for jf in json_files:
            ann_path = annotations_path_for(jf)
            if os.path.exists(ann_path):
                st.session_state.exported_jsons.add(jf)
                st.session_state.edited_jsons.discard(jf)
        st.rerun()

if uploaded_zip is not None:
    st.session_state.allow_json_loading = True
    st.session_state.last_uploaded_zip_name = uploaded_zip.name

if uploaded_zip is not None and st.session_state.allow_json_loading:
    try:
        with zipfile.ZipFile(uploaded_zip, "r") as zip_ref:
            updated_count = 0
            loaded_for_active = None

            for name in zip_ref.namelist():
                if not name.endswith("_annotations.json"):
                    continue
                try:
                    with zip_ref.open(name) as f:
                        content = json.load(f)

                    # Match the paper
                    paper_name = name.replace("_annotations.json", "_captions.json")
                    matching_jsons = [jf for jf in json_files if os.path.basename(jf) == paper_name]
                    if not matching_jsons:
                        continue
                    json_path = matching_jsons[0]

                    # Load or create existing annotation file
                    ann_path = annotations_path_for(json_path)
                    if os.path.exists(ann_path):
                        with open(ann_path, "r", encoding="utf-8") as f:
                            existing = json.load(f)
                    else:
                        existing = {}

                    # Merge new content
                    existing.setdefault("ratings", {}).update(content.get("ratings", {}))
                    existing.setdefault("highlights", {}).update(content.get("highlights", {}))

                    # Write back merged file
                    with open(ann_path, "w", encoding="utf-8") as f:
                        json.dump(existing, f, indent=2, ensure_ascii=False)

                    # Capture active paper for immediate sync
                    if json_path == selected_json:
                        loaded_for_active = existing

                    updated_count += 1

                except Exception as e:
                    st.warning(f"⚠️ Skipped {name}: {e}")

        # Sync the current paper in session
        if loaded_for_active:
            annotations.update(loaded_for_active)
            st.session_state.annotations = annotations

            for img_name, score in loaded_for_active.get("ratings", {}).items():
                st.session_state[f"rating_{img_name}"] = score

            for img_name, hl in loaded_for_active.get("highlights", {}).items():
                key_prefix = f"highlight_{os.path.basename(selected_json)}_{img_name}_"
                if img_name in current_images:
                    key = f"{key_prefix}{st.session_state.page_index}"
                    st.session_state[key] = hl

        st.cache_data.clear()

        # Disable further loads until user selects a new ZIP
        st.session_state.allow_json_loading = False
        st.session_state.upload_counter += 1
        st.rerun()

    except Exception as e:
        st.error(f"❌ Failed to read ZIP: {e}")

with download_col:
    for key, value in st.session_state.items():
        if key.startswith("highlight_") and isinstance(value, list):
            # Parse out image name from key pattern
            parts = key.split("_", 2)
            if len(parts) >= 3:
                for img in image_list:
                    if img in key:
                        annotations.setdefault("highlights", {})[img] = value
                        break

        # Write updated annotations for the active paper to disk
    ann_path = annotations_path_for(selected_json)
    with open(ann_path, "w", encoding="utf-8") as f:
        json.dump(annotations, f, indent=2, ensure_ascii=False)

    # Create ZIP in memory
    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zipf:
        for jf in json_files:
            ann_path = annotations_path_for(jf)
            if os.path.exists(ann_path):
                with open(ann_path, "r", encoding="utf-8") as f:
                    content = f.read()
                # add with proper name
                zipf.writestr(os.path.basename(ann_path), content)
    zip_buffer.seek(0)

    if st.download_button(
        label="💾 Download All Annotations (ZIP)",
        data=zip_buffer,
        file_name="all_reviews.zip",
        mime="application/zip",
        use_container_width=True,
        help="Download all saved annotation files as a single ZIP"
    ):
        for jf in json_files:
            ann_path = annotations_path_for(jf)
            if os.path.exists(ann_path):
                st.session_state.exported_jsons.add(jf)
                st.session_state.edited_jsons.discard(jf)
        st.rerun()

# =========================================================
# Paper metadata display
# =========================================================
doi_html = f"<a href='https://doi.org/{metadata['doi']}' target='_blank'>{metadata['doi']}</a>" if metadata['doi'] != "N/A" else "N/A"
st.markdown(f"""
<div style="padding:15px;border-radius:10px;margin-bottom:20px">
  <h4 style="margin-bottom:5px;">{metadata['title']}</h4>
  <p><strong>Authors:</strong> {', '.join(metadata['authors']) if metadata['authors'] else 'Unknown'}</p>
  <p><strong>Journal:</strong> {metadata['journal']} ({metadata['year']})</p>
  <p><strong>DOI:</strong> {doi_html}</p>
</div>
""", unsafe_allow_html=True)

# =========================================================
# Display Figures (5 per page)
# =========================================================
for current_img in current_images:
    info = data[current_img]

    cols = st.columns([1, 2])
    with cols[0]:
        img_path = os.path.join(os.path.dirname(selected_json), current_img)
        if os.path.exists(img_path):
            st.image(load_image(img_path), width='stretch')

        highlight_key = f"highlight_{os.path.basename(selected_json)}_{current_img}_{st.session_state.page_index}"

        # --- Load persistent highlights from session ---
        if highlight_key not in st.session_state:
            st.session_state[highlight_key] = annotations.get("highlights", {}).get(current_img, [])

        # --- Interactive highlighting ---
        new_highlights = highlight_text(
            text=info.get("enriched_caption", ""),
            key=highlight_key,
            highlights=st.session_state[highlight_key],
        )

        # --- Update persistent state (in memory + annotations file model) ---
        if new_highlights and new_highlights != st.session_state[highlight_key]:
            st.session_state[highlight_key] = new_highlights
            annotations.setdefault("highlights", {})[current_img] = new_highlights

        rating_key = f"rating_{current_img}"
        if rating_key not in st.session_state:
            st.session_state[rating_key] = annotations.get("ratings", {}).get(current_img, 0)

        cols_rating = st.columns(6)
        if cols_rating[0].button("🚫", key=f"{rating_key}_NR_{current_img}", width='stretch'):
            st.session_state[rating_key] = "NR"
            annotations.setdefault("ratings", {})[current_img] = "NR"
            st.session_state.exported_jsons.discard(selected_json)
            st.session_state.edited_jsons.add(selected_json)
            #st.rerun()

        for n, c in enumerate(cols_rating[1:], start=1):
            if c.button(str(n), key=f"{rating_key}_{n}_{current_img}", width='stretch'):
                st.session_state[rating_key] = n
                annotations.setdefault("ratings", {})[current_img] = n
                st.session_state.exported_jsons.discard(selected_json)
                st.session_state.edited_jsons.add(selected_json)

        selected_rating = st.session_state[rating_key]
        annotations.setdefault("ratings", {})[current_img] = selected_rating

        if selected_rating == "NR":
            st.markdown("<p style='text-align:center;font-size:1.3em;'>🚫 <b>Figure is Not Relevant</b></p>", unsafe_allow_html=True)
            allow_loading_from_file = False
        elif isinstance(selected_rating, int) and selected_rating > 0:
            st.markdown(f"<p style='text-align:center;font-size:1.3em;'><b>Score:</b> {selected_rating}/5</p>", unsafe_allow_html=True)
            allow_loading_from_file = False

    with cols[1]:
        st.markdown(f"**Panel:** {info.get('panel', '—')} | **Label:** {info.get('label', '—')}")
        st.markdown("##### Original Caption")
        st.markdown(highlight_verbatims(info.get("caption", ""), info.get("used_verbatims", [])), unsafe_allow_html=True)
        st.markdown("##### Context Paragraph(s)")
        st.markdown(highlight_verbatims(info.get("context", ""), info.get("used_verbatims", [])), unsafe_allow_html=True)

    st.markdown("---")

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
        if st.sidebar.button(btn_label, key=f"page_btn_{page_num}", width='stretch'):
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
        st.session_state.allow_json_loading = False
        st.rerun()
