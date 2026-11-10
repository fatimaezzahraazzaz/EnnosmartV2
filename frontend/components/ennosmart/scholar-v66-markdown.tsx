"use client"

import { useEffect, useState } from "react"
import ReactMarkdown, { defaultUrlTransform } from "react-markdown"
import remarkGfm from "remark-gfm"
import * as api from "@/lib/scholar-v66"
import { SourceEvidenceCitations, useProjectSourceDocuments } from "./source-documents-dialog"

function OriginalVisual({ sessionId, id, alt, source }: { sessionId: string; id: string; alt: string; source?: api.Source }) {
  const [url, setUrl] = useState("")
  const [error, setError] = useState("")
  useEffect(() => {
    let active = true; let created = ""
    setUrl(""); setError("")
    api.getMedia(sessionId, id).then(blob => {
      if (active) { created = URL.createObjectURL(blob); setUrl(created) }
    }).catch(e => { if (active) setError(e instanceof Error ? e.message : "Visuel indisponible.") })
    return () => { active = false; if (created) URL.revokeObjectURL(created) }
  }, [sessionId, id])
  return <span role="figure" aria-label={alt} className="my-5 block rounded-xl border bg-background p-3">
    {url ? <a href={url} target="_blank" rel="noopener noreferrer" aria-label={`Agrandir ${alt}`}><img src={url} alt={alt} width={source?.width} height={source?.height} loading="lazy" className="h-auto max-w-full rounded" /></a> : <span className="block p-4 text-sm text-muted-foreground">{error || "Chargement du visuel original…"}</span>}
    <span className="mt-3 block text-xs leading-5 text-muted-foreground">{source?.label || alt} · Source : {source?.title || source?.source_id || "document consulté"}{source?.page ? ` · page ${source.page}` : ""}. Rendu du PDF original.</span>
    {api.publicUrl(source?.source_url) && <a href={api.publicUrl(source?.source_url)} target="_blank" rel="noopener noreferrer" className="mt-1 inline-block text-xs text-primary underline">Consulter le document source</a>}
  </span>
}

