"use client"

import { useCallback, useEffect, useRef, useState } from "react"
import { createPortal } from "react-dom"
import ReactMarkdown from "react-markdown"
import remarkGfm from "remark-gfm"
import { ArrowUp, Check, CheckCheck, Download, FilePenLine, FileText, Loader2, Paperclip, Plus, Scale, Square, Trash2, Undo2, X } from "lucide-react"
import { Button } from "@/components/ui/button"
import * as api from "@/lib/amelioration-v2"

const MODES: Record<api.Mode, { label: string; short: string; kicker: string; title: string; intro: string; placeholder: string; suggestions: string[] }> = {
  amelioration: {
    label: "Amélioration", short: "Amélioration", kicker: "Votre relecteur CIR", title: "Trouvons ensemble les points faibles de votre CIR.",
    intro: "Déposez un CIR complet ou collez un paragraphe. Je vous explique ce qui est fragile, pourquoi, et je propose une version améliorée, appuyée sur des articles scientifiques quand il le faut.",
    placeholder: "Collez un paragraphe ou posez une question…",
    suggestions: [
      "Analyse les points faibles de mon CIR et explique-moi les priorités.",
      "Améliore le style de ce paragraphe pour qu’il soit plus CIR : ",
      "Renforce l’état de l’art avec des articles scientifiques récents.",
      "Pourquoi ce verrou risque-t-il d’être requalifié en innovation ?",
    ],
  },
  expert: {
    label: "Expert du ministère", short: "Expert ministère", kicker: "Préparation au contrôle", title: "Votre dossier, vu par un expert du ministère.",
    intro: "Je juge le dossier comme l’expert scientifique mandaté lors d’un contrôle : risque par partie, questions qu’il poserait, preuves à préparer. Je peux aussi vous aider à répondre à un rapport d’expert.",
    placeholder: "Demandez une pré-expertise ou collez un rapport…",
    suggestions: [
      "Évalue mon CIR comme un expert du ministère : risque par partie et preuves à préparer.",
      "Quelles questions un expert me poserait-il sur les verrous ? Pose-les-moi une par une.",
      "L’état de l’art tient-il face à un expert ? Cherche des travaux antérieurs qui fragiliseraient la nouveauté.",
      "Voici le rapport de l’expert, aide-moi à préparer la réponse point par point : ",
    ],
  },
}
const RISK_STYLE: Record<api.Risk, string> = {
  "élevé": "bg-destructive/15 text-destructive", moyen: "bg-amber-500/15 text-amber-700 dark:text-amber-400",
  faible: "bg-emerald-500/15 text-emerald-700 dark:text-emerald-400",
}
const STATUS: Record<api.RevisionStatus, string> = {
  proposed: "À valider", accepted: "Acceptée", rejected: "Refusée",
  replaced: "Remplacée par une proposition plus récente", obsolete: "Porte sur une version précédente du document",
}

