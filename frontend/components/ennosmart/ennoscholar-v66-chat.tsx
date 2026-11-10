"use client"

import { useCallback, useEffect, useRef, useState } from "react"
import { ArrowUp, BookOpen, FileText, Loader2, Paperclip, Plus, Square, X } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Dialog, DialogTrigger } from "@/components/ui/dialog"
import * as api from "@/lib/scholar-v66"
import { Markdown, Sources } from "./scholar-v66-markdown"
import { ScholarArtifact } from "./scholar-v66-artifact"
import { NewScholarConversation } from "./scholar-v66-new-conversation"
import { ScholarDemo } from "./scholar-v66-demo"

export default function EnnoScholarV66Chat({ userId, showBudget = false, initialProjectId, onProjectContextOpened }: {
  userId: number; showBudget?: boolean; initialProjectId?: number | null; onProjectContextOpened?: () => void
}) {
  const [sessions, setSessions] = useState<api.SessionSummary[]>([])
  const [session, setSession] = useState<api.Session | null>(null)
  const [settings, setSettings] = useState<api.Settings | null>(null)
  const [run, setRun] = useState<api.Run | null>(null)
  const [draft, setDraft] = useState("")
  const [error, setError] = useState("")
  const [loading, setLoading] = useState(true)
  const [submitting, setSubmitting] = useState(false)
  const [attachments, setAttachments] = useState<File[]>([])
  const [workspace, setWorkspace] = useState(false)
  const [newOpen, setNewOpen] = useState(false)
  const input = useRef<HTMLTextAreaElement>(null)
  const fileInput = useRef<HTMLInputElement>(null)
  const messageScroll = useRef<HTMLDivElement>(null)
  const selectedId = useRef("")
  const savedCheckpoint = useRef("")
  const busy = loading || submitting || api.running(run)
  const attachmentLimit = settings?.max_attachments ?? 3
  const batchLimit = settings?.max_attachment_batch_bytes ?? 60 * 1024 * 1024

  const open = useCallback(async (id: string) => {
    selectedId.current = id
    const data = await api.getSession(id)
    if (selectedId.current !== id) return
    setSession(data); setRun(data.active_run)
    localStorage.setItem(`ennoscholar-v66-${userId}`, id)
  }, [userId])

  const refresh = useCallback(async (id: string) => {
    const [data, list] = await Promise.all([api.getSession(id), api.listSessions()])
    if (selectedId.current !== id) return
    setSession(data); setSessions(list)
  }, [])

  useEffect(() => {
    let active = true
    async function init() {
      try {
        const [list, config] = await Promise.all([api.listSessions(), api.getSettings()])
        if (!active) return
        setSettings(config); setSessions(list)
        const requested = initialProjectId || Number(new URLSearchParams(window.location.search).get("project_id"))
        if (requested) {
          const created = await api.openDiagProject(requested)
          if (!active) return
          await open(created.id); setSessions(await api.listSessions())
          onProjectContextOpened?.()
          return
        }
        const remembered = localStorage.getItem(`ennoscholar-v66-${userId}`)
        const id = list.find(s => s.id === remembered)?.id || list[0]?.id
        if (id) await open(id)
      } catch (e) { if (active) setError(e instanceof Error ? e.message : "Chargement impossible.") }
      finally { if (active) setLoading(false) }
    }
    void init()
    return () => { active = false; selectedId.current = "" }
  }, [userId, open])

  useEffect(() => {
    if (!run || !api.running(run)) return
    let active = true
    let timer: ReturnType<typeof setTimeout>
    async function poll() {
      try {
        const latest = await api.getRun(run!.id)
        if (!active) return
        setError("")
        setRun(latest)
        if (!api.running(latest)) { await refresh(latest.session); input.current?.focus(); return }
        const checkpoint = latest.writing && [latest.writing.outline_id, ...latest.writing.sections.map(s => s.saved_id)].join(",")
        if (checkpoint && savedCheckpoint.current !== `${latest.id}:${checkpoint}`) {
          savedCheckpoint.current = `${latest.id}:${checkpoint}`
          await refresh(latest.session)
        }
      } catch (e) { if (active) setError(e instanceof Error ? e.message : "Connexion interrompue. Nouvelle tentative…") }
      if (active) timer = setTimeout(poll, 1600)
    }
    timer = setTimeout(poll, 800)
    return () => { active = false; clearTimeout(timer) }
  }, [run?.id, run?.status, refresh])

  useEffect(() => {
    const messages = messageScroll.current
    if (messages) messages.scrollTop = messages.scrollHeight
  }, [session?.id, session?.messages.length, run?.id, workspace])

  async function createConversation(projectId?: number) {
    if (busy) return
    setSubmitting(true)
    setError("")
    try {
      const created = projectId === undefined ? await api.createSession() : await api.openDiagProject(projectId, true)
      setSessions(list => [created, ...list.filter(item => item.id !== created.id)])
      await open(created.id)
      setDraft("")
      setAttachments([])
      setWorkspace(false)
      setNewOpen(false)
    } finally { setSubmitting(false) }
  }

  async function send() {
    if (!draft.trim() || busy) return
    setSubmitting(true); setError("")
    try {
      let sid = session?.id
      if (!sid) { const created = await api.createSession(); sid = created.id; await open(sid) }
      const started = await api.sendMessage(sid, draft.trim(), attachments)
      setRun(started); setDraft(""); setAttachments([]); await refresh(sid)
    } catch (e) { setError(e instanceof Error ? e.message : "Envoi impossible.") }
    finally { setSubmitting(false) }
  }

  function attach(files: File[]) {
    if (fileInput.current) fileInput.current.value = ""
    if (!files.length || busy) return
    const next = [...attachments, ...files]
    if (next.length > attachmentLimit) {
      setError(`Joignez au plus ${attachmentLimit} documents par message. Envoyez le lot suivant dans un autre message.`)
      return
    }
    if (next.some(file => !file.size || file.size > (settings?.max_document_bytes ?? batchLimit))
        || next.reduce((total, file) => total + file.size, 0) > batchLimit) {
      setError(`Chaque fichier doit être lisible et le lot doit tenir dans ${Math.floor(batchLimit / 1024 / 1024)} Mo.`)
      return
    }
    setAttachments(next); setError(""); input.current?.focus()
  }

  const documents = session?.documents || []
  const wroteDocument = (m: api.Message) => !!m.extra.writing?.sections.some(s => s.saved_id) &&
    (m.extra.writing_updated ?? !!(m.extra.run_id && m.extra.writing?.run_id === m.extra.run_id))
  const lastWritingMessage = [...(session?.messages || [])].reverse().find(m => m.role === "assistant" && wroteDocument(m))
  function openWriting() {
    setWorkspace(true)
  }
  useEffect(() => { if (lastWritingMessage) openWriting() }, [session?.id, lastWritingMessage?.id])

  return <Dialog open={newOpen} onOpenChange={value => { if (!submitting) setNewOpen(value) }}>
  <div className="flex h-full min-h-0 min-w-0 flex-col overflow-hidden rounded-2xl border bg-background lg:flex-row">
    {!workspace && <aside className="hidden min-h-0 w-52 shrink-0 border-r bg-muted/25 p-4 lg:flex lg:flex-col xl:w-60">
      <DialogTrigger render={<Button disabled={busy} variant="outline" className="mb-6 w-full justify-start gap-2" />}><Plus className="size-4" />Nouvelle conversation</DialogTrigger>
      <p className="mb-3 text-xs font-medium uppercase tracking-wider text-muted-foreground">Conversations</p>
      <nav aria-label="Historique scientifique" className="min-h-0 space-y-1 overflow-y-auto">{sessions.map(s => <button key={s.id} disabled={busy} onClick={() => { setError(""); if (session?.id !== s.id) setAttachments([]); void open(s.id).catch(e => setError(e.message)) }} className={`w-full cursor-pointer rounded-lg px-3 py-3 text-left text-sm transition-colors hover:bg-muted focus-visible:outline-2 focus-visible:outline-primary disabled:opacity-50 ${session?.id === s.id ? "bg-muted font-medium" : ""}`}>{s.title}</button>)}</nav>
      <p className="mt-6 text-xs leading-5 text-muted-foreground">Les articles, analyses et brouillons restent dans leur conversation.</p>
    </aside>}

    <section className={`min-h-0 min-w-0 flex-1 flex-col overflow-y-auto ${workspace && session ? "hidden lg:flex" : "flex"}`} aria-label="Conversation EnnoScholar V66">
      <header className="flex shrink-0 flex-wrap items-center justify-between gap-3 border-b px-4 py-3 sm:px-8">
        <div className="min-w-0"><div className="flex flex-wrap items-center gap-2"><h1 className="flex items-center gap-2 text-lg font-semibold"><BookOpen aria-hidden="true" className="size-5 text-primary" />EnnoScholar</h1><ScholarDemo /></div><p className="mt-1 text-xs text-muted-foreground">{settings?.model || "Assistant scientifique"} · Recherche, analyse et état de l’art CIR</p></div>
        <div className="flex items-center gap-2">
          <DialogTrigger render={<Button className={workspace ? undefined : "lg:hidden"} variant="outline" size="sm" disabled={busy} />}><Plus className="mr-1 size-4" />Nouveau</DialogTrigger>
          <Button variant={workspace ? "secondary" : "outline"} size="sm" aria-expanded={workspace} aria-controls="v66-writing" onClick={() => setWorkspace(!workspace)}><FileText className="mr-2 size-4" />Rédaction ({documents.length})</Button>
        </div>
      </header>
      {session?.context.mode === "diag" && <details className="max-h-[min(7rem,15dvh)] shrink-0 overflow-y-auto border-b bg-muted/20 px-4 py-2 sm:px-8 lg:max-h-[26dvh]">
        <summary className="cursor-pointer text-sm font-medium">Contexte de {session.context.project?.name}{session.context.project?.subproject ? ` / ${session.context.project.subproject}` : ""} · objectifs, démarches et résultats</summary>
        <dl className="mt-3 space-y-3 text-sm">{[["project_summary", "Synthèse"], ["main_objective", "Objectif"], ["methods_summary", "Méthodes et démarches"], ["results_summary", "Résultats"], ["constraints_summary", "Contraintes"]].map(([key, label]) => <div key={key}><dt className="font-medium">{label}</dt><dd className="mt-1 whitespace-pre-wrap leading-6 text-muted-foreground">{session.context.project_summary?.[key] || "Non renseigné dans le diagnostic."}</dd></div>)}</dl>
        <p className="mt-3 text-sm font-medium">Verrous conservés</p><ul className="mt-1 list-disc space-y-1 pl-5 text-sm">{session.context.locks?.map((l, i) => <li key={i}>{l.title}</li>)}</ul>
        {!session.context.locks?.length && <p className="mt-1 text-sm text-muted-foreground">Aucun verrou marqué « gardé » ; les objectifs et résultats restent utilisables.</p>}
        <p className="mt-3 text-xs leading-5 text-muted-foreground">Aperçu du diagnostic sauvegardé. Posez une question pour explorer les détails et les preuves. Le gap avec la littérature reste à établir.</p>
      </details>}
      <div className="flex shrink-0 items-center gap-2 border-b px-4 py-2 lg:hidden"><label className="shrink-0 text-xs text-muted-foreground" htmlFor="v66-history">Conversation</label><select id="v66-history" value={session?.id || ""} disabled={busy} className="min-w-0 flex-1 rounded-md border bg-background p-2 text-sm" onChange={e => void open(e.target.value).catch(err => setError(err.message))}><option value="" disabled>Choisir une conversation</option>{sessions.map(s => <option value={s.id} key={s.id}>{s.title}</option>)}</select></div>
      {settings && !settings.configured && <p role="alert" className="border-b px-6 py-3 text-sm text-destructive">La clé OpenAI doit être configurée côté serveur avant d’envoyer un message.</p>}

      <div ref={messageScroll} className="min-h-0 min-w-0 flex-1 overflow-y-auto overscroll-contain px-4 sm:px-8">
        <div className="mx-auto max-w-3xl py-4 sm:py-6">
          {loading ? <p role="status" className="text-sm text-muted-foreground">Chargement de vos conversations…</p> : !session?.messages.length ? <div className="py-10 sm:py-16">
            <p className="mb-4 text-xs font-medium uppercase tracking-widest text-primary">Votre espace scientifique</p>
            <h2 className="max-w-xl text-3xl font-semibold leading-tight">Une question, un article,<br />un état de l’art à construire.</h2>
            <p className="mt-5 max-w-xl leading-7 text-muted-foreground">Explorez un sujet, approfondissez une méthode ou confrontez plusieurs résultats. Posez vos questions librement.</p>
            <div className="mt-8 grid gap-3 sm:grid-cols-2">{["Trouve des articles sur mon problème de recherche.", "Explique en détail l’article que je vais importer.", "Compare ces méthodes et leurs limites.", "Construisons le plan de mon état de l’art CIR."].map(text => <button key={text} onClick={() => {setDraft(text); input.current?.focus()}} className="cursor-pointer rounded-xl border p-4 text-left text-sm leading-6 hover:bg-muted focus-visible:outline-2 focus-visible:outline-primary">{text}</button>)}</div>
          </div> : session.messages.map(message => <article key={message.id} className="mb-9" aria-label={message.role === "user" ? "Votre message" : "Réponse scientifique"}>
            <p className="mb-2 text-xs font-semibold uppercase tracking-wider text-muted-foreground">{message.role === "user" ? "Vous" : "EnnoScholar"}</p>
            {message.role === "user" ? <div className="rounded-xl bg-muted/60 px-5 py-4">
              <p className="whitespace-pre-wrap break-words leading-7">{message.content}</p>
              {!!message.extra.attachments?.length && <ul aria-label="Documents joints" className="mt-2 space-y-1 text-sm text-muted-foreground">
                {message.extra.attachments.map((file, index) => <li key={`${file.id}-${index}`} className="flex items-center gap-2 break-all"><Paperclip className="size-4 shrink-0" />{file.title}</li>)}
              </ul>}
            </div> : <>
              {wroteDocument(message) ? <>
                <div className="rounded-xl border bg-muted/20 p-5"><p className="flex items-center gap-2 font-semibold"><FileText className="size-5" />État de l’art sauvegardé</p><p className="mt-2 text-sm text-muted-foreground">{message.extra.writing!.sections.filter(s => s.saved).length} sections rédigées · Texte intégral et historique des versions</p><Button className="mt-4" onClick={openWriting}>Lire le texte intégral</Button></div>
                { /!\[[^\]\n]*\]\(media:M_[a-f0-9]{16}\)/.test(message.content) ? <><Markdown text={message.content} sessionId={session.id} sources={Object.fromEntries((message.extra.references || []).map(r => [r.id, r]))} /><Sources refs={message.extra.references} links={message.extra.web_sources} /></> : <details className="mt-4"><summary className="cursor-pointer text-sm font-medium">Bilan de la rédaction et compléments</summary><Markdown text={message.content} sessionId={session.id} sources={Object.fromEntries((message.extra.references || []).map(r => [r.id, r]))} /><Sources refs={message.extra.references} links={message.extra.web_sources} /></details>}
              </> : <><Markdown text={message.content} sessionId={session.id} sources={Object.fromEntries((message.extra.references || []).map(r => [r.id, r]))} /><Sources refs={message.extra.references} links={message.extra.web_sources} /></>}
              {message.extra.warnings?.filter(w => w.trim() !== message.content.trim()).map((w, i) => <p key={i} className="mt-3 rounded-lg border px-3 py-2 text-sm text-muted-foreground">{message.extra.writing?.complete && w.startsWith("Limite d'appels atteinte") ? "Les sections sont sauvegardées. Le traitement a ensuite atteint sa limite d’appels." : w}</p>)}
            </>}
          </article>)}
          {api.running(run) && <div role="status" aria-live="polite" className="mb-6 flex items-center gap-3 rounded-xl border p-4 text-sm"><Loader2 className="size-4 motion-safe:animate-spin" /><div><p>{run?.cancelled ? "Arrêt demandé : l’opération en cours doit se terminer." : run?.events.at(-1)?.label || "Votre demande est en attente de traitement…"}</p><p className="mt-1 text-xs text-muted-foreground">Les résultats et les documents seront sauvegardés dans cette conversation.</p></div></div>}
        </div>
      </div>
      <div className="shrink-0 border-t px-4 py-2 sm:px-8 sm:py-3">
        <div className="mx-auto max-w-3xl">
          {error && <p role="alert" className="mb-3 rounded-lg bg-destructive/10 p-3 text-sm text-destructive">{error}</p>}
          <form onSubmit={e => {e.preventDefault(); void send()}} className="rounded-2xl border bg-background p-3 focus-within:ring-2 focus-within:ring-primary/30">
            <label htmlFor="v66-message" className="sr-only">Votre question scientifique</label>
            <textarea ref={input} id="v66-message" value={draft} onChange={e => setDraft(e.target.value)} maxLength={16000} rows={2} placeholder="Posez votre question ou décrivez ce que vous voulez approfondir…" className="max-h-[min(12rem,25dvh)] w-full resize-y bg-transparent p-2 text-base leading-6 outline-none" onKeyDown={e => {if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {e.preventDefault(); void send()}}} />
            {!!attachments.length && <ul aria-label="Documents à joindre" className="mb-2 flex flex-wrap gap-2 px-2">
              {attachments.map((file, index) => <li key={`${file.name}-${index}`} className="flex max-w-full items-center gap-1 rounded-lg border pl-2 text-sm">
                <span className="truncate" title={file.name}>{file.name}</span>
                <Button type="button" size="icon" variant="ghost" disabled={busy} aria-label={`Retirer ${file.name}`} onClick={() => setAttachments(files => files.filter((_, i) => i !== index))}><X className="size-4" /></Button>
              </li>)}
            </ul>}
            <div className="flex items-center justify-between gap-3"><div className="flex flex-wrap items-center gap-2">
              <input ref={fileInput} type="file" multiple accept=".pdf,.docx,.xlsx,.xls,.txt,.md" className="hidden" onChange={e => attach(Array.from(e.target.files ?? []))} />
              <Button type="button" variant="ghost" size="sm" disabled={busy || attachments.length >= attachmentLimit} onClick={() => fileInput.current?.click()}>
                <Paperclip aria-hidden="true" className="size-4" />Joindre
                <span className="rounded-full bg-brand/10 px-1.5 py-0.5 text-[10px] font-semibold text-brand">Nouveau</span>
              </Button>
              <span className="text-xs text-muted-foreground">{attachments.length}/{attachmentLimit} · PDF, Word, Excel</span>
            </div>
              {api.running(run) ? <Button type="button" variant="outline" onClick={() => void api.stopRun(run!.id).then(setRun).catch(e => setError(e.message))}><Square className="mr-2 size-4" />Arrêter</Button> : <Button type="submit" disabled={busy || !draft.trim() || settings?.configured === false} aria-label="Envoyer votre question">{submitting ? <Loader2 className="size-4 motion-safe:animate-spin" /> : <ArrowUp className="size-4" />}</Button>}
            </div>
          </form>
          <p className="mt-2 text-center text-xs leading-4 text-muted-foreground">
            {showBudget && <>Coût estimé : {(session?.estimated_cost_usd || 0).toFixed(3)} $ · Budget par demande : {settings?.max_turn_usd ?? "—"} $ · Conversation : {settings?.max_session_usd ?? "—"} $. </>}
            L’IA peut faire des erreurs. Vérifiez les informations importantes.
          </p>
        </div>
      </div>
    </section>

    {workspace && session && <ScholarArtifact sessionId={session.id} revision={lastWritingMessage?.id} documents={documents} busy={busy} onClose={() => setWorkspace(false)} onRestored={() => refresh(session.id)} />}
  </div>
  {newOpen && <NewScholarConversation creating={submitting} onCreate={createConversation} />}
  </Dialog>
}
