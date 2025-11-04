import os
import streamlit.components.v1 as components

_component_func = components.declare_component(
    "highlight_component",
    path=os.path.join(os.path.dirname(__file__)),
)

def highlight_text(text: str, key=None, highlights=None):
    """
    Render highlightable text and return a list of highlights.
    """
    if highlights is None:
        highlights = []

    result = _component_func(
        text=text,
        highlights=highlights,
        key=key,
        default={"highlights": highlights},
    )

    # Normalize return value to a list
    if isinstance(result, dict) and "highlights" in result:
        return result["highlights"]
    return result or []
