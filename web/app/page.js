'use client';

import { useMemo, useState } from 'react';

const review = 'The treatment was good, but I waited almost 40 minutes after my appointment time. The staff were kind, but the delay was frustrating.';

function draftReply({ business, rating, customerReview, tone }) {
  const name = 'Ananya';
  const isNegative = rating <= 2;
  const isMixed = rating === 3;
  const voice = {
    warm: { thank: `Thank you for sharing this, ${name}.`, close: 'We appreciate the opportunity to make this right.' },
    direct: { thank: `Thank you, ${name}.`, close: 'We would appreciate the chance to discuss this with you.' },
    polished: { thank: `${name}, thank you for taking the time to leave detailed feedback.`, close: 'We would welcome the opportunity to address this directly.' },
  }[tone];
  if (isNegative || isMixed) return `${voice.thank} We are sorry that your visit to ${business || 'our practice'} did not run as smoothly as it should have. Waiting beyond your appointment time is frustrating, and your feedback helps us improve. Please contact us directly so we can understand the details and follow up personally. ${voice.close}`;
  return `${voice.thank} We are grateful you chose ${business || 'us'} and delighted to hear about your experience. Your support means a great deal to our team. We look forward to welcoming you again.`;
}

const proof = [
  ['Draft in your voice', 'Choose warm, direct, or polished. Each reply stays editable before it is published.'],
  ['Handle difficult reviews carefully', 'Acknowledge the experience, move details offline, and never argue in public.'],
  ['Keep the final say', 'Replyline drafts. You approve and post through your verified Google Business Profile.'],
];

