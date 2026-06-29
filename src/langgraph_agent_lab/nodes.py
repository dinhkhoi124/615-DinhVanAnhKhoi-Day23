"""Node functions for the LangGraph workflow.

Each function receives AgentState and returns a partial state update dict.
Do NOT mutate input state — return new values only.

LLM REQUIREMENT:
- classify_node MUST use a real LLM call (structured output for intent classification)
- answer_node MUST use a real LLM call (grounded response generation)
- evaluate_node SHOULD use LLM-as-judge (bonus points; heuristic acceptable for base score)
"""

from __future__ import annotations

import os

from pydantic import BaseModel, Field

from .llm import get_llm
from .state import AgentState, Route, make_event


class ClassificationResponse(BaseModel):
    route: Route = Field(
        ...,
        description=(
            "The classified route for the ticket. "
            "Priority: 'risky' if query requests destructive actions (deletions, "
            "refunds, changing subscriptions) or sends confirmation emails. "
            "Else 'tool' if query needs order/status/data lookup. "
            "Else 'missing_info' if the query is extremely vague. "
            "Else 'error' if query mentions a system error, timeout, crash, or failure. "
            "Else 'simple' for general questions answerable without tools."
        )
    )


class EvaluationResponse(BaseModel):
    needs_retry: bool = Field(
        ...,
        description="True if the tool result contains an error/failure. False if successful."
    )
    reason: str = Field(..., description="Reason for the decision.")


# ─── EXAMPLE: working node (provided for reference) ──────────────────
def intake_node(state: AgentState) -> dict:
    """Normalize raw query. This node is provided as a working example."""
    query = state.get("query", "").strip()
    return {
        "query": query,
        "messages": [f"intake:{query[:40]}"],
        "events": [make_event("intake", "completed", "query normalized")],
    }


# ─── TODO(student): implement ALL nodes below ────────────────────────


def classify_node(state: AgentState) -> dict:
    """Classify the query into a route using an LLM."""
    query = state.get("query", "")
    llm = get_llm(temperature=0.0)
    structured_llm = llm.with_structured_output(ClassificationResponse)
    
    prompt = (
        "You are an AI support ticket routing assistant. "
        "Your task is to classify the support query "
        "into one of the following routes. You must follow the priority strictly:\n"
        "1. 'risky': Actions that have significant side effects or mutate databases, "
        "e.g. refunds, account deletion, sending confirmation emails, cancelling subscriptions, "
        "etc.\n"
        "2. 'tool': Information lookup requests that require calling tools to check status, "
        "details, e.g. order tracking, order lookup, search, product lookup, etc.\n"
        "3. 'missing_info': Very vague, incomplete, or ambiguous questions where you cannot "
        "perform any action without additional information, e.g. 'Can you fix it?', 'Help me "
        "with this', 'It does not work'.\n"
        "4. 'error': Reports of system failures, timeouts, connection errors, crashes, "
        "or unrecoverable issues, e.g. 'Timeout failure while processing request', "
        "'System crash', 'Database connection error'.\n"
        "5. 'simple': General questions, support requests, or greetings that can be answered "
        "directly without tool calls, e.g. 'How do I reset my password?', 'What is your refund "
        "policy?'.\n\n"
        f"Query: {query}"
    )
    
    result = structured_llm.invoke(prompt)
    route_val = result.route.value if hasattr(result.route, "value") else str(result.route)
    risk_level = "high" if route_val == "risky" else "low"
    
    event_msg = f"classified query as {route_val} with {risk_level} risk"
    return {
        "route": route_val,
        "risk_level": risk_level,
        "events": [make_event("classify", "completed", event_msg)],
    }


def tool_node(state: AgentState) -> dict:
    """Execute a mock tool call."""
    attempt = state.get("attempt", 0)
    route = state.get("route", "")
    query = state.get("query", "")
    
    if route == "error" and attempt < 2:
        result_string = f"ERROR: Tool call failed due to timeout (attempt {attempt})."
    else:
        result_string = f"Success: Action processed successfully for query '{query}'."
        
    return {
        "tool_results": [result_string],
        "events": [make_event("tool", "completed", "executed mock tool")],
    }


def evaluate_node(state: AgentState) -> dict:
    """Evaluate tool results — the retry-loop gate."""
    tool_results = state.get("tool_results", [])
    if not tool_results:
        msg = "no tool results to evaluate, assuming success"
        return {
            "evaluation_result": "success",
            "events": [make_event("evaluate", "completed", msg)],
        }
    
    latest_result = tool_results[-1]
    
    # LLM-as-judge implementation
    try:
        llm = get_llm(temperature=0.0)
        structured_llm = llm.with_structured_output(EvaluationResponse)
        prompt = (
            "You are a quality assurance judge for a software agent. "
            "Evaluate the following tool execution result and decide "
            "if it was successful or if it contains a transient error/failure "
            "that requires a retry.\n\n"
            f"Tool Result: {latest_result}\n\n"
            "If the tool result contains error messages, timeout messages, "
            "or failure codes, set needs_retry=True. Otherwise set "
            "needs_retry=False."
        )
        
        eval_res = structured_llm.invoke(prompt)
        needs_retry = eval_res.needs_retry
    except Exception:
        needs_retry = "ERROR" in latest_result
        
    # Heuristic override for absolute safety:
    if "ERROR" in latest_result:
        needs_retry = True
        
    eval_str = "needs_retry" if needs_retry else "success"
    return {
        "evaluation_result": eval_str,
        "events": [make_event("evaluate", "completed", f"evaluated result as {eval_str}")],
    }


