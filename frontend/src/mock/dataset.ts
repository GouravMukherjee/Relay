// The "Northwind" demo dataset and a tiny keyword-grounded knowledge base.
// Mirrors DEMO_SCRIPT.md so the standalone demo returns the exact cards the
// presenter rehearses — every answer quotes a specific doc, visibly.

import type { Mode, Source } from "../types";

export interface KbEntry {
  id: string;
  keywords: string[];
  title: string;
  answer: string;
  sources: Source[];
  modes?: Mode[]; // restrict to certain modes if set
}

const doc = (document_id: string, title: string, snippet: string, score: number): Source => ({
  document_id,
  title,
  snippet,
  score,
});

export const KNOWLEDGE_BASE: KbEntry[] = [
  {
    id: "sla",
    keywords: ["uptime", "sla", "guarantee", "availability", "reliable"],
    title: "99.9% uptime SLA",
    answer:
      "We offer a 99.9% uptime Service Level Agreement (SLA) financially backed by service credits, ensuring maximum availability for your deployment.",
    sources: [doc("doc_msa", "MSA.pdf", "Service availability of 99.9% measured monthly… customer data deleted within 30 days of termination.", 0.93)],
  },
  {
    id: "security",
    keywords: ["soc 2", "soc2", "compliant", "compliance", "security", "encryption", "sso"],
    title: "Enterprise-grade encryption",
    answer:
      "Data is secured with AES-256 encryption at rest and TLS 1.3 in transit. We maintain SOC 2 Type II attestation, exceeding standard enterprise compliance requirements.",
    sources: [doc("doc_security", "Security_Whitepaper.pdf", "SOC 2 Type II. All data encrypted at rest (AES-256) and in transit (TLS 1.3). SSO (SAML) on Enterprise.", 0.95)],
  },
  {
    id: "battlecard",
    keywords: ["acme", "competitor", "cheaper", "less", "same thing", "versus", "vs"],
    title: "Why Relay beats Acme",
    answer:
      "Acme processes recordings post-call — they summarize after the fact. Relay is real-time and grounded in your own docs, so cited answers appear live, mid-conversation.",
    sources: [doc("doc_battlecard", "Battlecard.pdf", "vs Acme: Acme is post-call only. Relay wins on real-time surfacing + grounded citations during the call.", 0.91)],
  },
  {
    id: "pricing",
    keywords: ["price", "pricing", "cost", "enterprise", "run us", "seat", "plan", "discount"],
    title: "Enterprise pricing",
    answer:
      "Enterprise is custom-priced. Growth is $99/seat and Starter $49/seat, with a 15% discount on annual billing.",
    sources: [doc("doc_pricing", "Pricing.pdf", "Starter $49/seat/mo · Growth $99/seat/mo · Enterprise custom. Annual billing: 15% discount.", 0.94)],
  },
  {
    id: "datacenter",
    keywords: ["data center", "datacenter", "region", "failover", "outage", "global"],
    title: "Global data center coverage",
    answer:
      "To support that uptime, we deploy across 14 global regions with active-active failover, so local outages won't impact your service.",
    sources: [doc("doc_infra", "Infrastructure_Map.pdf", "14 regions, active-active failover. Regional outage isolation.", 0.9)],
  },
  {
    id: "desk_sync",
    keywords: ["sync", "crm", "export", "broken"],
    modes: ["desk"],
    title: "Re-authenticate the CRM connection",
    answer:
      "Re-authenticate the CRM connection under Settings → Integrations — this fixed the same sync issue on your account in March.",
    sources: [
      doc("doc_faq", "FAQ.pdf", "If CRM sync stalls, re-authenticate the integration from Settings → Integrations.", 0.9),
      doc("doc_ticket_1023", "Ticket #1023", "CRM export sync failing. Resolved via OAuth re-auth on Growth tier (Mar 12).", 0.86),
    ],
  },
];

export function retrieve(text: string, mode: Mode): KbEntry | null {
  const q = text.toLowerCase();
  let best: { entry: KbEntry; hits: number } | null = null;
  for (const entry of KNOWLEDGE_BASE) {
    if (entry.modes && !entry.modes.includes(mode)) continue;
    const hits = entry.keywords.reduce((n, k) => (q.includes(k) ? n + 1 : n), 0);
    if (hits > 0 && (!best || hits > best.hits)) best = { entry, hits };
  }
  return best?.entry ?? null;
}

// ── Scripted demo beats (the rehearsed flow) ──────────────────────────────────

