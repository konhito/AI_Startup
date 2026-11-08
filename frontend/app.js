'use strict';
const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => [...document.querySelectorAll(selector)];
const state = { user: null, authMode: 'register', messages: [], busy: false, pricing: null, activePanel: 'chat' };
const money = (micro, precision = 4) => new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD', maximumFractionDigits: precision, minimumFractionDigits: 2 }).format((Number(micro) || 0) / 1e6);
const number = (value) => new Intl.NumberFormat('en-US').format(Number(value) || 0);
const date = (value) => value ? new Date(value).toLocaleDateString(undefined, { month: 'short', day: 'numeric' }) : '—';
async function api(path, options = {}) {
  const response = await fetch(path, { credentials: 'same-origin', ...options, headers: { 'Content-Type': 'application/json', ...options.headers } });
  const body = await response.json().catch(() => ({}));
  if (!response.ok) { const detail = body.detail || body.error?.message || body.error; const fallback = response.status === 429 ? 'ArliAI’s free route is busy. Wait about one minute, then send one message.' : `Request failed (${response.status}). Please try again.`; const error = new Error(typeof detail === 'string' ? detail : Array.isArray(detail) ? detail.map(item => item.msg).join('; ') : fallback); error.status = response.status; throw error; }
  return body;
}
function toast(message) { $('#toast').textContent = message; $('#toast').hidden = false; clearTimeout(toast.timer); toast.timer = setTimeout(() => $('#toast').hidden = true, 3500); }
async function copy(text) { try { await navigator.clipboard.writeText(text); toast('Copied to clipboard.'); } catch { toast('Clipboard unavailable. Select and copy the text manually.'); } }
function showWorkspace(updateHash = true) { $('#marketing').hidden = true; $('#workspace').hidden = false; if (updateHash) location.hash = 'workspace'; window.scrollTo(0, 0); }
function showMarketing() { $('#workspace').hidden = true; $('#marketing').hidden = false; }
function route() { if (location.hash === '#workspace') showWorkspace(false); else showMarketing(); }
window.addEventListener('hashchange', route);
$$('[data-open-app]').forEach(button => button.addEventListener('click', () => showWorkspace()));
$('#back-home').addEventListener('click', () => { location.hash = 'home'; });
$('#year').textContent = new Date().getFullYear();
$('#api-base').textContent = `${location.origin}/v1`;
$('#example-code').textContent = $('#example-code').textContent.replace('YOUR_UNBOUND_URL', location.origin);
$('#copy-example').addEventListener('click', () => copy($('#example-code').textContent));
const glm53Option = document.createElement('option');
glm53Option.value = 'glm-5.3';
glm53Option.textContent = 'GLM-5.3 · OpenRouter';
glm53Option.title = 'Explicit GLM-5.3 reasoning and coding route through OpenRouter';
$('#chat-model').querySelector('[value="glm"]').before(glm53Option);
const qwenOption = document.createElement('option');
qwenOption.value = 'qwen-derestricted';
qwenOption.textContent = 'Qwen3.5 Derestricted · ArliAI';
qwenOption.title = 'Free-plan hosted chat through ArliAI';
$('#chat-model').querySelector('[value="glm-5.3"]').before(qwenOption);
function setAuthMode(mode) { state.authMode = mode; const register = mode === 'register'; $('#name-label').hidden = !register; $('[name=name]').required = register; $('[name=password]').autocomplete = register ? 'new-password' : 'current-password'; $('[name=password]').minLength = register ? 12 : 1; $('[name=password]').placeholder = register ? 'At least 12 characters' : 'Your password'; $('#auth-submit').textContent = register ? 'Create account ↗' : 'Sign in ↗'; $('#auth-error').textContent = ''; ['register', 'login'].forEach(value => { $(`#${value}-tab`).classList.toggle('active', mode === value); $(`#${value}-tab`).setAttribute('aria-selected', String(mode === value)); }); }
$('#register-tab').addEventListener('click', () => setAuthMode('register'));
$('#login-tab').addEventListener('click', () => setAuthMode('login'));
function updateAccount(data) { state.user = data.user; $('#auth-panel').hidden = true; $('#dashboard').hidden = false; $('#user-name').textContent = data.user.name || data.user.email; const mock = data.inference_mode === 'mock'; $('#mode-label').textContent = mock ? 'MOCK INFERENCE · NO GPU' : data.inference_mode === 'arliai' ? 'ARLIAI · QWEN3.5' : data.inference_mode === 'openrouter' ? 'OPENROUTER + GLM' : 'SELF-HOSTED INFERENCE'; $('#chat-mode-description').textContent = mock ? 'Demo mode: responses are simulated. Add an ArliAI key to enable the free hosted Qwen chatbot.' : data.inference_mode === 'arliai' ? 'Chat with Qwen3.5 Derestricted through ArliAI. Conversations stay in this browser session.' : 'Auto sends your messages to the configured hosted model. Self-hosted GLM runs on demand; startup can take time.'; $('#credit-balance').textContent = money(data.wallet?.available_micro_usd); $('#held-balance').textContent = `${money(data.wallet?.held_micro_usd)} reserved for active requests`; let testBalance = $('#test-balance'); if (!testBalance) { testBalance = document.createElement('small'); testBalance.id = 'test-balance'; $('#held-balance').after(testBalance); } const amount = Number(data.wallet?.test_balance_micro_usd) || 0; testBalance.textContent = amount ? `${money(amount)} test credit · cannot fund real inference` : ''; }
async function refreshMe() { const data = await api('/api/me'); updateAccount(data); return data; }
$('#auth-form').addEventListener('submit', async event => { event.preventDefault(); const button = $('#auth-submit'); button.disabled = true; $('#auth-error').textContent = ''; try { const fields = new FormData(event.target); const payload = { email: fields.get('email'), password: fields.get('password') }; if (state.authMode === 'register') payload.name = fields.get('name'); await api(`/api/auth/${state.authMode}`, { method: 'POST', body: JSON.stringify(payload) }); await refreshMe(); event.target.reset(); await loadPanel(state.activePanel); await handleCheckoutReturn(); } catch (error) { $('#auth-error').textContent = error.message; } finally { button.disabled = false; } });
$('#logout').addEventListener('click', async () => { try { await api('/api/auth/logout', { method: 'POST' }); state.user = null; clearTimeout(billingState.poll); billingState.returnHandled = false; billingState.requestKeys.clear(); $('#checkout-result').hidden = true; state.messages = []; $('#dashboard').hidden = true; $('#auth-panel').hidden = false; dismissKey(); clearChat(); } catch (error) { toast(error.message); } });
async function loadPanel(panel) { state.activePanel = panel; $$('[data-panel]').forEach(tab => { const active = tab.dataset.panel === panel; tab.classList.toggle('active', active); tab.setAttribute('aria-selected', String(active)); }); $$('.app-panel').forEach(element => element.hidden = element.id !== `panel-${panel}`); $('#dashboard-error').textContent = ''; try { if (panel === 'keys') await loadKeys(); if (panel === 'usage') await loadUsage(); } catch (error) { $('#dashboard-error').textContent = error.message; } }
$$('[data-panel]').forEach(button => button.addEventListener('click', () => loadPanel(button.dataset.panel)));
function cell(row, text) { const td = document.createElement('td'); td.textContent = text; row.append(td); return td; }
function emptyTable(selector, columns, text) { const body = $(selector); body.replaceChildren(); const row = document.createElement('tr'); const td = cell(row, text); td.colSpan = columns; td.className = 'empty-row'; body.append(row); }
async function loadKeys() { emptyTable('#keys-body', 5, 'Loading your keys…'); const data = await api('/api/keys'); $('#keys-body').replaceChildren(); if (!data.keys?.length) { emptyTable('#keys-body', 5, 'No API keys yet. Create one to connect your first project.'); return; } data.keys.forEach(key => { const row = document.createElement('tr'); cell(row, key.name); cell(row, `${key.prefix}…`); cell(row, date(key.created_at)); cell(row, key.revoked_at ? 'Revoked' : 'Active'); const actions = cell(row, ''); if (!key.revoked_at) { const button = document.createElement('button'); button.className = 'text-link'; button.textContent = 'Revoke'; button.setAttribute('aria-label', `Revoke ${key.name}`); button.addEventListener('click', async () => { if (!confirm(`Revoke “${key.name}”? Applications using this key will immediately lose access.`)) return; button.disabled = true; try { await api(`/api/keys/${encodeURIComponent(key.id)}`, { method: 'DELETE' }); await loadKeys(); toast('API key revoked.'); } catch (error) { $('#dashboard-error').textContent = error.message; button.disabled = false; } }); actions.append(button); } $('#keys-body').append(row); }); }
$('#key-form').addEventListener('submit', async event => { event.preventDefault(); const button = event.target.querySelector('button'); button.disabled = true; $('#dashboard-error').textContent = ''; try { const data = await api('/api/keys', { method: 'POST', body: JSON.stringify({ name: $('#key-name').value.trim() }) }); $('#secret-key').textContent = data.key; $('#new-key').hidden = false; $('#key-name').value = ''; await loadKeys(); } catch (error) { $('#dashboard-error').textContent = error.message; } finally { button.disabled = false; } });
function dismissKey() { $('#secret-key').textContent = ''; $('#new-key').hidden = true; }
$('#dismiss-key').addEventListener('click', dismissKey);
$('#copy-key').addEventListener('click', () => copy($('#secret-key').textContent));
function renderDailyActivity(daily) {
  const end = new Date(); const today = Date.UTC(end.getUTCFullYear(), end.getUTCMonth(), end.getUTCDate());
  const byDate = new Map((daily || []).map(day => [String(day.date).slice(0, 10), day]));
  const days = Array.from({length: 30}, (_, index) => { const day = new Date(today - (29 - index) * 86400000).toISOString().slice(0, 10); return {date: day, input_tokens: 0, output_tokens: 0, requests: 0, cost_micro_usd: 0, ...byDate.get(day)}; });
  const peak = Math.max(0, ...days.map(day => Number(day.input_tokens) + Number(day.output_tokens)));
  $('#token-chart').replaceChildren(); $('#daily-body').replaceChildren();
  $('#activity-empty').hidden = peak > 0;
  $('#chart-start').textContent = days[0].date; $('#chart-end').textContent = days[29].date;
  days.forEach(day => {
    const column = document.createElement('div'); column.className = 'chart-column';
    const description = `${day.date}: ${number(day.input_tokens)} input tokens, ${number(day.output_tokens)} output tokens, ${number(day.requests)} requests`;
    column.title = description; column.setAttribute('role', 'img'); column.setAttribute('aria-label', description);
    const input = document.createElement('span'); input.className = 'chart-input'; input.style.height = `${peak ? Number(day.input_tokens) / peak * 100 : 0}%`;
    const output = document.createElement('span'); output.className = 'chart-output'; output.style.height = `${peak ? Number(day.output_tokens) / peak * 100 : 0}%`;
    column.append(output, input); $('#token-chart').append(column);
    const row = document.createElement('tr'); cell(row, day.date); cell(row, number(day.input_tokens)); cell(row, number(day.output_tokens)); cell(row, number(day.requests)); cell(row, money(day.cost_micro_usd, 6)); $('#daily-body').append(row);
  });
}
async function loadLedger() {
  emptyTable('#ledger-body', 4, 'Loading credit transactions…');
  try { const data = await api('/api/ledger'); $('#ledger-body').replaceChildren();
    if (!data.entries?.length) { emptyTable('#ledger-body', 4, 'No credit transactions yet. Credits and usage charges will appear here.'); return; }
    data.entries.forEach(entry => { const row = document.createElement('tr'); cell(row, date(entry.created_at)); cell(row, String(entry.kind || 'Transaction').replaceAll('_', ' ')); cell(row, entry.reference || '—'); const amount = Number(entry.amount_micro_usd) || 0; const amountCell = cell(row, `${amount > 0 ? '+' : ''}${money(amount, 6)}`); amountCell.className = amount > 0 ? 'credit-positive' : ''; $('#ledger-body').append(row); });
  } catch (error) { emptyTable('#ledger-body', 4, `Could not load credit transactions: ${error.message}`); }
}
async function loadUsage() {
  emptyTable('#usage-body', 6, 'Loading request history…');
  const [data] = await Promise.all([api('/api/usage'), refreshMe(), loadLedger(), loadOrders()]);
  $('#input-tokens').textContent = number(data.summary.input_tokens); $('#output-tokens').textContent = number(data.summary.output_tokens); $('#total-cost').textContent = money(data.summary.cost_micro_usd, 6); $('#request-count').textContent = `${number(data.summary.requests)} provider ${data.summary.requests === 1 ? 'attempt' : 'attempts'}`;
  renderDailyActivity(data.daily);
  $('#usage-body').replaceChildren();
  if (!data.recent?.length) { emptyTable('#usage-body', 6, 'Your request history will appear here after your first conversation or API call.'); return; }
  data.recent.forEach(request => { const row = document.createElement('tr'); cell(row, date(request.created_at)); cell(row, request.model); cell(row, number(request.input_tokens)); cell(row, number(request.output_tokens)); cell(row, money(request.cost_micro_usd, 6)); cell(row, request.status); $('#usage-body').append(row); });
}
$('#refresh-usage').addEventListener('click', async event => { event.target.disabled = true; try { await loadUsage(); if (billingState.orderId) await verifyCheckout(false); } catch (error) { $('#dashboard-error').textContent = error.message; } finally { event.target.disabled = false; } });
const emptyChat = $('#chat-empty').cloneNode(true);
function wirePrompts() { $$('[data-prompt]').forEach(button => button.addEventListener('click', () => { $('#chat-input').value = button.dataset.prompt; $('#chat-input').focus(); })); }
wirePrompts();
function clearChat() { if (state.busy) return; state.messages = []; $('#messages').replaceChildren(emptyChat.cloneNode(true)); wirePrompts(); $('#chat-input').value = ''; $('#dashboard-error').textContent = ''; }
$('#new-chat').addEventListener('click', clearChat);
function addMessage(role, text, pending = false) { $('#chat-empty')?.remove(); const element = document.createElement('div'); element.className = `message ${role}${pending ? ' pending' : ''}`; const label = document.createElement('strong'); label.textContent = role === 'user' ? 'You' : pending ? 'Connecting' : 'Unbound'; const content = document.createElement('div'); content.textContent = text; element.append(label, content); $('#messages').append(element); $('#messages').scrollTop = $('#messages').scrollHeight; return element; }
$('#chat-input').addEventListener('keydown', event => { if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) { event.preventDefault(); if (!state.busy) $('#chat-form').requestSubmit(); } });
$('#chat-form').addEventListener('submit', async event => { event.preventDefault(); const content = $('#chat-input').value.trim(); if (!content || state.busy) return; state.busy = true; $('#logout').disabled = true; $('#send-chat').disabled = true; $('#new-chat').disabled = true; $('#chat-input').disabled = true; $('#dashboard-error').textContent = ''; addMessage('user', content); $('#chat-input').value = ''; const requestMessages = [...state.messages, { role: 'user', content }]; const pendingText = state.pricing?.arliai_enabled ? 'Connecting to ArliAI… Free-plan requests may take a moment.' : 'Waiting for your model. On-demand GPU startup may take a few minutes…'; const pending = addMessage('assistant', pendingText, true); try { const response = await api('/api/chat', { method: 'POST', body: JSON.stringify({ model: $('#chat-model').value, messages: requestMessages, max_tokens: state.pricing?.arliai_enabled ? 512 : 1024 }) }); const message = response.choices?.[0]?.message; if (!message || (!message.content && !message.tool_calls && !message.refusal)) throw new Error('The model returned an empty response. Please try again.'); const answer = message.content || message.refusal || `The model requested tool calls. Use an API integration to execute tools.\n\n${JSON.stringify(message.tool_calls, null, 2)}`; pending.remove(); const bubble = addMessage('assistant', answer); if (response.routing) { const route = response.routing; bubble.querySelector('strong').textContent = `${route.provider || 'Unbound'} · ${route.model || response.model || ''}${route.fallback ? ' · fallback' : ''}`; } state.messages = [...requestMessages, { ...message, role: 'assistant' }]; refreshMe().catch(() => {}); } catch (error) { pending.remove(); $('#dashboard-error').textContent = error.message; $('#chat-input').value = content; toast('Request failed. Your message is ready to retry.'); } finally { state.busy = false; $('#logout').disabled = false; $('#send-chat').disabled = false; $('#new-chat').disabled = false; $('#chat-input').disabled = false; $('#chat-input').focus(); } });
async function loadPricing() { try { const data = await api('/api/pricing'); state.pricing = data; $('#input-price').replaceChildren(document.createTextNode(money(data.input_per_million_micro_usd))); $('#output-price').replaceChildren(document.createTextNode(money(data.output_per_million_micro_usd))); ['input', 'output'].forEach(type => { const small = document.createElement('small'); small.textContent = 'per 1M tokens'; $(`#${type}-price`).append(small); }); $('#model-name').textContent = data.arliai_enabled ? 'Qwen3.5 Derestricted' : data.model || 'GLM'; $('#pricing-note').textContent = data.arliai_enabled ? 'The chatbot MVP is connected to the ArliAI free route. Customer token billing is disabled while we validate the product.' : `Self-hosted GLM retail rates (preview). ${data.trial_credit_micro_usd ? `${money(data.trial_credit_micro_usd)} trial credit on signup. ` : ''}${data.inference_mode === 'mock' ? 'This deployment is in mock mode; simulated responses are not charged.' : 'Rates shown are configured for this deployment.'}${data.router_enabled ? ` OpenRouter retail rates (preview): input ${money(data.openrouter_input_per_million_micro_usd)} / output ${money(data.openrouter_output_per_million_micro_usd)} per 1M tokens.` : ''}`; $('#chat-model').value = data.default_model === 'auto' ? 'auto' : 'glm'; $('#routing-note').textContent = data.arliai_enabled ? `Auto uses ArliAI: ${data.arliai_model}. The free plan may queue requests and allows limited concurrency.` : data.router_enabled ? `Auto sends messages to OpenRouter: ${data.openrouter_model || 'configured OpenRouter model'}${data.fallback_enabled ? ' → GLM on temporary provider errors' : ''}. Content refusals do not trigger fallback.` : 'No hosted provider is configured. Auto uses the available local route; a real GPU may need time to start.'; } catch { $('#pricing-note').textContent = 'Pricing is temporarily unavailable. Rates will appear when the API is reachable.'; } }
route();
loadPricing();
loadBillingConfig();
refreshMe().then(handleCheckoutReturn).catch(error => { if (error.status !== 401 && error.status !== 403) $('#auth-error').textContent = 'Could not connect to the workspace API. Please try again shortly.'; });

