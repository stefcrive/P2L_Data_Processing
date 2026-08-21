import { NextResponse } from "next/server";

export const dynamic = "force-dynamic";

const apiTarget = (
  process.env.IRMS_API_PROXY_TARGET ||
  process.env.IRMS_API_URL ||
  "http://127.0.0.1:8000"
).replace(/\/+$/, "");

export async function POST(request: Request) {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 110_000);
  try {
    const contentType = request.headers.get("Content-Type") || "application/json";
    const hasAttachments = contentType.toLowerCase().startsWith("multipart/form-data");
    const body = hasAttachments ? await request.arrayBuffer() : await request.text();
    const endpoint = hasAttachments
      ? "scientific-assistant-with-files"
      : "scientific-assistant";
    const response = await fetch(`${apiTarget}/chat/${endpoint}`, {
      method: "POST",
      body,
      headers: { "Content-Type": contentType },
      cache: "no-store",
      signal: controller.signal,
    });
    const payload = await response.text();
    return new NextResponse(payload, {
      status: response.status,
      headers: { "Content-Type": response.headers.get("Content-Type") || "application/json" },
    });
  } catch (error) {
    const detail = error instanceof Error && error.name === "AbortError"
      ? "The scientific assistant timed out after 110 seconds."
      : "The scientific assistant backend could not be reached.";
    return NextResponse.json({ detail }, { status: 503 });
  } finally {
    clearTimeout(timeout);
  }
}
