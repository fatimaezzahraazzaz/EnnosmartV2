"use client"

import { apiRequest } from "@/lib/api"

export type Article = { id: string; title: string; authors: string[]; year?: number; doi?: string; venue?: string; abstract?: string; url?: string }
export type Usage = { model: string; input_tokens: number; output_tokens: number; cached_tokens: number; estimated_cost_usd: number }
export type RevisionStatus = "proposed" | "accepted" | "rejected" | "replaced" | "obsolete"
export type Revision = { id: string; number: number; run: string; paragraph_ids: string[]; original: string; proposed: string; explanation: string; refs: string[]; status: RevisionStatus }
export type Run = { id: string; session: string; status: string; cancelled: number; answer: string; warnings: string[]; events: { label: string; seconds: number }[]; usage: Usage }
export type Mode = "amelioration" | "expert"
export type Risk = "faible" | "moyen" | "élevé"
export type SectionRisk = { title: string; first_id: string; last_id: string; risk: Risk; findings: string; expert_questions: string[]; to_prepare: string[] }
export type Assessment = { id: string; run: string; overall_risk: Risk; summary: string; sections: SectionRisk[] }
export type Message = { id: number; role: "user" | "assistant"; content: string; extra: { run_id?: string; mode?: Mode; attachment?: { name: string; paragraphs: number }; status?: string; warnings?: string[]; references?: Article[]; revision_ids?: string[]; assessment_ids?: string[]; usage?: Usage } }
export type OutlineRow = { title: string; level: number; from: string; to: string; words: number }
export type Paragraph = { id: string; text: string; level: number; section: string; editable: boolean }
export type DocumentSummary = { name: string; kind: string; paragraph_count: number; outline: OutlineRow[] }
export type SessionSummary = { id: string; title: string; updated: number; mode: Mode }
export type Session = SessionSummary & { messages: Message[]; revisions: Revision[]; assessments: Assessment[]; articles: Article[]; active_run: Run | null; estimated_cost_usd: number; document: DocumentSummary | null }
export type Settings = { model: string; version: string; configured: boolean; max_turn_usd: number; max_session_usd: number; max_document_bytes: number; extensions: string[]; max_message_chars: number }

const base = "/amelioration-v2"
const id = encodeURIComponent
const request = <T,>(path: string, init: RequestInit = {}) => apiRequest<T>(`${base}${path}`, { cache: "no-store", ...init })
const post = (body: unknown): RequestInit => ({ method: "POST", body: JSON.stringify(body) })

export const getSettings = () => request<Settings>("/config")
export const listSessions = () => request<SessionSummary[]>("/sessions")
export const createSession = (mode: Mode = "amelioration") => request<SessionSummary>("/sessions", post({ mode }))
export const getSession = (sid: string) => request<Session>(`/sessions/${id(sid)}`)
export const setMode = (sid: string, mode: Mode) => request<SessionSummary>(`/sessions/${id(sid)}`, { method: "PATCH", body: JSON.stringify({ mode }) })
export const deleteSession = (sid: string) => request<null>(`/sessions/${id(sid)}`, { method: "DELETE" })
export const getDocument = (sid: string) => request<{ name: string; kind: string; paragraphs: Paragraph[] }>(`/sessions/${id(sid)}/document`)
export const getRun = (rid: string) => request<Run>(`/runs/${id(rid)}`)
export const stopRun = (rid: string) => request<Run>(`/runs/${id(rid)}/cancel`, post({}))
export const decide = (sid: string, rev: string, decision: "accepted" | "rejected" | "proposed") =>
  request<Revision>(`/sessions/${id(sid)}/revisions/${id(rev)}/decision`, post({ decision }))
export const decideMany = (sid: string, revisionIds: string[], decision: "accepted" | "rejected") =>
  request<Revision[]>(`/sessions/${id(sid)}/revisions/decision`, post({ revision_ids: revisionIds, decision }))
export const exportWord = (sid: string, tracked = false) =>
  apiRequest<Blob>(`${base}/sessions/${id(sid)}/export${tracked ? "?tracked=true" : ""}`, { cache: "no-store" }, true, "blob")
export const exportAssessment = (sid: string, aid: string) =>
  apiRequest<Blob>(`${base}/sessions/${id(sid)}/assessments/${id(aid)}/export`, { cache: "no-store" }, true, "blob")

/** « Tufano et al., 2020 »: how a consultant cites an article. */
export function citationLabel(article?: Article) {
  if (!article) return ""
  const authors = article.authors.filter(a => a.trim())
  const name = authors[0]?.trim().split(/\s+/).at(-1) || "Auteur non renseigné"
  return `${name}${authors.length > 1 ? " et al." : ""}, ${article.year || "s.d."}`
}

/** Internal codes such as [A_7f8e…] become (Author et al., year); unknown codes stay visible. */
export const cite = (text: string, articles: Map<string, Article>) =>
  text.replace(/\[?(A_[a-f0-9]{16})\]?/g, (code, ref: string) => articles.has(ref) ? `(${citationLabel(articles.get(ref))})` : code)

export function sendMessage(sid: string, message: string, file?: File | null) {
  if (!file) return request<Run>(`/sessions/${id(sid)}/messages`, post({ message }))
  const body = new FormData()
  body.append("message", message)
  body.append("file", file)
  return request<Run>(`/sessions/${id(sid)}/messages/attachment`, { method: "POST", body })
}

export const running = (run: Run | null) => !!run && ["queued", "running"].includes(run.status)

export function publicUrl(raw?: string) {
  try {
    const url = new URL(raw || "")
    return ["http:", "https:"].includes(url.protocol) && !url.username && !url.password ? url.href : undefined
  } catch { return undefined }
}
