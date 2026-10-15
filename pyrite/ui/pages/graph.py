"""
Relationship Graph Page - Interactive visualization of entry relationships.
"""

import streamlit as st

from pyrite.exceptions import AmbiguousEntryError
from pyrite.ui.data import get_entry, get_entry_graph


def _show_text_graph(graph_data):
    """Fallback text display when streamlit-agraph is not installed."""
    st.markdown("### Connections")
    for edge in graph_data["edges"]:
        st.markdown(f"- **{edge['source']}** → *{edge['label']}* → **{edge['target']}**")


st.header("🕸 Relationship Graph")

# Get selected entry from session state
entry_id = st.session_state.get("selected_entry_id")
entry_kb = st.session_state.get("selected_entry_kb")

if not entry_id:
    st.info("No entry selected. Use Search or Timeline to find an entry, then view its graph.")

    quick_id = st.text_input("Or enter an entry ID directly:")
    if quick_id:
        entry_id = quick_id

if entry_id:
    try:
        entry = get_entry(entry_id, entry_kb)
    except AmbiguousEntryError as exc:
        st.error(
            f"Entry '{entry_id}' exists in more than one KB: "
            f"{', '.join(exc.candidate_kbs)}. Open it from a search result "
            "or the timeline, which know the KB."
        )
        st.stop()
    if not entry:
        st.error(f"Entry '{entry_id}' not found.")
    else:
        st.subheader(f"Graph for: {entry['title']}")

        graph_data = get_entry_graph(entry_id, entry["kb_name"])

        if not graph_data["edges"]:
            st.info("This entry has no links or backlinks. The graph is empty.")
        else:
            try:
                from streamlit_agraph import Config, Edge, Node, agraph

                # Color map for entry types
                type_colors = {
                    "event": "#4A90D9",
                    "actor": "#E74C3C",
                    "organization": "#F39C12",
                    "theme": "#2ECC71",
                    "document": "#9B59B6",
                    "unknown": "#95A5A6",
                }

                nodes = []
                for n in graph_data["nodes"]:
                    color = type_colors.get(n["entry_type"], type_colors["unknown"])
                    size = 30 if n["is_center"] else 20
                    if n.get("importance") and n["importance"] >= 7:
                        size = 35

                    nodes.append(
                        Node(
                            id=n["id"],
                            label=n["title"][:40],
                            size=size,
                            color=color,
                            title=f"{n['title']}\n({n['entry_type']}, {n['kb_name']})",
                        )
                    )

                edges = []
                for e in graph_data["edges"]:
                    edges.append(
                        Edge(
                            source=e["source"],
                            target=e["target"],
                            label=e["label"],
                            color="#666666",
                        )
                    )

                config = Config(
                    width=800,
                    height=500,
                    directed=True,
                    physics=True,
                    hierarchical=False,
                    nodeHighlightBehavior=True,
                    highlightColor="#F7A7A6",
                )

                selected = agraph(nodes=nodes, edges=edges, config=config)

                # Navigate to clicked node
                if selected and selected != entry_id:
                    st.session_state.selected_entry_id = selected
                    st.session_state.selected_entry_kb = None
                    st.rerun()

            except ImportError:
                st.warning(
                    "Install `streamlit-agraph` for interactive graph visualization: "
                    "`pip install streamlit-agraph`"
                )
                # Fallback: show links as text
                _show_text_graph(graph_data)

        # Legend
        st.divider()
        st.caption("Node colors: 🔵 Event | 🔴 Actor | 🟠 Organization | 🟢 Theme | 🟣 Document")

        # Stats
        col1, col2 = st.columns(2)
        with col1:
            st.metric("Nodes", len(graph_data["nodes"]))
        with col2:
            st.metric("Edges", len(graph_data["edges"]))
