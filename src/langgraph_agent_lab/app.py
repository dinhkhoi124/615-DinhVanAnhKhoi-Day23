# ruff: noqa: E501
import os
import sqlite3
import time
from typing import Any

import streamlit as st
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import Command

# Set environment variable to enable real HITL interrupts in the approval node
os.environ["LANGGRAPH_INTERRUPT"] = "true"

from langgraph_agent_lab.graph import build_graph
from langgraph_agent_lab.state import Route, Scenario, initial_state

# Page configuration
st.set_page_config(
    page_title="LangGraph Ticket Agent Dashboard",
    page_icon="🤖",
    layout="wide",
    initial_sidebar_state="expanded"
)

# Custom Styling (Glassmorphism & Outfit Font)
st.markdown("""
<style>
    @import url('https://fonts.googleapis.com/css2?family=Outfit:wght@300;400;600;800&display=swap');
    
    html, body, [class*="css"] {
        font-family: 'Outfit', sans-serif;
    }
    
    .main-title {
        font-size: 2.5rem;
        font-weight: 800;
        background: linear-gradient(135deg, #a8ff78 0%, #78ffd6 100%);
        -webkit-background-clip: text;
        -webkit-text-fill-color: transparent;
        margin-bottom: 20px;
        text-align: center;
    }
    
    .custom-card {
        background: rgba(255, 255, 255, 0.03);
        border: 1px solid rgba(255, 255, 255, 0.08);
        border-radius: 12px;
        padding: 20px;
        margin-bottom: 20px;
    }
    
    .timeline-container {
        display: flex;
        flex-wrap: wrap;
        gap: 10px;
        align-items: center;
        padding: 15px;
        background: rgba(255, 255, 255, 0.02);
        border-radius: 10px;
        border: 1px solid rgba(255, 255, 255, 0.05);
    }
    
    .node-badge {
        display: inline-flex;
        align-items: center;
        padding: 8px 16px;
        border-radius: 30px;
        font-size: 0.85rem;
        font-weight: 600;
        text-transform: uppercase;
        letter-spacing: 0.05em;
        box-shadow: 0 4px 6px rgba(0, 0, 0, 0.1);
    }
    
    .node-intake { background: #3498db; color: white; }
    .node-classify { background: #9b59b6; color: white; }
    .node-tool { background: #e67e22; color: white; }
    .node-evaluate { background: #1abc9c; color: white; }
    .node-answer { background: #2ecc71; color: white; }
    .node-clarify { background: #f1c40f; color: black; }
    .node-risky_action { background: #e74c3c; color: white; }
    .node-approval { background: #e74c3c; color: white; border: 2px dashed white; animation: pulse 2s infinite; }
    .node-retry { background: #34495e; color: white; }
    .node-dead_letter { background: #7f8c8d; color: white; }
    .node-finalize { background: #27ae60; color: white; }
    
    @keyframes pulse {
        0% { transform: scale(1); opacity: 0.8; }
        50% { transform: scale(1.05); opacity: 1; }
        100% { transform: scale(1); opacity: 0.8; }
    }
</style>
""", unsafe_allow_html=True)

