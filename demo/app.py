"""Thin Streamlit demo for the hospital operations assistant.

All logic lives in hospops/ (via hospops.demo_helpers); this file only wires
widgets. streamlit is imported lazily inside main() so importing this module
never breaks the stdlib test suite.

Live run:
    pip install streamlit
    streamlit run demo/app.py
"""

import os

SOPS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "docs", "sops")


def main():
    import streamlit as st  # lazy: keeps stdlib tests dependency-free
    import sys

    # `streamlit run demo/app.py` puts demo/ on sys.path; hospops/ lives one up.
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

    from hospops import demo_helpers, hospital, orchestrator, views

    st.set_page_config(page_title="Hospital Ops Assistant", layout="wide")
    st.title("Hospital Ops Assistant (demo)")
    st.caption("Operational questions only -- no clinical advice. Synthetic data.")

    if "planner" not in st.session_state:
        conn = hospital.connect()
        views.bootstrap(conn, seed=42)
        st.session_state.planner = orchestrator.PlannerAgent(conn, SOPS_DIR)
    planner = st.session_state.planner

    question = st.text_input(
        "Ask a planner question",
        "Which wards are over 90% occupied, and who is boarding in the ED?",
    )
    if st.button("Ask"):
        with st.spinner("Reasoning over telemetry, SOPs, and demand signals..."):
            result = planner.ask(question)
        st.session_state.last_result = result

    result = st.session_state.get("last_result")
    if result:
        st.subheader("Answer")
        st.text(demo_helpers.format_answer(result))
        st.caption(f"Escalation tier: {result['tier']}")

    st.subheader("Pending confirmation gates")
    for gate in demo_helpers.pending_gates(planner.gate):
        cols = st.columns([3, 1, 1])
        cols[0].write(f"**{gate['token']}** [{gate['action']}] {gate['summary']}")
        if cols[1].button("Approve", key=f"approve-{gate['token']}"):
            planner.gate.approve(gate["token"])
            st.rerun()
        if cols[2].button("Deny", key=f"deny-{gate['token']}"):
            planner.gate.deny(gate["token"], reason="denied in demo")
            st.rerun()

    st.subheader("Recent audit events")
    rows = demo_helpers.recent_audit_rows(planner.audit, n=10)
    st.table(rows)


if __name__ == "__main__":
    main()
