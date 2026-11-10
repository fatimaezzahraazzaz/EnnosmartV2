import { createHmac, randomBytes, timingSafeEqual } from "node:crypto"
import { createReadStream } from "node:fs"
import { stat } from "node:fs/promises"
import { Readable } from "node:stream"
import { NextRequest, NextResponse } from "next/server"

const path = "/ennoscholar-v66/demo"
const file = "/app/media/ennoscholar-demo.mp4"
const cookie = "__Secure-ennoscholar-demo"
const lifetime = 60 * 60
const key = randomBytes(32)

function signature(expires: string) {
  return createHmac("sha256", key).update(expires).digest("hex")
}

function canPlay(grant: string) {
  const match = /^(\d{10})\.([a-f0-9]{64})$/.exec(grant)
  if (!match || Number(match[1]) <= Date.now() / 1000) return false
  return timingSafeEqual(Buffer.from(signature(match[1])), Buffer.from(match[2]))
}

export async function POST(request: NextRequest) {
  const authorization = request.headers.get("authorization")
  if (!authorization?.startsWith("Bearer ")) return new NextResponse(null, { status: 401 })

  let account: Response
  try {
    account = await fetch(process.env.NEXT_PUBLIC_API_URL + "/auth/me", {
      headers: { authorization },
      cache: "no-store",
      redirect: "error",
      signal: AbortSignal.timeout(8_000),
    })
  } catch {
    return new NextResponse(null, { status: 503 })
  }
  if (!account.ok) return new NextResponse(null, { status: account.status < 500 ? 401 : 503 })

  const expires = String(Math.floor(Date.now() / 1000) + lifetime)
  const response = new NextResponse(null, { status: 204, headers: { "Cache-Control": "no-store" } })
  response.cookies.set(cookie, expires + "." + signature(expires), {
    httpOnly: true, secure: true, sameSite: "strict", path, maxAge: lifetime,
  })
  return response
}

export async function GET(request: NextRequest) {
  if (!canPlay(request.cookies.get(cookie)?.value ?? "")) {
    return new NextResponse(null, { status: 401, headers: { "Cache-Control": "no-store" } })
  }

  const { size } = await stat(file)
  const range = request.headers.get("range")
  const match = range && /^bytes=(\d*)-(\d*)$/.exec(range)
  const start = match?.[1] ? Number(match[1]) : Math.max(0, size - Number(match?.[2] || size))
  const end = match?.[1] && match[2] ? Math.min(size - 1, Number(match[2])) : size - 1
  const headers = new Headers({
    "Accept-Ranges": "bytes",
    "Cache-Control": "private, no-store",
    "Content-Type": "video/mp4",
  })
  if ((range && (!match || !(match[1] || match[2]) || (!match[1] && Number(match[2]) === 0))) ||
      start >= size || end < start) {
    headers.set("Content-Range", "bytes */" + size)
    return new NextResponse(null, { status: 416, headers })
  }
  headers.set("Content-Length", String(end - start + 1))
  if (range) headers.set("Content-Range", "bytes " + start + "-" + end + "/" + size)
  const stream = Readable.toWeb(createReadStream(file, { start, end })) as ReadableStream<Uint8Array>
  return new NextResponse(stream, { status: range ? 206 : 200, headers })
}
