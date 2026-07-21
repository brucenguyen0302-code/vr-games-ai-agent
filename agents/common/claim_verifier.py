"""Shared "claimed action" detector.

Given a piece of agent-generated response text plus the tool calls that were
actually made while producing it, finds narration that claims an action
happened in the past tense ("I've flagged this", "logged", "has been
scheduled") without a matching tool call to back it up.

Used from two places that must never drift out of sync:
  - scripts/demo_scenarios.py, as a test-time assertion.
  - agents/sales_agent/agent.py, as a runtime after_agent_callback guard that
    rewrites or neutralizes unverified claims before the response is returned.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable

# LLM output commonly uses a curly apostrophe (’, U+2019) instead of a
# straight one (') in contractions like "I've" — match both.
_APOS_VE = r"(?:'ve|’ve)"

# Verbs like "booked"/"scheduled" show up in legitimate future/conditional
# phrasing too ("help you get that booked") which is not a claim at all —
# require an explicit past-tense construction around the verb so only real
# "this already happened" narration matches.
_PAST_CLAIM = (
    r"(?:\b(?:has been|have been|was|is)\b(?:\s+\S+){{0,3}}?\s+{verb}\b"
    r"|\bsuccessfully\s+{verb}\b"
    r"|\balready\s+{verb}\b"
    r"|\bi(?:" + _APOS_VE + r"| have)\s+(?:already\s+)?{verb}\b)"
)


def _past_claim(verb: str) -> re.Pattern:
    return re.compile(_PAST_CLAIM.format(verb=verb), re.IGNORECASE)


# (pattern, tool_name, label, personal)
#
# `personal` marks patterns whose match span already includes its own
# "I have"/"I've" subject (e.g. "I've flagged"), as opposed to a bare
# fragment ("flagged for owner", bare "escalated"/"logged") that could sit
# in many different sentence shapes. Only `personal` matches can be
# confidently rewritten in place with a matching "I recommend ..." replacement
# — bare fragments get neutralized with a generic substitute instead, since
# we can't tell what grammatical role they're playing in the sentence.
#
# Ordered most-specific first: the personal variants are tried before their
# bare counterparts so "I've escalated" is claimed by the personal pattern
# and the bare "escalated" pattern doesn't also fire on the same span.
_CLAIM_PATTERNS: list[tuple[re.Pattern, str, str, bool]] = [
    (re.compile(rf"\bi(?:{_APOS_VE}| have) flagged\b", re.IGNORECASE), "flag_for_owner_review", "I have flagged", True),
    (re.compile(rf"\bi(?:{_APOS_VE}| have) escalated\b", re.IGNORECASE), "flag_for_owner_review", "I have escalated", True),
    (re.compile(rf"\bi(?:{_APOS_VE}| have) logged\b", re.IGNORECASE), "log_customer_interaction", "I have logged", True),
    (re.compile(r"(?<!auto-)(?<!auto )flagged for (?:the )?owner", re.IGNORECASE), "flag_for_owner_review", "flagged for owner", False),
    (re.compile(r"\bescalated\b", re.IGNORECASE), "flag_for_owner_review", "escalated", False),
    (re.compile(r"\blogged\b(?!\s+in\b)", re.IGNORECASE), "log_customer_interaction", "logged", False),
    (_past_claim("booked"), "create_booking", "booked", False),
    (_past_claim("scheduled"), "schedule_post", "scheduled", False),
]

# Matches an itemized entry that names a handle in its header — model output
# has used all of "*   **@party_planner (...):**", "1. **@user1 ...**",
# a bare "**@user1 (...):**" with no list marker, and "*   **To @user1:**"
# with a filler word before the mention, across different runs of the exact
# same prompt. Allow up to 3 filler words between "**" and the handle, but
# require them on the header *line itself* (optionally after a list marker)
# so an @-mention inside a sentence body doesn't falsely start a new section.
_HANDLE_HEADER_RE = re.compile(
    r"(?m)^[ \t]*(?:(?:[*\-]|\d+[.)])[ \t]+)?\*\*(?:[A-Za-z]+[ \t]+){0,3}@([A-Za-z0-9_.]+)"
)
# A blank line NOT followed by another handle header marks the end of the
# itemized block — text after it (e.g. a trailing "**Note:** ..." summary)
# is back to top-level narrative and shouldn't be attributed to the last item.
_LIST_EXIT_RE = re.compile(
    r"\n[ \t]*\n(?![ \t]*(?:(?:[*\-]|\d+[.)])[ \t]+)?\*\*(?:[A-Za-z]+[ \t]+){0,3}@)"
)


def _handle_sections(text: str) -> list[tuple[str, int, int]]:
    """Best-effort split of a response into (handle, start, end) spans, one
    per itemized customer/DM entry. Returns [] if the response isn't
    structured as a per-handle list (e.g. a single-customer conversation) —
    callers should fall back to a global check in that case."""
    headers = list(_HANDLE_HEADER_RE.finditer(text))
    sections: list[tuple[str, int, int]] = []
    for i, h in enumerate(headers):
        end = headers[i + 1].start() if i + 1 < len(headers) else len(text)
        exit_match = _LIST_EXIT_RE.search(text, pos=h.end())
        if exit_match and exit_match.start() < end:
            end = exit_match.start()
        sections.append((h.group(1), h.start(), end))
    return sections


def _args_reference_handle(args: dict, handle: str) -> bool:
    """True if any string-valued tool-call argument mentions this handle,
    with or without the leading @ (e.g. flag_for_owner_review's free-text
    `context` field, or log_customer_interaction's `customer_handle`)."""
    needles = {handle.lower(), f"@{handle.lower()}"}
    return any(
        isinstance(v, str) and any(n in v.lower() for n in needles)
        for v in args.values()
    )


def _snippet(text: str, start: int, end: int, context: int = 30) -> str:
    """Short excerpt around a regex match, for pointing at the exact claim
    instead of just naming the pattern that fired."""
    s, e = max(0, start - context), min(len(text), end + context)
    excerpt = " ".join(text[s:e].split())  # collapse newlines/whitespace
    return f"{'…' if s > 0 else ''}{excerpt}{'…' if e < len(text) else ''}"


@dataclass(frozen=True)
class Claim:
    label: str
    tool_name: str
    excerpt: str
    start: int
    end: int
    handle: str | None  # None if the claim couldn't be attributed to a specific handle
    personal: bool  # True if match includes its own "I have/I've" subject (safely rewritable in place)
    verified: bool  # True if a matching tool call was actually found


def find_claims(text: str, tool_calls: Iterable[tuple[str, dict]]) -> list[Claim]:
    """Find every past-tense action claim in `text` and check it against
    `tool_calls` (an iterable of (tool_name, args) pairs actually made while
    producing this response).

    Claims inside an itemized "@handle" section are correlated against tool
    calls whose arguments reference that same handle — a flag_for_owner_review
    call for one customer doesn't verify a claim about a different one.
    Claims that can't be attributed to a handle fall back to a coarser
    "was this tool called at all" check.
    """
    tool_calls = list(tool_calls)
    called_tools = {name for name, _ in tool_calls}
    sections = _handle_sections(text)

    claimed_spans: list[tuple[int, int]] = []
    claims: list[Claim] = []
    for pattern, tool_name, label, personal in _CLAIM_PATTERNS:
        for match in pattern.finditer(text):
            if any(s <= match.start() < e or s < match.end() <= e for s, e in claimed_spans):
                continue  # already claimed by a more specific pattern
            claimed_spans.append((match.start(), match.end()))

            handle = next((h for h, s, e in sections if s <= match.start() < e), None)
            if handle is not None:
                verified = any(
                    name == tool_name and _args_reference_handle(args, handle)
                    for name, args in tool_calls
                )
            else:
                verified = tool_name in called_tools

            claims.append(Claim(
                label=label,
                tool_name=tool_name,
                excerpt=_snippet(text, match.start(), match.end()),
                start=match.start(),
                end=match.end(),
                handle=handle,
                personal=personal,
                verified=verified,
            ))

    claims.sort(key=lambda c: c.start)
    return claims


def unverified(claims: Iterable[Claim]) -> list[Claim]:
    return [c for c in claims if not c.verified]


# Gerund for claims we can confidently rewrite in place (the match already
# includes its own "I have/I've" subject, e.g. "I have flagged your question
# about our cake policy" -> "I recommend flagging your question about our
# cake policy"). Deliberately just the bare gerund with no trailing object —
# the original sentence already supplies the object right after the match,
# so adding our own here would duplicate it instead of composing with it.
_GERUND_BY_LABEL = {
    "I have flagged": "flagging",
    "I have escalated": "escalating",
    "I have logged": "logging",
}
# Neutral substitute for bare-fragment claims we can't safely rewrite in
# place without risking broken grammar (see `personal` above).
_NEUTRALIZE = {
    "flag_for_owner_review": "recommended for owner review",
    "log_customer_interaction": "recommended for logging",
    "create_booking": "recommended for booking",
    "schedule_post": "recommended for scheduling",
}


def rewrite_response(text: str, claims: Iterable[Claim]) -> str:
    """Return `text` with every unverified claim neutralized: `personal`
    claims ("I've flagged this...") are rewritten in place into an accurate
    recommendation ("I recommend flagging this..."); bare fragments are
    replaced with a neutral substitute since an in-place grammatical rewrite
    can't be done reliably without knowing the surrounding sentence shape.
    A short transparency note is appended listing what was corrected, so the
    guard's intervention stays visible instead of silently disappearing.

    Pure function: does not mutate `claims` or `text`.
    """
    bad = sorted(unverified(claims), key=lambda c: c.start)
    if not bad:
        return text

    out = text
    for c in sorted(bad, key=lambda c: c.start, reverse=True):
        replacement = (
            f"I recommend {_GERUND_BY_LABEL[c.label]}" if c.personal else _NEUTRALIZE[c.tool_name]
        )
        out = out[: c.start] + replacement + out[c.end :]

    # Describe what was corrected by tool name, not by quoting the original
    # claim labels verbatim ("I have flagged", "logged", ...) — those labels
    # are literally the trigger phrases this same detector looks for, so
    # quoting them here would make the note itself look like a fresh
    # unverified claim the next time this text is scanned.
    affected_tools = ", ".join(sorted({c.tool_name for c in bad}))
    note = (
        f"\n\n_(Guard note: {len(bad)} claim(s) above weren't backed by a "
        f"completed action yet and were adjusted for accuracy — affected: {affected_tools}.)_"
    )
    return out + note
