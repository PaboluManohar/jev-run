/**
 * Thin proxy — forwards all /api/studio/* requests to the Python FastAPI backend.
 *
 * This replaces the previous handler that imported MobilerunDevice and the
 * server-side store directly.  The Next.js frontend continues to call the same
 * /api/studio/* paths; this route simply relays them to the FastAPI server.
 */

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

const BACKEND_URL = process.env.FASTAPI_URL || 'http://127.0.0.1:8000';

async function handle(request: Request, context: { params: Promise<{ path: string[] }> }) {
  const { path } = await context.params;
  const targetPath = `/api/studio/${path.join('/')}`;
  const targetUrl = `${BACKEND_URL}${targetPath}`;

  try {
    // Forward the request method, headers, and body to FastAPI.
    const headers: Record<string, string> = {
      'Content-Type': request.headers.get('Content-Type') || 'application/json',
    };

    const init: RequestInit = {
      method: request.method,
      headers,
    };

    // Forward the body for POST/PUT/PATCH.
    if (['POST', 'PUT', 'PATCH'].includes(request.method)) {
      init.body = await request.text();
    }

    const response = await fetch(targetUrl, init);

    // For SSE endpoints, stream the response directly.
    const contentType = response.headers.get('Content-Type') || '';
    if (contentType.includes('text/event-stream')) {
      return new Response(response.body, {
        status: response.status,
        headers: {
          'Content-Type': 'text/event-stream',
          'Cache-Control': 'no-cache, no-transform',
          'Connection': 'keep-alive',
        },
      });
    }

    // For regular responses, relay status and body.
    const body = await response.text();
    return new Response(body, {
      status: response.status,
      headers: {
        'Content-Type': contentType || 'application/json',
        'Cache-Control': 'no-store',
      },
    });
  } catch {
    return Response.json(
      {
        error:
          'Backend is unavailable. Ensure the FastAPI server is running on ' +
          BACKEND_URL +
          ' (cd backend && uvicorn backend.main:app --port 8000).',
      },
      { status: 502 },
    );
  }
}

export const GET = handle;
export const POST = handle;
