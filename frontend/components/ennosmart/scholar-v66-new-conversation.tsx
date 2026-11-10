"use client"

import { useEffect, useState } from "react"
import { ClipboardList, Loader2, Zap } from "lucide-react"
import { Button } from "@/components/ui/button"
import { DialogClose, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog"
import { listDiagProjects, type DiagProject } from "@/lib/scholar-v66"

const options = [
  { id: "express", title: "EnnoScholar Express", description: "Posez vos questions et ajoutez vos documents au fil de la conversation.", icon: Zap },
  { id: "diag", title: "EnnoScholar avec Diag", description: "Reprenez les objectifs, verrous, méthodes et résultats d’un projet déjà diagnostiqué.", icon: ClipboardList },
] as const

type Mode = typeof options[number]["id"]

export function NewScholarConversation({ creating, onCreate }: { creating: boolean; onCreate: (projectId?: number) => Promise<void> }) {
  const [mode, setMode] = useState<Mode>("express")
  const [projectId, setProjectId] = useState("")
  const [projects, setProjects] = useState<DiagProject[] | null>(null)
  const [projectError, setProjectError] = useState("")
  const [error, setError] = useState("")

  useEffect(() => {
    if (mode !== "diag" || projects !== null) return
    let active = true
    void listDiagProjects().then(rows => {
      if (active) setProjects(rows.filter(project => project.ready))
    }).catch(() => {
      if (active) setProjectError("Les projets diagnostiqués sont temporairement indisponibles.")
    })
    return () => { active = false }
  }, [mode, projects])

  const needsProject = mode === "diag"
  const canCreate = !creating && (!needsProject || !!projects?.some(project => String(project.id) === projectId))

  return <DialogContent className="gap-5 sm:max-w-md" showCloseButton={false}>
    <DialogHeader>
      <DialogTitle>Nouvelle conversation</DialogTitle>
      <DialogDescription>Choisissez comment démarrer avec EnnoScholar.</DialogDescription>
    </DialogHeader>
    <form id="v66-new-conversation" className="space-y-4" onSubmit={async event => {
      event.preventDefault()
      if (!canCreate) return
      setError("")
      try { await onCreate(needsProject ? Number(projectId) : undefined) }
      catch (e) { setError(e instanceof Error ? e.message : "Création impossible.") }
    }}>
      <fieldset disabled={creating} className="grid gap-2">
        <legend className="sr-only">Type de conversation</legend>
        {options.map(({ id, title, description, icon: Icon }) => <label key={id} className={"flex cursor-pointer items-start gap-3 rounded-lg border p-3 transition-colors hover:bg-muted/60 focus-within:ring-2 focus-within:ring-ring " + (mode === id ? "border-primary bg-primary/5" : "border-border")}>
          <input type="radio" name="v66-conversation-type" value={id} checked={mode === id} onChange={() => { setMode(id); setProjectError(""); setError("") }} className="mt-1 size-4 shrink-0 accent-primary" />
          <Icon aria-hidden="true" className="mt-0.5 size-4 shrink-0 text-primary" />
          <span><span className="block font-medium">{title}</span><span className="mt-0.5 block leading-5 text-muted-foreground">{description}</span></span>
        </label>)}
      </fieldset>
      {needsProject && <div className="space-y-2">
        {projectError ? <p role="alert" className="text-destructive">{projectError}</p> : projects === null
          ? <p role="status" className="text-muted-foreground">Chargement des projets diagnostiqués…</p>
          : projects.length === 0
            ? <p className="text-muted-foreground">Aucun diagnostic terminé n’est disponible. Vous pouvez commencer avec Express.</p>
            : <>
              <label htmlFor="v66-new-project" className="block font-medium">Projet diagnostiqué</label>
              <select id="v66-new-project" value={projectId} disabled={creating} onChange={e => { setProjectId(e.target.value); setError("") }} className="min-h-11 w-full min-w-0 rounded-md border bg-background px-3 focus-visible:outline-2 focus-visible:outline-primary">
                <option value="" disabled>Choisir un projet</option>
                {projects.map(project => <option key={project.id} value={project.id}>{project.name}{project.subproject ? " / " + project.subproject : ""} · {project.organisme} · {project.year}</option>)}
              </select>
            </>}
      </div>}
      {error && <p role="alert" className="text-destructive">{error}</p>}
    </form>
    <DialogFooter>
      <DialogClose render={<Button type="button" variant="outline" size="lg" disabled={creating} />}>Annuler</DialogClose>
      <Button type="submit" form="v66-new-conversation" size="lg" disabled={!canCreate}>
        {creating ? <><Loader2 aria-hidden="true" className="size-4 motion-safe:animate-spin" />Création…</> : "Créer la conversation"}
      </Button>
    </DialogFooter>
  </DialogContent>
}
