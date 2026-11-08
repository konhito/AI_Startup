import { NextResponse } from 'next/server';
import crypto from 'node:crypto';

export async function GET(request) {
  const clientId = process.env.GOOGLE_CLIENT_ID;
  const baseUrl = process.env.NEXT_PUBLIC_APP_URL || new URL(request.url).origin;
  if (!clientId) return NextResponse.json({ detail: 'Google OAuth is not configured. Set GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET, and NEXT_PUBLIC_APP_URL before enabling customer connections.' }, { status: 503 });
  const state = crypto.randomUUID();
  const url = new URL('https://accounts.google.com/o/oauth2/v2/auth');
  url.searchParams.set('client_id', clientId);
  url.searchParams.set('redirect_uri', `${baseUrl}/api/google/callback`);
  url.searchParams.set('response_type', 'code');
  url.searchParams.set('access_type', 'offline');
  url.searchParams.set('prompt', 'consent');
  url.searchParams.set('scope', 'https://www.googleapis.com/auth/business.manage');
  url.searchParams.set('state', state);
  const response = NextResponse.redirect(url);
  response.cookies.set('replyline_google_oauth_state', state, { httpOnly: true, secure: process.env.NODE_ENV === 'production', sameSite: 'lax', maxAge: 600, path: '/' });
  return response;
}
