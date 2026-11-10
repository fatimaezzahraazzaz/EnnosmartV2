import { execFile } from "node:child_process"
import { mkdir, mkdtemp, readFile, rm, writeFile } from "node:fs/promises"
import { tmpdir } from "node:os"
import { join } from "node:path"
import { pathToFileURL } from "node:url"
import { promisify } from "node:util"
import { NextRequest, NextResponse } from "next/server"

export const runtime = "nodejs"

const execute = promisify(execFile)
const identifier = /^[a-zA-Z0-9_-]{1,80}$/
const visualPath = /^media\/(M_[a-f0-9]{16})\.png$/
const imageLimit = 10 * 1024 * 1024
const types = {
  docx: "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
  pdf: "application/pdf",
}
let running = 0

class ExportError extends Error {
  constructor(readonly status: number, message: string) { super(message) }
}

function filterDocument(node: unknown, images: Set<string>): unknown {
  if (Array.isArray(node)) return node.map(child => filterDocument(child, images))
  if (node === null || typeof node !== "object") return node

  const value = node as Record<string, unknown>
  if ((value.t === "Image" || value.t === "Link") && Array.isArray(value.c)) {
    const [attributes, label, target] = value.c
    if (value.t === "Image" && typeof target?.[0] === "string" && visualPath.test(target[0])) {
      images.add(target[0])
      return { t: "Image", c: [["", [], []], filterDocument(label, images), [target[0], ""]] }
    }
    if (value.t === "Link" && typeof target?.[0] === "string" && /^(https?:\/\/|#)/i.test(target[0])) {
      return { t: "Link", c: [attributes, filterDocument(label, images), target] }
    }
    return { t: "Span", c: [["", [], []], filterDocument(label, images)] }
  }
  return Object.fromEntries(Object.entries(value).map(([key, child]) => [key, filterDocument(child, images)]))
}

async function saveVisual(response: Response, directory: string, path: string) {
  const length = Number(response.headers.get("content-length"))
  if (!response.ok || !response.body || response.headers.get("content-type") !== "image/png" ||
      !Number.isSafeInteger(length) || length <= 0 || length > imageLimit) {
    await response.body?.cancel()
    throw new ExportError(422, "Un visuel de cette version est indisponible ou trop volumineux.")
  }
  const image = Buffer.from(await response.arrayBuffer())
  if (image.length !== length) throw new ExportError(422, "Un visuel de cette version est incomplet.")
  await writeFile(join(directory, path), image)
  return length
}

export async function GET(request: NextRequest) {
  const fail = (status: number, detail: string) => NextResponse.json({ detail }, { status, headers: { "Cache-Control": "no-store" } })
  const authorization = request.headers.get("authorization")
  if (!authorization?.startsWith("Bearer ")) return fail(401, "Reconnectez-vous pour exporter le document.")
  const params = request.nextUrl.searchParams
  const session = params.get("session") ?? ""
  const version = params.get("version") ?? ""
  const format = params.get("format")
  if (!identifier.test(session) || !identifier.test(version) || (format !== "docx" && format !== "pdf")) {
    return fail(400, "Choisissez une version et un format Word ou PDF.")
  }
  if (running >= 4) return fail(429, "Plusieurs exports sont en cours. Réessayez dans quelques instants.")

  running++
  let directory: string | undefined
  try {
    const signal = AbortSignal.any([request.signal, AbortSignal.timeout(75_000)])
    const api = process.env.SCHOLAR_EXPORT_API_URL || process.env.NEXT_PUBLIC_API_URL
    const source = api + "/scholar-v66/sessions/" + session
    const fetchSource = (suffix: string) => fetch(source + suffix, {
      headers: { Authorization: authorization },
      cache: "no-store",
      redirect: "error",
      signal,
    })
    const markdown = await fetchSource("/export?version_id=" + version)
    if (!markdown.ok) {
      await markdown.body?.cancel()
      return fail(markdown.status, "Cette version est indisponible pour l’export.")
    }
    const { content } = await markdown.json()
    if (typeof content !== "string" || !content.trim()) return fail(422, "Cette version ne contient aucun texte à exporter.")
    if (Buffer.byteLength(content) > 1024 * 1024) return fail(413, "Cette version est trop volumineuse pour l’export.")

    directory = await mkdtemp(join(tmpdir(), "scholar-export-"))
    const options = { cwd: directory, signal, timeout: 55_000, maxBuffer: 16 * 1024 * 1024 }
    await writeFile(join(directory, "source.md"), content)
    const { stdout } = await execute("pandoc", [
      "--sandbox", "--from=markdown-raw_html-raw_tex-yaml_metadata_block-pandoc_title_block-implicit_figures",
      "--to=json", "source.md",
    ], options)
    const images = new Set<string>()
    const document = filterDocument(JSON.parse(stdout), images) as { meta: Record<string, unknown> }
    document.meta = {
      title: { t: "MetaString", c: "État de l’art CIR" },
      lang: { t: "MetaString", c: "fr-FR" },
    }
    if (images.size > 24) return fail(413, "Cette version contient trop de visuels pour un seul export.")
    if (images.size) await mkdir(join(directory, "media"))
    let imageBytes = 0
    for (const path of images) {
      const id = visualPath.exec(path)![1]
      imageBytes += await saveVisual(await fetchSource("/media/" + id), directory, path)
      if (imageBytes > 40 * 1024 * 1024) return fail(413, "Les visuels de cette version sont trop volumineux.")
    }
    await writeFile(join(directory, "source.json"), JSON.stringify(document))
    await execute("pandoc", [
      "--from=json", "--to=docx", "--output=export.docx", "source.json",
    ], options)
    if (format === "pdf") {
      const profile = pathToFileURL(join(directory, "office-profile")).href
      await execute("libreoffice", [
        "-env:UserInstallation=" + profile, "--headless", "--convert-to", "pdf", "--outdir", directory, "export.docx",
      ], options)
    }
    const output = await readFile(join(directory, "export." + format))
    return new NextResponse(new Uint8Array(output), {
      headers: {
        "Cache-Control": "private, no-store",
        "Content-Type": types[format],
        "Content-Disposition": 'attachment; filename="etat-de-l-art.' + format + '"',
      },
    })
  } catch (error) {
    if (error instanceof ExportError) return fail(error.status, error.message)
    console.error("Scholar export failed", error instanceof Error ? error.name : "UnknownError")
    return fail(503, "L’export est momentanément indisponible. Réessayez dans quelques instants.")
  } finally {
    running--
    if (directory) await rm(directory, { recursive: true, force: true }).catch(() => {})
  }
}
