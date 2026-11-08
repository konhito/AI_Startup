import { NextResponse } from 'next/server';

export async function POST(request) {
  const body = await request.json().catch(() => null);
  if (!body || typeof body.reviewName !== 'string' || typeof body.comment !== 'string' || !body.comment.trim()) return NextResponse.json({ detail: 'reviewName and a non-empty comment are required.' }, { status: 400 });
  if (Buffer.byteLength(body.comment, 'utf8') > 4096) return NextResponse.json({ detail: 'Google review replies must be 4096 bytes or fewer.' }, { status: 400 });
  // A production implementation obtains this token from encrypted per-tenant
  // OAuth storage after the account owner gives permission. Never accept it from the browser.
  return NextResponse.json({ detail: 'Google posting is not configured for this account. Complete OAuth and encrypted token storage first.' }, { status: 503 });
}
