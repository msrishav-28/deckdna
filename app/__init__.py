"""DeckDNA web application (blueprint section 4.1, Milestone 7).

A thin FastAPI layer over the core pipeline: upload and extract a deck's
style, review and edit the style guide, plan and approve an outline,
generate the deck (optionally through the critique loop), preview it, and
download editable PowerPoint. All heavy lifting lives in core/; this
package owns HTTP, storage and job orchestration only.
"""