// Follow the standard keyboard interaction for the workspace and account tabs.
$$('[role="tablist"]').forEach(list => { const tabs = [...list.querySelectorAll('[role="tab"]')]; function activate(index) { tabs.forEach((tab, i) => tab.tabIndex = i === index ? 0 : -1); tabs[index].focus(); tabs[index].click(); } tabs.forEach((tab, index) => { tab.tabIndex = tab.getAttribute('aria-selected') === 'true' ? 0 : -1; tab.addEventListener('click', () => tabs.forEach(other => other.tabIndex = other === tab ? 0 : -1)); tab.addEventListener('keydown', event => { let next; if (event.key === 'ArrowRight') next = (index + 1) % tabs.length; if (event.key === 'ArrowLeft') next = (index - 1 + tabs.length) % tabs.length; if (event.key === 'Home') next = 0; if (event.key === 'End') next = tabs.length - 1; if (next !== undefined) { event.preventDefault(); activate(next); } }); }); });
$$('[data-panel]').forEach(tab => { tab.id = `tab-${tab.dataset.panel}`; tab.setAttribute('aria-controls', `panel-${tab.dataset.panel}`); const panel = $(`#panel-${tab.dataset.panel}`); panel.setAttribute('role', 'tabpanel'); panel.setAttribute('aria-labelledby', tab.id); });

