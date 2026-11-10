"use client"

import { apiRequest } from "@/lib/api"

export type Source = { id: string; title?: string; url?: string; doi?: string; year?: number; authors?: string[]; text?: string; page?: number; source_url?: string; article_id?: string; source_id?: string; source_type?: string; basis?: string; label?: string; width?: number; height?: number; kind?: string; project_id?: number; document_id?: string | number; original_evidence_id?: string; quote_verified?: boolean; source_role?: string; diagnostic_origin?: string }
export type DiagProject = { id: number; name: string; organisme: string; subproject?: string; year: string; diagnostic_id?: number; status: string; ready: boolean }
export type DiagContext = { mode?: string; project?: {id: number; name: string; organisme: string; year: string; subproject?: string; domain?: string}; diagnostic?: {id: number; fingerprint: string; completed_at?: string}; project_summary?: Record<string, string>; locks?: {title: string; consultant_status?: string}[]; project_evidence_count?: number }
export type WebLink = { id?: string; url: string; title?: string }
export type Usage = { model: string; input_tokens: number; output_tokens: number; cached_tokens: number; web_calls: number; estimated_cost_usd: number }
export type WritingState = { run_id?: string; outline_id?: string; sections: { name: string; saved: boolean; saved_id?: string }[]; pending: string[]; complete: boolean; outdated_plan: boolean }
export type Run = { id: string; session: string; status: string; cancelled: number; answer: string; warnings: string[]; events: {label: string; tool: string; seconds: number}[]; usage: Usage; writing?: WritingState | null }
export type Message = { id: number; role: string; content: string; extra: { run_id?: string; attachments?: {id: string; title: string}[]; writing_updated?: boolean; status?: string; warnings?: string[]; references?: Source[]; web_sources?: WebLink[]; usage?: Usage; writing?: WritingState | null } }
export type Document = { id: string; name: string; kind: "outline" | "section"; body: string; refs: string[]; created: number }
export type ArtifactVersion = { id: string; number: number; created: number; section_count: number; complete: boolean; pending: string[]; restored_from?: string | null }
export type Artifact = ArtifactVersion & { versions: ArtifactVersion[]; latest_id: string; outline: Document; sections: Document[]; content: string; sources: Record<string, Source>; media?: Record<string, Source>; outdated_plan: boolean }
export type SessionSummary = { id: string; title: string; updated: number }
export type Session = SessionSummary & { context: Record<string, unknown> & DiagContext; messages: Message[]; articles: Source[]; documents: Document[]; document_sources: Record<string, Source>; estimated_cost_usd: number; active_run: Run | null; writing?: WritingState | null }
export type Settings = { max_document_bytes?: number; max_attachments?: number; max_attachment_batch_bytes?: number; max_task_usd?: number; stage?: string; model: string; configured: boolean; max_turn_usd: number; max_session_usd: number; web_enabled: boolean; cost_notice: string }

const request = <T,>(path: string, init: RequestInit = {}) => apiRequest<T>(`/scholar-v66${path}`, {cache: "no-store", ...init})
const post = (body: unknown): RequestInit => ({ method: "POST", body: JSON.stringify(body) })
export const getSettings = () => request<Settings>("/config")
export const listSessions = () => request<SessionSummary[]>("/sessions")
export const createSession = () => request<SessionSummary>("/sessions", post({}))
export const listDiagProjects = () => request<DiagProject[]>("/projects")
export const openDiagProject = (projectId: number, fresh = false) => request<SessionSummary>(`/projects/${projectId}/sessions`, post({fresh}))
export const getSession = (id: string) => request<Session>(`/sessions/${encodeURIComponent(id)}`)
export function sendMessage(id: string, message: string, files: File[] = []) {
  const path = `/sessions/${encodeURIComponent(id)}/messages`
  if (!files.length) return request<Run>(path, post({ message }))
  const body = new FormData()
  body.append("message", message)
  for (const file of files) body.append("files", file)
  return request<Run>(`${path}/attachments`, { method: "POST", body })
}
export const getRun = (id: string) => request<Run>(`/runs/${encodeURIComponent(id)}`)
export const stopRun = (id: string) => request<Run>(`/runs/${encodeURIComponent(id)}/cancel`, post({}))
export type ExportFormat = "docx" | "pdf"
export function exportDocument(session: string, version: string, format: ExportFormat) {
  const query = new URLSearchParams({ session, version, format })
  return apiRequest<Blob>("/ennoscholar-v66/export?" + query, { cache: "no-store" }, true, "blob", "")
}
export const getArtifact = (id: string, versionId = "") => request<Artifact>(`/sessions/${encodeURIComponent(id)}/artifact${versionId ? `?version_id=${encodeURIComponent(versionId)}` : ""}`)
export const restoreArtifact = (id: string, versionId: string) => request<Artifact>(`/sessions/${encodeURIComponent(id)}/artifact/restore`, post({version_id: versionId}))
export const getMedia = (id: string, mediaId: string) => apiRequest<Blob>(`/scholar-v66/sessions/${encodeURIComponent(id)}/media/${encodeURIComponent(mediaId)}`, {cache: "no-store"}, true, "blob")
export const running = (run: Run | null) => !!run && ["queued", "running"].includes(run.status)
export function publicUrl(raw?: string) {
  try { const url = new URL(raw || ""); return ["http:", "https:"].includes(url.protocol) && !url.username && !url.password ? url.href : undefined } catch { return undefined }
}

export function uniqueWebLinks(links: WebLink[]) {
  const unique = new Map<string, WebLink>()
  for (const link of links) {
    const safe = publicUrl(link.url)
    if (!safe) continue
    const url = new URL(safe); url.hash = ""
    for (const key of Array.from(url.searchParams.keys())) {
      if (key.toLowerCase().startsWith("utm_") || ["gclid", "fbclid"].includes(key.toLowerCase())) url.searchParams.delete(key)
    }
    const key = url.href
    const old = unique.get(key)
    unique.set(key, {url: key, id: old?.id || link.id, title: old?.title || link.title})
  }
  return Array.from(unique.values())
}