export default function EnnoAmeliorationV2Chat({ userId, showBudget = false }: { userId: number; showBudget?: boolean }) {
  const [sessions, setSessions] = useState<api.SessionSummary[]>([])
  const [session, setSession] = useState<api.Session | null>(null)
  const [settings, setSettings] = useState<api.Settings | null>(null)
  const [run, setRun] = useState<api.Run | null>(null)
  const [draft, setDraft] = useState("")
  const [file, setFile] = useState<File | null>(null)
  const [error, setError] = useState("")
  const [loading, setLoading] = useState(true)
  const [submitting, setSubmitting] = useState(false)
  const [panel, setPanel] = useState<"document" | null>(null)
  const [focusId, setFocusId] = useState("")
  const [draftMode, setDraftMode] = useState<api.Mode>("amelioration")
  const [headerSlot, setHeaderSlot] = useState<HTMLElement | null>(null)
  const [wordMenu, setWordMenu] = useState(false)
  const input = useRef<HTMLTextAreaElement>(null)
  const fileInput = useRef<HTMLInputElement>(null)
  const scroller = useRef<HTMLDivElement>(null)
  const selected = useRef("")
  const busy = loading || submitting || api.running(run)
  const storageKey = `ennoamelioration-v2-${userId}`
  const mode = session?.mode ?? draftMode
  const view = MODES[mode]

  const open = useCallback(async (sid: string) => {
    selected.current = sid
    const data = await api.getSession(sid)
    if (selected.current !== sid) return
    setSession(data); setRun(data.active_run)
    try { localStorage.setItem(storageKey, sid) } catch { /* stockage indisponible */ }
  }, [storageKey])

  const refresh = useCallback(async (sid: string) => {
    const [data, list] = await Promise.all([api.getSession(sid), api.listSessions()])
    if (selected.current !== sid) return
    setSession(data); setSessions(list)
  }, [])

  useEffect(() => {
    let active = true
    async function init() {
      try {
        const [list, config] = await Promise.all([api.listSessions(), api.getSettings()])
        if (!active) return
        setSessions(list); setSettings(config)
        let remembered = ""
        try { remembered = localStorage.getItem(storageKey) || "" } catch { /* stockage indisponible */ }
        const sid = list.find(s => s.id === remembered)?.id || list[0]?.id
        if (sid) await open(sid)
      } catch (e) { if (active) setError(e instanceof Error ? e.message : "Chargement impossible.") }
      finally { if (active) setLoading(false) }
    }
    void init()
    return () => { active = false; selected.current = "" }
  }, [open, storageKey])

  useEffect(() => {
    if (!run || !api.running(run)) return
    let active = true
    let timer: ReturnType<typeof setTimeout>
    async function poll() {
      try {
        const latest = await api.getRun(run!.id)
        if (!active) return
        setRun(latest); setError("")
        if (!api.running(latest)) { await refresh(latest.session); input.current?.focus(); return }
      } catch (e) { if (active) setError(e instanceof Error ? e.message : "Connexion interrompue. Nouvelle tentative…") }
      if (active) timer = setTimeout(poll, 1500)
    }
    timer = setTimeout(poll, 700)
    return () => { active = false; clearTimeout(timer) }
  }, [run?.id, run?.status, refresh])

  useEffect(() => {
    if (scroller.current) scroller.current.scrollTop = scroller.current.scrollHeight
  }, [session?.id, session?.messages.length, run?.id])

  // The mode switch and the actions live in the application header (see app-shell).
  useEffect(() => { setHeaderSlot(document.getElementById("page-header-actions")) }, [])

  // One line like a chat box; it grows with the text up to about eight lines.
  useEffect(() => {
    const box = input.current
    if (!box) return
    box.style.height = "auto"
    box.style.height = `${Math.min(box.scrollHeight, 208)}px`
    box.style.overflowY = box.scrollHeight > 208 ? "auto" : "hidden"
  }, [draft])

  async function newConversation() {
    if (busy) return
    setError("")
    const created = await api.createSession(mode)
    setSessions(list => [created, ...list])
    await open(created.id)
    setDraft(""); setFile(null); setPanel(null)
  }

  async function changeMode(next: api.Mode) {
    if (busy || next === mode) return
    if (!session) { setDraftMode(next); return }
    try { await api.setMode(session.id, next); setDraftMode(next); await refresh(session.id) }
    catch (e) { setError(e instanceof Error ? e.message : "Changement de mode impossible.") }
  }

  async function removeConversation(sid: string) {
    if (busy || !window.confirm("Supprimer définitivement cette conversation et ses propositions ?")) return
    try {
      await api.deleteSession(sid)
      const list = sessions.filter(s => s.id !== sid)
      setSessions(list)
      if (session?.id === sid) { setSession(null); setRun(null); if (list[0]) await open(list[0].id) }
    } catch (e) { setError(e instanceof Error ? e.message : "Suppression impossible.") }
  }

  async function send() {
    const fallback = mode === "expert" ? "Évalue ce CIR comme un expert du ministère." : "Analyse ce CIR et explique-moi ses points faibles."
    const message = draft.trim() || (file ? fallback : "")
    if (!message || busy) return
    setSubmitting(true); setError("")
    try {
      let sid = session?.id
      if (!sid) { const created = await api.createSession(mode); sid = created.id; selected.current = sid }
      const started = await api.sendMessage(sid, message, file)
      setRun(started); setDraft(""); setFile(null)
      await refresh(sid)
    } catch (e) { setError(e instanceof Error ? e.message : "Envoi impossible.") }
    finally { setSubmitting(false) }
  }

  function attach(chosen?: File) {
    if (fileInput.current) fileInput.current.value = ""
    if (!chosen) return
    if (!chosen.size || chosen.size > (settings?.max_document_bytes ?? 30 * 1024 * 1024)) {
      setError("Le fichier est vide ou trop volumineux."); return
    }
    setFile(chosen); setError(""); input.current?.focus()
  }

  function target(paragraphId: string) {
    setDraft(text => `${text}${text && !text.endsWith(" ") ? " " : ""}[${paragraphId}] `)
    input.current?.focus()
  }

  function showParagraph(paragraphId: string) {
    setFocusId(paragraphId); setPanel("document")
  }

  async function decide(revision: api.Revision, decision: "accepted" | "rejected" | "proposed") {
    if (!session) return
    try { await api.decide(session.id, revision.id, decision); await refresh(session.id) }
    catch (e) { setError(e instanceof Error ? e.message : "Décision impossible.") }
  }

  async function acceptAll(revisionIds: string[]) {
    if (!session) return
    try { await api.decideMany(session.id, revisionIds, "accepted"); await refresh(session.id) }
    catch (e) { setError(e instanceof Error ? e.message : "Décision impossible.") }
  }

  async function save(load: () => Promise<Blob>, name: string) {
    setWordMenu(false)
    try {
      const link = document.createElement("a")
      link.href = URL.createObjectURL(await load())
      link.download = name
      link.click()
      setTimeout(() => URL.revokeObjectURL(link.href), 1000)
    } catch (e) { setError(e instanceof Error ? e.message : "Téléchargement impossible.") }
  }

  function downloadCir(tracked: boolean) {
    if (!session) return
    const stem = session.document?.name.replace(/\.[^.]+$/, "") || "EnnoAmelioration - passages"
    void save(() => api.exportWord(session.id, tracked), `${stem} - amélioré${tracked ? " (suivi des modifications)" : ""}.docx`)
  }

  function downloadAssessment(assessment: api.Assessment) {
    if (!session) return
    const stem = session.document?.name.replace(/\.[^.]+$/, "") || "EnnoAmelioration"
    void save(() => api.exportAssessment(session.id, assessment.id), `Pré-expertise - ${stem}.docx`)
  }

  const revisions = new Map((session?.revisions || []).map(r => [r.id, r]))
  const assessments = new Map((session?.assessments || []).map(a => [a.id, a]))
  const articles = new Map((session?.articles || []).map(a => [a.id, a]))
  const accepted = session?.revisions.filter(r => r.status === "accepted").length || 0

  return <div className="flex h-full min-h-0 min-w-0 overflow-hidden bg-background">
    {headerSlot && createPortal(<>
      <div role="radiogroup" aria-label="Mode de travail" className="flex rounded-full bg-muted p-0.5">
        {(Object.keys(MODES) as api.Mode[]).map(key => <button key={key} type="button" role="radio" aria-checked={mode === key} disabled={busy}
          onClick={() => void changeMode(key)} title={MODES[key].label}
          className={`flex cursor-pointer items-center gap-1.5 rounded-full px-3 py-1.5 text-sm transition-colors disabled:cursor-default disabled:opacity-60 ${mode === key ? "bg-background font-medium shadow-sm" : "text-muted-foreground hover:text-foreground"}`}>
          {key === "expert" ? <Scale aria-hidden="true" className="size-4" /> : <FilePenLine aria-hidden="true" className="size-4" />}
          <span className="hidden sm:inline">{MODES[key].short}</span>
        </button>)}
      </div>
      <Button variant="ghost" size="sm" disabled={busy} title="Nouvelle conversation" onClick={() => void newConversation().catch(e => setError(e.message))}><Plus className="size-4" /><span className="hidden lg:inline">Nouveau</span></Button>
      {session?.document && <Button variant={panel === "document" ? "secondary" : "ghost"} size="sm" aria-expanded={panel === "document"} title="Voir le document" onClick={() => setPanel(panel === "document" ? null : "document")}><FileText className="size-4" /><span className="hidden lg:inline">Document</span></Button>}
      <div className="relative">
        <Button variant="outline" size="sm" disabled={!accepted} aria-expanded={wordMenu} onClick={() => setWordMenu(!wordMenu)} title={accepted ? "Télécharger le CIR amélioré" : "Acceptez au moins une proposition"}><Download className="size-4" /><span className="hidden lg:inline">Word</span> ({accepted})</Button>
        {wordMenu && <div role="menu" className="absolute right-0 top-full z-50 mt-1 w-72 rounded-xl border bg-background p-1 shadow-lg">
          <button type="button" role="menuitem" onClick={() => downloadCir(true)} className="w-full cursor-pointer rounded-lg px-3 py-2 text-left text-sm hover:bg-muted">
            <span className="block font-medium">Avec suivi des modifications</span><span className="block text-xs text-muted-foreground">Le client voit et valide chaque changement dans Word</span></button>
          <button type="button" role="menuitem" onClick={() => downloadCir(false)} className="w-full cursor-pointer rounded-lg px-3 py-2 text-left text-sm hover:bg-muted">
            <span className="block font-medium">Version propre</span><span className="block text-xs text-muted-foreground">Les changements déjà appliqués</span></button>
        </div>}
      </div>
    </>, headerSlot)}
    <aside className="hidden w-56 shrink-0 flex-col border-r bg-muted/25 p-4 lg:flex xl:w-64">
      <Button variant="outline" disabled={busy} className="mb-6 w-full justify-start gap-2" onClick={() => void newConversation().catch(e => setError(e.message))}><Plus className="size-4" />Nouvelle conversation</Button>
      <p className="mb-3 text-xs font-medium uppercase tracking-wider text-muted-foreground">Conversations</p>
      <nav aria-label="Historique des conversations" className="min-h-0 space-y-1 overflow-y-auto">
        {sessions.map(s => <div key={s.id} className={`group flex items-center rounded-lg hover:bg-muted ${session?.id === s.id ? "bg-muted" : ""}`}>
          <button disabled={busy} onClick={() => { setError(""); setFile(null); void open(s.id).catch(e => setError(e.message)) }} className={`min-w-0 flex-1 cursor-pointer truncate px-3 py-3 text-left text-sm disabled:opacity-50 ${session?.id === s.id ? "font-medium" : ""}`} title={s.title}>{s.title}</button>
          <Button variant="ghost" size="icon" disabled={busy} aria-label={`Supprimer ${s.title}`} className="opacity-0 focus-visible:opacity-100 group-hover:opacity-100" onClick={() => void removeConversation(s.id)}><Trash2 className="size-4" /></Button>
        </div>)}
      </nav>
    </aside>

    <section className="flex min-h-0 min-w-0 flex-1 flex-col" aria-label="Conversation EnnoAmélioration">
      <div className="flex shrink-0 items-center gap-2 border-b px-3 py-1.5 lg:hidden">
        <label className="sr-only" htmlFor="v2-history">Conversation</label>
        <select id="v2-history" value={session?.id || ""} disabled={busy} className="min-w-0 flex-1 rounded-md bg-transparent p-1.5 text-sm" onChange={e => void open(e.target.value).catch(err => setError(err.message))}>
          <option value="" disabled>Choisir une conversation</option>
          {sessions.map(s => <option value={s.id} key={s.id}>{s.title}</option>)}
        </select>
      </div>
      {settings && !settings.configured && <p role="alert" className="border-b px-6 py-3 text-sm text-destructive">La clé OpenAI doit être configurée côté serveur avant d’envoyer un message.</p>}

      <div ref={scroller} className="min-h-0 flex-1 overflow-y-auto overscroll-contain px-4 sm:px-6">
        <div className="mx-auto max-w-3xl py-5">
          {loading ? <p role="status" className="text-sm text-muted-foreground">Chargement de vos conversations…</p>
          : !session?.messages.length ? <div className="py-6 sm:py-12">
            <p className="mb-3 text-xs font-medium uppercase tracking-widest text-primary">{view.kicker}</p>
            <h2 className="max-w-xl text-2xl font-semibold leading-tight sm:text-3xl">{view.title}</h2>
            <p className="mt-3 max-w-xl leading-7 text-muted-foreground">{view.intro}</p>
            <div className="mt-6 grid gap-2.5 sm:grid-cols-2">{view.suggestions.map(text => <button key={text} onClick={() => { setDraft(text); input.current?.focus() }} className="cursor-pointer rounded-xl border p-3.5 text-left text-sm leading-6 hover:bg-muted focus-visible:outline-2 focus-visible:outline-primary">{text}</button>)}</div>
          </div>
          : session.messages.map(message => <article key={message.id} className="mb-9" aria-label={message.role === "user" ? "Votre message" : "Réponse d’EnnoAmélioration"}>
            <p className="mb-2 text-xs font-semibold uppercase tracking-wider text-muted-foreground">{message.role === "user" ? "Vous" : message.extra.mode === "expert" ? "EnnoAmélioration · Expert du ministère" : "EnnoAmélioration"}</p>
            {message.role === "user" ? <div className="rounded-xl bg-muted/60 px-5 py-4">
              <p className="whitespace-pre-wrap break-words leading-7">{message.content}</p>
              {message.extra.attachment && <p className="mt-2 flex items-center gap-2 text-sm text-muted-foreground"><Paperclip className="size-4 shrink-0" />{message.extra.attachment.name} · {message.extra.attachment.paragraphs} paragraphes</p>}
            </div> : <>
              <Markdown text={message.content} articles={articles} onParagraph={showParagraph} />
              {(message.extra.assessment_ids || []).map(aid => assessments.get(aid)).filter(Boolean).map(a => <AssessmentCard key={a!.id} assessment={a!} articles={articles} onParagraph={showParagraph} onDownload={downloadAssessment} />)}
              {(() => {
                const pending = (message.extra.revision_ids || []).filter(rid => revisions.get(rid)?.status === "proposed")
                return pending.length > 1 && <Button className="mt-5" size="sm" disabled={busy} onClick={() => void acceptAll(pending)}><CheckCheck className="mr-1 size-4" />Tout accepter ({pending.length})</Button>
              })()}
              {(message.extra.revision_ids || []).map(rid => revisions.get(rid)).filter(Boolean).map(r => <RevisionCard key={r!.id} revision={r!} articles={articles} busy={busy} onDecide={decide} onParagraph={showParagraph} />)}
              <References items={message.extra.references || []} />
              {message.extra.warnings?.filter(w => w.trim() !== message.content.trim()).map((w, i) => <p key={i} className="mt-3 rounded-lg border px-3 py-2 text-sm text-muted-foreground">{w}</p>)}
            </>}
          </article>)}
          {api.running(run) && <div role="status" aria-live="polite" className="mb-6 flex items-center gap-3 rounded-xl border p-4 text-sm">
            <Loader2 className="size-4 motion-safe:animate-spin" />
            <p>{run?.cancelled ? "Arrêt demandé…" : run?.events.at(-1)?.label || "Votre demande est en attente…"}</p>
          </div>}
        </div>
      </div>

      <div className="shrink-0 px-3 pb-2 pt-1 sm:px-6">
        <div className="mx-auto max-w-3xl">
          {error && <p role="alert" className="mb-2 rounded-lg bg-destructive/10 p-2.5 text-sm text-destructive">{error}</p>}
          <form onSubmit={e => { e.preventDefault(); void send() }} className="rounded-3xl border bg-background px-2 py-1.5 shadow-sm focus-within:ring-2 focus-within:ring-primary/30">
            {file && <p className="flex max-w-full items-center gap-1 px-2 pt-1 text-sm"><Paperclip className="size-4 shrink-0" /><span className="truncate">{file.name}</span>
              <Button type="button" size="icon" variant="ghost" aria-label={`Retirer ${file.name}`} onClick={() => setFile(null)}><X className="size-4" /></Button></p>}
            <div className="flex items-end gap-1">
              <input ref={fileInput} type="file" accept={(settings?.extensions || [".docx", ".pdf", ".txt", ".md"]).join(",")} className="hidden" onChange={e => attach(e.target.files?.[0])} />
              <Button type="button" variant="ghost" size="icon" className="shrink-0 rounded-full" disabled={busy} onClick={() => fileInput.current?.click()}
                title={session?.document ? "Remplacer le CIR (Word, PDF ou texte)" : "Déposer un CIR (Word, PDF ou texte)"} aria-label={session?.document ? "Remplacer le CIR" : "Déposer un CIR"}><Paperclip className="size-4" /></Button>
              <label htmlFor="v2-message" className="sr-only">Votre message</label>
              <textarea ref={input} id="v2-message" value={draft} onChange={e => setDraft(e.target.value)} maxLength={settings?.max_message_chars ?? 30000} rows={1}
                placeholder={view.placeholder}
                className="min-w-0 flex-1 resize-none self-center bg-transparent px-1 py-2 text-base leading-6 outline-none placeholder:overflow-hidden placeholder:text-ellipsis placeholder:whitespace-nowrap"
                onKeyDown={e => { if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) { e.preventDefault(); void send() } }} />
              {api.running(run)
                ? <Button type="button" variant="outline" size="icon" className="shrink-0 rounded-full" aria-label="Arrêter" title="Arrêter" onClick={() => void api.stopRun(run!.id).then(setRun).catch(e => setError(e.message))}><Square className="size-4" /></Button>
                : <Button type="submit" size="icon" className="shrink-0 rounded-full" disabled={busy || (!draft.trim() && !file) || settings?.configured === false} aria-label="Envoyer">{submitting ? <Loader2 className="size-4 motion-safe:animate-spin" /> : <ArrowUp className="size-4" />}</Button>}
            </div>
          </form>
          <p className="mt-1.5 truncate text-center text-[11px] text-muted-foreground">
            {showBudget && <>Coût : {(session?.estimated_cost_usd || 0).toFixed(3)} $ · </>}
            L’IA peut se tromper : vérifiez les faits et les références.
          </p>
        </div>
      </div>
    </section>

    {panel === "document" && session?.document && <DocumentPanel key={`${session.id}-${session.document.name}-${session.document.paragraph_count}`} sessionId={session.id} revisions={session.revisions} focusId={focusId} onTarget={target} onClose={() => setPanel(null)} />}
  </div>
}

