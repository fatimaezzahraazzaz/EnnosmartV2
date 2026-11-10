"use client"

import { useCallback, useEffect, useRef, useState } from "react"
import { Download, Loader2, RotateCcw, X } from "lucide-react"
import { Button } from "@/components/ui/button"
import * as api from "@/lib/scholar-v66"
import { Markdown, Sources } from "./scholar-v66-markdown"

export function ScholarArtifact({ sessionId, revision, documents, busy, onClose, onRestored }: {
  sessionId: string; revision?: number; documents: api.Document[]; busy: boolean;
  onClose: () => void; onRestored: () => Promise<void>;
}) {
  const [artifact, setArtifact] = useState<api.Artifact | null>(null)
  const [loading, setLoading] = useState(false)
  const [restoring, setRestoring] = useState(false)
  const [exporting, setExporting] = useState(false)
  const [format, setFormat] = useState<api.ExportFormat>("docx")
  const [error, setError] = useState("")
  const [view, setView] = useState("full")
  const requests = useRef(0)
  const hasSections = documents.some(d => d.kind === "section")
  const load = useCallback(async (versionId = "") => {
    const sequence = ++requests.current
    setLoading(true); setError("")
    try {
      const data = await api.getArtifact(sessionId, versionId)
      if (sequence === requests.current) { setArtifact(data); setView("full") }
    } catch (e) { if (sequence === requests.current) setError(e instanceof Error ? e.message : "Document indisponible.") }
    finally { if (sequence === requests.current) setLoading(false) }
  }, [sessionId])
  useEffect(() => {
    setArtifact(null)
    if (hasSections) void load()
    return () => { requests.current++ }
  }, [load, revision, hasSections])

  async function restore() {
    if (!artifact || busy) return
    setRestoring(true); setError("")
    try { setArtifact(await api.restoreArtifact(sessionId, artifact.id)); setView("full"); await onRestored() }
    catch (e) { setError(e instanceof Error ? e.message : "Restauration impossible.") }
    finally { setRestoring(false) }
  }
  async function download() {
    if (!artifact || exporting) return
    setExporting(true)
    setError("")
    try {
      const blob = await api.exportDocument(sessionId, artifact.id, format)
      const url = URL.createObjectURL(blob)
      const link = document.createElement("a")
      link.href = url
      link.download = "etat-de-l-art-v" + artifact.number + "." + format
      link.click()
      setTimeout(() => URL.revokeObjectURL(url), 1_000)
    } catch (e) {
      setError(e instanceof Error ? e.message : "Export impossible.")
    } finally {
      setExporting(false)
    }
  }
  const selected = view === "plan" ? artifact?.outline : artifact?.sections.find(d => d.id === view)
  const shown = selected ? [selected] : artifact?.sections || []
  const refs = new Map<string, api.Source>()
  for (const document of shown) for (const id of document.refs) {
    const source = artifact?.sources[id]
    if (source) refs.set(id, source)
    const parent = source?.source_id || source?.article_id
    if (parent && artifact?.sources[parent]) refs.set(parent, artifact.sources[parent])
  }
  const prefix = `source-artifact-${artifact?.id || "draft"}`
  return <aside id="v66-writing" className="flex h-full min-h-0 w-full min-w-0 flex-col bg-background lg:w-[55%] lg:shrink-0 lg:border-l" aria-label="Document et versions de l’état de l’art">
    <header className="border-b p-5">
      <div className="flex items-center justify-between gap-2"><h2 className="font-semibold">État de l’art · Document</h2><Button variant="ghost" size="sm" onClick={onClose} aria-label="Fermer le document"><X className="size-4" /></Button></div>
      {artifact && <>
        <label className="mt-4 mb-1 block text-xs text-muted-foreground" htmlFor="v66-artifact-version">Historique des versions</label>
        <select id="v66-artifact-version" className="w-full rounded-lg border bg-background p-2 text-sm" value={artifact.id} disabled={loading || restoring} onChange={e => void load(e.target.value)}>
          {[...artifact.versions].reverse().map(v => <option key={v.id} value={v.id}>Version {v.number} · {new Date(v.created * 1000).toLocaleString("fr-FR")} · {v.section_count} sections{v.complete ? "" : " · partielle"}</option>)}
        </select>
        <div className="mt-3 flex flex-wrap items-center gap-2">
          <select
            aria-label="Format d’export"
            className="h-9 rounded-lg border bg-background px-2 text-sm"
            value={format}
            disabled={loading || restoring || exporting}
            onChange={event => setFormat(event.target.value === "pdf" ? "pdf" : "docx")}
          >
            <option value="docx">Word (.docx)</option>
            <option value="pdf">PDF (.pdf)</option>
          </select>
          <Button size="sm" variant="outline" disabled={loading || restoring || exporting} onClick={() => void download()}>
            {exporting ? <Loader2 aria-hidden="true" className="size-4 motion-safe:animate-spin" /> : <Download aria-hidden="true" className="size-4" />}
            {exporting ? "Export en cours…" : "Exporter cette version"}
          </Button>
          {artifact.id !== artifact.latest_id && <Button size="sm" variant="outline" disabled={busy || loading || restoring} onClick={() => void restore()}><RotateCcw className="mr-2 size-4" />{restoring ? "Restauration…" : "Restaurer cette version"}</Button>}</div>
        <p className="mt-2 text-xs leading-5 text-muted-foreground">Une restauration crée une nouvelle version. Les versions suivantes restent conservées.</p>
      </>}
    </header>
    <div className="min-h-0 flex-1 overflow-y-auto p-5 sm:p-6">
      {error && <p role="alert" className="mb-4 rounded-lg border p-3 text-sm text-destructive">{error}</p>}
      {loading ? <p role="status" className="flex items-center gap-2 text-sm"><Loader2 className="size-4 motion-safe:animate-spin" />Chargement du document…</p> : artifact ? <>
        {!artifact.complete && <p role="status" className="mb-4 rounded-lg border p-3 text-sm">Brouillon partiel. À sauvegarder : {artifact.pending.join(" ; ")}</p>}
        {artifact.outdated_plan && <p className="mb-4 text-sm text-muted-foreground">Le plan a été modifié depuis cette rédaction. Cette version reste consultable.</p>}
        <label className="mb-1 block text-xs text-muted-foreground" htmlFor="v66-artifact-view">Lecture</label>
        <select id="v66-artifact-view" value={view} onChange={e => setView(e.target.value)} className="mb-5 w-full rounded-lg border bg-background p-2 text-sm"><option value="full">Texte intégral · {artifact.sections.length} sections</option><option value="plan">Plan de cette version</option>{artifact.sections.map(d => <option key={d.id} value={d.id}>{d.name}</option>)}</select>
        <Markdown text={selected?.body || artifact.content} anchorPrefix={prefix} sessionId={sessionId} sources={artifact.sources} />
        <Sources refs={[...refs.values()]} anchorPrefix={prefix} />
      </> : <><p className="mb-4 text-sm text-muted-foreground">Les sections rédigées apparaîtront ici dès leur sauvegarde.</p>{documents.filter(d => d.kind === "outline").map(d => <Markdown key={d.id} text={d.body} />)}</>}
    </div>
  </aside>
}
