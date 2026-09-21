import { useEffect, useRef, useState } from 'react';
import type { FormEvent, ReactNode } from 'react';

type Mode = 'offline' | 'live';
type Config = { default_mode: Mode; base_url: string; model: string; configured: boolean; demo_date: string; email_mode: string };
type Credentials = { session_id: string; access_token: string };
type Snapshot = {
  session_id: string; access_token?: string;
  state: { phase: string; status: string; verified: boolean; matched_fields: string[]; identity_collected: string[]; intent: string | null; case_hints: Record<string, unknown>; selected_case_id: string | null; language: string; email_status: string; model_mode: Mode; demo_date: string; pending: string | null };
  messages: { role: 'user' | 'assistant'; content: string; turn_id?: string }[];
  events: { kind: string; phase: string; detail: unknown; at: string }[];
  email_summary: { subject: string; body: string; to_masked: string; status: string } | null;
};
const STORAGE_KEY = 'claims-companion-session';
const PHASES = [
  { id: 'VERIFY_ID', title: 'Verify identity', description: 'Protect customer information', icon: 'shield' },
  { id: 'RESOLVE_INTENT', title: 'Understand the request', description: 'Use context to find the right case', icon: 'search' },
  { id: 'PROCESS_CASE', title: 'Support the claim', description: 'Answer using verified case data', icon: 'file' },
  { id: 'POST_PROCESS', title: 'Wrap up together', description: 'Offer an optional email summary', icon: 'mail' },
] as const;
const COMPLETE_SAMPLE = 'I’m the policyholder. My name is Margaret Chen, policy POL-9921. I’m calling about my denied healthcare claim from January. DOB is 1985-03-15, SSN last four is 4472.';
const PARTIAL_SAMPLE = 'My name is Margaret Chen. I’m calling about my denied healthcare claim from January, but I don’t want to share my SSN.';
const LABELS: Record<string, string> = { name: 'Full name', full_name: 'Full name', dob: 'Date of birth', date_of_birth: 'Date of birth', phone: 'Phone', email: 'Email', ssn_last4: 'SSN last four', id_last4: 'ID last four', policy_number: 'Policy number' };

function Icon({ name, size = 20 }: { name: string; size?: number }) {
  const paths: Record<string, ReactNode> = {
    shield: <><path d="M12 3 4 6v6c0 4 8 9 8 9s8-5 8-9V6l-8-3Z"/><path d="m8 12 3 3 5-6"/></>,
    search: <><circle cx="10.5" cy="10.5" r="6.5"/><path d="m16 16 5 5"/></>,
    file: <><path d="M14 3H5v18h14V8l-5-5Z"/><path d="M14 3v5h5M8 12h8M8 16h6"/></>,
    mail: <><rect x="3" y="5" width="18" height="14" rx="3"/><path d="m4 7 8 6 8-6"/></>,
    send: <><path d="m3 11 18-8-8 18-2-8-8-2Z"/><path d="m11 13 10-10"/></>,
    plus: <path d="M12 5v14M5 12h14"/>,
    close: <path d="m6 6 12 12M6 18 18 6"/>,
    check: <path d="m5 12 4 4L19 6"/>,
    key: <><circle cx="8" cy="9" r="5"/><path d="m12 13 8 8m-4-4 3-3m-6 0 3-3"/></>,
    arrow: <path d="M4 12h16m-6-6 6 6-6 6"/>,
    spark: <><path d="m12 3 2.5 6.5L21 12l-6.5 2.5L12 21l-2.5-6.5L3 12l6.5-2.5L12 3Z"/></>,
    info: <><circle cx="12" cy="12" r="9"/><path d="M12 11v6M12 7h.01"/></>,
    refresh: <><path d="M20 7v5h-5M4 17v-5h5"/><path d="M6 7a7 7 0 0 1 12-1l2 3M4 15l2 3a7 7 0 0 0 12-1"/></>,
  };
  return <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.65" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">{paths[name] || paths.file}</svg>;
}
async function api<T>(path: string, options: RequestInit = {}, token?: string): Promise<T> {
  const response = await fetch(path, { ...options, headers: { 'Content-Type': 'application/json', ...(token ? { Authorization: `Bearer ${token}` } : {}), ...options.headers } });
  const body = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(typeof body.detail === 'string' ? body.detail : `The request could not be completed (${response.status}). Please try again.`);
  return body as T;
}
function display(value: unknown): string { return typeof value === 'string' ? value.replaceAll('_', ' ') : JSON.stringify(value); }
function savedCredentials(): Credentials | null { try { return JSON.parse(sessionStorage.getItem(STORAGE_KEY) || 'null'); } catch { return null; } }

