import { NextRequest, NextResponse } from "next/server";

const AGENT_URL = process.env.AGENT_URL || process.env.NEXT_PUBLIC_AGENT_URL || "http://localhost:8123";

const OFFLINE_BODY = {
  detail: "The demo service is offline. Start the demo server, then try again.",
};

async function proxy(request: NextRequest, path: string[]) {
  const target = `${AGENT_URL}/${path.join("/")}${request.nextUrl.search}`;

  try {
    const headers = new Headers();
    const contentType = request.headers.get("content-type");
    if (contentType) headers.set("content-type", contentType);
    const accept = request.headers.get("accept");
    if (accept) headers.set("accept", accept);

    const init: RequestInit = {
      method: request.method,
      headers,
      cache: "no-store",
    };

    if (request.method !== "GET" && request.method !== "HEAD") {
      init.body = await request.arrayBuffer();
    }

    const res = await fetch(target, init);
    const body = await res.arrayBuffer();
    const out = new NextResponse(body, { status: res.status });
    const resType = res.headers.get("content-type");
    if (resType) out.headers.set("content-type", resType);
    return out;
  } catch {
    return NextResponse.json(OFFLINE_BODY, { status: 503 });
  }
}

type RouteContext = { params: Promise<{ path: string[] }> };

export async function GET(request: NextRequest, context: RouteContext) {
  const { path } = await context.params;
  return proxy(request, path);
}

export async function POST(request: NextRequest, context: RouteContext) {
  const { path } = await context.params;
  return proxy(request, path);
}

export async function PUT(request: NextRequest, context: RouteContext) {
  const { path } = await context.params;
  return proxy(request, path);
}

export async function PATCH(request: NextRequest, context: RouteContext) {
  const { path } = await context.params;
  return proxy(request, path);
}

export async function DELETE(request: NextRequest, context: RouteContext) {
  const { path } = await context.params;
  return proxy(request, path);
}