export function Markdown({ text, anchorPrefix = "source", sessionId, sources = {} }: { text: string; anchorPrefix?: string; sessionId?: string; sources?: Record<string, api.Source> }) {
  const linked = text.replace(/(?<!!)\[((?:A|E|W|M)_[a-f0-9]{16}(?:\s*[,;]\s*(?:A|E|W|M)_[a-f0-9]{16})*)\](?!\()/g,
    (_, group: string) => group.split(/[,;]/).map(id => `[${id.trim()}](#${anchorPrefix}-${id.trim()})`).join(" "))
  return <div className="space-y-4 break-words leading-7 [&_h1]:mt-7 [&_h1]:text-xl [&_h1]:font-semibold [&_h2]:mt-6 [&_h2]:text-lg [&_h2]:font-semibold [&_h3]:mt-5 [&_h3]:font-semibold [&_p]:my-3 [&_ul]:list-disc [&_ul]:pl-6 [&_ol]:list-decimal [&_ol]:pl-6 [&_li]:my-1 [&_table]:w-full [&_table]:text-sm [&_th]:border [&_th]:bg-muted [&_th]:p-3 [&_td]:border [&_td]:p-3 [&_blockquote]:border-l-2 [&_blockquote]:pl-4 [&_blockquote]:text-muted-foreground [&_pre]:overflow-auto [&_pre]:rounded-lg [&_pre]:bg-muted [&_pre]:p-4">
    <ReactMarkdown remarkPlugins={[remarkGfm]} urlTransform={url => /^media:M_[a-f0-9]{16}$/.test(url) ? url : defaultUrlTransform(url)} components={{
      img: ({src, alt}) => {
        const match = typeof src === "string" && /^media:(M_[a-f0-9]{16})$/.exec(src)
        return match && sessionId ? <OriginalVisual sessionId={sessionId} id={match[1]} alt={alt || "Visuel original"} source={sources[match[1]]} /> : <span className="text-sm text-muted-foreground">[Visuel non conservé : {alt || "source à vérifier"}]</span>
      },
      a: ({href, children}) => href?.startsWith(`#${anchorPrefix}-`)
        ? <a href={href} className="font-medium text-primary underline underline-offset-4">{children}</a>
        : api.publicUrl(href) ? <a href={api.publicUrl(href)} target="_blank" rel="noopener noreferrer" className="text-primary underline underline-offset-4">{children}</a> : <span>{children}</span>,
      table: ({children}) => <div className="overflow-x-auto"><table>{children}</table></div>,
    }}>{linked}</ReactMarkdown>
  </div>
}

export function Sources({ refs = [], links = [], anchorPrefix = "source" }: { refs?: api.Source[]; links?: api.WebLink[]; anchorPrefix?: string }) {
  const projectId = refs.find(ref => ref.source_type === "project")?.project_id
  const projectDocuments = useProjectSourceDocuments(projectId)
  if (!refs.length && !links.length) return null
  const distinctRefs = Array.from(new Map(refs.map(ref => [ref.id, ref])).values())
  const notices = distinctRefs.filter(ref => ref.id.startsWith("A_"))
  const passages = distinctRefs.filter(ref => ref.id.startsWith("E_"))
  const visuals = distinctRefs.filter(ref => ref.id.startsWith("M_"))
  const web = api.uniqueWebLinks([...distinctRefs.filter(ref => ref.id.startsWith("W_")).map(ref => ({id: ref.id, url: ref.url || "", title: ref.title})), ...links])
  return <details className="mt-5 rounded-xl border bg-muted/20 p-4">
    <summary className="cursor-pointer text-sm font-medium">Sources : {notices.length} notices · {passages.length} passages · {web.length} liens Web uniques{visuals.length ? ` · ${visuals.length} visuels` : ""}</summary>
    <div className="mt-4 space-y-4 text-sm">{[...notices, ...passages, ...visuals].map(ref => <div id={`${anchorPrefix}-${ref.id}`} key={ref.id} className="scroll-mt-24 border-t pt-3">
      <p className="font-medium">{ref.title || `Passage de ${ref.source_id || ref.article_id}`}{ref.page ? ` · page ${ref.page}` : ""}</p>
      <p className="text-xs text-muted-foreground">{ref.id}{ref.year ? ` · ${ref.year}` : ""}</p>
      {ref.id.startsWith("A_") ? <p className="text-xs text-muted-foreground">Notice bibliographique ; les passages cités sont présentés séparément.</p> : ref.id.startsWith("M_") ? <p className="text-xs text-muted-foreground">{ref.label} · Visuel original conservé avec sa page et son cadrage.</p> : <p className="text-xs text-muted-foreground">Passage sauvegardé · {ref.source_type === "project" ? `${ref.diagnostic_origin === "express" ? "Document joint au Diag Express" : "Document transmis par Diag"} · rôle : ${ref.source_role || "non précisé"}` : ref.source_type === "web" ? "source Web" : "document"} · {ref.basis || "texte"}</p>}
      {ref.source_type === "project" && ref.project_id && <><p className="mt-1 text-xs text-muted-foreground">Citation {ref.quote_verified ? "vérifiée" : "non certifiée"} par Diag ; le résultat scientifique reste à interpréter.</p><SourceEvidenceCitations projectId={ref.project_id} documents={projectDocuments} evidence={[{evidence_id: ref.original_evidence_id, document_id: ref.document_id, document_name: ref.title, page: ref.page, quote: ref.text}]} /></>}
      {ref.text && <p className="mt-2 max-h-52 overflow-auto whitespace-pre-wrap leading-6">{ref.text}</p>}
      {api.publicUrl(ref.url || ref.source_url) && <a href={api.publicUrl(ref.url || ref.source_url)} target="_blank" rel="noopener noreferrer" className="mt-2 inline-block text-primary underline">Consulter la source</a>}
    </div>)}{web.length > 0 && <p className="border-t pt-3 text-xs text-muted-foreground">Liens Web découverts ou cités. Leur présence ne signifie pas que tout le texte a été lu.</p>}{web.map(link => <a id={link.id ? `${anchorPrefix}-${link.id}` : undefined} key={link.url} href={link.url} target="_blank" rel="noopener noreferrer" className="block text-primary underline">{link.title || link.url}</a>)}</div>
  </details>
}