export default function App() {
  const [config, setConfig] = useState<Config | null>(null);
  const [snapshot, setSnapshot] = useState<Snapshot | null>(null);
  const [credentials, setCredentials] = useState<Credentials | null>(null);
  const [input, setInput] = useState('');
  const [busy, setBusy] = useState(false);
  const [booting, setBooting] = useState(true);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [modal, setModal] = useState(false);
  const [tab, setTab] = useState<'overview' | 'activity'>('overview');
  const [apiKey, setApiKey] = useState('');
  const [baseUrl, setBaseUrl] = useState('');
  const [model, setModel] = useState('');
  const [mode, setMode] = useState<Mode>('offline');
  const [modelBusy, setModelBusy] = useState(false);
  const [modelError, setModelError] = useState('');
  const [modelMessage, setModelMessage] = useState('');
  const started = useRef(false);
  const bottom = useRef<HTMLDivElement>(null);
  const textarea = useRef<HTMLTextAreaElement>(null);
  const dialog = useRef<HTMLDialogElement>(null);
  const pendingTurn = useRef<{ message: string; turn_id: string } | null>(null);

  function retainSession(data: Snapshot) {
    setSnapshot(data);
    if (data.access_token) {
      const saved = { session_id: data.session_id, access_token: data.access_token };
      setCredentials(saved);
      sessionStorage.setItem(STORAGE_KEY, JSON.stringify(saved));
    }
  }
  useEffect(() => {
    if (started.current) return;
    started.current = true;
    (async () => {
      try {
        const defaults = await api<Config>('/api/config');
        setConfig(defaults); setBaseUrl(defaults.base_url || ''); setModel(defaults.model || '');
        const saved = savedCredentials();
        if (saved) {
          try { const existing = await api<Snapshot>(`/api/sessions/${saved.session_id}`, {}, saved.access_token); setCredentials(saved); setSnapshot(existing); return; }
          catch { sessionStorage.removeItem(STORAGE_KEY); }
        }
        retainSession(await api<Snapshot>('/api/sessions', { method: 'POST', body: JSON.stringify({ mode: 'offline' }) }));
      } catch (e) { setError(e instanceof Error ? e.message : 'Could not reach the local server.'); }
      finally { setBooting(false); }
    })();
  }, []);
  useEffect(() => { bottom.current?.scrollIntoView({ behavior: 'smooth', block: 'end' }); }, [snapshot?.messages.length, busy]);
  useEffect(() => { if (modal) dialog.current?.showModal(); else dialog.current?.close(); }, [modal]);

  async function newSession() {
    if (busy || modelBusy) return;
    setBusy(true); setError(''); setInput(''); pendingTurn.current = null;
    try {
      retainSession(await api<Snapshot>('/api/sessions', { method: 'POST', body: JSON.stringify({ mode: 'offline' }) }));
      setNotice('New conversation started in offline fixture mode. Connect a model for natural language testing.');
    } catch (e) { setError(e instanceof Error ? e.message : 'Could not start a conversation.'); }
    finally { setBusy(false); }
  }
  async function send(message = input) {
    const clean = message.trim();
    if (!clean || busy || !credentials) return;
    setBusy(true); setError(''); setNotice('');
    const payload = pendingTurn.current?.message === clean ? pendingTurn.current : { message: clean, turn_id: crypto.randomUUID() };
    pendingTurn.current = payload;
    try {
      const next = await api<Snapshot>(`/api/sessions/${credentials.session_id}/messages`, { method: 'POST', body: JSON.stringify(payload) }, credentials.access_token);
      setSnapshot(next); setInput(''); pendingTurn.current = null;
    } catch (e) { setError(e instanceof Error ? e.message : 'Message could not be sent.'); setInput(clean); }
    finally { setBusy(false); textarea.current?.focus(); }
  }
  function openSettings() {
    setMode(snapshot?.state.model_mode || 'offline'); setModelError(''); setModelMessage(''); setApiKey(''); setModal(true);
  }
  function closeSettings() { if (!modelBusy) { setApiKey(''); setModal(false); } }
  async function modelAction(testOnly = false) {
    if (!credentials || modelBusy) return;
    if ((mode === 'live' || testOnly) && !model.trim()) { setModelError('Enter the model name provided by your API service.'); return; }
    setModelBusy(true); setModelError(''); setModelMessage('');
    const payload = { mode: testOnly ? 'live' : mode, ...(apiKey.trim() ? { api_key: apiKey.trim() } : {}), ...(baseUrl.trim() ? { base_url: baseUrl.trim() } : {}), ...(model.trim() ? { model: model.trim() } : {}) };
    try {
      if (testOnly) {
        const result = await api<{ ok: boolean; message: string }>('/api/models/test', { method: 'POST', body: JSON.stringify(payload) });
        if (!result.ok) throw new Error(result.message);
        setModelMessage(`${result.message} ${apiKey ? 'Re-enter your key to apply it to this session.' : ''}`);
      } else {
        const next = mode === 'offline'
          ? await api<Snapshot>(`/api/sessions/${credentials.session_id}/model`, { method: 'DELETE' }, credentials.access_token)
          : await api<Snapshot>(`/api/sessions/${credentials.session_id}/model`, { method: 'POST', body: JSON.stringify(payload) }, credentials.access_token);
        setSnapshot(next); setModal(false); setNotice(mode === 'live' ? 'Live model connected to this session.' : 'Session key cleared. Offline fixture mode is active.');
      }
    } catch (e) { setModelError(e instanceof Error ? e.message : 'Could not configure the model.'); }
    finally { setApiKey(''); setModelBusy(false); }
  }
  function fillSample(value: string) { setInput(value); textarea.current?.focus(); }
  const state = snapshot?.state;
  const phaseIndex = PHASES.findIndex(p => p.id === state?.phase);
  const isLive = state?.model_mode === 'live';
  const isClosed = !!state && ['completed', 'complete', 'closed', 'ended', 'handoff', 'handoff_requested', 'transferred', 'cancelled'].includes(state.status?.toLowerCase());
  const identityFields = [...new Set([...(state?.identity_collected || []), ...(state?.matched_fields || [])])];
  const hints = Object.entries(state?.case_hints || {}).filter(([, value]) => value !== null && value !== '' && value !== undefined);

  return <div className="app-shell">
    <header className="topbar">
      <a className="brand" href="#" onClick={e => e.preventDefault()} aria-label="Claims Companion home"><span className="brand-mark"><Icon name="shield" size={25}/></span><span>Claims Companion<small>INSURANCE SUPPORT · SOP DEMO</small></span></a>
      <div className="header-actions"><span className="local-badge"><span/> Local workspace</span><button className="button subtle" aria-label="Model settings" onClick={openSettings} disabled={booting || !credentials}><Icon name="key" size={17}/><span>Model settings</span></button><button className="button new-button" aria-label="New conversation" onClick={newSession} disabled={busy || booting}><Icon name="plus" size={17}/><span>New conversation</span></button></div>
    </header>
    <main className="workspace">
      <section className="conversation-panel" aria-label="Customer conversation">
        <div className="conversation-heading"><div><div className="eyebrow">A LITTLE CLARITY GOES A LONG WAY</div><h1>Let’s work through your claim.</h1><p>A natural conversation. A protected, step-by-step process.</p></div><span className="conversation-emblem"><Icon name="spark" size={27}/></span></div>
        <div className={`mode-banner ${isLive ? 'live' : ''}`}><Icon name={isLive ? 'spark' : 'info'} size={16}/><span>{isLive ? 'Live AI model · business rules enforced by the server' : 'Offline fixture mode — deterministic, not an LLM'}</span>{!isLive && <button onClick={openSettings} disabled={!credentials}>Connect model <Icon name="arrow" size={13}/></button>}</div>
        <div className="messages" role="log" aria-label="Conversation messages" aria-live="polite">
          {booting && <div className="welcome-state"><div className="loading-dots"><i/><i/><i/></div><p>Preparing your local workspace…</p></div>}
          {!booting && !snapshot?.messages.length && <div className="welcome-state"><span className="welcome-icon"><Icon name="shield" size={28}/></span><h2>Claim support, with care.</h2><p>We’ll verify your identity first, then help you understand your claim and next steps.</p></div>}
          {snapshot?.messages.map((message, index) => <div className={`message-row ${message.role}`} key={`${message.turn_id || index}-${message.role}`}>
            {message.role === 'assistant' && <span className="avatar"><Icon name="shield" size={18}/></span>}
            <div className="message-body"><div className="message-label">{message.role === 'assistant' ? 'Claims Companion' : 'You'}</div><div className="bubble">{message.content}</div></div>
          </div>)}
          {busy && <div className="message-row assistant"><span className="avatar"><Icon name="shield" size={18}/></span><div className="bubble thinking"><span className="loading-dots"><i/><i/><i/></span><span>Working through the next step</span></div></div>}
          {snapshot?.email_summary && <div className="email-preview"><div className="preview-label"><Icon name="mail" size={16}/><strong>Email summary</strong><span className="badge amber">MOCK EMAIL</span></div><h3>{snapshot.email_summary.subject}</h3><p className="email-meta">To: {snapshot.email_summary.to_masked || 'Verified email'} · {display(snapshot.email_summary.status)}</p><div className="email-body">{snapshot.email_summary.body}</div><small>This demo does not deliver email to a real inbox.</small></div>}
          <div ref={bottom}/>
        </div>
        <div className="composer-area">
          {notice && <div className="notice" role="status"><Icon name="check" size={15}/><span>{notice}</span><button aria-label="Dismiss notice" onClick={() => setNotice('')}><Icon name="close" size={14}/></button></div>}
          {error && <div className="error-banner" role="alert"><span>{error}</span>{!credentials ? <button onClick={() => window.location.reload()}>Retry</button> : pendingTurn.current ? <button onClick={() => send(pendingTurn.current!.message)} disabled={busy}>Retry message</button> : <button onClick={() => setError('')}>Dismiss</button>}</div>}
          {state?.phase === 'POST_PROCESS' && !isClosed && <div className="action-choices"><button className="button primary" disabled={busy} onClick={() => send('Yes, send the email summary to my verified email.')}><Icon name="mail" size={16}/>Send mock email</button><button className="button subtle" disabled={busy} onClick={() => send('Skip the email.')}>Skip email</button></div>}
          {state?.phase === 'PROCESS_CASE' && !isClosed && <div className="action-choices"><button className="chip" disabled={busy} onClick={() => send('That is all, please summarize.')}>I’m all set — wrap up <Icon name="arrow" size={14}/></button></div>}
          {(!state || state.phase === 'VERIFY_ID') && !isClosed && <div className="sample-row"><span>TRY A SCENARIO</span><button className="chip" onClick={() => fillSample(COMPLETE_SAMPLE)} disabled={busy}>Complete identity <Icon name="plus" size={13}/></button><button className="chip" onClick={() => fillSample(PARTIAL_SAMPLE)} disabled={busy}>Partial answer & refusal <Icon name="plus" size={13}/></button></div>}
          {isClosed ? <div className="closed-state"><Icon name="check" size={18}/><span>{['handoff', 'handoff_requested'].includes(state?.status || '') ? 'This conversation has been prepared for human support.' : 'This conversation has ended.'}</span><button onClick={newSession}>Start a new one</button></div> : <form className="composer" onSubmit={(e: FormEvent) => { e.preventDefault(); void send(); }}>
            <label className="sr-only" htmlFor="message">Your message</label><textarea id="message" ref={textarea} value={input} onChange={e => setInput(e.target.value)} placeholder="Tell us what you need help with…" rows={2} maxLength={4000} disabled={booting || busy || !credentials} onKeyDown={e => { if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) { e.preventDefault(); void send(); } }}/><button type="submit" className="send-button" aria-label="Send message" disabled={!input.trim() || busy || booting || !credentials}><Icon name="send" size={20}/></button>
          </form>}
          <div className="composer-footer"><span><Icon name="shield" size={12}/> Synthetic customer data · use demo identities only</span><span>Enter to send · Shift + Enter for a new line</span></div>
        </div>
      </section>
      <aside className="inspector" aria-label="Read-only workflow inspector">
        <div className="inspector-header"><div><div className="eyebrow">BEHIND THE CONVERSATION</div><h2>Workflow, made visible.</h2></div><span className="read-only">Read only</span></div>
        <div className="inspector-tabs" role="tablist" aria-label="Workflow information"><button role="tab" aria-selected={tab === 'overview'} onClick={() => setTab('overview')} className={tab === 'overview' ? 'active' : ''}>Overview</button><button role="tab" aria-selected={tab === 'activity'} onClick={() => setTab('activity')} className={tab === 'activity' ? 'active' : ''}>Activity <span>{snapshot?.events.length || 0}</span></button></div>
        <div className="inspector-content">
          {tab === 'overview' ? <>
            <section className="inspector-section"><div className="section-heading"><h3>THE FOUR-STEP SOP</h3><span className="badge purple">{phaseIndex >= 0 ? `${phaseIndex + 1} OF 4` : 'READY'}</span></div><ol className="phase-list">{PHASES.map((phase, index) => <li key={phase.id} className={`${index === phaseIndex ? 'current' : ''} ${index < phaseIndex ? 'done' : ''}`} aria-current={index === phaseIndex ? 'step' : undefined}><span className="phase-node"><Icon name={index < phaseIndex ? 'check' : phase.icon} size={18}/></span><div><strong>{phase.title}</strong><p>{phase.description}</p>{index === phaseIndex && <span className="current-label">{isClosed ? display(state?.status) : 'IN PROGRESS'}</span>}</div></li>)}</ol></section>
            <section className="inspector-section identity-section"><div className="section-heading"><h3>IDENTITY CHECK</h3><span className={`badge ${state?.verified ? 'green' : 'neutral'}`}>{state?.verified ? 'VERIFIED' : 'PROTECTED'}</span></div><div className="identity-number"><strong>{Math.min(state?.matched_fields.length || 0, 3)}<span> / 3</span></strong><span>distinct fields matched</span></div><div className="identity-bars" aria-label={`${state?.matched_fields.length || 0} of 3 identity fields matched`}>{[0, 1, 2].map(i => <span key={i} className={(state?.matched_fields.length || 0) > i ? 'filled' : ''}/>)}</div><p className="muted-note">Claim details stay protected until identity is verified.</p>{identityFields.length > 0 && <div className="field-chips">{identityFields.map(field => <span key={field}><Icon name={state?.matched_fields.includes(field) ? 'check' : 'file'} size={12}/>{LABELS[field] || display(field)}</span>)}</div>}</section>
            <section className="inspector-section"><div className="section-heading"><h3>CONVERSATION MEMORY</h3><Icon name="file" size={16}/></div><p className="muted-note">Useful context is remembered across steps. Identity values are hidden.</p><dl className="memory-list"><div><dt>Intent</dt><dd>{state?.intent ? display(state.intent) : 'Not established yet'}</dd></div><div><dt>Selected case</dt><dd>{state?.selected_case_id || 'Waiting for a verified match'}</dd></div>{hints.map(([key, value]) => <div key={key}><dt>{display(key)}</dt><dd>{display(value)}</dd></div>)}</dl>{state?.pending && <div className="pending-note"><span>AWAITING</span>{display(state.pending)}</div>}</section>
            <section className="inspector-section session-section"><div className="section-heading"><h3>DEMO ENVIRONMENT</h3><span className="status-dot"/></div><dl className="memory-list"><div><dt>Model mode</dt><dd>{isLive ? 'Live API' : 'Offline fixture'}</dd></div><div><dt>Business date</dt><dd>{state?.demo_date || config?.demo_date || '—'}</dd></div><div><dt>Email delivery</dt><dd>Simulated only</dd></div><div><dt>Session</dt><dd className="mono">{snapshot?.session_id.slice(0, 12) || 'Connecting…'}</dd></div></dl><p className="muted-note">The demo date keeps fixture deadlines reproducible.</p></section>
          </> : <section className="inspector-section activity-section"><div className="section-heading"><h3>WORKFLOW EVENTS</h3><span className="badge neutral">SERVER RECORDED</span></div><p className="muted-note">A concise record of decisions and tool activity. No model chain of thought is displayed.</p>{!snapshot?.events.length && <p className="empty-events">Events will appear as the conversation progresses.</p>}<ol className="event-list">{[...(snapshot?.events || [])].reverse().map((event, i) => <li key={`${event.at}-${i}`}><span className="event-dot"/><div><div className="event-title"><strong>{display(event.kind)}</strong><time>{event.at && !Number.isNaN(Date.parse(event.at)) ? new Date(event.at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }) : ''}</time></div><p>{display(event.detail)}</p><small>{event.phase}</small></div></li>)}</ol></section>}
        </div>
        <div className="inspector-bottom"><Icon name="shield" size={15}/><span>Natural language. Explicit boundaries.</span></div>
      </aside>
    </main>
    <dialog ref={dialog} className="settings-dialog" onCancel={e => { e.preventDefault(); closeSettings(); }} onClick={e => { if (e.target === e.currentTarget) closeSettings(); }}>
      <div className="dialog-content"><div className="dialog-header"><span className="settings-icon"><Icon name="key" size={23}/></span><button className="icon-button" onClick={closeSettings} aria-label="Close model settings" disabled={modelBusy}><Icon name="close"/></button></div><div className="eyebrow">YOUR MODEL, YOUR WORKSPACE</div><h2>Connect the conversation.</h2><p className="dialog-intro">Use an OpenAI-compatible API for natural language testing, or explore the fixed demo offline.</p>
        <div className="mode-selector"><button className={mode === 'offline' ? 'selected' : ''} onClick={() => setMode('offline')} disabled={modelBusy}><strong>Offline fixture</strong><span>No key needed · deterministic</span></button><button className={mode === 'live' ? 'selected' : ''} onClick={() => setMode('live')} disabled={modelBusy}><strong>Live AI model</strong><span>Your OpenAI-compatible API</span></button></div>
        {mode === 'live' ? <div className="settings-fields"><label>API base URL<input type="url" value={baseUrl} placeholder="https://api.openai.com/v1" onChange={e => setBaseUrl(e.target.value)} autoComplete="off" disabled={modelBusy}/><small>Use the API base address, including /v1 when required.</small></label><label>Model name<input value={model} placeholder="Enter a model supported by your provider" onChange={e => setModel(e.target.value)} autoComplete="off" disabled={modelBusy}/></label><label>API key <span className="optional">{config?.configured ? 'Server default available' : 'Required unless already configured'}</span><input type="password" value={apiKey} placeholder="Paste your API key" onChange={e => setApiKey(e.target.value)} autoComplete="off" spellCheck={false} disabled={modelBusy}/></label><div className="privacy-note"><Icon name="shield" size={17}/><p>Your key is submitted to the backend and cleared from this form after each request. It is never saved in browser storage. Live mode sends relevant conversation context to your model provider.</p></div></div> : <div className="offline-explanation"><Icon name="info" size={20}/><p><strong>This mode does not use an LLM.</strong> It exercises the same SOP gates with deterministic fixture handling. Switch to a live model to evaluate natural language flexibility. Selecting offline clears this session’s model key.</p></div>}
        {modelError && <div className="error-banner" role="alert">{modelError}</div>}{modelMessage && <div className="notice" role="status">{modelMessage}</div>}
        <div className="dialog-footer">{mode === 'live' && <button className="button subtle" disabled={modelBusy} onClick={() => modelAction(true)}><Icon name="refresh" size={16}/>Test connection</button>}<button className="button primary" disabled={modelBusy} onClick={() => modelAction()}>{modelBusy ? 'Connecting…' : mode === 'live' ? 'Use live model' : 'Use offline mode'}<Icon name="arrow" size={17}/></button></div>
      </div>
    </dialog>
  </div>;
}