const billingState = { config: null, busy: false, returnHandled: false, poll: null, attempts: 0, orderId: new URLSearchParams(location.search).get('checkout'), canceled: new URLSearchParams(location.search).get('canceled') === '1', requestKeys: new Map() };
async function loadBillingConfig() {
  try {
    const config = await api('/api/billing/config'); billingState.config = config;
    const testing = config.mode === 'test'; const enabled = config.enabled;
    $('#billing-mode').textContent = testing ? 'STRIPE TEST MODE' : enabled ? 'STRIPE CHECKOUT' : 'CHECKOUT DISABLED';
    $('#billing-status').textContent = enabled ? testing ? 'Test mode: use Stripe test payment details only. Any resulting credit is test credit, not a real purchase.' : 'Add prepaid credits through Stripe. Credits appear only after the server verifies your payment.' : 'Credit checkout is not configured for this deployment. Existing credits remain visible; contact the operator to enable billing.';
    $('#public-billing-status').textContent = enabled ? testing ? 'Credit checkout is available in test mode only; no real payments.' : 'Prepaid credit checkout is available through Stripe.' : 'Credit checkout is not configured for this deployment.';
    $('#topup-description').textContent = enabled ? testing ? 'Test credit packs · no real payment. Use this flow to verify your integration.' : 'Choose a credit pack. You will review and complete payment on Stripe.' : 'Credit packs will be available when the deployment operator configures Stripe.';
    $('#credit-packs').replaceChildren();
    if (enabled) config.packs.forEach(pack => {
      const card = document.createElement('article'); card.className = 'credit-pack';
      const name = document.createElement('span'); name.className = 'pack-name'; name.textContent = pack.id;
      const amount = document.createElement('strong'); amount.textContent = money(pack.credit_micro_usd, 2);
      const caption = document.createElement('p'); caption.textContent = testing ? 'test usage credit' : 'prepaid usage credit';
      const button = document.createElement('button'); button.className = 'button outline small'; button.textContent = `${testing ? 'Test checkout' : 'Buy'} · ${money(pack.amount_cents * 10000, 2)} ↗`; button.addEventListener('click', () => beginCheckout(pack.id));
      card.append(name, amount, caption, button); $('#credit-packs').append(card);
    });
  } catch (error) { $('#billing-status').textContent = 'Credit checkout availability could not be loaded. Refresh the page to try again.'; $('#public-billing-status').textContent = 'Credit checkout availability is temporarily unknown.'; $('#topup-description').textContent = 'Checkout is unavailable until the billing configuration can be loaded.'; }
}
async function beginCheckout(packId) {
  if (billingState.busy || !billingState.config?.enabled) return;
  billingState.busy = true; $('#checkout-error').textContent = ''; $$('#credit-packs button').forEach(button => button.disabled = true);
  try {
    if (!billingState.requestKeys.has(packId)) billingState.requestKeys.set(packId, crypto.randomUUID());
    const data = await api('/api/billing/checkout', {method: 'POST', headers: {'Idempotency-Key': billingState.requestKeys.get(packId)}, body: JSON.stringify({pack_id: packId})});
    const destination = new URL(data.url);
    if (destination.protocol !== 'https:' || destination.hostname !== 'checkout.stripe.com') throw new Error('The server returned an unexpected checkout URL. Please contact the operator.');
    location.assign(destination.href);
  } catch (error) { $('#checkout-error').textContent = error.message; billingState.busy = false; $$('#credit-packs button').forEach(button => button.disabled = false); }
}
async function loadOrders() {
  emptyTable('#orders-body', 5, 'Loading credit orders…');
  try { const data = await api('/api/billing/orders'); $('#orders-body').replaceChildren();
    if (!data.orders?.length) { emptyTable('#orders-body', 5, 'No credit orders yet. Checkout sessions will appear here.'); return; }
    data.orders.forEach(order => { const row = document.createElement('tr'); cell(row, date(order.created_at)); const id = cell(row, order.id); id.className = 'order-reference'; cell(row, money(order.amount_cents * 10000, 2)); cell(row, money(order.credit_micro_usd, 2)); cell(row, order.status); $('#orders-body').append(row); });
  } catch (error) { emptyTable('#orders-body', 5, `Could not load credit orders: ${error.message}`); }
}
async function verifyCheckout(poll = false) {
  if (!billingState.orderId || !state.user) return;
  clearTimeout(billingState.poll); $('#verify-checkout').disabled = true; $('#checkout-result').hidden = false;
  try {
    const data = await api(`/api/billing/orders/${encodeURIComponent(billingState.orderId)}`); const order = data.order;
    if (order.status === 'paid') { $('#checkout-result-text').textContent = `Payment verified. ${money(order.credit_micro_usd, 2)} ${billingState.config?.mode === 'test' ? 'test ' : ''}credit has been added to your account.`; await Promise.all([refreshMe(), loadLedger(), loadOrders()]); }
    else if (order.status === 'failed' || order.status === 'expired') { $('#checkout-result-text').textContent = `This checkout is ${order.status}. No credits were added for this order. You can start a new checkout.`; }
    else { $('#checkout-result-text').textContent = billingState.canceled ? 'Checkout was canceled. No credits will be added unless payment is verified. You can check the status or choose another credit pack.' : 'Payment is awaiting server verification. Credits will appear only after payment is confirmed. You can check again below.';
      if (poll && billingState.attempts < 5 && !billingState.canceled) { billingState.attempts += 1; billingState.poll = setTimeout(() => verifyCheckout(true), 2000); }
    }
  } catch (error) { $('#checkout-result-text').textContent = `Payment status could not be verified: ${error.message}. No payment confirmation is assumed.`; }
  finally { $('#verify-checkout').disabled = false; }
}
async function handleCheckoutReturn() {
  if (!billingState.orderId || billingState.returnHandled || !state.user) return;
  billingState.returnHandled = true; showWorkspace(); if (!billingState.config) await loadBillingConfig(); await loadPanel('usage'); await verifyCheckout(true);
}
$('#verify-checkout').addEventListener('click', () => verifyCheckout(false));