function Markdown({ text, articles, onParagraph }: { text: string; articles: Map<string, api.Article>; onParagraph: (id: string) => void }) {
  const linked = text
    .replace(/\[?(A_[a-f0-9]{16})\]?(?!\()/g, (_, ref: string) => `[${api.citationLabel(articles.get(ref)) || ref}](#ref-${ref})`)
    .replace(/\[(P\d+)\](?!\()/g, (_, ref: string) => `[${ref}](#para-${ref})`)
  return <div className="space-y-4 break-words leading-7 [&_blockquote]:border-l-2 [&_blockquote]:pl-4 [&_blockquote]:text-muted-foreground [&_h1]:mt-6 [&_h1]:text-xl [&_h1]:font-semibold [&_h2]:mt-6 [&_h2]:text-lg [&_h2]:font-semibold [&_h3]:mt-5 [&_h3]:font-semibold [&_li]:my-1 [&_ol]:list-decimal [&_ol]:pl-6 [&_table]:w-full [&_table]:text-sm [&_td]:border [&_td]:p-2 [&_th]:border [&_th]:bg-muted [&_th]:p-2 [&_ul]:list-disc [&_ul]:pl-6">
    <ReactMarkdown remarkPlugins={[remarkGfm]} components={{
      a: ({ href, children }) => href?.startsWith("#para-")
        ? <button type="button" onClick={() => onParagraph(href.slice(6))} className="cursor-pointer font-medium text-primary underline underline-offset-4">{children}</button>
        : href?.startsWith("#ref-") ? <a href={href} className="font-medium text-primary underline underline-offset-4">{children}</a>
        : api.publicUrl(href) ? <a href={api.publicUrl(href)} target="_blank" rel="noopener noreferrer" className="text-primary underline underline-offset-4">{children}</a>
        : <span>{children}</span>,
      table: ({ children }) => <div className="overflow-x-auto"><table>{children}</table></div>,
    }}>{linked}</ReactMarkdown>
  </div>
}

function RiskBadge({ risk }: { risk: api.Risk }) {
  return <span className={`shrink-0 rounded-full px-2 py-0.5 text-xs font-medium ${RISK_STYLE[risk]}`}>Risque {risk}</span>
}

function AssessmentCard({ assessment, articles, onParagraph, onDownload }: {
  assessment: api.Assessment; articles: Map<string, api.Article>; onParagraph: (id: string) => void; onDownload: (a: api.Assessment) => void
}) {
  const order: api.Risk[] = ["élevé", "moyen", "faible"]
  const sections = [...assessment.sections].sort((a, b) => order.indexOf(a.risk) - order.indexOf(b.risk))
  const text = (value: string) => api.cite(value, articles)
  return <div className="mt-5 rounded-xl border p-5">
    <div className="flex flex-wrap items-center justify-between gap-2">
      <p className="flex items-center gap-2 font-semibold"><Scale aria-hidden="true" className="size-4" />Pré-expertise du dossier</p>
      <div className="flex items-center gap-2">
        <RiskBadge risk={assessment.overall_risk} />
        <Button size="sm" variant="outline" onClick={() => onDownload(assessment)} title="Télécharger la pré-expertise en Word"><Download className="size-4" />Word</Button>
      </div>
    </div>
    <p className="mt-2 text-sm leading-6 text-muted-foreground">{text(assessment.summary)}</p>
    <div className="mt-4 space-y-2">{sections.map((s, i) => <details key={i} className="rounded-lg border bg-muted/20 p-3" open={s.risk === "élevé"}>
      <summary className="flex cursor-pointer flex-wrap items-center justify-between gap-2 text-sm font-medium">
        <span>{s.title}{s.first_id && <button type="button" className="ml-2 cursor-pointer text-xs font-normal text-muted-foreground underline" onClick={e => { e.preventDefault(); onParagraph(s.first_id) }}>{s.first_id === s.last_id ? s.first_id : `${s.first_id}–${s.last_id}`}</button>}</span>
        <RiskBadge risk={s.risk} />
      </summary>
      <p className="mt-3 text-sm leading-6">{text(s.findings)}</p>
      {s.expert_questions.length > 0 && <><p className="mt-3 text-xs font-semibold uppercase tracking-wider text-muted-foreground">Questions de l’expert</p>
        <ul className="mt-1 list-disc space-y-1 pl-5 text-sm">{s.expert_questions.map((q, j) => <li key={j}>{text(q)}</li>)}</ul></>}
      {s.to_prepare.length > 0 && <><p className="mt-3 text-xs font-semibold uppercase tracking-wider text-muted-foreground">À préparer</p>
        <ul className="mt-1 list-disc space-y-1 pl-5 text-sm">{s.to_prepare.map((q, j) => <li key={j}>{text(q)}</li>)}</ul></>}
    </details>)}</div>
    <p className="mt-3 text-xs text-muted-foreground">Pour corriger les points à risque, passez en mode Amélioration : l’évaluation reste dans la conversation.</p>
  </div>
}

function RevisionCard({ revision, articles, busy, onDecide, onParagraph }: {
  revision: api.Revision; articles: Map<string, api.Article>; busy: boolean
  onDecide: (revision: api.Revision, decision: "accepted" | "rejected" | "proposed") => void; onParagraph: (id: string) => void
}) {
  const decided = revision.status === "accepted" || revision.status === "rejected"
  const tone = revision.status === "accepted" ? "border-primary/50 bg-primary/5" : revision.status === "proposed" ? "" : "opacity-70"
  return <div className={`mt-5 rounded-xl border p-5 ${tone}`}>
    <div className="flex flex-wrap items-center justify-between gap-2">
      <p className="font-semibold">Proposition {revision.number}
        {revision.paragraph_ids.length > 0 && <span className="ml-2 text-sm font-normal text-muted-foreground">· {revision.paragraph_ids.map(id => <button key={id} type="button" className="mr-1 cursor-pointer underline" onClick={() => onParagraph(id)}>{id}</button>)}</span>}
      </p>
      <span className="rounded-full bg-muted px-2 py-0.5 text-xs">{STATUS[revision.status]}</span>
    </div>
    <p className="mt-2 text-sm leading-6 text-muted-foreground">{api.cite(revision.explanation, articles)}</p>
    <div className="mt-4 space-y-3 rounded-lg bg-muted/40 p-4 leading-7">{revision.proposed.split("\n").filter(Boolean).map((line, i) => <p key={i}>{api.cite(line, articles)}</p>)}</div>
    <details className="mt-3 text-sm"><summary className="cursor-pointer text-muted-foreground">Texte d’origine</summary><p className="mt-2 whitespace-pre-wrap leading-6 text-muted-foreground">{revision.original}</p></details>
    {revision.refs.length > 0 && <ul className="mt-3 space-y-1 text-sm">{revision.refs.map(ref => <li key={ref} className="text-muted-foreground">{reference(articles.get(ref)) || ref}</li>)}</ul>}
    <div className="mt-4 flex flex-wrap gap-2">
      {revision.status === "proposed" && <>
        <Button size="sm" disabled={busy} onClick={() => onDecide(revision, "accepted")}><Check className="mr-1 size-4" />Accepter</Button>
        <Button size="sm" variant="outline" disabled={busy} onClick={() => onDecide(revision, "rejected")}><X className="mr-1 size-4" />Refuser</Button>
      </>}
      {decided && <Button size="sm" variant="ghost" disabled={busy} onClick={() => onDecide(revision, "proposed")}><Undo2 className="mr-1 size-4" />Annuler ma décision</Button>}
      <Button size="sm" variant="ghost" onClick={() => void navigator.clipboard?.writeText(revision.proposed)}>Copier le texte</Button>
    </div>
  </div>
}

function reference(article?: api.Article) {
  if (!article) return ""
  const authors = article.authors.slice(0, 3).join(", ") + (article.authors.length > 3 ? " et al." : "")
  return `${authors || "Auteur non renseigné"} (${article.year || "s.d."}). ${article.title}${article.doi ? ` · doi:${article.doi}` : ""}`
}

function References({ items }: { items: api.Article[] }) {
  if (!items.length) return null
  return <details className="mt-5 rounded-xl border bg-muted/20 p-4">
    <summary className="cursor-pointer text-sm font-medium">Articles cités : {items.length}</summary>
    <div className="mt-4 space-y-4 text-sm">{items.map(a => <div id={`ref-${a.id}`} key={a.id} className="scroll-mt-24 border-t pt-3">
      <p className="font-medium">{a.title}</p>
      <p className="text-xs text-muted-foreground">{reference(a)}{a.venue ? ` · ${a.venue}` : ""}</p>
      {a.abstract && <p className="mt-2 max-h-40 overflow-auto leading-6 text-muted-foreground">{a.abstract}</p>}
      {api.publicUrl(a.doi ? `https://doi.org/${a.doi}` : a.url) && <a href={api.publicUrl(a.doi ? `https://doi.org/${a.doi}` : a.url)} target="_blank" rel="noopener noreferrer" className="mt-2 inline-block text-primary underline">Consulter l’article</a>}
    </div>)}</div>
  </details>
}

function DocumentPanel({ sessionId, revisions, focusId, onTarget, onClose }: {
  sessionId: string; revisions: api.Revision[]; focusId: string; onTarget: (id: string) => void; onClose: () => void
}) {
  const [doc, setDoc] = useState<{ name: string; paragraphs: api.Paragraph[] } | null>(null)
  const [error, setError] = useState("")
  useEffect(() => {
    let active = true
    api.getDocument(sessionId).then(d => { if (active) setDoc(d) }).catch(e => { if (active) setError(e.message) })
    return () => { active = false }
  }, [sessionId])
  useEffect(() => {
    if (doc && focusId) document.getElementById(`para-${focusId}`)?.scrollIntoView({ block: "center", behavior: "smooth" })
  }, [doc, focusId])
  const changed = new Set(revisions.filter(r => r.status === "accepted").flatMap(r => r.paragraph_ids))
  return <aside aria-label="Document déposé" className="flex w-full min-w-0 flex-col border-l bg-muted/10 md:w-[26rem] xl:w-[32rem]">
    <header className="flex shrink-0 items-center justify-between gap-2 border-b px-4 py-3">
      <div className="min-w-0"><p className="truncate font-medium">{doc?.name || "Document"}</p><p className="text-xs text-muted-foreground">Cliquez sur un paragraphe pour le cibler dans votre message.</p></div>
      <Button size="icon" variant="ghost" aria-label="Fermer le document" onClick={onClose}><X className="size-4" /></Button>
    </header>
    <div className="min-h-0 flex-1 overflow-y-auto p-4 text-sm">
      {error && <p role="alert" className="text-destructive">{error}</p>}
      {!doc && !error && <p role="status" className="text-muted-foreground">Chargement du document…</p>}
      {doc?.paragraphs.map(p => p.level
        ? <h3 key={p.id} id={`para-${p.id}`} className="mb-2 mt-5 font-semibold">{p.text} <span className="text-xs font-normal text-muted-foreground">{p.id}</span></h3>
        : <button key={p.id} id={`para-${p.id}`} type="button" disabled={!p.editable} onClick={() => onTarget(p.id)}
            className={`mb-2 block w-full cursor-pointer rounded-lg border p-3 text-left leading-6 hover:bg-muted disabled:cursor-default ${focusId === p.id ? "ring-2 ring-primary" : ""} ${changed.has(p.id) ? "border-primary/50" : "border-transparent"}`}>
            <span className="mr-2 text-xs text-muted-foreground">{p.id}{changed.has(p.id) ? " · modifié" : ""}</span>{p.text}
          </button>)}
    </div>
  </aside>
}