# Cache graph compilation
@st.cache_resource
def get_graph() -> Any:  # noqa: ANN401
    conn = sqlite3.connect("streamlit_checkpoint.db", check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL;")
    checkpointer = SqliteSaver(conn)
    return build_graph(checkpointer=checkpointer)

graph = get_graph()

# Session State Initialization
if "thread_id" not in st.session_state:
    st.session_state.thread_id = f"thread-st-{int(time.time())}"

if "user_input" not in st.session_state:
    st.session_state.user_input = ""

# Sidebar controls
with st.sidebar:
    st.image("https://img.icons8.com/nolan/96/bot.png", width=70)
    st.markdown("### Graph Checkpointer Setup")
    
    # Select or enter thread ID
    selected_thread = st.text_input(
        "Current Thread ID",
        value=st.session_state.thread_id,
        help="Thread ID maps to your state checkpointer thread."
    )
    if selected_thread != st.session_state.thread_id:
        st.session_state.thread_id = selected_thread
        st.rerun()
        
    if st.button("🔄 Generate New Thread"):
        st.session_state.thread_id = f"thread-st-{int(time.time())}"
        st.rerun()
        
    st.markdown("---")
    st.markdown("### Database Utilities")
    if st.button("🗑️ Clear Checkpoint DB"):
        if os.path.exists("streamlit_checkpoint.db"):
            try:
                # Close connection by replacing cache resource (effectively done next run)
                os.remove("streamlit_checkpoint.db")
                st.success("Database cleared successfully!")
                st.session_state.thread_id = f"thread-st-{int(time.time())}"
                time.sleep(1)
                st.rerun()
            except Exception as e:
                st.error(f"Could not clear DB: {e}")

# Load current state
config = {"configurable": {"thread_id": st.session_state.thread_id}}
state_snapshot = graph.get_state(config)
current_values = state_snapshot.values if state_snapshot else {}
next_nodes = state_snapshot.next if state_snapshot else ()

# Layout Columns
col1, col2 = st.columns([2, 1])

with col1:
    st.markdown("<h1 class='main-title'>🤖 LangGraph Agent Dashboard</h1>", unsafe_allow_html=True)
    
    # Active Interrupt Check (HITL block)
    is_interrupted = "approval" in next_nodes
    
    if is_interrupted:
        st.warning("⚠️ **Human-In-The-Loop Approval Needed!** The workflow has been paused at the `approval` node.")
        
        with st.container(border=True):
            st.subheader("Action Awaiting Authorization")
            proposed_action = current_values.get("proposed_action", "No description provided.")
            st.info(f"**Proposed Action:** {proposed_action}")
            
            # Review fields
            comment = st.text_input("Reviewer Comment", value="Approved via Streamlit Dashboard", key="reviewer_comment")
            
            c1, c2 = st.columns(2)
            with c1:
                if st.button("✅ Approve Action", use_container_width=True):
                    # Resume execution with approved = True
                    decision = {"approved": True, "reviewer": "admin-streamlit", "comment": comment}
                    graph.invoke(Command(resume=decision), config=config)
                    st.success("Action approved! Resuming graph...")
                    time.sleep(1.5)
                    st.rerun()
            with c2:
                if st.button("❌ Reject Action", use_container_width=True):
                    # Resume execution with approved = False
                    decision = {"approved": False, "reviewer": "admin-streamlit", "comment": comment}
                    graph.invoke(Command(resume=decision), config=config)
                    st.warning("Action rejected! Routing back to clarify...")
                    time.sleep(1.5)
                    st.rerun()
                    
    # Chat Input
    if not is_interrupted:
        st.markdown("### Submit a Support Ticket")
        with st.form("query_form", clear_on_submit=True):
            query_text = st.text_input("Enter ticket request:", placeholder="e.g. Refund order 12345 or lookup status")
            submitted = st.form_submit_button("Submit to Graph")
            
            if submitted and query_text.strip():
                # Initial State
                scenario = Scenario(id=st.session_state.thread_id, query=query_text, expected_route=Route.SIMPLE)
                state = initial_state(scenario)
                # Execute graph
                graph.invoke(state, config=config)
                st.rerun()

    # Chat / Interaction History
    st.markdown("### Conversation History")
    messages = current_values.get("messages", [])
    final_answer = current_values.get("final_answer")
    pending_question = current_values.get("pending_question")
    
    if not messages and not final_answer:
        st.info("No active conversation in this thread. Enter a ticket above to start!")
    else:
        for msg in messages:
            st.chat_message("assistant").write(msg)
            
        if pending_question and not final_answer:
            st.chat_message("assistant").markdown(f"**Clarification Question:** {pending_question}")
        elif final_answer:
            st.chat_message("assistant").markdown(f"**Final Answer:** {final_answer}")

with col2:
    st.markdown("### 🗺️ Node Execution Trace")
    events = current_values.get("events", [])
    
    if not events:
        st.info("Workflow has not run on this thread yet.")
    else:
        st.write("Below is the execution flow of the Agent:")
        
        timeline_html = "<div class='timeline-container'>"
        for i, ev in enumerate(events):
            node = ev.get("node", "unknown")
            # Draw arrow separator if not first
            if i > 0:
                timeline_html += "<span style='color: #a4a2b2;'>➡️</span>"
            timeline_html += f"<span class='node-badge node-{node}'>{node}</span>"
            
        # Draw current active node if interrupted or running
        if is_interrupted:
            timeline_html += "<span style='color: #a4a2b2;'>➡️</span>"
            timeline_html += "<span class='node-badge node-approval'>approval (paused)</span>"
            
        timeline_html += "</div>"
        st.markdown(timeline_html, unsafe_allow_html=True)
        
        # Details list
        st.markdown("#### Audit Logs")
        for ev in events:
            node = ev.get("node", "unknown")
            msg = ev.get("message", "")
            st.caption(f"**[{node}]** {msg}")
            
    st.markdown("---")
    st.markdown("#### State Variables Inspect")
    with st.expander("Show Raw State"):
        st.json(current_values)
