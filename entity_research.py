"""AI-assisted, on-demand scan of publicly available information (news,
company registries, social media, etc.) about a client's business, its
directors/shareholders, and its products - part of "Understanding the
Entity's Business" (see models.EntityPublicResearch and
company_documents.py's run_public_research, which calls this).

Uses the Claude API's built-in web_search tool (the same ANTHROPIC_API_KEY
already configured for entity_extraction.py, so nothing new needs to be set
up on Render for this to work once that key is in place) so Claude can
actually look things up rather than answering from what it already knows,
which could be stale or simply wrong for a private Zimbabwean company. Every
result is presented as a lead to check, not a finding of fact - the same
conservative principle used throughout this app's other automated/AI-
assisted features: this never flags a client as high-risk or clears them by
itself, it just surfaces what a web search turns up (with its sources) for a
person to read and judge.

Never triggered automatically: each run is a billed AI call (a handful of
web searches plus the model's own tokens), so it only ever runs when someone
clicks the button for it.
"""
import os

import anthropic

DEFAULT_MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-5")
REQUEST_TIMEOUT = 90
MAX_SEARCHES = 8

RESEARCH_TOOL = {
    "name": "record_entity_research",
    "description": "Record what was found from publicly available web sources about this company, its directors/shareholders, and its products.",
    "input_schema": {
        "type": "object",
        "properties": {
            "business_profile": {
                "type": "string",
                "description": "A short, factual summary of what the company and its products/services actually are, its scale/reputation, and any notable news. Leave blank if the requested scope excludes a business profile.",
            },
            "risk_findings": {
                "type": "string",
                "description": "Any adverse-media / due-diligence concerns actually found about the company, or about its named directors or shareholders: litigation, fraud allegations, sanctions or regulatory action, negative press, etc. State plainly if a thorough search found nothing adverse - do not leave this blank just because nothing was found. Leave blank only if the requested scope excludes a risk check.",
            },
            "sources": {
                "type": "array",
                "description": "Every web source actually used to support the findings above, so a person can check the original for themselves.",
                "items": {
                    "type": "object",
                    "properties": {
                        "title": {"type": "string", "description": "The page's title."},
                        "url": {"type": "string", "description": "The page's URL."},
                    },
                    "required": ["url"],
                },
            },
        },
        "required": ["sources"],
    },
}

SCOPE_INSTRUCTIONS = {
    "profile": "Focus only on a general business profile: what the company and its products/services are, its scale and reputation, and notable news. Leave risk_findings blank.",
    "risk": "Focus only on adverse-media / due-diligence risk: fraud allegations, litigation, sanctions or regulatory action, and negative press about the company, its directors or its shareholders. Leave business_profile blank.",
    "both": "Cover both a general business profile AND any adverse-media / due-diligence risk findings, clearly separated between business_profile and risk_findings.",
}

SYSTEM_PROMPT = (
    "You are helping a chartered accountancy firm carry out client due diligence as part of "
    "'Understanding the Entity's Business'. Use the web_search tool to look for publicly "
    "available information - including news and social media - about the named company, its "
    "directors/shareholders, and its products. Search using the company name, each named person, "
    "and product names as appropriate; a handful of well-chosen searches is normally enough - you "
    "do not need to exhaust every possible query. {scope_instruction} Be strictly factual and only "
    "state something a search result actually supports - never guess, infer, or fabricate a "
    "finding, and do not confuse a similarly-named person or company with the one being "
    "researched. If searches turn up nothing relevant, say so plainly rather than inventing "
    "content. When you have finished searching, call record_entity_research exactly once with "
    "what you found, listing every source you actually used."
)


def _client():
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return None
    return anthropic.Anthropic(api_key=api_key, timeout=REQUEST_TIMEOUT)


def research_entity_public_info(company_name, people=None, industry=None, scope="both"):
    """Look up publicly available information about a company (and, best
    effort, its directors/shareholders) via Claude's web search tool.

    Returns (result, status, error):
      - status "done": result is a {"business_profile", "risk_findings",
        "sources"} dict ("sources" a list of {"title", "url"} dicts)
      - status "not_configured": no ANTHROPIC_API_KEY is set
      - status "error": the request failed, or there was nothing to research
    Never raises - a failure here should never take down the client's page."""
    if os.environ.get("ANTHROPIC_API_KEY") is None:
        return None, "not_configured", "ANTHROPIC_API_KEY is not set - see the README for how to add it on Render."
    company_name = (company_name or "").strip()
    if not company_name:
        return None, "error", "No company name was available to research."
    if scope not in SCOPE_INSTRUCTIONS:
        scope = "both"

    prompt_lines = [f"Company: {company_name}"]
    if industry:
        prompt_lines.append(f"Industry (as recorded by the firm): {industry}")
    if people:
        prompt_lines.append("Confirmed directors/shareholders: " + ", ".join(people[:20]))
    prompt_lines.append(
        "Research this company (and, where relevant, the named people) using web search, then "
        "call record_entity_research with your findings."
    )
    user_content = "\n".join(prompt_lines)
    system = SYSTEM_PROMPT.format(scope_instruction=SCOPE_INSTRUCTIONS[scope])

    # Client construction and the API call are both wrapped here - neither
    # should ever be allowed to raise out of this function (a bad/expired
    # key, a network blip, or any other client-side failure all degrade to
    # status "error" instead), the same contract as entity_extraction.py.
    try:
        client = _client()
        response = client.messages.create(
            model=DEFAULT_MODEL,
            max_tokens=4096,
            system=system,
            tools=[
                {"type": "web_search_20250305", "name": "web_search", "max_uses": MAX_SEARCHES},
                RESEARCH_TOOL,
            ],
            messages=[{"role": "user", "content": user_content}],
        )
    except Exception as exc:
        return None, "error", str(exc)[:2000]

    tool_input = None
    fallback_text_parts = []
    for block in getattr(response, "content", None) or []:
        block_type = getattr(block, "type", None)
        if block_type == "tool_use" and getattr(block, "name", None) == "record_entity_research":
            tool_input = block.input or {}
            break
        if block_type == "text" and getattr(block, "text", None):
            fallback_text_parts.append(block.text)

    if tool_input is None:
        # The model finished (or ran out of budget) without calling the
        # tool - fall back to whatever plain text it produced rather than
        # losing the searches it already did, but with no sources list to
        # show alongside it.
        fallback_text = "\n".join(fallback_text_parts).strip()
        if not fallback_text:
            return None, "error", "The AI didn't return any findings - try again, or narrow the scope."
        return {"business_profile": fallback_text[:8000], "risk_findings": "", "sources": []}, "done", None

    sources = []
    for s in (tool_input.get("sources") or [])[:20]:
        if isinstance(s, dict) and s.get("url"):
            sources.append({
                "title": (s.get("title") or "").strip()[:300],
                "url": str(s.get("url")).strip()[:500],
            })

    result = {
        "business_profile": (tool_input.get("business_profile") or "").strip()[:8000],
        "risk_findings": (tool_input.get("risk_findings") or "").strip()[:8000],
        "sources": sources,
    }
    return result, "done", None