def answer_node(state: AgentState) -> dict:
    """Generate a final response using an LLM."""
    query = state.get("query", "")
    tool_results = state.get("tool_results", [])
    approval = state.get("approval")
    
    context = []
    if tool_results:
        context.append("Tool results:\n" + "\n".join(tool_results))
    if approval:
        context.append(f"Approval history: {approval}")
        
    context_str = "\n\n".join(context)
    
    llm = get_llm(temperature=0.7)
    prompt = (
        "You are a helpful customer support agent. Answer the user's query grounded strictly "
        "on the provided context (tool results, approval status). If no tools were used, "
        "answer the question directly and professionally.\n\n"
        "Context:\n{context_str}\n\n"
        "User Query: {query}\n\n"
        "Please provide a final response to the user."
    ).format(context_str=context_str or "No external context available.", query=query)
    
    response = llm.invoke(prompt)
    final_answer = response.content
    
    return {
        "final_answer": final_answer,
        "events": [make_event("answer", "completed", "generated grounded response")],
    }


def ask_clarification_node(state: AgentState) -> dict:
    """Ask for missing information instead of hallucinating."""
    query = state.get("query", "")
    llm = get_llm(temperature=0.5)
    prompt = (
        "The customer query is incomplete, vague, or ambiguous. Please ask a polite, "
        "specific clarification question to help resolve their issue.\n\n"
        f"Customer Query: {query}\n\n"
        "Clarification Question:"
    )
    
    response = llm.invoke(prompt)
    clarification_question = response.content.strip()
    
    return {
        "pending_question": clarification_question,
        "final_answer": clarification_question,
        "events": [make_event("clarify", "completed", "generated clarification request")],
    }


def risky_action_node(state: AgentState) -> dict:
    """Prepare a risky action for human approval."""
    query = state.get("query", "")
    llm = get_llm(temperature=0.0)
    prompt = (
        "A user is requesting a risky operation. Describe the proposed action and explain why it "
        "requires human administrator review.\n\n"
        f"User Request: {query}\n\n"
        "Proposed Action Description:"
    )
    response = llm.invoke(prompt)
    action_desc = response.content.strip()
    
    return {
        "proposed_action": action_desc,
        "events": [make_event("risky_action", "completed", "prepared proposed action description")],
    }


def approval_node(state: AgentState) -> dict:
    """Human-in-the-loop approval step."""
    if os.getenv("LANGGRAPH_INTERRUPT", "false").lower() == "true":
        from langgraph.types import interrupt
        res = interrupt({
            "query": state.get("query"),
            "proposed_action": state.get("proposed_action"),
            "prompt": "Please review and approve/reject this risky action.",
        })
        
        if isinstance(res, dict):
            approved = res.get("approved", False)
            reviewer = res.get("reviewer", "admin")
            comment = res.get("comment", "")
        elif isinstance(res, bool):
            approved = res
            reviewer = "admin"
            comment = "manual choice"
        else:
            approved = str(res).lower() in ("true", "yes", "approved")
            reviewer = "admin"
            comment = str(res)
            
        approval_dict = {
            "approved": approved,
            "reviewer": reviewer,
            "comment": comment
        }
    else:
        approval_dict = {
            "approved": True,
            "reviewer": "mock-reviewer",
            "comment": "Automatically approved in mock mode"
        }
        
    approval_msg = f"approval decision: {approval_dict['approved']} by {approval_dict['reviewer']}"
    return {
        "approval": approval_dict,
        "events": [make_event("approval", "completed", approval_msg)]
    }


def retry_or_fallback_node(state: AgentState) -> dict:
    """Record a retry attempt."""
    attempt = state.get("attempt", 0) + 1
    error_msg = f"Attempt {attempt} failed: Transient system issue encountered."
    
    return {
        "attempt": attempt,
        "errors": [error_msg],
        "events": [make_event("retry", "completed", f"recorded retry attempt {attempt}")],
    }


def dead_letter_node(state: AgentState) -> dict:
    """Handle unresolvable failures after max retries exceeded."""
    final_answer = (
        "Error: The request could not be completed after maximum retry attempts. "
        "Our support team has been notified."
    )
    return {
        "final_answer": final_answer,
        "events": [make_event("dead_letter", "completed", "moved to dead letter queue")],
    }


def finalize_node(state: AgentState) -> dict:
    """Emit a final audit event. All routes must pass through here before END."""
    return {
        "events": [make_event("finalize", "completed", "workflow finished")]
    }

