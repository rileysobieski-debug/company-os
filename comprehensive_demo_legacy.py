"""
comprehensive_demo_legacy.py — Phase 13.2 archive (DO NOT RE-EMBED)
====================================================================
Plan §13 Phase 13 line 690 required `comprehensive_demo.py` to be
rewritten as a "thin vertical-agnostic runner driven by the active
vertical pack (Old Press content archived, not re-embedded)".

This file preserves the original Old-Press-specific `DEPT_BRIEFS` dict
and `DEFAULT_DEPT_BRIEF` string for reference. It is NOT imported by
`comprehensive_demo.py` anymore. Running this file does nothing useful
— it exists only so future readers can see what the pre-Phase-13
hardcoded demo briefs looked like without grepping git history.

If you need to regenerate an Old Press-specific brief, use the
vertical-pack + CompanyConfig render path in `core.vertical_pack`
instead. Company-level overrides for specific briefs are the right
way to express Old-Press-unique text, not by re-embedding this dict
into the runner.

Frozen on: 2026-04-18
"""
from __future__ import annotations


# ---------------------------------------------------------------------------
# Legacy DEPT_BRIEFS — 9 department briefs as shipped pre-Phase 13
# All text is Old Press-specific. Kept verbatim for archival inspection.
# ---------------------------------------------------------------------------
LEGACY_DEPT_BRIEFS: dict[str, str] = {
    "marketing": (
        "DEMO BRIEF: Produce a launch-positioning memo for the inaugural "
        "Old Press Wine Company release.\n\n"
        "Constraints to honor:\n"
        "  - Operating base is Maine (Downeast). Brand narrative is rooted in "
        "    Southwest Virginia (where Riley worked at VMV). Both must be honest "
        "    and coherent — not pasted together.\n"
        "  - Settled positioning: 'Pioneering the future of American Wine'.\n"
        "  - Voice: aspirational, stewardship-focused, American. NOT regional/folksy. "
        "    NOT luxury-pretentious.\n"
        "  - Pre-revenue, solo founder, no outside equity.\n\n"
        "Deliverables in your synthesis:\n"
        "  1. Target customer (one paragraph — psychographic, not demographic).\n"
        "  2. Three positioning pillars that distinguish Old Press from a generic "
        "     'craft American wine' play.\n"
        "  3. The 90-day launch arc: which channels, in what order, why.\n"
        "  4. One worked example of brand voice: a 60-word product page intro for "
        "     the inaugural release. Demonstrate the voice — don't describe it.\n"
        "  5. The single decision Riley needs to make to unblock the next 30 days.\n\n"
        "Use your specialists. Show your reasoning. Reference specific Old Press "
        "context — generic answers will be rejected."
    ),
    "finance": (
        "DEMO BRIEF: Build a 12-month cash-discipline plan for the path to first "
        "commercial sale.\n\n"
        "Constraints to honor:\n"
        "  - Pre-revenue, solo founder, NO outside equity. Debt + grants + revenue-"
        "    funded growth only.\n"
        "  - TTB compliance must be cleared before any sales activity.\n"
        "  - Operating base is Maine; brand narrative tied to Virginia (VMV sale "
        "    closing in a few months — proceeds may or may not flow to Old Press).\n"
        "  - Spend gates: $50 reportable, $500 requires Riley approval.\n\n"
        "Deliverables in your synthesis:\n"
        "  1. Three operating-model scenarios (own bonded winery / alternating "
        "     proprietor / négociant / private-label) with rough capital implications "
        "     for each. Pick which 1-2 the financial modeler should model in depth.\n"
        "  2. The minimum-viable cash budget for the next 12 months under your "
        "     leading scenario, with line items grouped (regulatory, production, "
        "     equipment, brand/web, working capital).\n"
        "  3. The funding stack to cover that budget: debt instruments, grant "
        "     opportunities (specific programs by name where possible), and the "
        "     contingency if VMV proceeds do NOT arrive.\n"
        "  4. Compliance gate status — which TTB / state ABC items are blockers "
        "     for which budget lines.\n"
        "  5. The single biggest financial risk you see, and the early-warning "
        "     metric you'd watch.\n\n"
        "Use your specialists. Show your reasoning. No equity-based options."
    ),
    "operations": (
        "DEMO BRIEF: Map the operational + regulatory critical path from today "
        "to the first TTB-approved bottle under the Old Press label.\n\n"
        "Constraints to honor:\n"
        "  - Operating base is Maine (Downeast, near Frenchmans Bay). VMV (Virginia) "
        "    is being sold; Maine is the chosen permanent base.\n"
        "  - Solo founder. Limited capital. Producing a real wine, not a brand-only "
        "    private-label exercise (unless you make the case otherwise in your output).\n"
        "  - TTB federal + Maine state ABC compliance required.\n"
        "  - Three-tier system (producer/distributor/retailer) constrains DTC "
        "    architecture.\n\n"
        "Deliverables in your synthesis:\n"
        "  1. The four candidate operating models (own bonded winery / alternating "
        "     proprietor / négociant / private-label) — for each: regulatory "
        "     timeline, capital intensity, control over winemaking, primary risk.\n"
        "  2. Your recommended sequencing (what gets done first, second, third) "
        "     with explicit dependencies. Federal TTB items vs Maine state items.\n"
        "  3. The fruit/bulk-wine sourcing decision: Maine, Virginia (sourced from "
        "     Riley's network), or both. Implications of each.\n"
        "  4. A best-guess timeline (in months from today) to first labeled bottle "
        "     under your recommended sequence — with the assumptions you're holding.\n"
        "  5. The single operational blocker most likely to slip the timeline, and "
        "     what would mitigate it.\n\n"
        "Use your specialists. Be specific to Maine + TTB realities — generic "
        "alcohol-startup advice will be rejected."
    ),
    "product-design": (
        "DEMO BRIEF: Propose the product format and visual identity for the "
        "inaugural Old Press release.\n\n"
        "Constraints to honor:\n"
        "  - 'Pioneering the future of American Wine' — the bottle/label needs to "
        "    embody this without being pretentious or folksy.\n"
        "  - Maine ops, Virginia narrative roots, American positioning.\n"
        "  - Pre-revenue, capital-constrained — production format choices have "
        "    real cost implications.\n"
        "  - Old Press Standard: stewardship, craft, integrity.\n\n"
        "Deliverables in your synthesis:\n"
        "  1. Format recommendation: bottle size + closure (cork/screwcap/other), "
        "     case size, with rationale tied to positioning AND capital.\n"
        "  2. Label direction: typography, palette, imagery posture (without "
        "     specifying the exact final art). 2-3 distinct directions if there "
        "     is a real choice; recommend one.\n"
        "  3. The brand mark / wordmark direction — anchored on the existing "
        "     'Old Press' name; consistency with oldpresswine.com.\n"
        "  4. Packaging materials story (closure, capsule, paper, ink) — how "
        "     stewardship shows up in physical choices, not just copy.\n"
        "  5. The single design decision Riley should make next to unlock label "
        "     production for COLA submission.\n\n"
        "Use your specialists. Be specific. Tie every choice back to brand or "
        "capital."
    ),
    "ai-workflow": (
        "DEMO BRIEF: Identify the operational workflows a solo-founder wine "
        "company should automate FIRST, and propose the implementation path.\n\n"
        "Constraints to honor:\n"
        "  - Solo founder. Riley's leverage comes from automation + this "
        "    Company OS itself, not from headcount.\n"
        "  - Pre-revenue. Tools should be free/cheap or pay for themselves clearly.\n"
        "  - Heavy regulatory environment — automations cannot create compliance risk.\n"
        "  - Riley already has a Company OS (this multi-agent system).\n\n"
        "Deliverables in your synthesis:\n"
        "  1. The top 5 workflows to automate, ranked by founder-time-saved per "
        "     dollar spent. Brief description of each.\n"
        "  2. For the #1 priority: the actual implementation plan (tools, data "
        "     flow, who owns it inside the Company OS).\n"
        "  3. Workflows that MUST stay manual (Riley-judgment, high-risk, "
        "     compliance-touching) — name them explicitly.\n"
        "  4. How Company OS itself could be extended to handle 1-2 of the "
        "     workflows directly.\n"
        "  5. The single automation Riley should ship next month.\n\n"
        "Use your specialists. Be specific to a wine startup — generic "
        "'use Zapier' answers will be rejected."
    ),
    "ai-architecture": (
        "DEMO BRIEF: Produce an architectural memo on the Company OS itself "
        "as it currently runs Old Press — read it as a system, not a feature list.\n\n"
        "Constraints to honor:\n"
        "  - Solo founder. Riley's review bandwidth is the binding constraint.\n"
        "  - Pre-revenue, no outside equity.\n"
        "  - Maine operational base, Southwest-Virginia narrative — the system "
        "    must hold this split without re-introducing it as confusion.\n"
        "  - The Company OS code lives at company-os/ in the parent vault. "
        "    Read core/orchestrator.py, core/board.py, core/managers/base.py, "
        "    core/managers/loader.py, comprehensive_demo.py before writing.\n"
        "  - Read this company's config.json, founder_profile.md, context.md, "
        "    and a sample of recent demo-artifacts/ before writing.\n\n"
        "Deliverables in your systems-thinking memo:\n"
        "  1. System map — agents/components as nodes, information flows as edges, "
        "     marking explicit (in code/prompts) vs implicit (assumed) edges.\n"
        "  2. Coherence reading — is the system pointed at one 'Old Press', or do "
        "     the orchestrator / managers / board hold subtly different versions?\n"
        "  3. Top 3 coupling risks — where would changing one thing silently break "
        "     another? Rank by confidence.\n"
        "  4. Top 3 leverage points (Donella-Meadows-style) — smallest changes that "
        "     would most improve the whole system. Rank by cost-to-value.\n"
        "  5. Top 3 drift watches — things working now that won't keep working if "
        "     left alone (memory budgets, stale prompts, cross-dept protocols only "
        "     Riley enforces today).\n"
        "  6. ONE recommended structural change with second-order effects traced "
        "     one-to-two steps downstream.\n\n"
        "Use your systems-thinking specialist. Cite specific file paths from "
        "company-os/ and from this vault. Generic 'multi-agent systems best "
        "practices' will be rejected — this memo is about THIS system running "
        "THIS company."
    ),
    "community": (
        "DEMO BRIEF: Define the audience-building strategy for Old Press, given "
        "the Maine-ops / Virginia-narrative duality.\n\n"
        "Constraints to honor:\n"
        "  - Brand narrative roots in Southwest Virginia (Riley's VMV winemaking "
        "    history). Operating base in Maine (Frenchmans Bay).\n"
        "  - 'Pioneering the future of American Wine' — audience is wine-curious "
        "    Americans, not regional folksiness, not luxury collectors.\n"
        "  - No equity. Owned audience > paid audience for sustainability.\n"
        "  - Pre-revenue — community building has to start before there's a product "
        "    to sell.\n\n"
        "Deliverables in your synthesis:\n"
        "  1. The 'first 1000 true fans' definition — who they are, where they "
        "     already gather, what would draw them to Old Press specifically.\n"
        "  2. Owned-channel strategy: which platforms (newsletter, podcast, "
        "     long-form essay site, Instagram, etc.) and the 90-day content arc.\n"
        "  3. The Maine vs Virginia narrative balance — how both stories live "
        "     together honestly without confusing the audience.\n"
        "  4. Pre-launch cadence: what should be published before there is "
        "     anything to buy, to earn trust by the time the bottle ships.\n"
        "  5. The single community decision Riley needs to make this month.\n\n"
        "Use your specialists. Be specific. Give example post titles or topics."
    ),
    "data": (
        "DEMO BRIEF: Define the metrics and data infrastructure Old Press needs "
        "to track in the first 12 months.\n\n"
        "Constraints to honor:\n"
        "  - Pre-revenue. Most metrics start at zero — the question is which "
        "    leading indicators tell us we're on the path.\n"
        "  - Solo founder — every metric you propose has a maintenance cost.\n"
        "  - DTC-first commerce intended; eventual wholesale TBD.\n"
        "  - Heavy compliance environment.\n\n"
        "Deliverables in your synthesis:\n"
        "  1. The 5-7 metrics that matter MOST in the first 12 months — split "
        "     into operational (TTB/compliance progress), brand (audience growth), "
        "     and financial (cash runway, unit economics once selling).\n"
        "  2. The data infrastructure to capture them: actual tools (e.g. "
        "     Plausible, Klaviyo, a Google Sheet, the Company OS itself).\n"
        "  3. The reporting cadence — what gets reviewed weekly vs monthly vs "
        "     quarterly, and where the dashboard lives.\n"
        "  4. Vanity metrics to AVOID — the things that look productive but "
        "     don't predict survival or growth.\n"
        "  5. The single data investment Riley should make first.\n\n"
        "Use your specialists. Be specific. Avoid 'set up a data warehouse' "
        "answers — pre-revenue means light infrastructure."
    ),
    "editorial": (
        "DEMO BRIEF: Produce demo editorial content that proves the brand voice "
        "is understood. This is a writing exercise, not a strategy memo.\n\n"
        "Constraints to honor:\n"
        "  - Voice: aspirational, stewardship-focused, American. NOT regional/folksy. "
        "    NOT luxury-pretentious. Sophisticated, direct, no hype.\n"
        "  - Themes: stewardship, craft, integrity, long-horizon thinking, "
        "    'Pioneering the future of American Wine'.\n"
        "  - Riley's lived experience: VMV winemaking, soil + vineyard work, "
        "    Maine moving forward.\n\n"
        "Deliverables in your synthesis:\n"
        "  1. A 250-word brand essay titled 'What We Mean By American Wine'. "
        "     Publishable. Embodies the voice.\n"
        "  2. A 100-word product-page intro for the inaugural release (you can "
        "     reference an unnamed varietal — focus on voice not product specs).\n"
        "  3. A short 'About' page bio for Riley as the winemaker — 150 words.\n"
        "  4. A list of 5 essay/post titles you would propose for the first 90 "
        "     days of editorial — each one a single line.\n"
        "  5. The tone-of-voice rules you would put in a one-page style guide "
        "     for any future writer (or AI agent) working on Old Press copy.\n\n"
        "Write the actual content. Do not describe the content. Voice work is "
        "the deliverable."
    ),
}


LEGACY_DEFAULT_DEPT_BRIEF = (
    "DEMO BRIEF: Demonstrate your department's role and value to Old Press Wine "
    "Company in the context of being a pre-revenue, solo-founder, debt-funded "
    "startup with operations in Maine and brand narrative roots in Southwest "
    "Virginia.\n\n"
    "Produce a real artifact (not a meta-description of what your department "
    "would do): a memo, plan, framework, or analysis specific to Old Press's "
    "current priorities. Use your specialists. Show role understanding by what "
    "you produce, not by claiming it."
)


if __name__ == "__main__":
    raise SystemExit(
        "comprehensive_demo_legacy.py is an archive. "
        "Run comprehensive_demo.py instead."
    )
