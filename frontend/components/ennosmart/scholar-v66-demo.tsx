"use client"

import { useEffect, useState } from "react"
import { Play } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Dialog, DialogContent, DialogDescription, DialogTitle, DialogTrigger } from "@/components/ui/dialog"
import { getAccessToken } from "@/lib/api"

const videoPath = "/ennoscholar-v66/demo"

function DemoVideo() {
  const [status, setStatus] = useState<"loading" | "ready" | "error">("loading")

  useEffect(() => {
    const controller = new AbortController()
    const token = getAccessToken()
    fetch(videoPath, {
      method: "POST",
      headers: token ? { Authorization: "Bearer " + token } : undefined,
      signal: controller.signal,
    }).then((response) => {
      if (!controller.signal.aborted) setStatus(response.ok ? "ready" : "error")
    }).catch(() => {
      if (!controller.signal.aborted) setStatus("error")
    })
    return () => controller.abort()
  }, [])

  if (status === "loading") return <p role="status">Chargement de la vidéo…</p>
  if (status === "error") return <p role="alert">La vidéo est momentanément indisponible. Réessayez en rouvrant la démo.</p>
  return (
    <video
      aria-label="Démonstration complète d’EnnoScholar"
      className="max-h-[70dvh] w-full rounded-lg bg-black"
      controls
      playsInline
      preload="metadata"
      src={videoPath}
      onError={() => setStatus("error")}
    />
  )
}

export function ScholarDemo() {
  return (
    <Dialog>
      <DialogTrigger render={<Button variant="outline" size="sm" />}>
        <Play aria-hidden="true" className="size-3.5" /> Démo
      </DialogTrigger>
      <DialogContent className="sm:max-w-4xl">
        <DialogTitle className="pr-8">Démonstration d’EnnoScholar</DialogTitle>
        <DialogDescription className="sr-only">Lecteur vidéo de la démonstration complète.</DialogDescription>
        <DemoVideo />
      </DialogContent>
    </Dialog>
  )
}