export interface Beat {
  speaker: string;
  text: string;
}

export const LIVE_BEATS: Beat[] = [
  { speaker: "prospect", text: "Before we go further — what's your uptime guarantee?" },
  { speaker: "prospect", text: "And are you SOC 2 compliant?" },
  { speaker: "prospect", text: "Honestly, Acme told us they do the same thing for less." },
  { speaker: "prospect", text: "What would Enterprise run us?" },
];

export const DESK_BEATS: Beat[] = [
  { speaker: "customer", text: "Hey, that CRM export sync issue is back again." },
  { speaker: "rep", text: "Looking into that now for you, Sarah. One moment." },
  { speaker: "customer", text: "Thanks, it's blocking the end-of-quarter report." },
];

// Fixed customer identity for the Desk demo (matches the Stitch screen).
export const DESK_CUSTOMER = {
  name: "Sarah Chen",
  company: "Acme Corp",
  plan: "Growth Plan",
  tickets: [
    { title: "CRM export sync", meta: "resolved Mar 12" },
    { title: "Onboarding setup", meta: "resolved Feb 2" },
  ],
};

// Intake is a guided Q&A; the engine fills qualifiers as the "caller" answers.
export const INTAKE_BEATS: Beat[] = [
  { speaker: "relay", text: "What's the primary challenge you're looking to solve with real-time AI?" },
  { speaker: "caller", text: "We're struggling with onboarding latency. Reps spend too much time looking up technical specs." },
  { speaker: "relay", text: "Understood. Who owns this decision, and what budget are you working with?" },
  { speaker: "caller", text: "I'm the VP of Engineering, so this is my call. Budget is around $40 to 60k a year." },
  { speaker: "relay", text: "Great. And what's your timeline for getting this in place?" },
  { speaker: "caller", text: "We're evaluating this quarter and want to replace our manual onboarding." },
];

// Fixed lead identity for the Intake demo (matches the Stitch screen).
export const INTAKE_LEAD = {
  name: "Jordan Mraz",
  company: "Brightwave Inc.",
  email: "vp.eng@brightwave.io",
};

// Demo data for the dashboard section tabs (used when no backend is connected).
export const DEMO_SESSIONS = [
  { session_id: "ses_a1", mode: "live" as const, status: "ended" as const, started_at: "2026-06-06T16:02:00Z", ended_at: "2026-06-06T16:14:00Z", card_count: 4 },
  { session_id: "ses_a2", mode: "desk" as const, status: "ended" as const, started_at: "2026-06-06T14:31:00Z", ended_at: "2026-06-06T14:39:00Z", card_count: 1 },
  { session_id: "ses_a3", mode: "intake" as const, status: "ended" as const, started_at: "2026-06-05T11:20:00Z", ended_at: "2026-06-05T11:33:00Z", card_count: 0 },
];

export const DEMO_USERS = [
  { id: "usr_1", name: "Riyan Anosh", role: "Founder", email: "riyananosh@gmail.com" },
  { id: "usr_2", name: "Sarah Lin", role: "Head of CS", email: "sarah@relay.ai" },
  { id: "usr_3", name: "Marcus Reed", role: "Account Executive", email: "marcus@relay.ai" },
];

export const DEMO_NOTIFICATIONS = [
  { id: "ntf_1", text: "Hot lead routed to #sales — Jordan Mraz (82)", read: false, created_at: "2026-06-06T16:05:00Z" },
  { id: "ntf_2", text: "Security_Whitepaper.pdf finished ingesting (22 chunks)", read: false, created_at: "2026-06-06T15:48:00Z" },
  { id: "ntf_3", text: "Desk resolution sent to Sarah Chen", read: true, created_at: "2026-06-06T14:39:00Z" },
];

export const DEMO_DOCS = [
  { document_id: "doc_msa", title: "MSA.pdf", status: "ready" as const, chunk_count: 18, created_at: new Date().toISOString() },
  { document_id: "doc_security", title: "Security_Whitepaper.pdf", status: "ready" as const, chunk_count: 22, created_at: new Date().toISOString() },
  { document_id: "doc_pricing", title: "Pricing.pdf", status: "ready" as const, chunk_count: 9, created_at: new Date().toISOString() },
  { document_id: "doc_battlecard", title: "Battlecard.pdf", status: "ready" as const, chunk_count: 6, created_at: new Date().toISOString() },
  { document_id: "doc_faq", title: "FAQ.pdf", status: "ready" as const, chunk_count: 14, created_at: new Date().toISOString() },
];