export default function Home() {
  const [rating, setRating] = useState(2);
  const [tone, setTone] = useState('warm');
  const [business, setBusiness] = useState('Harbor Dental');
  const [customerReview, setCustomerReview] = useState(review);
  const [generated, setGenerated] = useState(true);
  const [editedReply, setEditedReply] = useState('');
  const [copied, setCopied] = useState(false);
  const reply = useMemo(() => draftReply({ business, rating, customerReview, tone }), [business, rating, customerReview, tone]);
  const scrollToDemo = () => document.getElementById('demo')?.scrollIntoView({ behavior: 'smooth' });
  const activeReply = editedReply || reply;
  const copy = async () => { await navigator.clipboard.writeText(activeReply); setCopied(true); setTimeout(() => setCopied(false), 1600); };

  return <>
    <header className="nav"><a href="#top" className="logo"><span>R</span> REPLYLINE</a><nav><a href="#how">How it works</a><a href="#for">Who it&apos;s for</a><a href="#faq">FAQ</a></nav><button onClick={scrollToDemo} className="navCta">Write a free reply <b>→</b></button></header>
    <main id="top">
      <section className="hero">
        <div className="heroCopy"><p className="eyebrow">GOOGLE REVIEW REPLIES, WITHOUT THE BLANK PAGE</p><h1>Every review is<br />a chance to show<br /><em>you care.</em></h1><p className="subhead">Write thoughtful Google review replies in minutes — including the hard ones. Keep your voice, keep the final say.</p><div className="heroActions"><button onClick={scrollToDemo} className="primary">Write a free reply <b>→</b></button><span>No account. No card. Starts in your browser.</span></div><div className="microProof"><span>✦ Edit every word</span><span>✦ Post only when ready</span><span>✦ Built for local businesses</span></div></div>
        <div className="heroVisual" aria-label="Replyline product preview"><div className="browserBar"><i></i><i></i><i></i><span>replyline.app</span></div><div className="previewBody"><div className="previewHeader"><span className="avatar">HD</span><div><b>Harbor Dental</b><small>Reply to review</small></div><span className="live">DRAFT</span></div><div className="reviewCard"><div><strong>Ananya P.</strong><span className="stars">★★☆☆☆</span></div><p>{review}</p></div><div className="replyCard"><span>YOUR REPLY</span><p>{draftReply({ business: 'Harbor Dental', rating: 2, customerReview: review, tone: 'warm' })}</p><div><small>Edited for your voice</small><button>Ready to copy</button></div></div></div></div>
      </section>
      <section className="logos"><p>ONE SIMPLE JOB: HELP LOCAL BUSINESSES REPLY WITH CARE.</p><div><span>CLINICS</span><span>RESTAURANTS</span><span>SALONS</span><span>HOME SERVICES</span><span>HOSPITALITY</span></div></section>
      <section className="problem"><div className="sectionIntro"><p className="eyebrow">A PUBLIC MOMENT, EVERY TIME</p><h2>Silence says something.<br /><em>A generic reply does too.</em></h2></div><div className="problemGrid"><article><span>01</span><h3>The review sits there.</h3><p>A great review goes unanswered because someone was busy. A difficult one becomes more visible with every passing day.</p></article><article><span>02</span><h3>The words are hard.</h3><p>Owners want to be human and professional, especially when a customer is upset. Starting from zero makes it easy to delay.</p></article><article><span>03</span><h3>The reply is public.</h3><p>Future customers read how you respond. That calls for a calm, clear answer — not a rushed template.</p></article></div></section>
      <section id="how" className="how"><div className="sectionIntro"><p className="eyebrow">A BETTER WAY TO RESPOND</p><h2>From review to ready<br /><em>in three small steps.</em></h2></div><div className="steps"><article><span>1</span><h3>Paste the review</h3><p>Bring in the customer&apos;s words. No account connection is needed to try it.</p></article><article><span>2</span><h3>Set your voice</h3><p>Choose the tone that sounds like your business, then get a useful first draft.</p></article><article><span>3</span><h3>Edit, approve, post</h3><p>Make it yours. Copy the final reply and post it through your verified Google Business Profile.</p></article></div></section>
      <section id="demo" className="demo"><div className="demoTop"><div><p className="eyebrow">TRY IT ON A REAL REVIEW</p><h2>See the response<br /><em>before you sign up.</em></h2></div><p>Nothing is sent or posted. This is a local preview for your browser.</p></div><div className="demoGrid"><form onSubmit={e => { e.preventDefault(); setEditedReply(reply); setGenerated(true); }} className="controls"><label>Business name<input value={business} onChange={e => { setBusiness(e.target.value); setGenerated(false); }} placeholder="Your business" /></label><label>Review rating<div className="rating" role="radiogroup">{[1,2,3,4,5].map(n => <button key={n} type="button" aria-pressed={rating === n} onClick={() => { setRating(n); setGenerated(false); }} className={n <= rating ? 'on' : ''}>★</button>)}</div></label><label>Reply tone<select value={tone} onChange={e => { setTone(e.target.value); setGenerated(false); }}><option value="warm">Warm & professional</option><option value="direct">Brief & direct</option><option value="polished">Premium & polished</option></select></label><label>Customer review<textarea value={customerReview} onChange={e => { setCustomerReview(e.target.value); setGenerated(false); }} /></label><button className="primary" type="submit">Generate my reply <b>→</b></button></form><div className="output"><div className="outputTop"><span>REPLYLINE DRAFT</span><small>{generated ? 'READY FOR YOUR REVIEW' : 'UPDATE INPUTS, THEN GENERATE'}</small></div><textarea aria-label="Generated reply" value={generated ? activeReply : ''} onChange={e => setEditedReply(e.target.value)} placeholder="Your draft will appear here." /><div className="outputFoot"><span>✦ You approve every reply before it is posted.</span><button onClick={copy} disabled={!generated}>{copied ? 'Copied ✓' : 'Copy reply'}</button></div></div></div></section>
      <section id="for" className="proof"><div className="proofVisual"><div className="score"><span>NEGATIVE REVIEW</span><b>★☆☆☆☆</b><p>“We are sorry your visit did not meet expectations. Please contact us directly…”</p></div><div className="arrow">→</div><div className="score after"><span>OWNER RESPONSE</span><b>CALM. CLEAR. HUMAN.</b><p>Drafted in a voice you can edit and approve.</p></div></div><div><p className="eyebrow">MADE FOR THE MOMENTS THAT MATTER</p><h2>Professional enough for your brand.<br /><em>Simple enough to use today.</em></h2>{proof.map(([title, description]) => <div className="proofItem" key={title}><b>{title}</b><p>{description}</p></div>)}</div></section>
      <section className="trust"><div><p className="eyebrow">CONTROL IS NOT OPTIONAL</p><h2>Your reputation.<br /><em>Your final approval.</em></h2></div><div className="trustList"><p><b>No automatic posting.</b> Replyline prepares a draft. You decide what gets published.</p><p><b>No invented reviews.</b> It is designed for authentic replies to feedback your customers already gave.</p><p><b>Made for Google&apos;s workflow.</b> You post through your own verified Business Profile, where replies are public and reviewed by Google.</p></div></section>
      <section className="offer"><p className="eyebrow">START WITH THE WORK, NOT A SALES CALL</p><h2>Write your next<br /><em>reply for free.</em></h2><p>Get a thoughtful starting point now. The reply workspace has no account wall in this MVP.</p><button onClick={scrollToDemo} className="primary light">Write a free reply <b>→</b></button><small>No card required · Nothing posts automatically</small></section>
      <section id="faq" className="faq"><div><p className="eyebrow">QUESTIONS, ANSWERED</p><h2>Before you reply.</h2></div><div>{[['Does Replyline post responses to Google?', 'No. Replyline prepares and copies a draft. You review it and post it from your verified Google Business Profile.'], ['Do I need to connect my Google account?', 'Not to try the free reply preview. The current MVP runs the preview in your browser.'], ['Can I use it for a one-star review?', 'Yes. The tool is especially useful when you need a calm starting point. It will never pretend a problem did not happen; edit the draft to match the facts.'], ['Will this help me get more reviews?', 'Replyline helps you respond to existing feedback. It does not create, solicit, filter, or incentivise reviews.'], ['What is coming next?', 'A paid workspace for saved brand voice, team review queues, and approved Google Business Profile integrations. We will only promise features once they are ready.']].map(([q,a]) => <details key={q}><summary>{q}<b>+</b></summary><p>{a}</p></details>)}</div></section>
    </main><footer><a className="logo" href="#top"><span>R</span> REPLYLINE</a><p>Thoughtful replies for local businesses.</p><button onClick={scrollToDemo}>Write a free reply →</button></footer>
  </>;
}
