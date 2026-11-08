import { NextResponse } from 'next/server';

export async function GET(request) {
  const url = new URL(request.url);
  const state = url.searchParams.get('state');
  const expected = request.cookies.get('replyline_google_oauth_state')?.value;
  if (!state || !expected || state !== expected) return NextResponse.json({ detail: 'Invalid or expired Google OAuth state.' }, { status: 400 });
  if (url.searchParams.get('error')) return NextResponse.redirect(new URL(`/app?google_error=${encodeURIComponent(url.searchParams.get('error'))}`, url));
  // Token exchange and encrypted, per-tenant storage belong in the authenticated
  // backend. This route intentionally never returns or stores a refresh token.
  return NextResponse.redirect(new URL('/app?google=callback_received', url));
}
