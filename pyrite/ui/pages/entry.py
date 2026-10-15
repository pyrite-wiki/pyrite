"""
Entry Detail Page - View and edit entry content.
"""

import streamlit as st

from pyrite.exceptions import AmbiguousEntryError
from pyrite.ui.data import clear_cache, get_entry, save_entry, search

st.header("📄 Entry Detail")

# Get selected entry from session state
entry_id = st.session_state.get("selected_entry_id")
entry_kb = st.session_state.get("selected_entry_kb")

if not entry_id:
    st.info("No entry selected. Use Search or Timeline to find an entry.")

    # Quick search
    quick_id = st.text_input("Or enter an entry ID directly:")
    if quick_id:
        entry_id = quick_id

if entry_id:
    try:
        with st.spinner("Loading entry..."):
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
        # =====================================================================
        # Header with edit toggle
        # =====================================================================
        col_title, col_edit = st.columns([4, 1])
        with col_title:
            st.title(entry["title"])
        with col_edit:
            editing = st.toggle("Edit", key="edit_mode")

        # Metadata badges
        col1, col2, col3, col4 = st.columns(4)
        with col1:
            st.markdown(f"**KB:** {entry['kb_name']}")
        with col2:
            st.markdown(f"**Type:** {entry['entry_type']}")
        with col3:
            if entry.get("date"):
                st.markdown(f"**Date:** {entry['date']}")
        with col4:
            if entry.get("importance"):
                st.markdown(f"**Importance:** {entry['importance']}/10")

        st.divider()

        if editing:
            # =================================================================
            # Edit Mode
            # =================================================================
            with st.form("edit_entry_form"):
                new_title = st.text_input("Title", value=entry["title"])

                # Tags as comma-separated
                current_tags = ", ".join(entry.get("tags", []))
                new_tags_str = st.text_input("Tags (comma-separated)", value=current_tags)

                # Type-specific fields
                if entry["entry_type"] == "event":
                    ecol1, ecol2 = st.columns(2)
                    with ecol1:
                        new_importance = st.slider(
                            "Importance",
                            1,
                            10,
                            value=entry.get("importance", 5),
                        )
                    with ecol2:
                        status_options = ["confirmed", "disputed", "alleged", "rumored"]
                        current_status = entry.get("status", "confirmed")
                        idx = (
                            status_options.index(current_status)
                            if current_status in status_options
                            else 0
                        )
                        new_status = st.selectbox("Status", status_options, index=idx)

                    new_actors_str = st.text_input(
                        "Actors (comma-separated)",
                        value=", ".join(entry.get("actors", [])),
                    )

                # Body editor with live preview (side by side)
                st.markdown("**Body** (Markdown)")
                edit_col, preview_col = st.columns(2)
                with edit_col:
                    new_body = st.text_area(
                        "Edit",
                        value=entry.get("body", ""),
                        height=400,
                        label_visibility="collapsed",
                    )
                with preview_col:
                    st.markdown("*Preview:*")
                    st.markdown(new_body)

                submitted = st.form_submit_button("Save Changes", type="primary")

                if submitted:
                    updates = {"title": new_title, "body": new_body}

                    # Parse tags
                    new_tags = [t.strip() for t in new_tags_str.split(",") if t.strip()]
                    updates["tags"] = new_tags

                    # Type-specific updates
                    if entry["entry_type"] == "event":
                        updates["importance"] = new_importance
                        updates["status"] = new_status
                        new_actors = [a.strip() for a in new_actors_str.split(",") if a.strip()]
                        updates["actors"] = new_actors

                    if save_entry(entry_id, entry["kb_name"], **updates):
                        st.success("Entry saved successfully!")
                        clear_cache()
                        st.rerun()
                    else:
                        st.error("Failed to save entry. The KB may be read-only.")

        else:
            # =================================================================
            # View Mode
            # =================================================================

            # Tags
            if entry.get("tags"):
                st.markdown("**Tags:** " + " ".join([f"`{tag}`" for tag in entry["tags"]]))

            # Actors
            if entry.get("actors"):
                st.markdown("**Actors:** " + ", ".join(entry["actors"]))

            st.divider()

            # Body content
            st.markdown("### Content")
            if entry.get("body"):
                st.markdown(entry["body"])
            else:
                st.info("No content available.")

            # Sources
            if entry.get("sources"):
                st.divider()
                st.markdown("### Sources")
                for i, source in enumerate(entry["sources"], 1):
                    if isinstance(source, dict):
                        title = source.get("title", f"Source {i}")
                        url = source.get("url", "")
                        outlet = source.get("outlet", "")
                        if url:
                            st.markdown(f"{i}. [{title}]({url}) - {outlet}")
                        else:
                            st.markdown(f"{i}. {title} - {outlet}")
                    else:
                        st.markdown(f"{i}. {source}")

        # =====================================================================
        # Links (shown in both view and edit mode)
        # =====================================================================
        col1, col2 = st.columns(2)

        with col1:
            if entry.get("outlinks"):
                st.divider()
                st.markdown("### Outgoing Links")
                for link in entry["outlinks"]:
                    target_id = link.get("target_id", link.get("id", ""))
                    relation = link.get("relation", link.get("type", "related"))
                    if st.button(f"→ {target_id} ({relation})", key=f"out_{target_id}"):
                        st.session_state.selected_entry_id = target_id
                        st.session_state.selected_entry_kb = None
                        st.rerun()

        with col2:
            if entry.get("backlinks"):
                st.divider()
                st.markdown("### Backlinks")
                for link in entry["backlinks"]:
                    source_id = link.get("source_id", link.get("id", ""))
                    relation = link.get("relation", "links to")
                    if st.button(f"← {source_id} ({relation})", key=f"back_{source_id}"):
                        st.session_state.selected_entry_id = source_id
                        st.session_state.selected_entry_kb = None
                        st.rerun()

        # View graph button
        if entry.get("outlinks") or entry.get("backlinks"):
            if st.button("🕸 View Relationship Graph"):
                st.switch_page(str(__file__).replace("entry.py", "graph.py"))

        # Related entries
        st.divider()
        st.markdown("### Related Entries")

        # Search for related entries using title keywords
        title_words = entry["title"].split()[:3]
        if title_words:
            related_query = " ".join(title_words)
            related = search(related_query, limit=5)
            related = [r for r in related if r["id"] != entry_id][:4]

            if related:
                for r in related:
                    if st.button(f"📄 {r['title']}", key=f"related_{r['id']}"):
                        st.session_state.selected_entry_id = r["id"]
                        st.session_state.selected_entry_kb = r["kb_name"]
                        st.rerun()
            else:
                st.info("No related entries found.")

        # File path
        st.divider()
        st.caption(f"File: `{entry.get('file_path', 'unknown')}`")
